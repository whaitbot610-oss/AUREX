import os
import sys
import sqlite3
import random
import string
import threading
import subprocess
import json
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, session

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "aurex_casino_secret_key_2026_secure"

# إعدادات الجلسات لضمان العمل المستقل والدخول الفوري
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 30

DB_NAME = "database.db"

# --- معالجة CORS والاتصال المستقل ---
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({'error': f'حدث خطأ غير متوقع في السيرفر: {str(e)}'}), 500

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
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

    columns = [
        ("site_username", "TEXT UNIQUE"),
        ("site_password", "TEXT"),
        ("bot_balance", "REAL DEFAULT 0.0"),
        ("site_balance", "REAL DEFAULT 0.0"),
        ("total_spent", "REAL DEFAULT 0.0"),
        ("deposit_count", "INTEGER DEFAULT 0"),
        ("withdraw_count", "INTEGER DEFAULT 0"),
        ("referrals_count", "INTEGER DEFAULT 0"),
        ("free_spins", "INTEGER DEFAULT 0"),
        ("referred_by", "INTEGER"),
        ("got_welcome_bonus", "INTEGER DEFAULT 0"),
        ("security_passed", "INTEGER DEFAULT 0"),
        ("is_admin", "INTEGER DEFAULT 0")
    ]

    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    # 3. جدول المعاملات المالية
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

    # 4. جدول الأكواد
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount REAL,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 5. سجل الأكواد المستخدمة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_codes (
            telegram_id INTEGER,
            code TEXT,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (telegram_id, code)
        )
    ''')

    # 6. إعدادات النظام والخوارزميات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # نسب كل رقم افتراضية بالعجلة (%)
    default_wheel_probs = {
        "0": 55.0,
        "5": 20.0,
        "10": 12.0,
        "15": 7.0,
        "25": 3.5,
        "50": 1.5,
        "100": 0.8,
        "500": 0.15,
        "1000": 0.05
    }

    defaults = [
        ('maintenance', 'off'),
        ('welcome_bonus', '0'),
        ('referral_bonus', '0'),
        ('wheel_probabilities', json.dumps(default_wheel_probs))
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("SELECT * FROM bots WHERE id = 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO bots (id, bot_name, cashier_balance) VALUES (1, 'AUREX Main Bot', 10000.0)")

    cursor.execute("SELECT * FROM bots WHERE id = 2")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO bots (id, bot_name, cashier_balance) VALUES (2, 'Secondary Bot', 10000.0)")

    cursor.execute("SELECT * FROM users WHERE site_username = 'Admin'")
    if not cursor.fetchone():
        cursor.execute('''
            INSERT INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', (999999, 'Admin', 'Admin', 'Admin096', 0.0, 0.0))

    conn.commit()
    conn.close()

init_db()

def get_req_data():
    if request.is_json:
        return request.get_json(silent=True) or {}
    data = dict(request.form)
    if not data and request.data:
        try:
            data = json.loads(request.data.decode('utf-8'))
        except Exception:
            pass
    if not data:
        data = dict(request.args)
    return data

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

# --- الصفحات ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({"status": "Server is awake"}), 200

@app.route('/wheel')
def wheel_page():
    return render_template('wheel.html')

# --- توثيق وتسجيل الدخول ---
@app.route('/api/auth/login', methods=['POST'])
def login_site():
    data = get_req_data()
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

@app.route('/api/auth/telegram_login', methods=['POST'])
def telegram_login():
    data = get_req_data()
    raw_tg = str(data.get('telegram_id', '')).strip()
    username = str(data.get('username', '')).strip()

    if not raw_tg or not raw_tg.isdigit():
        return jsonify({'error': 'معرف التليجرام غير صالح'}), 400

    telegram_id = int(raw_tg)
    conn = get_db_connection()
    cursor = conn.cursor()
    
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not user:
        site_user = username or f"user_{telegram_id}"
        cursor.execute('''
            INSERT INTO users (telegram_id, username, site_username, site_password, bot_id)
            VALUES (?, ?, ?, ?, 1)
        ''', (telegram_id, username, site_user, "tg_auto_pass"))
        conn.commit()
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    conn.close()

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
    user_id = session.get('user_id') or request.args.get('telegram_id')
    if not user_id:
        return jsonify({'error': 'غير مسجل الدخول'}), 401
    
    conn = get_db_connection()
    user = conn.execute("SELECT telegram_id, bot_id, username, site_username, site_password, bot_balance, site_balance, referrals_count, free_spins, is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user:
        return jsonify({'error': 'الحساب غير موجود'}), 404
    return jsonify(dict(user))

@app.route('/api/register_site', methods=['POST'])
def register_site():
    data = get_req_data()
    raw_tg = str(data.get('telegram_id', '')).strip()
    site_user = str(data.get('site_user', '')).strip()
    site_pass = str(data.get('site_pass', '')).strip()
    bot_id = int(data.get('bot_id', 1))
    referred_by = data.get('referred_by')
    
    if len(site_user) < 3 or len(site_pass) < 3:
        return jsonify({'error': 'اسم المستخدم وكلمة المرور يجب أن يتجاوزا 3 خانات'}), 400
        
    if not raw_tg or not raw_tg.isdigit():
        telegram_id = random.randint(100000000, 999999999)
    else:
        telegram_id = int(raw_tg)

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
        
        user_data = cursor.execute("SELECT telegram_id, site_username, bot_balance, site_balance, free_spins, is_admin FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        conn.close()
        
        session.permanent = True
        session['user_id'] = user_data['telegram_id']
        session['is_admin'] = bool(user_data['is_admin'])

        return jsonify({
            'status': 'success',
            'message': 'تم إنشاء الحساب وتسجيل الدخول بنجاح',
            'telegram_id': user_data['telegram_id'],
            'site_username': user_data['site_username'],
            'bot_balance': user_data['bot_balance'],
            'site_balance': user_data['site_balance'],
            'free_spins': user_data['free_spins']
        })
    except sqlite3.IntegrityError as e:
        conn.close()
        return jsonify({'error': f'حدث خطأ في قاعدة البيانات: {str(e)}'}), 400

# --- تحويل الرصيد ---
@app.route('/api/balance/transfer_to_site', methods=['POST'])
def transfer_to_site():
    data = get_req_data()
    telegram_id = session.get('user_id') or data.get('telegram_id')
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

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
        'message': f'تم نقل {amount} من رصيد البوت إلى رصيد الموقع بنجاح',
        'bot_balance': new_bot_bal,
        'site_balance': new_site_bal
    })

@app.route('/api/balance/transfer_to_bot', methods=['POST'])
def transfer_to_bot():
    data = get_req_data()
    telegram_id = session.get('user_id') or data.get('telegram_id')
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

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
        'message': f'تم نقل {amount} من رصيد الموقع إلى رصيد البوت بنجاح',
        'bot_balance': new_bot_bal,
        'site_balance': new_site_bal
    })

# --- خوارزمية العجلة بالنسب المئوية ---
@app.route('/api/wheel/spin', methods=['POST'])
def wheel_spin():
    data = get_req_data()
    user_id = session.get('user_id') or data.get('telegram_id')
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

    probs_str = get_setting(cursor, 'wheel_probabilities', '{}')
    try:
        probs = json.loads(probs_str)
    except Exception:
        probs = {"0": 55.0, "5": 20.0, "10": 12.0, "15": 7.0, "25": 3.5, "50": 1.5, "100": 0.8, "500": 0.15, "1000": 0.05}

    numbers = [int(k) for k in probs.keys()]
    weights = [float(v) for v in probs.values()]

    chosen_reward = random.choices(numbers, weights=weights, k=1)[0]

    bot_id = user['bot_id'] or 1
    cashier = get_bot_cashier(cursor, bot_id)

    if chosen_reward > cashier:
        chosen_reward = 0

    msg = "حظ أوفر، لم تكسب شيئاً" if chosen_reward == 0 else f"مبروك! لقد كسبت {chosen_reward} نقطة"

    if chosen_reward > 0:
        cursor.execute("UPDATE users SET site_balance = site_balance + ? WHERE telegram_id = ?", (chosen_reward, user_id))
        update_bot_cashier(cursor, -chosen_reward, bot_id)

    conn.commit()
    updated_user = cursor.execute("SELECT site_balance, free_spins FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    return jsonify({
        'status': 'success',
        'reward': chosen_reward,
        'message': msg,
        'is_free_spin': is_free_spin,
        'new_site_balance': updated_user['site_balance'],
        'free_spins_left': updated_user['free_spins']
    })

# --- الأكواد ---
@app.route('/api/code/create', methods=['POST'])
def create_code():
    data = get_req_data()
    code = str(data.get('code', '')).strip()
    try:
        amount = float(data.get('amount', 0))
        max_uses = int(data.get('max_uses', 1))
        bot_id = int(data.get('bot_id', 1))
    except (ValueError, TypeError):
        return jsonify({'error': 'بيانات الأرقام غير صالحة'}), 400

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
        return jsonify({'error': f'رصيد كاشيرة البوت غير كافٍ! المتاح: {current_cashier}'}), 400

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
        return jsonify({'error': 'هذا الكود موجود سابقاً، يرجى اختيار كود آخر'}), 400

@app.route('/api/code/active', methods=['GET'])
def get_active_codes():
    conn = get_db_connection()
    codes = conn.execute("SELECT * FROM gift_codes WHERE active = 1 AND used_count < max_uses ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(c) for c in codes])

@app.route('/api/code/cancel', methods=['POST'])
def cancel_code():
    data = get_req_data()
    code_text = str(data.get('code', '')).strip()
    bot_id = int(data.get('bot_id', 1))

    if not code_text:
        return jsonify({'error': 'يرجى إدخال الكود لإلغائه'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    code_obj = cursor.execute("SELECT * FROM gift_codes WHERE code = ? AND active = 1", (code_text,)).fetchone()

    if not code_obj:
        conn.close()
        return jsonify({'error': 'الكود غير موجود أو غير نشط'}), 404

    remaining_uses = code_obj['max_uses'] - code_obj['used_count']
    refund_amount = remaining_uses * code_obj['amount']

    cursor.execute("UPDATE gift_codes SET active = 0 WHERE code = ?", (code_text,))
    if refund_amount > 0:
        update_bot_cashier(cursor, refund_amount, bot_id)

    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تم إلغاء الكود بنجاح وإعادة {refund_amount} إلى كاشيرة البوت'
    })

@app.route('/api/code/use', methods=['POST'])
def use_code():
    data = get_req_data()
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
        return jsonify({'error': 'الكود غير صالح أو ملغى أو تم استخدامه بالكامل'}), 400

    used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND code = ?", (telegram_id, code_text)).fetchone()
    if used:
        conn.close()
        return jsonify({'error': 'لقد استخدمت هذا الكود سابقاً'}), 400

    cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (telegram_id, code_text))
    cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (code_text,))
    cursor.execute("UPDATE users SET bot_balance = bot_balance + ? WHERE telegram_id = ?", (code_obj['amount'], telegram_id))
    
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': f'تمت إضافة {code_obj["amount"]} إلى رصيد البوت الخاص بك بنجاح'})

# --- لوحة الإدارة ---
@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    conn = get_db_connection()
    users = conn.execute("SELECT telegram_id, bot_id, username, site_username, site_password, bot_balance, site_balance, referrals_count, free_spins, is_admin FROM users").fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/api/admin/spins/grant_user', methods=['POST'])
def admin_grant_spins_user():
    data = get_req_data()
    target_user = data.get('telegram_id') or data.get('site_username')
    try:
        spins = int(data.get('spins', 1))
    except (ValueError, TypeError):
        spins = 1

    if not target_user:
        return jsonify({'error': 'يرجى تحديد معرف المستخدم أو اسم الحساب'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_spins = free_spins + ? WHERE telegram_id = ? OR site_username = ?", (spins, target_user, target_user))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'تم منح {spins} لفة للمستخدم بنجاح'})

@app.route('/api/admin/spins/grant_all', methods=['POST'])
def admin_grant_spins_all():
    data = get_req_data()
    try:
        spins = int(data.get('spins', 1))
    except (ValueError, TypeError):
        spins = 1

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_spins = free_spins + ?", (spins,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'تم منح {spins} لفة لجميع المستخدمين بنجاح'})

@app.route('/api/admin/wheel/update_probs', methods=['POST'])
def update_wheel_probs():
    data = get_req_data()
    probs = data.get('probabilities')
    if not probs or not isinstance(probs, dict):
        return jsonify({'error': 'صيغة النسب المئوية غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wheel_probabilities', ?)", (json.dumps(probs),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': 'تم تحديث نسب احتمالات العجلة بنجاح'})

@app.route('/api/admin/user/update_balance', methods=['POST'])
def admin_update_user_balance():
    data = get_req_data()
    telegram_id = data.get('telegram_id')
    action = data.get('action')
    balance_type = data.get('balance_type', 'site')
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

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
    data = get_req_data()
    try:
        bot_id = int(data.get('bot_id', 1))
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'بيانات القيمة غير صالحة'}), 400

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

@app.route('/api/admin/settings/update', methods=['POST'])
def admin_update_settings():
    data = get_req_data()
    conn = get_db_connection()
    cursor = conn.cursor()

    for k, v in data.items():
        val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (str(k), val_str))

    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': 'تم تحديث الإعدادات بنجاح'})

@app.route('/api/admin/settings/get', methods=['GET'])
def admin_get_settings():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM settings").fetchall()
    conn.close()
    res = {}
    for r in rows:
        try:
            res[r['key']] = json.loads(r['value'])
        except Exception:
            res[r['key']] = r['value']
    return jsonify(res)

# --- تشغيل ملف البوت تلقائياً في الخلفية (متوافق مع Render) ---
def launch_bot():
    if os.environ.get("BOT_LAUNCHED") != "true":
        os.environ["BOT_LAUNCHED"] = "true"
        try:
            subprocess.Popen([sys.executable, "bot.py"])
            print(">>> تم تشغيل bot.py تلقائياً بنجاح <<<")
        except Exception as e:
            print(f"خطأ في تشغيل البوت: {e}")

launch_bot()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
