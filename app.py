import os
import sqlite3
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)
DB_NAME = "database.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # جدول المستخدمين الشامل
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
            code_restricted_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول معاملات الإيداع والسحب
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            type TEXT, -- deposit / withdraw
            method TEXT, -- Syriatel / Sham
            amount REAL,
            tx_number TEXT,
            status TEXT DEFAULT 'pending', -- pending / approved / rejected
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # جدول أكواد الهدايا
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount REAL,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0
        )
    ''')
    
    # سجل الأكواد المستعملة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_codes (
            telegram_id INTEGER,
            code TEXT,
            PRIMARY KEY (telegram_id, code)
        )
    ''')

    # جدول حسابات الدفع (سيريتل كاش / شام كاش)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            number TEXT,
            active INTEGER DEFAULT 1
        )
    ''')

    # جدول الإعدادات العامة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # الإعدادات الافتراضية
    defaults = [
        ('win_rate', '30'),
        ('maintenance', 'off'),
        ('welcome_bonus', '0'),
        ('referral_bonus', '0'),
        ('cashier_balance', '1000.0')
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    conn.commit()
    conn.close()

init_db()

# Middleware لفحص وضع الصيانة
@app.before_request
def check_maintenance():
    if request.path.startswith('/api/'):
        conn = get_db_connection()
        m = conn.execute("SELECT value FROM settings WHERE key = 'maintenance'").fetchone()
        conn.close()
        if m and m['value'] == 'on' and not request.path.startswith('/api/admin'):
            return jsonify({'error': 'البوت والموقع في وضع الصيانة حالياً'}), 533

@app.route('/api/user/<int:telegram_id>', methods=['GET'])
def get_user(telegram_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify(dict(user))

@app.route('/api/register_site', methods=['POST'])
def register_site():
    data = request.json
    telegram_id = data.get('telegram_id')
    site_user = data.get('site_user')
    site_pass = data.get('site_pass')
    
    if len(site_user) < 6 or not site_pass.isdigit() or len(site_pass) < 6:
        return jsonify({'error': 'اسم المستخدم يجب أن يكون 6 أحرف على الأقل، وكلمة المرور 6 أرقام'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?",
                       (site_user, site_pass, telegram_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'تم إنشاء حساب الموقع بنجاح'})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'اسم المستخدم هذا مأخوذ بالفعل'}), 400

@app.route('/api/play', methods=['POST'])
def play():
    data = request.json
    telegram_id = data.get('telegram_id')
    bet_amount = float(data.get('bet_amount', 0))

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    if not user or user['balance'] < bet_amount:
        conn.close()
        return jsonify({'error': 'الرصيد غير كافٍ'}), 400

    new_balance = user['balance'] - bet_amount
    total_spent = user['total_spent'] + bet_amount
    
    win_setting = cursor.execute("SELECT value FROM settings WHERE key = 'win_rate'").fetchone()
    win_rate = int(win_setting['value']) if win_setting else 30

    is_win = random.randint(1, 100) <= win_rate
    multiplier = 0
    payout = 0

    if is_win:
        multiplier = random.choice([1.5, 2.0, 3.0, 5.0])
        payout = bet_amount * multiplier
        new_balance += payout

    cursor.execute("UPDATE users SET balance = ?, total_spent = ? WHERE telegram_id = ?", 
                   (new_balance, total_spent, telegram_id))
    conn.commit()
    conn.close()

    return jsonify({
        'win': is_win,
        'multiplier': multiplier,
        'payout': payout,
        'new_balance': new_balance
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
