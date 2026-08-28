import os
import sqlite3
import random
import string
import threading
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "aurex_casino_secret_key_2026_secure"

# إعدادات الجلسات لضمان التوافق مع المتصفحات الخارجية
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

DB_NAME = "database.db"

# --- حل مشكلة CORS والاتصال بدون مكتبات خارجية ---
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. جدول البوتات المربوطة بالمنصة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            bot_token TEXT UNIQUE,
            cashier_balance REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. جدول المستخدمين (فصل رصيد البوت عن رصيد الموقع وتطبيق القيد الحصري)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            bot_id INTEGER DEFAULT 1,
            username TEXT,
            site_username TEXT UNIQUE,
            site_password TEXT,
            bot_balance REAL DEFAULT 0.0,
            site_balance REAL DEFAULT 0.0,
            total_spent REAL DEFAULT 0.0,
            deposit_count INTEGER DEFAULT 0,
            withdraw_count INTEGER DEFAULT 0,
            referrals_count INTEGER DEFAULT 0,
            referred_by INTEGER,
            got_welcome_bonus INTEGER DEFAULT 0,
            security_passed INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # تحديث الهيكل في حال وجود قاعدة بيانات قديمة
    cursor.execute("PRAGMA table_info(users)")
    columns = [col['name'] for col in cursor.fetchall()]
    if 'bot_balance' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN bot_balance REAL DEFAULT 0.0")
    if 'site_balance' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN site_balance REAL DEFAULT 0.0")
    if 'bot_id' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN bot_id INTEGER DEFAULT 1")

    # 3. جدول المعاملات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            bot_id INTEGER DEFAULT 1,
            type TEXT,
            method TEXT,
            amount REAL,
            tx_number TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 4. جدول أكواد الهدايا
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount REAL,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    ''')
    
    # 5. سجل الأكواد المستعملة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_codes (
            telegram_id INTEGER,
            code TEXT,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (telegram_id, code)
        )
    ''')

    # 6. جدول وسائط الدفع
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            number TEXT,
            active INTEGER DEFAULT 1
        )
    ''')

    # 7. جدول الإعدادات العامة وخوارزمية التحكم بالموقع
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    defaults = [
        ('maintenance', 'off'),
        ('welcome_bonus', '500'),
        ('referral_bonus', '100'),
        ('global_cashier_balance', '10000.0'),
        ('algo_loss_rate', '50'),       # نسبة الخسارة 50%
        ('algo_normal_rate', '25'),     # نسبة الربح العادي 25%
        ('algo_medium_rate', '15'),     # نسبة الربح المتوسط 15%
        ('algo_high_rate', '8'),        # نسبة الربح العالي 8%
        ('algo_huge_rate', '2')         # نسبة الربح الضخم 2%
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    # البوت الافتراضي الأساسي
    cursor.execute("SELECT * FROM bots WHERE id = 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO bots (id, bot_name, cashier_balance) VALUES (1, 'Main Bot', 10000.0)")

    # أدمن افتراضي
    cursor.execute("SELECT * FROM users WHERE site_username = 'Admin'")
    if not cursor.fetchone():
        cursor.execute('''
            INSERT INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', (999999, 'Admin', 'Admin', 'Admin096', 0.0, 0.0))

    conn.commit()
    conn.close()

init_db()

# --- أدوات مساعدة للكاشيرة والإعدادات ---
def get_bot_cashier(cursor, bot_id=1):
    cursor.execute("SELECT cashier_balance FROM bots WHERE id = ?", (bot_id,))
    row = cursor.fetchone()
    if row:
        return float(row['cashier_balance'])
    cursor.execute("SELECT value FROM settings WHERE key = 'global_cashier_balance'")
    row = cursor.fetchone()
    return float(row['value']) if row else 0.0

def update_bot_cashier(cursor, amount_change, bot_id=1):
    old_balance = get_bot_cashier(cursor, bot_id)
    new_balance = max(0.0, old_balance + amount_change)
    
    cursor.execute("UPDATE bots SET cashier_balance = ? WHERE id = ?", (new_balance, bot_id))
    if cursor.rowcount == 0:
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_cashier_balance', ?)", (str(new_balance),))
    return old_balance, new_balance

def get_setting(cursor, key, default="0"):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row['value'] if row else default

@app.before_request
def check_maintenance():
    if request.method == 'OPTIONS':
        return '', 200
        
    if request.path.startswith('/api/') and not request.path.startswith('/api/admin') and not request.path.startswith('/api/auth'):
        conn = get_db_connection()
        cursor = conn.cursor()
        m = get_setting(cursor, 'maintenance', 'off')
        conn.close()
        if m == 'on':
            return jsonify({'error': 'الموقع في وضع الصيانة حالياً'}), 533

# --- الواجهة الرئيسية ---
@app.route('/')
def home():
    return render_template('index.html')

# --- تسجيل الدخول والتوثيق ---
@app.route('/api/auth/login', methods=['POST'])
def login_site():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'error': 'يرجى إدخال اسم المستخدم وكلمة المرور'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE (site_username = ? OR username = ?) AND site_password = ?", 
                   (username, username, password))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return jsonify({'error': 'اسم المستخدم أو كلمة المرور غير صحيحة'}), 401

    session['user_id'] = user['telegram_id']
    session['is_admin'] = bool(user['is_admin'])

    return jsonify({
        'status': 'success',
        'telegram_id': user['telegram_id'],
        'username': user['site_username'] or user['username'],
        'bot_balance': user['bot_balance'],
        'site_balance': user['site_balance'],
        'got_welcome_bonus': user['got_welcome_bonus'],
        'is_admin': bool(user['is_admin'])
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout_site():
    session.clear()
    return jsonify({'status': 'success', 'message': 'تم تسجيل الخروج بنجاح'})

@app.route('/api/user/account', methods=['GET'])
def get_user_account():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401
    
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user:
        return jsonify({'error': 'الحساب غير موجود'}), 404
    return jsonify(dict(user))

# --- إنشاء حساب واحد فقط لكل عميل ---
@app.route('/api/register_site', methods=['POST'])
def register_site():
    data = request.json or {}
    telegram_id = data.get('telegram_id')
    site_user = data.get('site_user', '').strip()
    site_pass = data.get('site_pass', '').strip()
    bot_id = data.get('bot_id', 1)
    
    if not telegram_id or len(site_user) < 4 or len(site_pass) < 4:
        return jsonify({'error': 'اسم المستخدم وكلمة المرور يجب أن يتجاوزا 4 خانات'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    existing_user = cursor.fetchone()
    
    if existing_user and existing_user['site_username']:
        conn.close()
        return jsonify({'error': 'لديك حساب بالفعل، لا يمكن إنشاء أكثر من حساب واحد بنفس معرف تلجرام'}), 400
        
    try:
        if existing_user:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ?, bot_id = ? WHERE telegram_id = ?",
                           (site_user, str(site_pass), bot_id, telegram_id))
        else:
            cursor.execute('''
                INSERT INTO users (telegram_id, site_username, site_password, bot_id)
                VALUES (?, ?, ?, ?)
            ''', (telegram_id, site_user, str(site_pass), bot_id))
            
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'message': 'تم إنشاء حساب المنصة بنجاح'})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'اسم المستخدم هذا مأخوذ بالفعل، اختر اسماً آخر'}), 400

# --- التحويل بين رصيد البوت ورصيد الموقع ---
@app.route('/api/balance/transfer_to_site', methods=['POST'])
def transfer_to_site():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')
    amount = float(data.get('amount', 0))

    if not telegram_id or amount <= 0:
        return jsonify({'error': 'بيانات التحويل غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT bot_balance, site_balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    if not user or user['bot_balance'] < amount:
        conn.close()
        return jsonify({'error': 'رصيد البوت غير كافٍ للتحويل إلى الموقع'}), 400

    new_bot_bal = user['bot_balance'] - amount
    new_site_bal = user['site_balance'] + amount

    cursor.execute("UPDATE users SET bot_balance = ?, site_balance = ? WHERE telegram_id = ?",
                   (new_bot_bal, new_site_bal, telegram_id))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تم نقل {amount} من رصيد البوت إلى رصيد الموقع',
        'bot_balance': new_bot_bal,
        'site_balance': new_site_bal
    })

@app.route('/api/balance/transfer_to_bot', methods=['POST'])
def transfer_to_bot():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')
    amount = float(data.get('amount', 0))

    if not telegram_id or amount <= 0:
        return jsonify({'error': 'بيانات السحب غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT bot_balance, site_balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    if not user or user['site_balance'] < amount:
        conn.close()
        return jsonify({'error': 'رصيد الموقع غير كافٍ للسحب إلى البوت'}), 400

    new_site_bal = user['site_balance'] - amount
    new_bot_bal = user['bot_balance'] + amount

    cursor.execute("UPDATE users SET bot_balance = ?, site_balance = ? WHERE telegram_id = ?",
                   (new_bot_bal, new_site_bal, telegram_id))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تم نقل {amount} من رصيد الموقع إلى رصيد البوت',
        'bot_balance': new_bot_bal,
        'site_balance': new_site_bal
    })

# --- البونص الترحيبي وخصمه من الكاشيرة إشعاراً وبدقة ---
@app.route('/api/claim_welcome_bonus', methods=['POST'])
def claim_welcome_bonus():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')

    if not telegram_id:
        return jsonify({'error': 'يرجى تسجيل الدخول أولاً'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    if user['got_welcome_bonus'] == 1:
        conn.close()
        return jsonify({'error': 'لقد حصلت على البونص الترحيبي سابقاً'}), 400

    bot_id = user['bot_id'] or 1
    bonus_amount = float(get_setting(cursor, 'welcome_bonus', '500'))
    current_cashier = get_bot_cashier(cursor, bot_id)

    if current_cashier < bonus_amount:
        conn.close()
        return jsonify({'error': 'عذراً، رصيد كاشيرة البوت لا يكفي لإرسال البونص حالياً'}), 400

    old_cashier, new_cashier = update_bot_cashier(cursor, -bonus_amount, bot_id)
    new_bot_balance = user['bot_balance'] + bonus_amount

    cursor.execute("UPDATE users SET bot_balance = ?, got_welcome_bonus = 1 WHERE telegram_id = ?",
                   (new_bot_balance, telegram_id))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': 'تم الحصول على البونص الترحيبي بنجاح',
        'bonus_amount': bonus_amount,
        'old_cashier': old_cashier,
        'new_cashier': new_cashier,
        'new_bot_balance': new_bot_balance,
        'notification': f"تم خصم مبلغ {bonus_amount} من كاشيرة لدخول شخص جديد وحصل على البونص.\nالمبلغ القديم في الكاشيرة: {old_cashier}\nالمبلغ الجديد: {new_cashier}"
    })

# --- خوارزمية اللعب والتحكم بالأرباح والخسائر داخل الموقع حصراً ---
@app.route('/api/play', methods=['POST'])
def play():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'يجب تسجيل الدخول في الموقع لكي تتمكن من دخول الالعاب واللعب'}), 401

    data = request.json or {}
    bet_amount = float(data.get('bet_amount', 0))
    game_id = data.get('game_id', 'slot_default')

    if bet_amount <= 0:
        return jsonify({'error': 'قيمة الرهان غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()

    if not user or user['site_balance'] < bet_amount:
        conn.close()
        return jsonify({'error': 'رصيد الموقع غير كافٍ للعب'}), 400

    bot_id = user['bot_id'] or 1
    # الرهان يذهب إلى رصيد الكاشيرة
    update_bot_cashier(cursor, bet_amount, bot_id)
    site_balance = user['site_balance'] - bet_amount
    total_spent = user['total_spent'] + bet_amount

    p_loss = float(get_setting(cursor, 'algo_loss_rate', '50'))
    p_normal = float(get_setting(cursor, 'algo_normal_rate', '25'))
    p_med = float(get_setting(cursor, 'algo_medium_rate', '15'))
    p_high = float(get_setting(cursor, 'algo_high_rate', '8'))
    p_huge = float(get_setting(cursor, 'algo_huge_rate', '2'))

    roll = random.uniform(0, 100)
    current_cashier = get_bot_cashier(cursor, bot_id)

    multiplier = 0.0
    win_type = "loss"

    if roll < p_loss:
        multiplier = 0.0
        win_type = "loss"
    elif roll < (p_loss + p_normal):
        multiplier = random.uniform(1.2, 1.8)
        win_type = "normal"
    elif roll < (p_loss + p_normal + p_med):
        multiplier = random.uniform(2.0, 3.5)
        win_type = "medium"
    elif roll < (p_loss + p_normal + p_med + p_high):
        multiplier = random.uniform(4.0, 8.0)
        win_type = "high"
    else:
        multiplier = random.uniform(10.0, 25.0)
        win_type = "huge"

    payout = bet_amount * multiplier

    # التأكد من قدرة الكاشيرة على تغطية الأرباح
    if payout > current_cashier:
        multiplier = 0.0
        payout = 0.0
        win_type = "loss"

    if payout > 0:
        site_balance += payout
        update_bot_cashier(cursor, -payout, bot_id)

    cursor.execute("UPDATE users SET site_balance = ?, total_spent = ? WHERE telegram_id = ?",
                   (site_balance, total_spent, user_id))
    conn.commit()
    conn.close()

    return jsonify({
        'game_id': game_id,
        'win': payout > 0,
        'win_type': win_type,
        'multiplier': round(multiplier, 2),
        'payout': payout,
        'new_site_balance': site_balance
    })

# --- الأكواد وتوليدها والخصم المباشر من الكاشيرة ---
@app.route('/api/admin/code/create', methods=['POST'])
def create_code():
    data = request.json or {}
    user_id = session.get('user_id') or data.get('admin_id') or data.get('telegram_id')

    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    admin = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح لك بإجراء هذه العملية'}), 403

    code = data.get('code', '').strip()
    amount = float(data.get('amount', 0))
    max_uses = int(data.get('max_uses', 1))
    bot_id = int(data.get('bot_id', 1))

    if not code:
        code = "AUREX-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    total_cost = amount * max_uses

    if amount <= 0 or max_uses <= 0:
        conn.close()
        return jsonify({'error': 'يرجى إدخال بيانات كود صالحة'}), 400

    current_cashier = get_bot_cashier(cursor, bot_id)

    # الفحص المباشر لكاشيرة البوت
    if current_cashier < total_cost:
        conn.close()
        return jsonify({'error': f'رصيد كاشيرة البوت لا يكفي لتوليد الكود! المتاح: {current_cashier} ، المطلوب: {total_cost}'}), 400

    try:
        # إضافة الكود لجدول الأكواد
        cursor.execute("INSERT INTO gift_codes (code, amount, max_uses, used_count, active) VALUES (?, ?, ?, 0, 1)",
                       (code, amount, max_uses))
        
        # خصم القيمة الإجمالية للكود فوراً من كاشيرة البوت
        old_cashier, new_cashier = update_bot_cashier(cursor, -total_cost, bot_id)

        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'code': code,
            'amount': amount,
            'max_uses': max_uses,
            'total_deducted': total_cost,
            'old_cashier': old_cashier,
            'new_cashier': new_cashier,
            'message': f'تم إنشاء الكود {code} وخصم {total_cost} من كاشيرة البوت بنجاح'
        })
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'الكود موجود سابقاً'}), 400

@app.route('/api/admin/code/list', methods=['GET'])
def list_active_codes():
    conn = get_db_connection()
    codes = conn.execute("SELECT * FROM gift_codes WHERE active = 1").fetchall()
    conn.close()
    return jsonify([dict(c) for c in codes])

@app.route('/api/admin/code/cancel', methods=['POST'])
def cancel_code():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    admin = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح'}), 403

    data = request.json or {}
    code = data.get('code', '').strip()

    cursor.execute("UPDATE gift_codes SET active = 0 WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'تم إلغاء الكود {code} بنجاح'})

@app.route('/api/code/use', methods=['POST'])
def use_code():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')
    code_text = data.get('code', '').strip()

    if not telegram_id or not code_text:
        return jsonify({'error': 'بيانات الكود غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    code_obj = cursor.execute("SELECT * FROM gift_codes WHERE code = ? AND active = 1", (code_text,)).fetchone()
    if not code_obj:
        conn.close()
        return jsonify({'error': 'الكود غير صالح أو ملغى'}), 400

    if code_obj['used_count'] >= code_obj['max_uses']:
        conn.close()
        return jsonify({'error': 'تم استخدام هذا الكود بالكامل'}), 400

    used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND code = ?", (telegram_id, code_text)).fetchone()
    if used:
        conn.close()
        return jsonify({'error': 'لقد استخدمت هذا الكود من قبل'}), 400

    cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (telegram_id, code_text))
    cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (code_text,))
    
    new_balance = user['bot_balance'] + code_obj['amount']
    cursor.execute("UPDATE users SET bot_balance = ? WHERE telegram_id = ?", (new_balance, telegram_id))
    
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تمت إضافة {code_obj["amount"]} إلى رصيدك بنجاح',
        'amount': code_obj['amount'],
        'used_by_id': telegram_id,
        'used_by_name': user['username'] or user['site_username'],
        'code': code_text
    })

# --- ربط عدّة بوتات وإضافة رصيد للكاشيرة لكل بوت من الإدارة ---
@app.route('/api/admin/bots/add', methods=['POST'])
def add_new_bot():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    admin = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح'}), 403

    data = request.json or {}
    bot_name = data.get('bot_name', '').strip()
    bot_token = data.get('bot_token', '').strip()

    if not bot_name:
        conn.close()
        return jsonify({'error': 'اسم البوت مطلوب'}), 400

    cursor.execute("INSERT INTO bots (bot_name, bot_token, cashier_balance) VALUES (?, ?, 0.0)", (bot_name, bot_token))
    bot_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'bot_id': bot_id, 'message': 'تم إضافة البوت الجديد إلى لوحة المنصة'})

@app.route('/api/admin/bot/cashier_transfer', methods=['POST'])
def admin_bot_cashier_transfer():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    admin = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح'}), 403

    data = request.json or {}
    bot_id = int(data.get('bot_id', 1))
    amount = float(data.get('amount', 0))

    if amount <= 0:
        conn.close()
        return jsonify({'error': 'المبلغ غير صالح'}), 400

    old_b, new_b = update_bot_cashier(cursor, amount, bot_id)
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'bot_id': bot_id,
        'old_balance': old_b,
        'new_balance': new_b,
        'message': f'تم إرسال {amount} إلى كاشيرة البوت رقم {bot_id}'
    })

# --- الشحن والسحب بدقة (تجميد رصيد السحب فوراً لمنع التلاعب) ---
@app.route('/api/transaction/request', methods=['POST'])
def transaction_request():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')
    tx_type = data.get('type')  # deposit أو withdraw
    method = data.get('method')
    amount = float(data.get('amount', 0))
    tx_number = data.get('tx_number', '')

    if not telegram_id or amount <= 0:
        return jsonify({'error': 'بيانات الطلب غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    if tx_type == 'withdraw':
        if user['bot_balance'] < amount:
            conn.close()
            return jsonify({'error': 'رصيد البوت لا يكفي لطلب السحب'}), 400
        # خصم وتجميد الرصيد فوراً
        cursor.execute("UPDATE users SET bot_balance = bot_balance - ? WHERE telegram_id = ?", (amount, telegram_id))

    cursor.execute('''
        INSERT INTO transactions (telegram_id, bot_id, type, method, amount, tx_number)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (telegram_id, user['bot_id'] if user['bot_id'] else 1, tx_type, method, amount, tx_number))
    
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': 'تم تقديم الطلب بنجاح وهو قيد المراجعة'})

@app.route('/api/admin/transaction/action', methods=['POST'])
def admin_transaction_action():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()

    admin = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح'}), 403

    data = request.json or {}
    tx_id = data.get('tx_id')
    action = data.get('action')

    tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or tx['status'] != 'pending':
        conn.close()
        return jsonify({'error': 'المعاملة غير موجودة أو معالجة سابقاً'}), 400

    bot_id = tx['bot_id'] or 1

    if action == 'approve':
        if tx['type'] == 'deposit':
            # عند شحن العميل: يخصم من الكاشيرة ويزيد للعميل
            update_bot_cashier(cursor, -tx['amount'], bot_id)
            cursor.execute("UPDATE users SET bot_balance = bot_balance + ?, deposit_count = deposit_count + 1 WHERE telegram_id = ?",
                           (tx['amount'], tx['telegram_id']))
        elif tx['type'] == 'withdraw':
            # عند السحب: الرصيد مخصوم مسبقاً، تزداد الكاشيرة ويزيد عداد السحب
            cursor.execute("UPDATE users SET withdraw_count = withdraw_count + 1 WHERE telegram_id = ?", (tx['telegram_id'],))
            update_bot_cashier(cursor, tx['amount'], bot_id)

        cursor.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (tx_id,))
    else:
        # عند الرفض: إرجاع الرصيد المجمّد للمستخدم في حال كان السحب
        if tx['type'] == 'withdraw':
            cursor.execute("UPDATE users SET bot_balance = bot_balance + ? WHERE telegram_id = ?", (tx['amount'], tx['telegram_id']))
        cursor.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (tx_id,))

    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'تمت معالجة الطلب بـ {action}'})

# --- التحكم بنسب الخوارزمية من الإدارة ---
@app.route('/api/admin/algorithm/settings', methods=['POST'])
def set_algorithm_rates():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()

    admin = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح'}), 403

    data = request.json or {}
    keys = ['algo_loss_rate', 'algo_normal_rate', 'algo_medium_rate', 'algo_high_rate', 'algo_huge_rate']
    for k in keys:
        if k in data:
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(data[k])))

    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': 'تم تحديث نسب الخوارزمية بنجاح داخل المنصة'})

def start_bot_process():
    try:
        subprocess.run(["python", "bot.py"])
    except Exception as e:
        print(f"Error starting bot: {e}")

if __name__ == '__main__':
    threading.Thread(target=start_bot_process, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
