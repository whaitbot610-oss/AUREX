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

# توحيد مسار قاعدة البيانات لتفادي إنشائها في مسارات مختلفة على Render
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

# --- دالة إرسال إشعار تلغرام إلى الأدمن ---
def send_telegram_admin_notify(message):
    bot_token = os.environ.get("BOT_TOKEN")
    admin_id = os.environ.get("MAIN_ADMIN_ID")
    if not bot_token or not admin_id:
        return
    
    def _send():
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = urllib.parse.urlencode({
                "chat_id": admin_id,
                "text": message,
                "parse_mode": "HTML"
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload)
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print(f"Failed to send admin notification: {e}")

    threading.Thread(target=_send, daemon=True).start()

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
    return jsonify({'error': f'حدث خطأ غير متوقع في السيرفر: {str(e)}'}), 500

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

# --- تهيئة قاعدة البيانات ---
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

    user_columns = [
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

    for col_name, col_type in user_columns:
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
            bot_id INTEGER DEFAULT 1,
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

    # 6. إعدادات النظام
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
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
        ('welcome_bonus', '10.0'),
        ('referral_bonus', '1'),
        ('rtp_rate', '30.0'),
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
        ''', (999999, 'Admin', 'Admin', 'Admin096'))

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

def set_setting(cursor, key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

def get_authenticated_user_id():
    """استخراج المعرّف تلقائياً من الجلسة أو الطلب أو الرابط (Referer) أو بيانات التلجرام المشفّرة (initData) لضمان القراءة الصحيحة للفات"""
    user_id = session.get('user_id')
    data = get_req_data()
    
    raw_tg = (
        request.headers.get('X-Telegram-User-Id') or
        data.get('telegram_id') or 
        data.get('user_id') or 
        request.args.get('telegram_id') or 
        request.args.get('user_id')
    )

    # استخراج معرّف التلغرام من رابط الصفحة الأصلية (Referer) إذا قمت بفتح العجلة برابط يحتوي على telegram_id
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
    
    # تفكيك وفك تشفير معرّف المستخدم من initData الخاص بـ Telegram WebApp
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
        # البحث بالنص، وبالرقم، وباسم المستخدم لمنع التكرار والحصول على الحساب الصحيح
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

        # إنشاء الحساب فقط إذا كان المعرّف رقمياً ولم يوجد مسبقاً في القاعدة
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
        if m == 'on':
            return jsonify({'error': 'الموقع في وضع الصيانة حالياً'}), 533

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

@app.route('/api/user/account', methods=['GET'])
def get_user_account():
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({'error': 'تعذر التعرف على حساب المستخدم'}), 400
    
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
        return jsonify({'error': 'الحساب غير موجود'}), 404
        
    res = dict(user)
    res['free_spins'] = res['free_spins'] if res['free_spins'] is not None else 0
    res['spins'] = res['free_spins']
    res['freeSpins'] = res['free_spins']
    res['site_balance'] = res['site_balance'] if res['site_balance'] is not None else 0.0
    res['balance'] = res['site_balance']
    res['bot_balance'] = res['bot_balance'] if res['bot_balance'] is not None else 0.0
    return jsonify(res)

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
                        SET referrals_count = referrals_count + 1, free_spins = free_spins + 1 
                        WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?
                    ''', (ref_id, str(ref_id)))
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
        return jsonify({'error': f'حدث خطأ في قاعدة البيانات: {str(e)}'}), 400

# --- البونص الترحيبي ---
@app.route('/api/bonus/welcome', methods=['POST'])
def claim_welcome_bonus():
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'error': 'لم يتم التعرف على الحساب'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    if user['got_welcome_bonus'] == 1:
        conn.close()
        return jsonify({'error': 'لقد حصلت على البونص الترحيبي سابقاً'}), 400

    try:
        bonus_amount = float(get_setting(cursor, 'welcome_bonus', '10.0'))
    except ValueError:
        bonus_amount = 10.0

    if bonus_amount <= 0:
        conn.close()
        return jsonify({'error': 'لا يوجد بونص ترحيبي متاح حالياً'}), 400

    bot_id = user['bot_id'] or 1
    cashier = get_bot_cashier(cursor, bot_id)

    if cashier < bonus_amount:
        conn.close()
        return jsonify({'error': 'رصيد الكاشيرة غير كافٍ لصرف البونص الترحيبي حالياً'}), 400

    update_bot_cashier(cursor, -bonus_amount, bot_id)
    cursor.execute("UPDATE users SET site_balance = site_balance + ?, got_welcome_bonus = 1 WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (bonus_amount, telegram_id, str(telegram_id)))

    conn.commit()
    updated_user = cursor.execute("SELECT site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    conn.close()

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
        return jsonify({'error': 'تعذر التعرف على المعرف'}), 401

    data = get_req_data()
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

    if amount <= 0:
        return jsonify({'error': 'مبلغ التحويل غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT bot_balance, site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()

    if not user or (user['bot_balance'] or 0.0) < amount:
        conn.close()
        return jsonify({'error': 'رصيد البوت غير كافٍ للتحويل إلى الموقع'}), 400

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
        return jsonify({'error': 'تعذر التعرف على المعرف'}), 401

    data = get_req_data()
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

    if amount <= 0:
        return jsonify({'error': 'مبلغ السحب غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT bot_balance, site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()

    if not user or (user['site_balance'] or 0.0) < amount:
        conn.close()
        return jsonify({'error': 'رصيد الموقع غير كافٍ للسحب إلى البوت'}), 400

    new_site_bal = (user['site_balance'] or 0.0) - amount
    new_bot_bal = (user['bot_balance'] or 0.0) + amount

    cursor.execute("UPDATE users SET bot_balance = ?, site_balance = ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?",
                   (new_site_bal, new_bot_bal, telegram_id, str(telegram_id)))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': f'تم سحب {amount} من رصيد الموقع إلى رصيد البوت بنجاح',
        'bot_balance': new_bot_bal,
        'site_balance': new_site_bal
    })

# ==================== نظام الألعاب والعجلة ====================

@app.route('/api/play', methods=['POST'])
def play_slot_game():
    telegram_id = get_authenticated_user_id()
    if not telegram_id:
        return jsonify({'error': 'تعذر التعرف على المعرف الخاص بك'}), 401

    data = get_req_data()
    try:
        bet = float(data.get('bet_amount', 0))
    except (ValueError, TypeError):
        bet = 0

    if bet <= 0:
        return jsonify({'error': 'مبلغ الرهان غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()

    if not user or (user['site_balance'] or 0.0) < bet:
        conn.close()
        return jsonify({'error': 'رصيد الموقع غير كافٍ للرهان'}), 400

    try:
        rtp_rate = float(get_setting(cursor, 'rtp_rate', '30.0'))
    except Exception:
        rtp_rate = 30.0

    win = (random.uniform(0, 100)) <= rtp_rate
    bot_id = user['bot_id'] or 1
    cashier = get_bot_cashier(cursor, bot_id)

    payout = bet * 2.0

    if win and (payout - bet) > cashier:
        win = False
        payout = 0.0

    if win:
        new_balance = (user['site_balance'] or 0.0) - bet + payout
        update_bot_cashier(cursor, -(payout - bet), bot_id)
    else:
        payout = 0.0
        new_balance = (user['site_balance'] or 0.0) - bet
        update_bot_cashier(cursor, bet, bot_id)

    cursor.execute("UPDATE users SET site_balance = ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (new_balance, telegram_id, str(telegram_id)))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'win': win,
        'payout': payout,
        'new_balance': new_balance
    })

# 2. عجلة الحظ (تعمل بالكامل عبر ID مع التعامل الدقيق مع اللفات المجانية)
@app.route('/api/wheel/spin', methods=['POST'])
def wheel_spin():
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({'error': 'تعذر تحديد معرّف المستخدم'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id))).fetchone()

    if not user:
        cursor.execute("""
            INSERT INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance, free_spins)
            VALUES (?, ?, ?, ?, 0.0, 0.0, 0)
        """, (user_id, f"user_{user_id}", f"user_{user_id}", f"pass_{user_id}"))
        conn.commit()
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id))).fetchone()

    current_free_spins = user['free_spins'] if user['free_spins'] is not None else 0
    current_site_balance = user['site_balance'] if user['site_balance'] is not None else 0.0

    is_free_spin = False
    if current_free_spins > 0:
        is_free_spin = True
        cursor.execute("UPDATE users SET free_spins = MAX(0, free_spins - 1) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id)))
    else:
        spin_cost = 10.0
        if current_site_balance < spin_cost:
            conn.close()
            return jsonify({'error': 'ليس لديك لفتات مجانية أو رصيد كافٍ لتدوير العجلة'}), 400
        cursor.execute("UPDATE users SET site_balance = MAX(0, site_balance - ?) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (spin_cost, user_id, str(user_id)))
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
        cursor.execute("UPDATE users SET site_balance = site_balance + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (chosen_reward, user_id, str(user_id)))
        update_bot_cashier(cursor, -chosen_reward, bot_id)

    conn.commit()
    updated_user = cursor.execute("SELECT site_balance, free_spins FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (user_id, str(user_id))).fetchone()
    conn.close()

    return jsonify({
        'status': 'success',
        'reward': chosen_reward,
        'message': msg,
        'is_free_spin': is_free_spin,
        'new_site_balance': updated_user['site_balance'] if updated_user['site_balance'] is not None else 0.0,
        'free_spins_left': updated_user['free_spins'] if updated_user['free_spins'] is not None else 0
    })

# ==================== الأكواد وإشعارات التلغرام ====================

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
        cursor.execute("INSERT INTO gift_codes (code, amount, max_uses, used_count, active, bot_id) VALUES (?, ?, ?, 0, 1, ?)",
                       (code, amount, max_uses, bot_id))
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

# إصلاح واستخدام الكود للعميل والتفعيل المباشر
@app.route('/api/code/use', methods=['POST'])
def use_code():
    data = get_req_data()
    telegram_id = get_authenticated_user_id() or data.get('telegram_id') or data.get('user_id')
    code_text = str(data.get('code', '')).strip()

    if not telegram_id:
        return jsonify({'error': 'تعذر تحديد آيدي المستخدم لتفعيل الكود'}), 400
    if not code_text:
        return jsonify({'error': 'يرجى إدخال كود الهدية'}), 400

    try:
        telegram_id = int(telegram_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'معرف المستخدم غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # إنشاء حساب تلقائي للمستخدم إذا كان يستخدم الكود لأول مرة
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    if not user:
        cursor.execute("""
            INSERT INTO users (telegram_id, username, site_username, site_password, bot_balance, site_balance)
            VALUES (?, ?, ?, ?, 0.0, 0.0)
        """, (telegram_id, f"user_{telegram_id}", f"user_{telegram_id}", f"pass_{telegram_id}"))
        conn.commit()
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()

    # البحث عن الكود بشكل غير حساس لحالة الأحرف (Case-Insensitive)
    code_obj = cursor.execute("SELECT * FROM gift_codes WHERE LOWER(code) = LOWER(?) AND active = 1", (code_text,)).fetchone()
    if not code_obj or code_obj['used_count'] >= code_obj['max_uses']:
        conn.close()
        return jsonify({'error': 'الكود غير صالح أو ملغى أو تم استخدامه بالكامل'}), 400

    used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND LOWER(code) = LOWER(?)", (telegram_id, code_text)).fetchone()
    if used:
        conn.close()
        return jsonify({'error': 'لقد استخدمت هذا الكود سابقاً'}), 400

    real_code = code_obj['code']
    cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (telegram_id, real_code))
    cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (real_code,))
    
    # إضافة القيمة لرصيد البوت ورصيد الموقع معاً لتمكين الاستخدام الفوري
    cursor.execute("UPDATE users SET bot_balance = bot_balance + ?, site_balance = site_balance + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", 
                   (code_obj['amount'], code_obj['amount'], telegram_id, str(telegram_id)))
    
    conn.commit()
    
    user_info = cursor.execute("SELECT telegram_id, site_username, username, bot_balance, site_balance FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    conn.close()

    user_name_str = user_info['site_username'] or user_info['username'] or str(telegram_id)
    notify_text = (f"🎟️ <b>إشعار استخدام كود رصيد:</b>\n"
                   f"👤 المستخدم: {user_name_str} (ID: <code>{telegram_id}</code>)\n"
                   f"🔑 الكود: <code>{real_code}</code>\n"
                   f"💰 القيمة: {code_obj['amount']}$")
    send_telegram_admin_notify(notify_text)

    return jsonify({
        'status': 'success',
        'message': f'تم تفعيل الكود بنجاح وإضافة {code_obj["amount"]} إلى رصيدك',
        'user': {
            'telegram_id': user_info['telegram_id'],
            'username': user_name_str,
            'new_bot_balance': user_info['bot_balance'],
            'new_site_balance': user_info['site_balance']
        },
        'code': real_code,
        'amount': code_obj['amount']
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
    action = data.get('action') # 'add' أو 'deduct'
    balance_type = data.get('balance_type', 'site') # 'site' أو 'bot'
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

    if not telegram_id or amount <= 0 or action not in ['add', 'deduct']:
        return jsonify({'error': 'بيانات التعديل غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    user = cursor.execute("SELECT bot_id FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    bot_id = user['bot_id'] or 1
    col = "site_balance" if balance_type == 'site' else "bot_balance"

    if action == 'add':
        cursor.execute(f"UPDATE users SET {col} = {col} + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (amount, telegram_id, str(telegram_id)))
        update_bot_cashier(cursor, -amount, bot_id)
    else:
        cursor.execute(f"UPDATE users SET {col} = MAX(0, {col} - ?) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (amount, telegram_id, str(telegram_id)))
        update_bot_cashier(cursor, +amount, bot_id)

    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': f'تمت عملية {action} بمبلغ {amount} بنجاح وتم تحديث الكاشيرة'})

@app.route('/api/admin/spins/grant_user', methods=['POST'])
def admin_grant_spins_user():
    data = get_req_data()
    target_user = data.get('telegram_id') or data.get('site_username') or data.get('username')
    try:
        spins = int(data.get('spins', 1))
    except (ValueError, TypeError):
        spins = 1

    if not target_user:
        return jsonify({'error': 'يرجى تحديد معرف المستخدم أو اسم الحساب'}), 400

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
        return jsonify({'error': 'تعذر التعرف على آيدي المستخدم'}), 401

    data = get_req_data()
    tx_type = data.get('type')
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        amount = 0

    if tx_type not in ['deposit', 'withdraw'] or amount <= 0:
        return jsonify({'error': 'بيانات المعاملة غير صالحة'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (telegram_id, str(telegram_id))).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'المستخدم غير موجود'}), 404

    if tx_type == 'withdraw' and (user['site_balance'] or 0.0) < amount:
        conn.close()
        return jsonify({'error': 'رصيد الموقع غير كافٍ لطلب السحب'}), 400

    cursor.execute('''
        INSERT INTO transactions (telegram_id, bot_id, type, method, amount, tx_number, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    ''', (telegram_id, user['bot_id'] or 1, tx_type, data.get('method', 'manual'), amount, data.get('tx_number', '')))
    
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'message': 'تم تقديم الطلب بنجاح وهو قيد المراجعة'})

@app.route('/api/admin/transactions/process', methods=['POST'])
def process_transaction():
    data = get_req_data()
    try:
        tx_id = int(data.get('tx_id', 0))
        action = data.get('action')
    except (ValueError, TypeError):
        return jsonify({'error': 'بيانات غير صالحة'}), 400

    if action not in ['approve', 'reject']:
        return jsonify({'error': 'الإجراء غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or tx['status'] != 'pending':
        conn.close()
        return jsonify({'error': 'المعاملة غير موجودة أو معالجة سابقاً'}), 400

    bot_id = tx['bot_id'] or 1

    if action == 'approve':
        if tx['type'] == 'deposit':
            cursor.execute("UPDATE users SET site_balance = site_balance + ? WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (tx['amount'], tx['telegram_id'], str(tx['telegram_id'])))
            update_bot_cashier(cursor, -tx['amount'], bot_id)
        elif tx['type'] == 'withdraw':
            cursor.execute("UPDATE users SET site_balance = MAX(0, site_balance - ?) WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?", (tx['amount'], tx['telegram_id'], str(tx['telegram_id'])))
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
            print(">>> تم تشغيل bot.py تلقائياً بنجاح <<<")
        except Exception as e:
            print(f"خطأ في تشغيل البوت: {e}")

if __name__ == '__main__':
    launch_bot()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
