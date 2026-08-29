import os
import sqlite3
import random
import string
import threading
import subprocess
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, session

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "aurex_casino_secret_key_2026_secure"

# إعدادات الجلسات لضمان العمل المستقل والسلس على أي متصفح/رابط خارجي
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 30

DB_NAME = "database.db"

# --- حل مشاكل CORS والاتصال بدون انقطاع ---
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
    conn = sqlite3.connect(DB_NAME, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. جدول البوتات والكاشيرة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            bot_token TEXT UNIQUE,
            cashier_balance REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. جدول المستخدمين
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
            free_spins INTEGER DEFAULT 0,
            referred_by INTEGER,
            got_welcome_bonus INTEGER DEFAULT 0,
            security_passed INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # تحديث الأعمدة في حال وجود قاعدة بيانات قديمة لضمان عدم تلف البيانات
    cursor.execute("PRAGMA table_info(users)")
    columns = [col['name'] for col in cursor.fetchall()]
    if 'bot_balance' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN bot_balance REAL DEFAULT 0.0")
    if 'site_balance' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN site_balance REAL DEFAULT 0.0")
    if 'bot_id' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN bot_id INTEGER DEFAULT 1")
    if 'free_spins' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN free_spins INTEGER DEFAULT 0")

    # 3. جدول المعاملات المالية (سحب/إيداع)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            bot_id INTEGER DEFAULT 1,
            type TEXT, -- 'deposit' or 'withdraw'
            method TEXT,
            amount REAL,
            tx_number TEXT,
            status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
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

    # 6. جدول الإعدادات العامة والخوارزميات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    defaults = [
        ('maintenance', 'off'),
        ('welcome_bonus', '0'),
        ('referral_bonus', '0'),
        ('algo_mode', 'normal'),        # loss, normal, medium, high, huge
        ('algo_loss_rate', '50'),       # خسارة (0)
        ('algo_normal_rate', '25'),     # ربح عادي (5, 10)
        ('algo_medium_rate', '15'),     # ربح متوسط (15, 25)
        ('algo_high_rate', '8'),        # ربح عالي (50, 100)
        ('algo_huge_rate', '2')         # ربح ضخم (500, 1000)
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("SELECT * FROM bots WHERE id = 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO bots (id, bot_name, cashier_balance) VALUES (1, 'Main Bot (AUREX)', 10000.0)")

    cursor.execute("SELECT * FROM bots WHERE id = 2")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO bots (id, bot_name, cashier_balance) VALUES (2, 'Secondary Bot', 10000.0)")

    # حساب المدير الافتراضي للموقع
    cursor.execute("SELECT * FROM users WHERE site_username = 'Admin'")
    if not cursor.fetchone():
        cursor.execute('''
            INSERT INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', (999999, 'Admin', 'Admin', 'Admin096', 0.0, 0.0))

    conn.commit()
    conn.close()

init_db()

# --- أدوات الكاشيرة والإعدادات ---
def get_bot_cashier(cursor, bot_id=1):
    cursor.execute("SELECT cashier_balance FROM bots WHERE id = ?", (bot_id,))
    row = cursor.fetchone()
    return float(row['cashier_balance']) if row else 0.0

def update_bot_cashier(cursor, amount_change, bot_id=1):
    old_balance = get_bot_cashier(cursor, bot_id)
    new_balance = max(0.0, old_balance + amount_change)
    cursor.execute("UPDATE bots SET cashier_balance = ? WHERE id = ?", (new_balance, bot_id))
    return old_balance, new_balance

def get_setting(cursor, key, default="0"):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row['value'] if row else default

@app.before_request
def check_maintenance():
    if request.method == 'OPTIONS':
        return '', 200
    if request.path.startswith('/api/') and not request.path.startswith('/api/admin') and not request.path.startswith('/api/auth') and not request.path.startswith('/api/register_site'):
        conn = get_db_connection()
        cursor = conn.cursor()
        m = get_setting(cursor, 'maintenance', 'off')
        conn.close()
        if m == 'on':
            return jsonify({'error': 'الموقع في وضع الصيانة حالياً'}), 533

@app.route('/')
def home():
    return render_template('index.html')

# --- تسجيل الدخول والتوثيق ---
@app.route('/api/auth/login', methods=['POST'])
def login_site():
    data = request.json or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()

    if not username or not password:
        return jsonify({'error': 'يرجى إدخال اسم المستخدم وكلمة المرور'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE (LOWER(site_username) = LOWER(?) OR LOWER(username) = LOWER(?)) AND site_password = ?", 
                   (username, username, password))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return jsonify({'error': 'اسم المستخدم أو كلمة المرور غير صحيحة'}), 401

    session.permanent = True
    session['user_id'] = user['telegram_id']
    session['is_admin'] = bool(user['is_admin'])

    return jsonify({
        'status': 'success',
        'telegram_id': user['telegram_id'],
        'username': user['site_username'] or user['username'],
        'bot_balance': user['bot_balance'],
        'site_balance': user['site_balance'],
        'free_spins': user['free_spins'],
        'referrals_count': user['referrals_count'],
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
    user = conn.execute("SELECT telegram_id, bot_id, username, site_username, site_password, bot_balance, site_balance, referrals_count, free_spins, is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user:
        return jsonify({'error': 'الحساب غير موجود'}), 404
    return jsonify(dict(user))

# --- إنشاء حساب وتفعيل رابط الإحالة تلقائياً ---
@app.route('/api/register_site', methods=['POST'])
def register_site():
    data = request.json or {}
    telegram_id = data.get('telegram_id')
    site_user = str(data.get('site_user', '')).strip()
    site_pass = str(data.get('site_pass', '')).strip()
    bot_id = int(data.get('bot_id', 1))
    referred_by = data.get('referred_by')
    
    if len(site_user) < 3 or len(site_pass) < 3:
        return jsonify({'error': 'اسم المستخدم وكلمة المرور يجب أن يتجاوزا 3 خانات'}), 400
        
    if not telegram_id:
        telegram_id = random.randint(100000000, 999999999)
    else:
        try:
            telegram_id = int(telegram_id)
        except ValueError:
            telegram_id = random.randint(100000000, 999999999)

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE LOWER(site_username) = LOWER(?) AND telegram_id != ?", (site_user, telegram_id))
    if cursor.fetchone():
        conn.close()
        return jsonify({'error': 'اسم المستخدم هذا مأخوذ بالفعل، اختر اسماً آخر'}), 400

    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    existing_user = cursor.fetchone()
    
    try:
        if existing_user:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ?, bot_id = ? WHERE telegram_id = ?",
                           (site_user, site_pass, bot_id, telegram_id))
        else:
            cursor.execute('''
                INSERT INTO users (telegram_id, username, site_username, site_password, bot_id, referred_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (telegram_id, site_user, site_user, site_pass, bot_id, referred_by))
            
            # احتساب الإحالة وإعطاء الداعي لفة مجانية واحدة فوراً
            if referred_by:
                try:
                    ref_id = int(referred_by)
                    cursor.execute('''
                        UPDATE users 
                        SET referrals_count = referrals_count + 1, free_spins = free_spins + 1 
                        WHERE telegram_id = ?
                    ''', (ref_id,))
                except ValueError:
                    pass

        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'message': 'تم إنشاء حساب المنصة بنجاح ويمكنك تسجيل الدخول مباشرة'})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'حدث خطأ أثناء إنشاء الحساب'}), 400

# --- تحويل الرصيد التلقائي بين البوت والموقع ---
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

# --- خوارزمية العجلة الحية (Red & Black Wheel API) ---
@app.route('/api/wheel/spin', methods=['POST'])
def wheel_spin():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'يجب تسجيل الدخول لتدوير العجلة'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()

    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    is_free_spin = False
    if user['free_spins'] > 0:
        is_free_spin = True
        cursor.execute("UPDATE users SET free_spins = free_spins - 1 WHERE telegram_id = ?", (user_id,))
    else:
        spin_cost = 10.0
        if user['site_balance'] < spin_cost:
            conn.close()
            return jsonify({'error': 'ليس لديك رصيد كافٍ أو لفتات مجانية'}), 400
        cursor.execute("UPDATE users SET site_balance = site_balance - ? WHERE telegram_id = ?", (spin_cost, user_id))
        update_bot_cashier(cursor, spin_cost, user['bot_id'] or 1)

    # احتساب نسب الأرباح بناء على وضع الخوارزمية (Loss, Normal, Medium, High, Huge)
    algo_mode = get_setting(cursor, 'algo_mode', 'normal')
    
    if algo_mode == 'loss':
        p_loss, p_normal, p_med, p_high, p_huge = 85, 10, 4, 1, 0
    elif algo_mode == 'medium':
        p_loss, p_normal, p_med, p_high, p_huge = 35, 35, 20, 8, 2
    elif algo_mode == 'high':
        p_loss, p_normal, p_med, p_high, p_huge = 20, 30, 30, 15, 5
    elif algo_mode == 'huge':
        p_loss, p_normal, p_med, p_high, p_huge = 10, 20, 30, 25, 15
    else: # normal
        p_loss, p_normal, p_med, p_high, p_huge = 50, 25, 15, 8, 2

    roll = random.uniform(0, 100)
    bot_id = user['bot_id'] or 1
    cashier = get_bot_cashier(cursor, bot_id)

    if roll < p_loss:
        reward = 0
        win_type = "loss"
    elif roll < (p_loss + p_normal):
        reward = random.choice([5, 10])
        win_type = "normal"
    elif roll < (p_loss + p_normal + p_med):
        reward = random.choice([15, 25])
        win_type = "medium"
    elif roll < (p_loss + p_normal + p_med + p_high):
        reward = random.choice([50, 100])
        win_type = "high"
    else:
        reward = random.choice([500, 1000])
        win_type = "huge"

    # حماية كاشيرة البوت من الإفلاس
    if reward > cashier:
        reward = 0
        win_type = "loss"

    if reward > 0:
        cursor.execute("UPDATE users SET site_balance = site_balance + ? WHERE telegram_id = ?", (reward, user_id))
        update_bot_cashier(cursor, -reward, bot_id)

    conn.commit()
    updated_user = cursor.execute("SELECT site_balance, free_spins FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    return jsonify({
        'status': 'success',
        'reward': reward,
        'win_type': win_type,
        'is_free_spin': is_free_spin,
        'new_site_balance': updated_user['site_balance'],
        'free_spins_left': updated_user['free_spins']
    })

# --- توليد واستخدام الأكواد (مع الخصم من الكاشيرة) ---
@app.route('/api/code/create', methods=['POST'])
def create_code():
    data = request.json or {}
    code = str(data.get('code', '')).strip()
    amount = float(data.get('amount', 0))
    max_uses = int(data.get('max_uses', 1))
    bot_id = int(data.get('bot_id', 1))

    if amount <= 0 or max_uses <= 0:
        return jsonify({'error': 'يرجى إدخال مبلغ وعدد استخدامات صالحين'}), 400

    if not code:
        code = "AUREX-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    total_cost = amount * max_uses

    conn = get_db_connection()
    cursor = conn.cursor()
    current_cashier = get_bot_cashier(cursor, bot_id)

    if current_cashier < total_cost:
        conn.close()
        return jsonify({'error': f'رصيد الكاشيرة للبوت ({bot_id}) غير كافٍ! المتاح: {current_cashier}'}), 400

    try:
        cursor.execute("INSERT INTO gift_codes (code, amount, max_uses, used_count, active) VALUES (?, ?, ?, 0, 1)",
                       (code, amount, max_uses))
        old_cashier, new_cashier = update_bot_cashier(cursor, -total_cost, bot_id)

        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'code': code,
            'amount': amount,
            'max_uses': max_uses,
            'bot_id': bot_id,
            'new_cashier': new_cashier,
            'message': f'تم توليد الكود {code} وخصم {total_cost} من كاشيرة البوت بنجاح'
        })
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'الكود موجود سابقاً، يرجى اختيار كود آخر'}), 400

@app.route('/api/code/use', methods=['POST'])
def use_code():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')
    code_text = str(data.get('code', '')).strip()

    if not telegram_id or not code_text:
        return jsonify({'error': 'بيانات غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    code_obj = cursor.execute("SELECT * FROM gift_codes WHERE code = ? AND active = 1", (code_text,)).fetchone()
    if not code_obj or code_obj['used_count'] >= code_obj['max_uses']:
        conn.close()
        return jsonify({'error': 'الكود غير صالح أو تم استخدامه بالكامل'}), 400

    used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND code = ?", (telegram_id, code_text)).fetchone()
    if used:
        conn.close()
        return jsonify({'error': 'لقد استخدمت هذا الكود من قبل'}), 400

    cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (telegram_id, code_text))
    cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (code_text,))
    cursor.execute("UPDATE users SET bot_balance = bot_balance + ? WHERE telegram_id = ?", (code_obj['amount'], telegram_id))
    
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': f'تمت إضافة {code_obj["amount"]} إلى رصيد البوت الخاص بك بنجاح'})

# --- لوحة الإدارة المتقدمة (Admin Control Panel) ---

@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    conn = get_db_connection()
    users = conn.execute("SELECT telegram_id, bot_id, username, site_username, site_password, bot_balance, site_balance, referrals_count, free_spins, is_admin FROM users").fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/api/admin/user/update_balance', methods=['POST'])
def admin_update_user_balance():
    data = request.json or {}
    telegram_id = data.get('telegram_id')
    action = data.get('action') # 'add' or 'deduct'
    balance_type = data.get('balance_type', 'site') # 'site' or 'bot'
    amount = float(data.get('amount', 0))

    if not telegram_id or amount <= 0 or action not in ['add', 'deduct']:
        return jsonify({'error': 'بيانات غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    col = "site_balance" if balance_type == 'site' else "bot_balance"
    op = "+" if action == 'add' else "-"

    cursor.execute(f"UPDATE users SET {col} = MAX(0, {col} {op} ?) WHERE telegram_id = ?", (amount, telegram_id))
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': f'تمت عملية {action} بمبلغ {amount} بنجاح'})

@app.route('/api/admin/cashier/add', methods=['POST'])
def admin_add_cashier():
    data = request.json or {}
    bot_id = int(data.get('bot_id', 1))
    amount = float(data.get('amount', 0))

    conn = get_db_connection()
    cursor = conn.cursor()
    old_b, new_b = update_bot_cashier(cursor, amount, bot_id)
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'bot_id': bot_id, 'old_balance': old_b, 'new_balance': new_b})

@app.route('/api/admin/cashier/get', methods=['GET'])
def admin_get_cashiers():
    conn = get_db_connection()
    bots = conn.execute("SELECT id, bot_name, cashier_balance FROM bots").fetchall()
    conn.close()
    return jsonify([dict(b) for b in bots])

# --- معالجة طلبات السحب والإيداع مع خصم/زيادة الكاشيرة ---
@app.route('/api/admin/transaction/process', methods=['POST'])
def admin_process_transaction():
    data = request.json or {}
    tx_id = data.get('transaction_id')
    action = data.get('action') # 'approve' or 'reject'

    if not tx_id or action not in ['approve', 'reject']:
        return jsonify({'error': 'بيانات غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or tx['status'] != 'pending':
        conn.close()
        return jsonify({'error': 'الطلب غير موجود أو تمت معالجته سابقاً'}), 400

    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (tx['telegram_id'],)).fetchone()
    bot_id = tx['bot_id'] or 1
    amount = float(tx['amount'])

    if action == 'approve':
        if tx['type'] == 'deposit':
            # موافقة على الشحن: ينقص من كاشيرة البوت ويضاف لمتصفح العميل
            cashier = get_bot_cashier(cursor, bot_id)
            if cashier < amount:
                conn.close()
                return jsonify({'error': 'رصيد كاشيرة البوت غير كافٍ لتأكيد هذا الإيداع'}), 400
            
            update_bot_cashier(cursor, -amount, bot_id)
            cursor.execute("UPDATE users SET site_balance = site_balance + ?, deposit_count = deposit_count + 1 WHERE telegram_id = ?", (amount, tx['telegram_id']))
        elif tx['type'] == 'withdraw':
            # موافقة على السحب: يضاف لكاشيرة البوت ويخصم من رصيد العميل
            if user['site_balance'] < amount:
                conn.close()
                return jsonify({'error': 'رصيد العميل الحالي غير كافٍ لإتمام السحب'}), 400
            
            update_bot_cashier(cursor, amount, bot_id)
            cursor.execute("UPDATE users SET site_balance = site_balance - ?, withdraw_count = withdraw_count + 1 WHERE telegram_id = ?", (amount, tx['telegram_id']))

        cursor.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (tx_id,))
    else:
        cursor.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (tx_id,))

    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'تم {action} الطلب بنجاح'})

@app.route('/api/admin/settings/update', methods=['POST'])
def admin_update_settings():
    data = request.json or {}
    conn = get_db_connection()
    cursor = conn.cursor()

    for k, v in data.items():
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (str(k), str(v)))

    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': 'تم تحديث الإعدادات والخوارزميات بنجاح'})

@app.route('/api/admin/settings/get', methods=['GET'])
def admin_get_settings():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM settings").fetchall()
    conn.close()
    return jsonify({r['key']: r['value'] for r in rows})

def start_bot_process():
    try:
        subprocess.run(["python", "bot.py"])
    except Exception as e:
        print(f"Error starting bot process: {e}")

if __name__ == '__main__':
    threading.Thread(target=start_bot_process, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
