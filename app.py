import os
import sqlite3
import random
from datetime import datetime
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)
DB_NAME = "database.db"

# ---------------------------------------------------------
# إعداد قاعدة البيانات وتأسيس الجداول
# ---------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # جدول المستخدمين (حساب واحد لكل مستخدم عبر Telegram ID)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.0,
            is_admin INTEGER DEFAULT 0,
            is_cashier INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول سجل العمليات (إيداع وسحب)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            type TEXT, -- deposit / withdraw
            amount REAL,
            status TEXT, -- pending / completed / rejected
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
        )
    ''')

    # جدول إعدادات الخوارزمية ولوحة الإدارة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # تعيين نسبة الربح المباشرة الافتراضية للعبة (Win Rate: 30%)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('win_rate', '30')")
    
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------
# واجهات البرمجة (API) للموقع والبوت
# ---------------------------------------------------------

# 1. جلب بيانات وتفاصيل الحساب
@app.route('/api/user/<int:telegram_id>', methods=['GET'])
def get_user(telegram_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    if user is None:
        return jsonify({'error': 'User not found'}), 404
    return jsonify(dict(user))

# 2. إنشاء حساب جديد أو تسجيل الدخول للتطبيق
@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    telegram_id = data.get('telegram_id')
    username = data.get('username', '')
    
    if not telegram_id:
        return jsonify({'error': 'Telegram ID missing'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    
    if not user:
        cursor.execute("INSERT INTO users (telegram_id, username, balance) VALUES (?, ?, 0.0)", (telegram_id, username))
        conn.commit()
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        
    conn.close()
    return jsonify(dict(user))

# 3. خوارزمية اللعبة والخصم/الإضافة التلقائية للرصيد
@app.route('/api/play', methods=['POST'])
def play():
    data = request.json
    telegram_id = data.get('telegram_id')
    bet_amount = float(data.get('bet_amount', 0))
    
    if bet_amount <= 0:
        return jsonify({'error': 'مبلغ الرهان غير صالح'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    
    if not user or user['balance'] < bet_amount:
        conn.close()
        return jsonify({'error': 'الرصيد غير كافٍ'}), 400

    # خصم رصيد الرهان فوراً عند بدء الضربة
    new_balance = user['balance'] - bet_amount
    cursor.execute("UPDATE users SET balance = ? WHERE telegram_id = ?", (new_balance, telegram_id))
    
    # جلب نسبة التحكم من الخوارزمية
    win_setting = cursor.execute("SELECT value FROM settings WHERE key = 'win_rate'").fetchone()
    win_rate = int(win_setting['value']) if win_setting else 30

    # تدوير الخوارزمية
    is_win = random.randint(1, 100) <= win_rate
    multiplier = 0
    payout = 0

    if is_win:
        multiplier = random.choice([1.5, 2.0, 3.0, 5.0])
        payout = bet_amount * multiplier
        new_balance += payout
        cursor.execute("UPDATE users SET balance = ? WHERE telegram_id = ?", (new_balance, telegram_id))

    conn.commit()
    conn.close()

    return jsonify({
        'win': is_win,
        'multiplier': multiplier,
        'payout': payout,
        'new_balance': new_balance
    })

# 4. طلبات الإيداع والسحب التلقائي/الكاشير
@app.route('/api/transaction', methods=['POST'])
def transaction():
    data = request.json
    telegram_id = data.get('telegram_id')
    trans_type = data.get('type') # deposit / withdraw
    amount = float(data.get('amount', 0))
    
    if amount <= 0 or trans_type not in ['deposit', 'withdraw']:
        return jsonify({'error': 'بيانات غير صالحة'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if trans_type == 'withdraw':
        user = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not user or user['balance'] < amount:
            conn.close()
            return jsonify({'error': 'رصيدك غير كافٍ للسحب'}), 400
        # تجميد/خصم المباشر لمبلغ السحب
        cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amount, telegram_id))
        
    cursor.execute("INSERT INTO transactions (telegram_id, type, amount, status) VALUES (?, ?, ?, 'pending')", 
                   (telegram_id, trans_type, amount))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'تم ارسال الطلب بنجاح وهو قيد المعالجة'})

# 5. لوحة التحكم الإدارية (الخصم، تعديل الرصيد، والتحكم بالخوارزمية)
@app.route('/api/admin/adjust_balance', methods=['POST'])
def admin_adjust_balance():
    data = request.json
    target_id = data.get('telegram_id')
    amount = float(data.get('amount', 0)) # قيمة موجبة للإضافة، سالبة للخصم
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amount, target_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'تم تحديث رصيد الحساب بنجاح'})

@app.route('/api/admin/set_algorithm', methods=['POST'])
def set_algorithm():
    data = request.json
    win_rate = data.get('win_rate') # نسبة الفوز من 0 إلى 100
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'win_rate'", (str(win_rate),))
    conn.commit()
    conn.close()
    return jsonify({'message': 'تم تحديث خوارزمية الربح بنجاح'})

@app.route('/')
def home():
    return "Casino API & Server Engine is Running Successfully!"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
