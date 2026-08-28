import os
import sqlite3
import random
import threading
import subprocess
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, session

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "aurex_casino_secret_key_2026_secure"

DB_NAME = "database.db"

def get_db_connection():
    # إضافة timeout لتجنب إغلاق قاعدة البيانات عند الضغط
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. جدول المستخدمين الموحد
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            site_username TEXT UNIQUE,
            site_password TEXT,
            balance REAL DEFAULT 0.0,
            total_spent REAL DEFAULT 0.0,
            deposit_count INTEGER DEFAULT 0,
            withdraw_count INTEGER DEFAULT 0,
            referrals_count INTEGER DEFAULT 0,
            referred_by INTEGER,
            got_welcome_bonus INTEGER DEFAULT 0,
            security_passed INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            code_restricted_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. جدول المعاملات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            type TEXT,
            method TEXT,
            amount REAL,
            tx_number TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 3. جدول أكواد الهدايا
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount REAL,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0
        )
    ''')
    
    # 4. سجل الأكواد المستعملة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_codes (
            telegram_id INTEGER,
            code TEXT,
            PRIMARY KEY (telegram_id, code)
        )
    ''')

    # 5. جدول حسابات الدفع
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            number TEXT,
            active INTEGER DEFAULT 1
        )
    ''')

    # 6. جدول الإعدادات العامة والخزينة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    defaults = [
        ('win_rate', '30'),
        ('maintenance', 'off'),
        ('welcome_bonus', '500'),
        ('referral_bonus', '100'),
        ('cashier_balance', '10000.0'),
        ('jackpot_balance', '254005482.0')
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    # --- إنشاء حساب الأدمن الافتراضي ---
    cursor.execute("SELECT * FROM users WHERE site_username = 'Admin'")
    if not cursor.fetchone():
        cursor.execute('''
            INSERT INTO users (telegram_id, username, site_username, site_password, balance, is_admin)
            VALUES (?, ?, ?, ?, ?, 1)
        ''', (999999, 'Admin', 'Admin', 'Admin096', 0.0))

    conn.commit()
    conn.close()

init_db()

# --- أدوات مساعدة ---
def update_cashier_balance(cursor, amount_change):
    cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'")
    row = cursor.fetchone()
    current = float(row['value']) if row else 0.0
    new_balance = max(0.0, current + amount_change)
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cashier_balance', ?)", (str(new_balance),))
    return new_balance

def get_setting_val(cursor, key_name, default_val="0"):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key_name,))
    row = cursor.fetchone()
    return row['value'] if row else default_val

# Middleware لفحص وضع الصيانة
@app.before_request
def check_maintenance():
    if request.path.startswith('/api/') and not request.path.startswith('/api/admin') and not request.path.startswith('/api/auth'):
        conn = get_db_connection()
        cursor = conn.cursor()
        m = get_setting_val(cursor, 'maintenance', 'off')
        conn.close()
        if m == 'on':
            return jsonify({'error': 'الموقع والبوت في وضع الصيانة حالياً'}), 533

# --- الصفحة الرئيسية ---
@app.route('/')
def home():
    return render_template('index.html')

# --- APIs التوثيق وتسجيل الدخول اليدوي ---
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
        'balance': user['balance'],
        'got_welcome_bonus': user['got_welcome_bonus'],
        'is_admin': bool(user['is_admin'])
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout_site():
    session.clear()
    return jsonify({'status': 'success', 'message': 'تم تسجيل الخروج'})

@app.route('/api/auth/telegram', methods=['POST'])
def telegram_auth():
    data = request.json or {}
    telegram_id = data.get('telegram_id')
    first_name = data.get('first_name', '')
    username = data.get('username', '')

    if not telegram_id:
        return jsonify({'error': 'معرف تلجرام مطلوب'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cursor.fetchone()

    if not user:
        cursor.execute(
            "INSERT INTO users (telegram_id, username, balance, is_admin) VALUES (?, ?, 0.0, 0)",
            (telegram_id, username or first_name)
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        user = cursor.fetchone()

    session['user_id'] = user['telegram_id']
    session['is_admin'] = bool(user['is_admin'])
    conn.close()

    return jsonify({
        'status': 'success',
        'telegram_id': user['telegram_id'],
        'username': user['site_username'] or user['username'],
        'balance': user['balance'],
        'got_welcome_bonus': user['got_welcome_bonus'],
        'is_admin': bool(user['is_admin'])
    })

@app.route('/api/user/<int:telegram_id>', methods=['GET'])
def get_user(telegram_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    if not user:
        return jsonify({'error': 'المستخدم غير موجود'}), 404
    return jsonify(dict(user))

@app.route('/api/register_site', methods=['POST'])
def register_site():
    data = request.json or {}
    telegram_id = data.get('telegram_id')
    site_user = data.get('site_user', '').strip()
    site_pass = data.get('site_pass', '').strip()
    
    if len(site_user) < 4 or not site_pass.isdigit() or len(site_pass) < 4:
        return jsonify({'error': 'اسم المستخدم يجب أن يكون 4 أحرف على الأقل، وكلمة المرور 4 أرقام'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?",
                       (site_user, str(site_pass), telegram_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'تم إنشاء حساب الموقع بنجاح'})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'اسم المستخدم هذا مأخوذ بالفعل'}), 400

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

    bonus_amount = float(get_setting_val(cursor, 'welcome_bonus', '500'))
    cashier = float(get_setting_val(cursor, 'cashier_balance', '0'))

    if cashier < bonus_amount:
        conn.close()
        return jsonify({'error': 'عذراً، رصيد الكاشيرة لا يكفي لإرسال البونص حالياً'}), 400

    update_cashier_balance(cursor, -bonus_amount)
    new_user_balance = user['balance'] + bonus_amount

    cursor.execute("UPDATE users SET balance = ?, got_welcome_bonus = 1 WHERE telegram_id = ?",
                   (new_user_balance, telegram_id))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تمت إضافة البونص الترحيبي ({bonus_amount}) بنجاح وخصمه من الكاشيرة',
        'new_balance': new_user_balance
    })

@app.route('/api/play', methods=['POST'])
def play():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')
    bet_amount = float(data.get('bet_amount', 0))
    game_id = data.get('game_id', 'slot_default')

    if not telegram_id:
        return jsonify({'error': 'يرجى تسجيل الدخول للعب'}), 401

    if bet_amount <= 0:
        return jsonify({'error': 'قيمة الرهان غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    if not user or user['balance'] < bet_amount:
        conn.close()
        return jsonify({'error': 'الرصيد غير كافٍ'}), 400

    user_balance = user['balance'] - bet_amount
    total_spent = user['total_spent'] + bet_amount
    update_cashier_balance(cursor, bet_amount)

    win_rate = int(get_setting_val(cursor, 'win_rate', '30'))
    current_cashier = float(get_setting_val(cursor, 'cashier_balance', '0'))

    random_roll = random.randint(1, 100)
    is_win = False
    multiplier = 0.0
    payout = 0.0

    possible_multipliers = [1.5, 2.0, 3.0, 5.0]
    target_multiplier = random.choice(possible_multipliers)
    calculated_payout = bet_amount * target_multiplier

    if random_roll <= win_rate and current_cashier >= calculated_payout:
        is_win = True
        multiplier = target_multiplier
        payout = calculated_payout
        
        user_balance += payout
        update_cashier_balance(cursor, -payout)

    cursor.execute("UPDATE users SET balance = ?, total_spent = ? WHERE telegram_id = ?",
                   (user_balance, total_spent, telegram_id))
    conn.commit()
    conn.close()

    return jsonify({
        'game_id': game_id,
        'win': is_win,
        'multiplier': multiplier,
        'payout': payout,
        'new_balance': user_balance
    })

# --- APIs الإدارة (تم إصلاح الثغرة الأمنية باستعمال session حصراً) ---
@app.route('/api/admin/transfer-cashier', methods=['POST'])
def admin_transfer_cashier():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'يرجى تسجيل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()

    if not user or not user['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح لك للقيام بهذه العملية'}), 403

    data = request.json or {}
    amount = float(data.get('amount', 0))
    if amount <= 0:
        conn.close()
        return jsonify({'error': 'المبلغ غير صالح'}), 400

    new_balance = update_cashier_balance(cursor, amount)
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'new_cashier_balance': new_balance})

@app.route('/api/admin/set-rtp', methods=['POST'])
def admin_set_rtp():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'يرجى تسجيل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()

    if not user or not user['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح لك للقيام بهذه العملية'}), 403

    data = request.json or {}
    rtp_rate = data.get('rtp_rate')
    if rtp_rate is None or not (0 <= float(rtp_rate) <= 100):
        conn.close()
        return jsonify({'error': 'نسبة RTP يجب أن تكون بين 0 و 100'}), 400

    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('win_rate', ?)", (str(rtp_rate),))
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'new_win_rate': rtp_rate})

@app.route('/api/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'يرجى تسجيل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()

    user = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if not user or not user['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح لك للقيام بهذه العملية'}), 403

    if request.method == 'POST':
        data = request.json or {}
        if 'add_cashier' in data and float(data['add_cashier']) != 0:
            add_val = float(data['add_cashier'])
            update_cashier_balance(cursor, add_val)

        for key in ['win_rate', 'maintenance', 'welcome_bonus', 'referral_bonus']:
            if key in data:
                cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(data[key])))

        conn.commit()

    cursor.execute("SELECT * FROM settings")
    rows = cursor.fetchall()
    settings_dict = {row['key']: row['value'] for row in rows}
    conn.close()

    return jsonify(settings_dict)

# --- APIs المعاملات الإيداع والسحب ---
@app.route('/api/transaction/request', methods=['POST'])
def transaction_request():
    data = request.json or {}
    telegram_id = session.get('user_id') or data.get('telegram_id')
    tx_type = data.get('type')
    method = data.get('method')
    amount = float(data.get('amount', 0))
    tx_number = data.get('tx_number', '')

    if not telegram_id:
        return jsonify({'error': 'يرجى تسجيل الدخول أولاً'}), 401

    if amount <= 0:
        return jsonify({'error': 'المبلغ غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    if tx_type == 'withdraw':
        user = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not user or user['balance'] < amount:
            conn.close()
            return jsonify({'error': 'رصيد العميل لا يكفي للسحب'}), 400

    cursor.execute('''
        INSERT INTO transactions (telegram_id, type, method, amount, tx_number)
        VALUES (?, ?, ?, ?, ?)
    ''', (telegram_id, tx_type, method, amount, tx_number))
    
    conn.commit()
    conn.close()
    return jsonify({'message': 'تم تقديم الطلب بنجاح وهو قيد المراجعة'})

@app.route('/api/admin/transaction/action', methods=['POST'])
def admin_transaction_action():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'يرجى تسجيل الدخول'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()

    admin = cursor.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'غير مصرح لك'}), 403

    data = request.json or {}
    tx_id = data.get('tx_id')
    action = data.get('action')

    tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or tx['status'] != 'pending':
        conn.close()
        return jsonify({'error': 'المعاملة غير موجودة أو معالجة سابقاً'}), 400

    if action == 'approve':
        if tx['type'] == 'deposit':
            cursor.execute("UPDATE users SET balance = balance + ?, deposit_count = deposit_count + 1 WHERE telegram_id = ?",
                           (tx['amount'], tx['telegram_id']))
            update_cashier_balance(cursor, tx['amount'])
        elif tx['type'] == 'withdraw':
            cursor.execute("UPDATE users SET balance = balance - ?, withdraw_count = withdraw_count + 1 WHERE telegram_id = ?",
                           (tx['amount'], tx['telegram_id']))
            # خصم المبلغ من الخزينة عند الموافقة على السحب
            update_cashier_balance(cursor, -tx['amount'])

        cursor.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (tx_id,))
    else:
        cursor.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (tx_id,))

    conn.commit()
    conn.close()
    return jsonify({'message': f'تمت معالجة الطلب بـ {action}'})

# --- دالة تشغيل البوت في الخلفية عند بدء السيرفر ---
def start_bot_process():
    try:
        subprocess.run(["python", "bot.py"])
    except Exception as e:
        print(f"Error starting bot process: {e}")

if __name__ == '__main__':
    # تشغيل البوت في خيط خلفي حتى لا يعطل سيرفر Flask
    threading.Thread(target=start_bot_process, daemon=True).start()

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
