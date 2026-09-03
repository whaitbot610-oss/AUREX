import os
import sys
import sqlite3
import random
import string
import threading
import subprocess
import json
import urllib.request
import urllib.parse
import traceback
import glob
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, session, redirect, url_for

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "aurex_casino_secret_key_2026_secure")

# إعدادات الجلسات لضمان العمل المستقل والدخول الفوري
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 30

# توحيد مسار قاعدة البيانات
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

# --- دالة إرسال إشعار تلغرام إلى الأدمن والمستخدم ---
def send_telegram_notify(chat_id, message):
    bot_token = os.environ.get("BOT_TOKEN", "8948439052:AAHv-UWeTMQmHybxspFRVRpnjIqetmW8LbI")
    if not bot_token or not chat_id:
        return
    
    def _send():
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload)
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print(f"Failed to send telegram notification to {chat_id}: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()

def send_telegram_admin_notify(message):
    admin_id = os.environ.get("MAIN_ADMIN_ID", "7255100997")
    send_telegram_notify(admin_id, message)

# --- معالجة CORS والاتصال المستقل ---
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Telegram-User-Id, X-Telegram-Init-Data'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    print(f"!!! SERVER ERROR OCCURRED: {str(e)} !!!", flush=True)
    traceback.print_exc()
    return jsonify({'status': 'error', 'error': f'حدث خطأ غير متوقع في السيرفر: {str(e)}'}), 500

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

# --- تهيئة وتوحيد قاعدة البيانات ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. جدول البوتات والكاشيرة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            bot_token TEXT UNIQUE,
            cashier_balance REAL DEFAULT 10000.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. جدول المستخدمين الموحد
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            bot_id INTEGER DEFAULT 1,
            username TEXT,
            site_username TEXT UNIQUE,
            site_password TEXT,
            balance REAL DEFAULT 0.0,
            bot_balance REAL DEFAULT 0.0,
            site_balance REAL DEFAULT 0.0,
            total_spent REAL DEFAULT 0.0,
            deposit_count INTEGER DEFAULT 0,
            withdraw_count INTEGER DEFAULT 0,
            referrals_count INTEGER DEFAULT 0,
            spins_count INTEGER DEFAULT 0,
            free_spins INTEGER DEFAULT 0,
            referred_by INTEGER,
            got_welcome_bonus INTEGER DEFAULT 0,
            security_passed INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            code_restricted_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    user_columns = [
        ("site_username", "TEXT UNIQUE"),
        ("site_password", "TEXT"),
        ("balance", "REAL DEFAULT 0.0"),
        ("bot_balance", "REAL DEFAULT 0.0"),
        ("site_balance", "REAL DEFAULT 0.0"),
        ("total_spent", "REAL DEFAULT 0.0"),
        ("deposit_count", "INTEGER DEFAULT 0"),
        ("withdraw_count", "INTEGER DEFAULT 0"),
        ("referrals_count", "INTEGER DEFAULT 0"),
        ("spins_count", "INTEGER DEFAULT 0"),
        ("free_spins", "INTEGER DEFAULT 0"),
        ("referred_by", "INTEGER"),
        ("got_welcome_bonus", "INTEGER DEFAULT 0"),
        ("security_passed", "INTEGER DEFAULT 0"),
        ("is_admin", "INTEGER DEFAULT 0"),
        ("is_banned", "INTEGER DEFAULT 0")
    ]

    for col_name, col_type in user_columns:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    try:
        cursor.execute("UPDATE users SET bot_balance = balance WHERE bot_balance = 0.0 AND balance > 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("UPDATE users SET free_spins = spins_count WHERE free_spins = 0 AND spins_count > 0")
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

    # 4. جدول الأكواد الموحد (يدعم active و is_active للتوافق)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount REAL,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            bot_id INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    for code_col in [("active", "INTEGER DEFAULT 1"), ("is_active", "INTEGER DEFAULT 1")]:
        try:
            cursor.execute(f"ALTER TABLE gift_codes ADD COLUMN {code_col[0]} {code_col[1]}")
        except sqlite3.OperationalError:
            pass

    # 5. سجل الأكواد المستخدمة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_codes (
            telegram_id INTEGER,
            code TEXT,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (telegram_id, code)
        )
    ''')

    # 6. وسائل الدفع
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            number TEXT,
            active INTEGER DEFAULT 1
        )
    ''')

    # 7. إعدادات النظام
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # 8. جدول تتبع جولات الألعاب (لحفظ نتائج الدوران حتى انتهائها)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            game_name TEXT,
            bet_amount REAL,
            payout REAL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    default_wheel_probs = {
        "0": 50.0,
        "5": 20.0,
        "10": 12.0,
        "15": 8.0,
        "25": 5.0,
        "50": 3.0,
        "100": 1.5,
        "500": 0.4,
        "10000": 0.1
    }

    defaults = [
        ('maintenance', 'off'),
        ('welcome_bonus', '10.0'),
        ('welcome_bonus_enabled', '1'),
        ('referral_bonus', '1'),
        ('rtp_rate', '30.0'),
        ('cashier_balance', '10000.0'),
        ('wheel_probabilities', json.dumps(default_wheel_probs))
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("SELECT * FROM bots WHERE id = 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO bots (id, bot_name, cashier_balance) VALUES (1, 'AUREX Main Bot', 10000.0)")

    cursor.execute("SELECT * FROM users WHERE site_username = 'Admin'")
    if not cursor.fetchone():
        cursor.execute('''
            INSERT INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance, is_admin)
            VALUES (?, ?, ?, ?, 0.0, 0.0, 1)
        ''', (7255100997, 'Admin', 'Admin', 'Admin096'))

    conn.commit()
    conn.close()

init_db()

# --- أدوات مساعدة ---
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
    if row and row['cashier_balance'] is not None:
        return float(row['cashier_balance'])
    
    cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'")
    s_row = cursor.fetchone()
    return float(s_row['value']) if s_row else 0.0

def update_bot_cashier(cursor, amount_change, bot_id=1):
    old_balance = get_bot_cashier(cursor, bot_id)
    new_balance = max(0.0, old_balance + amount_change)
    cursor.execute("UPDATE bots SET cashier_balance = ? WHERE id = ?", (new_balance, bot_id))
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cashier_balance', ?)", (str(new_balance),))
    return old_balance, new_balance

def get_setting(cursor, key, default="0"):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row['value'] if row else default

def set_setting(cursor, key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

def get_authenticated_user_id():
    user_id = session.get('user_id')
    data = get_req_data()
    
    raw_tg = (
        request.headers.get('X-Telegram-User-Id') or
        data.get('telegram_id') or 
        data.get('user_id') or 
        request.args.get('telegram_id') or 
        request.args.get('user_id')
    )

    if not raw_tg and request.referrer:
        try:
            parsed_ref = urllib.parse.urlparse(request.referrer)
            ref_query = urllib.parse.parse_qs(parsed_ref.query)
            if 'telegram_id' in ref_query:
                raw_tg = ref_query['telegram_id'][0]
            elif 'user_id' in ref_query:
                raw_tg = ref_query['user_id'][0]
            elif 'tgWebAppData' in ref_query or 'initData' in ref_query:
                init_str = ref_query.get('tgWebAppData', ref_query.get('initData', ['']))[0]
                if init_str:
                    init_parsed = urllib.parse.parse_qs(init_str)
                    if 'user' in init_parsed:
                        u_json = json.loads(init_parsed['user'][0])
                        raw_tg = u_json.get('id')
        except Exception:
            pass
    
    if not raw_tg:
        init_data = (
            request.headers.get('X-Telegram-Init-Data') or 
            request.args.get('tgWebAppData') or 
            request.args.get('initData') or 
            data.get('initData')
        )
        if init_data:
            try:
                parsed = urllib.parse.parse_qs(init_data)
                if 'user' in parsed:
                    user_json = json.loads(parsed['user'][0])
                    raw_tg = user_json.get('id')
            except Exception:
                pass

    conn = get_db_connection()
    cursor = conn.cursor()

    if raw_tg:
        raw_str = str(raw_tg).strip()
        user = cursor.execute("""
            SELECT telegram_id FROM users 
            WHERE telegram_id = ? 
               OR CAST(telegram_id AS TEXT) = ? 
               OR LOWER(site_username) = LOWER(?) 
               OR LOWER(username) = LOWER(?)
        """, (raw_str, raw_str, raw_str, raw_str)).fetchone()

        if user:
            found_id = user['telegram_id']
            conn.close()
            session['user_id'] = found_id
            return found_id

        if raw_str.isdigit():
            tg_id = int(raw_str)
            cursor.execute("""
                INSERT OR IGNORE INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance, free_spins)
                VALUES (?, ?, ?, ?, 0.0, 0.0, 0)
            """, (tg_id, f"user_{tg_id}", f"user_{tg_id}", f"pass_{tg_id}"))
            conn.commit()
            conn.close()
            session['user_id'] = tg_id
            return tg_id

    if user_id:
        user = cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id))).fetchone()
        conn.close()
        if user:
            return user['telegram_id']

    conn.close()
    return None

@app.before_request
def check_maintenance():
    if request.method == 'OPTIONS':
        return '', 200
    if request.path.startswith('/api/') and not request.path.startswith('/api/admin') and not request.path.startswith('/api/auth') and not request.path.startswith('/api/register_site'):
        conn = get_db_connection()
        cursor = conn.cursor()
        m = get_setting(cursor, 'maintenance', 'off')
        conn.close()
        if m == 'on' or m == '1':
            return jsonify({'status': 'error', 'error': 'الموقع في وضع الصيانة حالياً'}), 533

# --- الصفحات ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/wheel')
def wheel_page():
    return render_template('wheel.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return "غير مصرح لك بالدخول إلى لوحة الإدارة", 403
    return render_template('admin.html')

# --- المصادقة والحسابات ---
@app.route('/api/auth/login', methods=['POST'])
def login_site():
    data = get_req_data()
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()

    if not username or not password:
        return jsonify({'status': 'error', 'error': 'يرجى إدخال اسم المستخدم وكلمة المرور'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE (LOWER(site_username) = LOWER(?) OR LOWER(username) = LOWER(?)) AND site_password = ?", 
                   (username, username, password))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return jsonify({'status': 'error', 'error': 'اسم المستخدم أو كلمة المرور غير صحيحة'}), 401

    session.permanent = True
    session['user_id'] = user['telegram_id']
    session['is_admin'] = bool(user['is_admin'])

    return jsonify({
        'status': 'success',
        'telegram_id': user['telegram_id'],
        'username': user['site_username'] or user['username'],
        'bot_balance': user['bot_balance'] or 0.0,
        'site_balance': user['site_balance'] or 0.0,
        'free_spins': user['free_spins'] or 0,
        'referrals_count': user['referrals_count'] or 0,
        'got_welcome_bonus': user['got_welcome_bonus'] or 0,
        'is_admin': bool(user['is_admin'])
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout_site():
    session.clear()
    return jsonify({'status': 'success', 'message': 'تم تسجيل الخروج بنجاح'})

@app.route('/api/user/account', methods=['GET', 'POST'])
def get_user_account():
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({'status': 'error', 'error': 'تعذر التعرف على حساب المستخدم'}), 400
    
    conn = get_db_connection()
    user = conn.execute("""
        SELECT telegram_id, bot_id, username, site_username, site_password, 
               bot_balance, site_balance, referrals_count, free_spins, 
               got_welcome_bonus, is_admin 
        FROM users 
        WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?
    """, (user_id, str(user_id))).fetchone()
    conn.close()
    if not user:
        return jsonify({'status': 'error', 'error': 'الحساب غير موجود'}), 404
        
    res = dict(user)
    res['free_spins'] = res['free_spins'] if res['free_spins'] is not None else 0
    res['spins'] = res['free_spins']
    res['freeSpins'] = res['free_spins']
    res['site_balance'] = res['site_balance'] if res['site_balance'] is not None else 0.0
    res['balance'] = res['site_balance']
    res['bot_balance'] = res['bot_balance'] if res['bot_balance'] is not None else 0.0
    return jsonify(res)

@app.route('/api/get-spins', methods=['GET', 'POST'])
@app.route('/api/wheel/status', methods=['GET', 'POST'])
def get_spins_status():
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({'status': 'error', 'error': 'تعذر تحديد آيدي المستخدم، يرجى فتح العجلة من البوت', 'free_spins': 0, 'spins': 0, 'bot_balance': 0.0}), 200

    conn = get_db_connection()
    user = conn.execute("""
        SELECT telegram_id, free_spins, bot_balance, site_balance 
        FROM users 
        WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?
    """, (user_id, str(user_id))).fetchone()
    conn.close()

    if not user:
        return jsonify({'free_spins': 0, 'spins': 0, 'bot_balance': 0.0, 'site_balance': 0.0}), 200

    spins_count = user['free_spins'] if user['free_spins'] is not None else 0
    return jsonify({
        'status': 'success',
        'telegram_id': user['telegram_id'],
        'free_spins': spins_count,
        'spins': spins_count,
        'freeSpins': spins_count,
        'bot_balance': user['bot_balance'] or 0.0,
        'site_balance': user['site_balance'] or 0.0
    })

@app.route('/api/register_site', methods=['POST'])
def register_site():
    data = get_req_data()
    raw_tg = str(data.get('telegram_id', '')).strip()
    site_user = str(data.get('site_user', '')).strip()
    site_pass = str(data.get('site_pass', '')).strip()
    bot_id = int(data.get('bot_id', 1))
    referred_by = data.get('referred_by')
    
    if len(site_user) < 3 or len(site_pass) < 3:
        return jsonify({'status': 'error', 'error': 'اسم المستخدم وكلمة المرور يجب أن يتجاوزا 3 خانات'}), 400
        
    if not raw_tg or not raw_tg.isdigit():
        telegram_id = random.randint(100000000, 999999999)
    else:
        telegram_id = int(raw_tg)

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE LOWER(site_username) = LOWER(?) AND telegram_id != ?", (site_user, telegram_id))
    if cursor.fetchone():
        conn.close()
        return jsonify({'status': 'error', 'error': 'اسم المستخدم هذا مأخوذ بالفعل، اختر اسماً آخر'}), 400

    cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id)))
    existing_user = cursor.fetchone()
    
    try:
        if existing_user:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ?, bot_id = ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?",
                           (site_user, site_pass, bot_id, telegram_id, str(telegram_id)))
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
                        SET referrals_count = COALESCE(referrals_count, 0) + 1, free_spins = COALESCE(free_spins, 0) + 1 
                        WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?
                    ''', (ref_id, str(ref_id)))
                    
                    send_telegram_notify(
                        ref_id,
                        f"🎉 <b>تم منحك لفة مجانية جديدة!</b>\nقام المستخدم <code>{site_user}</code> بإنشاء حساب عن طريق رابط إحالتك."
                    )
                except (ValueError, TypeError):
                    pass

        conn.commit()
        
        user_data = cursor.execute("SELECT telegram_id, site_username, bot_balance, site_balance, free_spins, is_admin FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
        conn.close()
        
        session.permanent = True
        session['user_id'] = user_data['telegram_id']
        session['is_admin'] = bool(user_data['is_admin'])

        return jsonify({
            'status': 'success',
            'message': 'تم إنشاء الحساب وتسجيل الدخول بنجاح',
            'telegram_id': user_data['telegram_id'],
            'site_username': user_data['site_username'],
            'bot_balance': user_data['bot_balance'] or 0.0,
            'site_balance': user_data['site_balance'] or 0.0,
            'free_spins': user_data['free_spins'] or 0
        })
    except sqlite3.IntegrityError as e:
        conn.close()
        return jsonify({'status': 'error', 'error': f'حدث خطأ في قاعدة البيانات: {str(e)}'}), 400

# --- البونص الترحيبي ---
@app.route('/api/bonus/welcome', methods=['POST'])
def claim_welcome_bonus():
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'status': 'error', 'error': 'لم يتم التعرف على الحساب'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    if not user:
        conn.close()
        return jsonify({'status': 'error', 'error': 'المستخدم غير موجود'}), 404

    if user['got_welcome_bonus'] == 1:
        conn.close()
        return jsonify({'status': 'error', 'error': 'لقد حصلت على البونص الترحيبي سابقاً'}), 400

    try:
        bonus_amount = float(get_setting(cursor, 'welcome_bonus', '10.0'))
    except ValueError:
        bonus_amount = 10.0

    if bonus_amount <= 0:
        conn.close()
        return jsonify({'status': 'error', 'error': 'لا يوجد بونص ترحيبي متاح حالياً'}), 400

    bot_id = user['bot_id'] or 1
    cashier = get_bot_cashier(cursor, bot_id)

    if cashier < bonus_amount:
        conn.close()
        return jsonify({'status': 'error', 'error': 'رصيد الكاشيرة غير كافٍ لصرف البونص الترحيبي حالياً'}), 400

    # خصم البونص فعلياً من الكاشيرة وإضافته لرصيد موقع المستخدم
    old_cashier, new_cashier = update_bot_cashier(cursor, -bonus_amount, bot_id)
    cursor.execute("UPDATE users SET site_balance = COALESCE(site_balance, 0.0) + ?, got_welcome_bonus = 1 WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (bonus_amount, telegram_id, str(telegram_id)))

    conn.commit()
    updated_user = cursor.execute("SELECT site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    conn.close()

    user_name_str = user['site_username'] or user['username'] or str(telegram_id)
    send_telegram_admin_notify(
        f"🎁 <b>خصم كاشيرة (بونص ترحيبي):</b>\n"
        f"👤 المستخدم: {user_name_str} (ID: <code>{telegram_id}</code>)\n"
        f"💰 القيمة: <b>{bonus_amount} NSP</b>\n"
        f"🏦 الكاشيرة قبل: <code>{old_cashier:.2f} NSP</code>\n"
        f"🏦 الكاشيرة بعد: <code>{new_cashier:.2f} NSP</code>"
    )

    return jsonify({
        'status': 'success',
        'message': f'مبروك! تم إضافة البونص الترحيبي ({bonus_amount}) إلى رصيد موقعك',
        'bonus_amount': bonus_amount,
        'new_site_balance': updated_user['site_balance']
    })

# --- تحويل الرصيد ---
@app.route('/api/balance/transfer_to_site', methods=['POST'])
def transfer_to_site():
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'status': 'error', 'error': 'تعذر التعرف على المعرف'}), 401

    data = get_req_data()
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

    if amount <= 0:
        return jsonify({'status': 'error', 'error': 'مبلغ التحويل غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT bot_balance, site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()

    if not user or (user['bot_balance'] or 0.0) < amount:
        conn.close()
        return jsonify({'status': 'error', 'error': 'رصيد البوت غير كافٍ للتحويل إلى الموقع'}), 400

    new_bot_bal = (user['bot_balance'] or 0.0) - amount
    new_site_bal = (user['site_balance'] or 0.0) + amount

    cursor.execute("UPDATE users SET bot_balance = ?, site_balance = ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?",
                   (new_bot_bal, new_site_bal, telegram_id, str(telegram_id)))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تم شحن {amount} من رصيد البوت إلى رصيد الموقع بنجاح',
        'bot_balance': new_bot_bal,
        'site_balance': new_site_bal
    })

@app.route('/api/balance/transfer_to_bot', methods=['POST'])
def transfer_to_bot():
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'status': 'error', 'error': 'تعذر التعرف على المعرف'}), 401

    data = get_req_data()
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

    if amount <= 0:
        return jsonify({'status': 'error', 'error': 'مبلغ السحب غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT bot_balance, site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()

    if not user or (user['site_balance'] or 0.0) < amount:
        conn.close()
        return jsonify({'status': 'error', 'error': 'رصيد الموقع غير كافٍ للسحب إلى البوت'}), 400

    new_site_bal = (user['site_balance'] or 0.0) - amount
    new_bot_bal = (user['bot_balance'] or 0.0) + amount

    cursor.execute("UPDATE users SET bot_balance = ?, site_balance = ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?",
                   (new_bot_bal, new_site_bal, telegram_id, str(telegram_id)))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تم سحب {amount} من رصيد الموقع إلى رصيد البوت بنجاح',
        'bot_balance': new_bot_bal,
        'site_balance': new_site_bal
    })

# ==================== نظام الألعاب الديناميكي (AUREX Dynamic Games) ====================

@app.route('/api/games/list', methods=['GET'])
def list_games_in_folder():
    """
    قراءة واستقبال الألعاب الموجودة في مجلد الألعاب تلقائياً دون الحاجة إلى تعديل الكود عند إضافة لعبة جديدة.
    """
    games = []
    base_dirs = [
        os.path.join(app.static_folder, 'games'),
        os.path.join(app.template_folder, 'games'),
        os.path.join(os.path.dirname(__file__), 'games')
    ]
    
    seen = set()
    for bdir in base_dirs:
        if os.path.exists(bdir):
            for item in os.listdir(bdir):
                item_path = os.path.join(bdir, item)
                if os.path.isdir(item_path) and item not in seen:
                    seen.add(item)
                    games.append({
                        'name': item,
                        'folder': item,
                        'url': f'/static/games/{item}/index.html' if os.path.exists(os.path.join(app.static_folder, 'games', item)) else f'/games/{item}'
                    })

    # إضافة الألعاب الافتراضية إذا لم يتوفر المجلد بعد
    if not games:
        games = [
            {'name': 'فواكة بالكيلو', 'folder': 'fruits', 'url': '/wheel'},
            {'name': 'AUREX Slots', 'folder': 'slots', 'url': '/'}
        ]

    return jsonify({'status': 'success', 'games': games})

@app.route('/api/play', methods=['POST'])
@app.route('/api/game/play', methods=['POST'])
@app.route('/api/game/start', methods=['POST'])
def start_game_round():
    """
    بدء اللعب في أي لعبة بالموقع (aurex):
    - يستجيب لرصيد الموقع حصراً والمستقل تماماً عن البوت والكاشيرة.
    - يخصم الرهان فوراً من رصيد الموقع (site_balance).
    - لا يخصم ولا يضيف أي شيء إلى الكاشيرة مطلقاً.
    - يتحكم بالخوارزمية بناءً على نسبة RTP المحددة في الإعدادات.
    - عند الفوز: يضاف الربح لرصيد الموقع عند توقف اللعبة/الأنيميشن.
    """
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'status': 'error', 'error': 'تعذر التعرف على المعرف الخاص بك'}), 401

    data = get_req_data()
    game_name = str(data.get('game_name', 'slot_game'))
    try:
        bet = float(data.get('bet_amount', data.get('bet', 0)))
    except (ValueError, TypeError):
        bet = 0.0

    if bet <= 0:
        return jsonify({'status': 'error', 'error': 'مبلغ الرهان غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()

    if not user or (user['site_balance'] or 0.0) < bet:
        conn.close()
        return jsonify({'status': 'error', 'error': 'رصيد الموقع غير كافٍ للرهان'}), 400

    # 1. الخصم الفوري للرهان من رصيد الموقع (مستقل تماماً ولا يمس الكاشيرة)
    new_site_balance = (user['site_balance'] or 0.0) - bet
    cursor.execute("UPDATE users SET site_balance = ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (new_site_balance, telegram_id, str(telegram_id)))

    # 2. التحكم الحقيقي في الخوارزمية حسب نسبة RTP من لوحة التحكم
    try:
        rtp_rate = float(get_setting(cursor, 'rtp_rate', '30.0'))
    except Exception:
        rtp_rate = 30.0

    win = (random.uniform(0, 100)) <= rtp_rate
    multiplier = float(data.get('multiplier', 2.0))
    payout = bet * multiplier if win else 0.0

    # 3. تسجيل الجولة (معلقة لحين توقف الدوران في اللعبة)
    cursor.execute("""
        INSERT INTO game_rounds (telegram_id, game_name, bet_amount, payout, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (telegram_id, game_name, bet, payout if win else 0.0))
    round_id = cursor.lastrowid

    is_instant = data.get('instant', False)
    conn.commit()

    if is_instant:
        if win and payout > 0:
            cursor.execute("UPDATE users SET site_balance = COALESCE(site_balance, 0.0) + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (payout, telegram_id, str(telegram_id)))
            new_site_balance += payout
        cursor.execute("UPDATE game_rounds SET status = 'completed' WHERE id = ?", (round_id,))
        conn.commit()
        conn.close()
        return jsonify({
            'status': 'success',
            'round_id': round_id,
            'win': win,
            'payout': payout,
            'new_balance': new_site_balance,
            'site_balance': new_site_balance
        })

    conn.close()

    return jsonify({
        'status': 'success',
        'round_id': round_id,
        'win': win,
        'payout': payout,
        'site_balance_after_bet': new_site_balance,
        'new_balance': new_site_balance,
        'message': 'تم بدء الدوران وحساب النتيجة تلقائياً، استدعِ /api/game/finish عند توقف اللعبة لترصيد الفوز'
    })

@app.route('/api/game/finish', methods=['POST'])
@app.route('/api/game/claim', methods=['POST'])
def finish_game_round():
    """
    عند إنهاء دوران/توقف أي لعبة في الموقع:
    يتم إضافة الربح المستحق لرصيد الموقع حصراً وبشكل تلقائي بناء على نتائج الخوارزمية.
    """
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'status': 'error', 'error': 'تعذر التعرف على المعرف الخاص بك'}), 401

    data = get_req_data()
    round_id = data.get('round_id')

    conn = get_db_connection()
    cursor = conn.cursor()

    if round_id:
        round_obj = cursor.execute("SELECT * FROM game_rounds WHERE id = ? AND telegram_id = ? AND status = 'pending'", (round_id, telegram_id)).fetchone()
    else:
        round_obj = cursor.execute("SELECT * FROM game_rounds WHERE telegram_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1", (telegram_id,)).fetchone()

    if not round_obj:
        conn.close()
        return jsonify({'status': 'error', 'error': 'لا توجد جولة معلقة برسم الانتهاء'}), 404

    payout = float(round_obj['payout'])

    if payout > 0:
        # إضافة الربح لرصيد الموقع فقط (دون المساس بالكاشيرة)
        cursor.execute("UPDATE users SET site_balance = COALESCE(site_balance, 0.0) + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (payout, telegram_id, str(telegram_id)))

    cursor.execute("UPDATE game_rounds SET status = 'completed' WHERE id = ?", (round_obj['id'],))
    conn.commit()

    updated_user = cursor.execute("SELECT site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    conn.close()

    return jsonify({
        'status': 'success',
        'payout_added': payout,
        'new_site_balance': updated_user['site_balance'] if updated_user else 0.0
    })

# ==================== عجلة الحظ (AUREX Lucky Wheel) ====================

@app.route('/api/wheel/spin', methods=['POST'])
def wheel_spin():
    """
    عجلة الحظ (المرتبطة بنظام الكاشيرة والبوت):
    - عند اللعب من رصيد البوت: يخصم سعر اللفة من محفظة البوت ويضاف إلى الكاشيرة.
    - عند اللعب باللفة المجانية: لا يضاف رصيد اللعب إلى الكاشيرة.
    - عند الربح في العجلة: يخصم رصيد الجائزة من الكاشيرة ويضاف إلى محفظة البوت للرابح.
    - تفعيل التحكم الحقيقي في خوارزميات العجلة داخل البوت ولوحة التحكم.
    """
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({'status': 'error', 'error': 'عذراً، يجب تشغيل العجلة عبر بوت التلغرام حصراً'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id))).fetchone()

    if not user:
        cursor.execute("""
            INSERT OR IGNORE INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance, free_spins)
            VALUES (?, ?, ?, ?, 0.0, 0.0, 0)
        """, (user_id, f"user_{user_id}", f"user_{user_id}", f"pass_{user_id}"))
        conn.commit()
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id))).fetchone()

    current_free_spins = user['free_spins'] if user and user['free_spins'] is not None else 0
    current_bot_balance = user['bot_balance'] if user and user['bot_balance'] is not None else 0.0

    is_free_spin = False
    spin_cost = 10.0
    bot_id = user['bot_id'] or 1

    # 1. حالة اللفة المجانية: لا يضاف أي رصيد إلى الكاشيرة
    if current_free_spins > 0:
        is_free_spin = True
        cursor.execute("UPDATE users SET free_spins = MAX(0, COALESCE(free_spins, 0) - 1) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id)))
    else:
        # 2. حالة اللعب من الرصيد: يخصم من البوت ويضاف إلى الكاشيرة
        if current_bot_balance >= spin_cost:
            cursor.execute("UPDATE users SET bot_balance = MAX(0.0, COALESCE(bot_balance, 0.0) - ?) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (spin_cost, user_id, str(user_id)))
            update_bot_cashier(cursor, +spin_cost, bot_id)
        else:
            conn.close()
            return jsonify({'status': 'error', 'error': 'ليس لديك لفات مجانية في البوت أو رصيد بوت كافٍ لتدوير العجلة'}), 400

    # 3. التحكم الحقيقي والفعلي في خوارزميات العجلة بحسب النسب المحددة
    probs_str = get_setting(cursor, 'wheel_probabilities', '{}')
    try:
        probs = json.loads(probs_str)
    except Exception:
        probs = {"0": 50.0, "5": 20.0, "10": 12.0, "15": 8.0, "25": 5.0, "50": 3.0, "100": 1.5, "500": 0.4, "10000": 0.1}

    numbers = [int(k) for k in probs.keys()]
    weights = [float(v) for v in probs.values()]

    chosen_reward = random.choices(numbers, weights=weights, k=1)[0]

    cashier_before = get_bot_cashier(cursor, bot_id)
    # حماية الكاشيرة من السحب السلبي
    if chosen_reward > cashier_before:
        chosen_reward = 0

    msg = "حظ أوفر، لم تكسب شيئاً" if chosen_reward == 0 else f"مبروك! لقد كسبت {chosen_reward} NSP تم إضافتها لرصيد البوت"

    # 4. عند الربح: يخصم الجائزة من الكاشيرة ويضيفها إلى محفظة البوت للعميل
    if chosen_reward > 0:
        cursor.execute("UPDATE users SET bot_balance = COALESCE(bot_balance, 0.0) + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (chosen_reward, user_id, str(user_id)))
        cashier_before, cashier_after = update_bot_cashier(cursor, -chosen_reward, bot_id)

        user_name_str = user['site_username'] or user['username'] or str(user_id)
        
        user_notify_msg = (f"🎡 <b>إشعار فوز في عجلة AUREX!</b>\n\n"
                           f"🎉 مبروك! لقد فزت بـ <b>{chosen_reward} NSP</b> تم إضافتها إلى محفظة البوت الخاصة بك مباشرة.")
        send_telegram_notify(user_id, user_notify_msg)

        admin_notify_msg = (f"🎰 <b>خصم كاشيرة (فوز بعجلة الحظ):</b>\n"
                            f"👤 المستخدم: {user_name_str} (ID: <code>{user_id}</code>)\n"
                            f"🎁 الجائزة: <b>{chosen_reward} NSP</b>\n"
                            f"🎰 نوع اللفة: {'مجانية' if is_free_spin else 'مدفوعة من رصيد البوت'}\n"
                            f"🏦 الكاشيرة قبل: <code>{cashier_before:.2f} NSP</code>\n"
                            f"🏦 الكاشيرة بعد: <code>{cashier_after:.2f} NSP</code>")
        send_telegram_admin_notify(admin_notify_msg)

    conn.commit()
    updated_user = cursor.execute("SELECT bot_balance, site_balance, free_spins FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id))).fetchone()
    conn.close()

    prize_index = numbers.index(chosen_reward) if chosen_reward in numbers else 0

    return jsonify({
        'status': 'success',
        'prize_index': prize_index,
        'reward': chosen_reward,
        'message': msg,
        'is_free_spin': is_free_spin,
        'new_bot_balance': updated_user['bot_balance'] if updated_user and updated_user['bot_balance'] is not None else 0.0,
        'new_site_balance': updated_user['site_balance'] if updated_user and updated_user['site_balance'] is not None else 0.0,
        'free_spins_left': updated_user['free_spins'] if updated_user and updated_user['free_spins'] is not None else 0
    })

# ==================== إدارة الأكواد ====================

@app.route('/api/code/create', methods=['POST'])
def create_code():
    """
    توليد الأكواد وخصم القيمة مباشرة من الكاشيرة عند الإنشاء
    """
    data = get_req_data()
    try:
        amount = float(data.get('amount', 0))
        max_uses = int(data.get('max_uses', 1))
        count = int(data.get('count', 1))
        bot_id = int(data.get('bot_id', 1))
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'error': 'بيانات الأرقام غير صالحة'}), 400

    if amount <= 0 or max_uses <= 0 or count <= 0:
        return jsonify({'status': 'error', 'error': 'يرجى إدخال قيم صحيحة للمبلغ وعدد الاستخدامات وعدد الأكواد'}), 400

    total_cost = amount * max_uses * count

    conn = get_db_connection()
    cursor = conn.cursor()
    current_cashier = get_bot_cashier(cursor, bot_id)

    if current_cashier < total_cost:
        conn.close()
        return jsonify({'status': 'error', 'error': f'رصيد كاشيرة البوت غير كافٍ! المطلوب: {total_cost}، المتاح: {current_cashier}'}), 400

    created_codes = []
    base_code_input = str(data.get('code', '')).strip().upper()

    try:
        for i in range(count):
            if base_code_input and count == 1:
                code = base_code_input
            else:
                code = "AUREX-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

            cursor.execute("""
                INSERT INTO gift_codes (code, amount, max_uses, used_count, active, is_active, bot_id) 
                VALUES (?, ?, ?, 0, 1, 1, ?)
            """, (code, amount, max_uses, bot_id))
            created_codes.append(code)

        # الخصم الفوري والمباشر من الكاشيرة عند التوليد
        old_cashier, new_cashier = update_bot_cashier(cursor, -total_cost, bot_id)

        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'codes': created_codes,
            'code': created_codes[0] if len(created_codes) == 1 else created_codes,
            'amount': amount,
            'max_uses': max_uses,
            'count': count,
            'bot_id': bot_id,
            'total_deducted': total_cost,
            'new_cashier': new_cashier,
            'message': f'تم توليد {count} كود بنجاح وخصم {total_cost}$ من كاشيرة البوت فوراً'
        })
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'status': 'error', 'error': 'أحد الأكواد المولدة موجود مسبقاً، يرجى المحاولة مجدداً'}), 400

@app.route('/api/code/use', methods=['POST'])
def use_code():
    data = get_req_data()
    telegram_id = get_authenticated_user_id() or data.get('telegram_id') or data.get('user_id')
    code_text = str(data.get('code', '')).strip().upper()

    if not telegram_id:
        return jsonify({'status': 'error', 'error': 'تعذر تحديد آيدي المستخدم'}), 400
    if not code_text:
        return jsonify({'status': 'error', 'error': 'الكود غير صحيح'}), 400

    try:
        telegram_id = int(telegram_id)
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'error': 'معرف المستخدم غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
        if not user:
            cursor.execute("""
                INSERT OR IGNORE INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance)
                VALUES (?, ?, ?, ?, 0.0, 0.0)
            """, (telegram_id, f"user_{telegram_id}", f"user_{telegram_id}", f"pass_{telegram_id}"))
            conn.commit()

        code_obj = cursor.execute("SELECT * FROM gift_codes WHERE UPPER(code) = ?", (code_text,)).fetchone()
        
        if not code_obj:
            conn.close()
            return jsonify({'status': 'error', 'error': 'الكود غير صحيح'}), 400

        active = code_obj['active'] if code_obj['active'] is not None else code_obj['is_active']
        used_count = code_obj['used_count'] if code_obj['used_count'] is not None else 0
        max_uses = code_obj['max_uses'] if code_obj['max_uses'] is not None else 1
        amount = float(code_obj['amount']) if code_obj['amount'] is not None else 0.0

        if active == 0 or used_count >= max_uses:
            conn.close()
            return jsonify({'status': 'error', 'error': 'الكود مستخدم'}), 400

        used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND UPPER(code) = ?", (telegram_id, code_text)).fetchone()
        if used:
            conn.close()
            return jsonify({'status': 'error', 'error': 'الكود مستخدم'}), 400

        real_code = code_obj['code']
        new_used_count = used_count + 1
        is_active_flag = 0 if new_used_count >= max_uses else 1

        cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (telegram_id, real_code))
        cursor.execute("UPDATE gift_codes SET used_count = ?, active = ?, is_active = ? WHERE code = ?", (new_used_count, is_active_flag, is_active_flag, real_code))
        
        cursor.execute("""
            UPDATE users 
            SET bot_balance = COALESCE(bot_balance, 0.0) + ?, balance = COALESCE(balance, 0.0) + ? 
            WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?
        """, (amount, amount, telegram_id, str(telegram_id)))
        
        cursor.execute("""
            INSERT INTO transactions (telegram_id, type, method, amount, tx_number, status)
            VALUES (?, 'gift_code', 'كود هدية', ?, ?, 'approved')
        """, (telegram_id, amount, real_code))

        conn.commit()
        
        user_info = cursor.execute("SELECT telegram_id, site_username, username, bot_balance, site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
        conn.close()

        user_name_str = (user_info['site_username'] if user_info and user_info['site_username'] else None) or (user_info['username'] if user_info and user_info['username'] else None) or str(telegram_id)
        bot_bal = float(user_info['bot_balance'] if user_info and user_info['bot_balance'] is not None else 0.0)
        site_bal = float(user_info['site_balance'] if user_info and user_info['site_balance'] is not None else 0.0)

        notify_text = (f"🎟️ <b>إشعار استخدام كود رصيد:</b>\n"
                       f"👤 المستخدم: {user_name_str} (ID: <code>{telegram_id}</code>)\n"
                       f"🔑 الكود: <code>{real_code}</code>\n"
                       f"💰 القيمة: {amount}$ (تمت إضافتها لرصيد البوت)")
        send_telegram_admin_notify(notify_text)

        return jsonify({
            'status': 'success',
            'message': 'تم تفعيل واضافة الرصيد الى محفظة البوت',
            'user': {
                'telegram_id': telegram_id,
                'username': user_name_str,
                'new_bot_balance': bot_bal,
                'new_site_balance': site_bal
            },
            'code': real_code,
            'amount': amount
        })
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"Error handling code use: {e}", flush=True)
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': f'حدث خطأ أثناء معالجة الكود: {str(e)}'}), 500

@app.route('/api/admin/codes/list', methods=['GET'])
def admin_list_codes():
    conn = get_db_connection()
    codes = conn.execute("""
        SELECT code, amount, max_uses, used_count, active, is_active, bot_id, created_at 
        FROM gift_codes 
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(c) for c in codes])

@app.route('/api/admin/codes/active', methods=['GET'])
def admin_list_active_codes():
    conn = get_db_connection()
    codes = conn.execute("""
        SELECT code, amount, max_uses, used_count, active, is_active, bot_id, created_at 
        FROM gift_codes 
        WHERE (active = 1 OR is_active = 1) AND used_count < max_uses
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(c) for c in codes])

@app.route('/api/code/deactivate', methods=['POST'])
@app.route('/api/admin/code/deactivate', methods=['POST'])
def admin_deactivate_code():
    data = get_req_data()
    code_text = str(data.get('code', '')).strip().upper()

    if not code_text:
        return jsonify({'status': 'error', 'error': 'يرجى تحديد الكود المراد إلغاؤه'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    code_obj = cursor.execute("SELECT * FROM gift_codes WHERE UPPER(code) = ?", (code_text,)).fetchone()
    if not code_obj:
        conn.close()
        return jsonify({'status': 'error', 'error': 'الكود غير موجود'}), 404

    active = code_obj['active'] if code_obj['active'] is not None else code_obj['is_active']
    if active == 0:
        conn.close()
        return jsonify({'status': 'error', 'error': 'الكود ملغى بالفعل'}), 400

    max_uses = code_obj['max_uses'] if code_obj['max_uses'] is not None else 1
    used_count = code_obj['used_count'] if code_obj['used_count'] is not None else 0
    amount = float(code_obj['amount']) if code_obj['amount'] is not None else 0.0

    remaining_uses = max(0, max_uses - used_count)
    refund_amount = remaining_uses * amount
    bot_id = code_obj['bot_id'] or 1

    cursor.execute("UPDATE gift_codes SET active = 0, is_active = 0 WHERE UPPER(code) = ?", (code_text,))
    
    if refund_amount > 0:
        update_bot_cashier(cursor, refund_amount, bot_id)

    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تم إلغاء الكود {code_text} بنجاح وإعادة {refund_amount}$ إلى كاشيرة البوت'
    })

# ==================== لوحة التحكم والطلب وإدارة الكاشيرة ====================

@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    conn = get_db_connection()
    users = conn.execute("""
        SELECT telegram_id, site_username, username, bot_balance, site_balance, 
               free_spins, referrals_count, got_welcome_bonus, created_at 
        FROM users ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

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
        return jsonify({'status': 'error', 'error': 'بيانات التعديل غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    user = cursor.execute("SELECT bot_id FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    if not user:
        conn.close()
        return jsonify({'status': 'error', 'error': 'المستخدم غير موجود'}), 404

    bot_id = user['bot_id'] or 1
    col = "site_balance" if balance_type == 'site' else "bot_balance"

    if action == 'add':
        cursor.execute(f"UPDATE users SET {col} = COALESCE({col}, 0.0) + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (amount, telegram_id, str(telegram_id)))
        old_c, new_c = update_bot_cashier(cursor, -amount, bot_id)
    else:
        cursor.execute(f"UPDATE users SET {col} = MAX(0, COALESCE({col}, 0.0) - ?) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (amount, telegram_id, str(telegram_id)))
        old_c, new_c = update_bot_cashier(cursor, +amount, bot_id)

    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success', 
        'message': f'تمت عملية {action} بمبلغ {amount} بنجاح وتم تحديث الكاشيرة',
        'cashier_balance': new_c
    })

@app.route('/api/admin/spins/grant_user', methods=['POST'])
def admin_grant_spins_user():
    data = get_req_data()
    target_user = data.get('telegram_id') or data.get('site_username') or data.get('username')
    try:
        spins = int(data.get('spins', 1))
    except (ValueError, TypeError):
        spins = 1

    if not target_user:
        return jsonify({'status': 'error', 'error': 'يرجى تحديد معرف المستخدم أو اسم الحساب'}), 400

    target_str = str(target_user).strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users 
        SET free_spins = COALESCE(free_spins, 0) + ? 
        WHERE CAST(telegram_id AS TEXT) = ? 
           OR LOWER(site_username) = LOWER(?) 
           OR LOWER(username) = LOWER(?)
    """, (spins, target_str, target_str, target_str))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'تم منح {spins} لفة مجانية بنجاح'})

@app.route('/api/admin/spins/grant_all', methods=['POST'])
def admin_grant_spins_all():
    data = get_req_data()
    try:
        spins = int(data.get('spins', 1))
    except (ValueError, TypeError):
        spins = 1

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_spins = COALESCE(free_spins, 0) + ?", (spins,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'تم منح {spins} لفة مجانية لجميع المستخدمين بنجاح'})

@app.route('/api/admin/cashier/add', methods=['POST'])
@app.route('/api/admin/cashier/update', methods=['POST'])
def admin_add_cashier():
    data = get_req_data()
    try:
        bot_id = int(data.get('bot_id', 1))
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'error': 'بيانات القيمة غير صالحة'}), 400

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

@app.route('/api/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        data = get_req_data()
        if 'rtp_rate' in data:
            set_setting(cursor, 'rtp_rate', data['rtp_rate'])
        if 'welcome_bonus' in data:
            set_setting(cursor, 'welcome_bonus', data['welcome_bonus'])
        if 'wheel_probabilities' in data:
            val = data['wheel_probabilities']
            if isinstance(val, dict):
                val = json.dumps(val)
            set_setting(cursor, 'wheel_probabilities', val)
        if 'maintenance' in data:
            set_setting(cursor, 'maintenance', data['maintenance'])
        
        conn.commit()

    rtp = get_setting(cursor, 'rtp_rate', '30.0')
    welcome = get_setting(cursor, 'welcome_bonus', '10.0')
    maint = get_setting(cursor, 'maintenance', 'off')
    wheel_p = get_setting(cursor, 'wheel_probabilities', '{}')
    conn.close()

    return jsonify({
        'status': 'success',
        'rtp_rate': float(rtp),
        'welcome_bonus': float(welcome),
        'maintenance': maint,
        'wheel_probabilities': json.loads(wheel_p) if wheel_p else {}
    })

@app.route('/api/transaction/request', methods=['POST'])
def create_transaction_request():
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'status': 'error', 'error': 'تعذر التعرف على آيدي المستخدم'}), 401

    data = get_req_data()
    tx_type = data.get('type')
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

    if tx_type not in ['deposit', 'withdraw'] or amount <= 0:
        return jsonify({'status': 'error', 'error': 'بيانات المعاملة غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    if not user:
        conn.close()
        return jsonify({'status': 'error', 'error': 'المستخدم غير موجود'}), 404

    if tx_type == 'withdraw' and (user['bot_balance'] or 0.0) < amount:
        conn.close()
        return jsonify({'status': 'error', 'error': 'رصيد محفظة البوت غير كافٍ لطلب السحب'}), 400

    cursor.execute('''
        INSERT INTO transactions (telegram_id, bot_id, type, method, amount, tx_number, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    ''', (telegram_id, user['bot_id'] or 1, tx_type, data.get('method', 'manual'), amount, data.get('tx_number', '')))
    
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': 'تم تقديم الطلب بنجاح وهو قيد المراجعة'})

@app.route('/api/admin/transactions/process', methods=['POST'])
def process_transaction():
    """
    معالجة طلبات الإيداع والسحب المقترنة بالكاشيرة ومحفظة البوت:
    - الشحن (deposit): يخصم من الكاشيرة ويضاف إلى محفظة العميل في البوت (bot_balance).
    - السحب (withdraw): يخصم من محفظة العميل في البوت (bot_balance) ويضاف إلى الكاشيرة.
    """
    data = get_req_data()
    try:
        tx_id = int(data.get('tx_id', 0))
        action = data.get('action')
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'error': 'بيانات غير صالحة'}), 400

    if action not in ['approve', 'reject']:
        return jsonify({'status': 'error', 'error': 'الإجراء غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or tx['status'] != 'pending':
        conn.close()
        return jsonify({'status': 'error', 'error': 'المعاملة غير موجودة أو معالجة سابقاً'}), 400

    bot_id = tx['bot_id'] or 1

    if action == 'approve':
        if tx['type'] == 'deposit':
            # طلب شحن وتمت الموافقة: يخصم من الكاشيرة ويضاف إلى محفظة البوت للعميل
            cursor.execute("UPDATE users SET bot_balance = COALESCE(bot_balance, 0.0) + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (tx['amount'], tx['telegram_id'], str(tx['telegram_id'])))
            update_bot_cashier(cursor, -tx['amount'], bot_id)
        elif tx['type'] == 'withdraw':
            # طلب سحب وتمت الموافقة: يخصم من محفظة البوت للعميل ويضاف إلى الكاشيرة
            cursor.execute("UPDATE users SET bot_balance = MAX(0, COALESCE(bot_balance, 0.0) - ?) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (tx['amount'], tx['telegram_id'], str(tx['telegram_id'])))
            update_bot_cashier(cursor, +tx['amount'], bot_id)

    cursor.execute("UPDATE transactions SET status = ? WHERE id = ?", (action, tx_id))
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': f'تمت معالجة الطلب بـ ({action}) بنجاح'})

def launch_bot():
    if os.environ.get("BOT_LAUNCHED") != "true":
        os.environ["BOT_LAUNCHED"] = "true"
        try:
            subprocess.Popen([sys.executable, "bot.py"])
            print(">>> تم تشغيل bot.py تلقائياً بنجاح <<<", flush=True)
        except Exception as e:
            print(f"خطأ في تشغيل البوت: {e}", flush=True)

if __name__ == '__main__':
    launch_bot()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
