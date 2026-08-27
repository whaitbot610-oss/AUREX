import os
import re
import sqlite3
import logging
import threading
import random
import string
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, session, redirect, url_for
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes
)

# ================= =========================================
# 1. الإعدادات الأساسية
# ==========================================================
MAIN_ADMIN_ID = 7255100997  # الآدمن الرئيسي
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8948439052:AAHv-UWeTMQmHybxspFRVRpnjIqetmW8LbI").strip()
SERVER_URL = os.environ.get("SERVER_URL", "https://aurex-my-bot.onrender.com").strip()
BOT_ID = "bot_main"

if not SERVER_URL.startswith("https://"):
    SERVER_URL = "https://" + SERVER_URL.replace("http://", "")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ================= =========================================
# 2. إدارة قاعدة البيانات عالية الكفاءة (WAL Mode)
# ==========================================================
def get_db():
    conn = sqlite3.connect("database.db", check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0.0,
        site_username TEXT UNIQUE, site_password TEXT, security_passed INTEGER DEFAULT 0,
        referred_by INTEGER, is_banned INTEGER DEFAULT 0, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER, type TEXT, method TEXT, amount REAL, tx_number TEXT, status TEXT DEFAULT 'pending'
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL, max_uses INTEGER, used_count INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_codes (telegram_id INTEGER, code TEXT, PRIMARY KEY (telegram_id, code))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS cashiers (bot_id TEXT PRIMARY KEY, balance REAL DEFAULT 0.0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (telegram_id INTEGER PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_methods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, number TEXT, active INTEGER DEFAULT 1)''')
    
    # الإعدادات الافتراضية
    cursor.execute("INSERT OR IGNORE INTO cashiers (bot_id, balance) VALUES (?, 10000.0)", (BOT_ID,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('win_rate', '30')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('welcome_bonus', '10.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', '0')")
    cursor.execute("INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)", (MAIN_ADMIN_ID,))
    cursor.execute("INSERT OR IGNORE INTO payment_methods (name, number) VALUES ('سيريتل كاش', '0987654321')")
    conn.commit()
    conn.close()

def get_setting(key, default="0"):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def is_admin(user_id):
    if user_id == MAIN_ADMIN_ID: return True
    conn = get_db()
    row = conn.execute("SELECT telegram_id FROM admins WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row)

def update_cashier(amount_change):
    conn = get_db()
    conn.execute("UPDATE cashiers SET balance = MAX(0.0, balance + ?) WHERE bot_id = ?", (amount_change, BOT_ID))
    conn.commit()
    conn.close()

# ================= =========================================
# 3. خادم الويب (Flask)
# ==========================================================
web_app = Flask(__name__)
web_app.secret_key = "aurex_ultra_secure_secret_key"

HTML_ADMIN = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>لوحة إدارة منصة AUREX</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, sans-serif; padding: 20px; margin: 0; }
        .container { max-width: 1000px; margin: 0 auto; }
        .card { background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
        h2, h3 { color: #38bdf8; margin-top: 0; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
        .stat-box { background: #334155; padding: 15px; border-radius: 8px; text-align: center; }
        .stat-value { font-size: 22px; font-weight: bold; color: #4ade80; margin-top: 5px; }
        input, select, button { width: 100%; padding: 10px; margin: 8px 0; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #fff; box-sizing: border-box; }
        button { background: #0284c7; font-weight: bold; cursor: pointer; border: none; }
        .alert { padding: 10px; background: #22c55e; color: #fff; border-radius: 6px; margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🌐 لوحة التحكم المركزية - AUREX Admin</h2>
        {% if msg %}<div class="alert">{{ msg }}</div>{% endif %}
        
        <div class="grid card">
            <div class="stat-box">إجمالي اللاعبين<div class="stat-value">{{ total_users }}</div></div>
            <div class="stat-box">إجمالي أرباح اللاعبين<div class="stat-value">{{ total_balance }} NSP</div></div>
            <div class="stat-box">رصيد كاشيرة البوت<div class="stat-value">{{ cashier_balance }} NSP</div></div>
            <div class="stat-box">نسبة الربح (RTP)<div class="stat-value">{{ win_rate }}%</div></div>
        </div>

        <div class="card">
            <h3>💰 شحن رصيد كاشيرة البوت</h3>
            <form method="POST" action="/admin/add_cashier">
                <label>المبلغ المراد إضافته (NSP):</label>
                <input type="number" step="0.01" name="amount" required>
                <button type="submit">إضافة رصيد للكاشيرة</button>
            </form>
        </div>

        <div class="card">
            <h3>🎰 خوارزمية الربح والخسارة (RTP)</h3>
            <form method="POST" action="/admin/set_algorithm">
                <label>نسبة فوز اللاعبين %:</label>
                <input type="number" min="0" max="100" name="win_rate" value="{{ win_rate }}" required>
                <button type="submit">حفظ الخوارزمية</button>
            </form>
        </div>
        <a href="/admin/logout" style="color: #ef4444; text-decoration: none;">تسجيل الخروج</a>
    </div>
</body>
</html>
"""

HTML_GAME = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AUREX CASINO</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { background: #0f172a; color: #fff; font-family: sans-serif; text-align: center; padding: 20px; margin: 0; }
        .card { background: #1e293b; padding: 20px; border-radius: 15px; margin-top: 10px; }
        .balance-box { font-size: 22px; color: #38bdf8; font-weight: bold; margin-bottom: 15px; }
        .slot-machine { display: flex; justify-content: center; gap: 10px; font-size: 50px; margin: 20px 0; background: #0f172a; padding: 15px; border-radius: 10px; }
        button { background: #e11d48; color: white; border: none; padding: 12px 25px; font-size: 18px; border-radius: 8px; cursor: pointer; width: 100%; font-weight: bold; }
        input { width: 90%; padding: 10px; margin: 8px 0; border-radius: 5px; border: 1px solid #334155; background: #0f172a; color: #fff; text-align: center; }
    </style>
</head>
<body>
    <h2>🎰 منصة ألعاب AUREX</h2>
    
    <div class="card" id="login-section" style="display:none;">
        <h3>🔑 تسجيل الدخول لحساب الموقع</h3>
        <input type="text" id="username" placeholder="اسم المستخدم (6 أحرف فأكثر)">
        <input type="password" id="password" placeholder="كلمة المرور">
        <button onclick="login()">دخول</button>
    </div>

    <div class="card" id="game-section" style="display:none;">
        <div class="balance-box">💎 الرصيد: <span id="user-balance">0.00</span> NSP</div>
        <div class="slot-machine">
            <span id="r1">🍒</span><span id="r2">🍋</span><span id="r3">🍇</span>
        </div>
        <button id="spin-btn" onclick="spin()">🎰 تدوير (1.00 NSP)</button>
        <p id="msg" style="margin-top:15px; font-weight:bold;"></p>
    </div>

    <script>
        const tg = window.Telegram?.WebApp;
        let activeUserId = tg?.initDataUnsafe?.user?.id;

        async function init() {
            if (tg) tg.expand();
            if (activeUserId) { loadUserData(activeUserId); } 
            else { document.getElementById('login-section').style.display = 'block'; }
        }

        async function loadUserData(id) {
            const res = await fetch(`/api/user/${id}`);
            const data = await res.json();
            if (data.success) {
                document.getElementById('login-section').style.display = 'none';
                document.getElementById('game-section').style.display = 'block';
                document.getElementById('user-balance').innerText = data.balance.toFixed(2);
            } else { document.getElementById('login-section').style.display = 'block'; }
        }

        async function login() {
            const u = document.getElementById('username').value;
            const p = document.getElementById('password').value;
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: u, password: p})
            });
            const data = await res.json();
            if(data.success) { activeUserId = data.telegram_id; loadUserData(activeUserId); } 
            else { alert(data.message); }
        }

        async function spin() {
            const btn = document.getElementById('spin-btn');
            btn.disabled = true;
            document.getElementById('msg').innerText = "جاري التدوير...";

            const res = await fetch('/api/spin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({telegram_id: activeUserId})
            });
            const data = await res.json();

            if (data.success) {
                document.getElementById('r1').innerText = data.symbols[0];
                document.getElementById('r2').innerText = data.symbols[1];
                document.getElementById('r3').innerText = data.symbols[2];
                document.getElementById('user-balance').innerText = data.new_balance.toFixed(2);
                document.getElementById('msg').innerText = data.message;
            } else { document.getElementById('msg').innerText = data.message; }
            btn.disabled = false;
        }

        init();
    </script>
</body>
</html>
"""

@web_app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'abod123':
            session['admin_logged'] = True
            return redirect('/admin')
        return render_template_string("<h3>❌ بيانات الدخول خاطئة</h3><a href='/admin/login'>إعادة المحاولة</a>")
    return '''<form method="POST" style="text-align:center;padding:50px;">
        <h2>تسجيل دخول لوحة الإدارة</h2>
        <input type="text" name="username" placeholder="المستخدم" required><br><br>
        <input type="password" name="password" placeholder="كلمة المرور" required><br><br>
        <button type="submit">دخول</button>
    </form>'''

@web_app.route('/admin')
def admin_panel_web():
    if not session.get('admin_logged'): return redirect('/admin/login')
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_bal = conn.execute("SELECT SUM(balance) FROM users").fetchone()[0] or 0.0
    cashier = conn.execute("SELECT balance FROM cashiers WHERE bot_id = ?", (BOT_ID,)).fetchone()
    cashier_bal = cashier['balance'] if cashier else 0.0
    win_rate = get_setting('win_rate', '30')
    conn.close()
    return render_template_string(HTML_ADMIN, total_users=total_users, total_balance=f"{total_bal:.2f}", cashier_balance=f"{cashier_bal:.2f}", win_rate=win_rate, msg=request.args.get('msg'))

@web_app.route('/admin/add_cashier', methods=['POST'])
def admin_add_cashier():
    if not session.get('admin_logged'): return redirect('/admin/login')
    amt = float(request.form.get('amount', 0))
    update_cashier(amt)
    return redirect('/admin?msg=تم+إضافة+الرصيد+للكاشيرة+بنجاح')

@web_app.route('/admin/set_algorithm', methods=['POST'])
def admin_set_algorithm():
    if not session.get('admin_logged'): return redirect('/admin/login')
    set_setting('win_rate', request.form.get('win_rate'))
    return redirect('/admin?msg=تم+تحديث+خوارزمية+الألعاب')

@web_app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect('/admin/login')

@web_app.route('/')
def index(): return render_template_string(HTML_GAME)

@web_app.route('/api/user/<int:user_id>')
def api_get_user(user_id):
    conn = get_db()
    u = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    if u: return jsonify({"success": True, "balance": u['balance']})
    return jsonify({"success": False, "message": "المستخدم غير موجود"})

@web_app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    conn = get_db()
    row = conn.execute("SELECT telegram_id FROM users WHERE site_username = ? AND site_password = ?", (data.get('username'), data.get('password'))).fetchone()
    conn.close()
    if row: return jsonify({"success": True, "telegram_id": row['telegram_id']})
    return jsonify({"success": False, "message": "بيانات غير صحيحة"})

@web_app.route('/api/spin', methods=['POST'])
def api_spin():
    data = request.json or {}
    user_id = data.get('telegram_id')
    conn = get_db()
    u = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    cashier = conn.execute("SELECT balance FROM cashiers WHERE bot_id = ?", (BOT_ID,)).fetchone()
    cashier_bal = cashier['balance'] if cashier else 0.0

    if not u or u['balance'] < 1.0:
        conn.close()
        return jsonify({"success": False, "message": "رصيدك غير كافٍ!"})

    win_rate = int(get_setting('win_rate', '30'))
    symbols = ["🍎", "🍋", "🍇", "🔔", "💎", "7️⃣"]
    
    # الرهان يذهب للكاشيرة
    update_cashier(1.0)
    
    # الفوز يتطلب تحقق النسبة + سيولة الكاشيرة
    is_win = (random.randint(1, 100) <= win_rate) and (cashier_bal >= 5.0)
    
    if is_win:
        s = random.choice(symbols)
        r1 = r2 = r3 = s
        win = 5.0
        msg = "🎉 فوز كبير! ربحت 5.00 NSP"
        update_cashier(-5.0)
    else:
        r1, r2, r3 = random.choice(symbols), random.choice(symbols), random.choice(symbols)
        while r1 == r2 == r3: r3 = random.choice(symbols)
        win = 0.0
        msg = "❌ خسارة! حاول مجدداً."

    new_bal = u['balance'] - 1.0 + win
    conn.execute("UPDATE users SET balance = ? WHERE telegram_id = ?", (new_bal, user_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "symbols": [r1, r2, r3], "new_balance": new_bal, "message": msg})

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# ================= =========================================
# 4. وظائف المساعدة والأمان
# ==========================================================
def validate_username(username): return len(username) >= 4
def validate_password(password): return len(password) >= 4

async def check_forced_sub(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels_str = get_setting('forced_channels', '')
    if not channels_str: return True
    channels = [c.strip() for c in channels_str.split(',') if c.strip()]
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ['left', 'kicked']: return False
        except Exception: return False
    return True

# ================= =========================================
# 5. معالجة الأوامر والقوائم
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    cursor = conn.cursor()

    db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
    if db_user and db_user['is_banned']:
        conn.close()
        await update.message.reply_text("🚫 حسابك محظور من استخدام البوت.")
        return

    if get_setting('maintenance', '0') == '1' and not is_admin(user.id):
        conn.close()
        await update.message.reply_text("🛠 البوت حالياً في حالة صيانة وتحديث، يرجى المحاولة لاحقاً.")
        return

    if not await check_forced_sub(user.id, context):
        channels = get_setting('forced_channels', '')
        btns = [[InlineKeyboardButton(f"اشترك هنا: {ch}", url=f"https://t.me/{ch.replace('@','')}")] for ch in channels.split(',') if ch]
        btns.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub")])
        conn.close()
        await update.message.reply_text("🚨 **يجب عليك الاشتراك بجميع القنوات التالية أولاً لاستخدام البوت:**", reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
        return

    ref_by = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user.id else None
    
    if not db_user:
        cursor.execute("INSERT INTO users (telegram_id, username, referred_by) VALUES (?, ?, ?)", 
                       (user.id, user.username or user.first_name, ref_by))
        conn.commit()
        if ref_by:
            try:
                await context.bot.send_message(ref_by, f"🎉 **انضم عميل جديد عبر رابط إحالتك!**\n🆔 العميل: `{user.first_name}`", parse_mode="Markdown")
            except Exception: pass
        db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()

    cursor.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE telegram_id = ?", (user.id,))
    conn.commit()
    conn.close()

    if not db_user['security_passed']:
        keyboard = [
            [InlineKeyboardButton("حمصية 🌺", callback_data="sec_wrong")],
            [InlineKeyboardButton("حموية 🍯", callback_data="sec_correct")]
        ]
        await update.message.reply_text("🔒 **سؤال حماية البوت:**\n\nحلاوة الجبن حمصية ولا حموية؟", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    db_user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    site_info = f"`{db_user['site_username']}`" if db_user and db_user['site_username'] else "❌ غير مربوط"
    balance = db_user['balance'] if db_user else 0.0

    text = (
        f"👑 **منصة AUREX المتطورة** 👑\n"
        f"──────────────────\n"
        f"👤 العميل: **{update.effective_user.first_name}**\n"
        f"🆔 المعرف: `{user_id}`\n"
        f"🌐 حساب الموقع: {site_info}\n"
        f"💎 الرصيد: **{balance:.2f} NSP**\n"
        f"──────────────────"
    )

    keyboard = [
        [InlineKeyboardButton("🌐 رابط الموقع (WebApp)", web_app=WebAppInfo(url=SERVER_URL))],
        [InlineKeyboardButton("🔑 إنشاء / تعديل حساب الموقع", callback_data="create_site_account"), InlineKeyboardButton("🔐 بيانات حسابي", callback_data="my_account")],
        [InlineKeyboardButton("📥 شحن للموقع", callback_data="dep_site"), InlineKeyboardButton("📤 سحب من الموقع", callback_data="with_site")],
        [InlineKeyboardButton("💳 شحن للبوت", callback_data="dep_bot"), InlineKeyboardButton("💰 سحب من البوت", callback_data="with_bot")],
        [InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="claim_gift")],
        [InlineKeyboardButton("📸 إرسال صورة إصابة", callback_data="send_win_shot"), InlineKeyboardButton("💬 مراسلة الدعم", callback_data="contact_support")],
        [InlineKeyboardButton("📜 سجلاتي المالية", callback_data="my_logs")]
    ]

    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم الإدارية (الآدمن)", callback_data="admin_panel")])

    await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ================= =========================================
# 6. معالج التفاعلات والأزرار
# ==========================================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    conn = get_db()
    cursor = conn.cursor()

    if data == "sec_correct":
        bonus_amt = float(get_setting('welcome_bonus', '10.0'))
        cashier = cursor.execute("SELECT balance FROM cashiers WHERE bot_id = ?", (BOT_ID,)).fetchone()
        cashier_bal = cashier['balance'] if cashier else 0.0

        if cashier_bal >= bonus_amt:
            update_cashier(-bonus_amt)
            cursor.execute("UPDATE users SET security_passed = 1, balance = balance + ? WHERE telegram_id = ?", (bonus_amt, user_id))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"كفو عليك! 🍯\n🎉 **حصلت على بونص ترحيبي بقيمة {bonus_amt:.2f} NSP وتم خصمه من الكاشيرة!**", parse_mode="Markdown")
        else:
            cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("كفو عليك! 🍯 (لم يتوفر رصيد كافٍ بالكاشيرة لمنح البونص حالياً).")

        await show_main_menu(update, context)
        return

    elif data == "sec_wrong":
        conn.close()
        await query.message.edit_text("راجع معلوماتك ملك ولا شكلك مابدك بونص ترحيبي ❌")
        return

    conn.close()

    if data == "check_sub":
        if await check_forced_sub(user_id, context):
            await query.message.delete()
            await show_main_menu(update, context)
        else: await update.effective_chat.send_message("❌ لم تشترك في كامل القنوات المطلوبة بعد.")

    elif data == "my_account":
        conn = get_db()
        u = conn.execute("SELECT site_username, site_password FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if u and u['site_username']:
            await update.effective_chat.send_message(f"🔐 **بيانات حسابك في الموقع:**\n\n👤 اسم المستخدم: `{u['site_username']}`\n🔑 كلمة المرور: `{u['site_password']}`", parse_mode="Markdown")
        else:
            await update.effective_chat.send_message("❌ ليس لديك حساب مربوط بعد! استخدم زر (إنشاء / تعديل حساب الموقع).")

    elif data == "create_site_account":
        context.user_data['state'] = 'WAIT_SITE_USER'
        await update.effective_chat.send_message("🔑 أدخل اسم المستخدم للموقع (4 أحرف على الأقل):")

    elif data in ["dep_site", "dep_bot"]:
        conn = get_db()
        pm = conn.execute("SELECT * FROM payment_methods WHERE active = 1").fetchall()
        conn.close()
        pm_txt = "\n".join([f"• {p['name']}: `{p['number']}`" for p in pm]) if pm else "سيريتل كاش"
        context.user_data['target_type'] = "الموقع" if data == "dep_site" else "البوت"
        context.user_data['state'] = 'WAIT_DEP_AMT'
        await update.effective_chat.send_message(f"📥 **طرق الدفع المتاحة:**\n{pm_txt}\n\nأرسل المبلغ المراد شحنه لـ **{context.user_data['target_type']}** بعملة NSP:", parse_mode="Markdown")

    elif data in ["with_site", "with_bot"]:
        context.user_data['target_type'] = "الموقع" if data == "with_site" else "البوت"
        context.user_data['state'] = 'WAIT_WITH_AMT'
        await update.effective_chat.send_message(f"📤 أرسل المبلغ المراد سحبه من **{context.user_data['target_type']}** بعملة NSP:", parse_mode="Markdown")

    elif data == "my_ref":
        me = await context.bot.get_me()
        await update.effective_chat.send_message(f"🔗 **رابط إحالتك الشخصي:**\n`https://t.me/{me.username}?start={user_id}`\n\n📢 سيصلك إشعار فوري عند دخول أي عميل جديد عبر رابطك!", parse_mode="Markdown")

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await update.effective_chat.send_message("🎁 أدخل كود الهدية:")

    elif data == "send_win_shot":
        context.user_data['state'] = 'WAIT_WIN_SHOT'
        await update.effective_chat.send_message("📸 أرسل صورة الإصابة / الفوز الآن:")

    elif data == "contact_support":
        context.user_data['state'] = 'WAIT_SUPPORT'
        await update.effective_chat.send_message("💬 يمكنك كتابة رسالتك أو إرسال صورة مباشرة للدعم الفني:")

    elif data == "my_logs":
        conn = get_db()
        logs = conn.execute("SELECT * FROM transactions WHERE telegram_id = ? ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        conn.close()
        if not logs:
            await update.effective_chat.send_message("📜 ليس لديك سجلات سابقة.")
            return
        txt = "📜 **سجل آخر عملياتك:**\n\n"
        for l in logs:
            txt += f"• {l['type']} | {l['amount']} NSP | الحالة: {l['status']}\n"
        await update.effective_chat.send_message(txt, parse_mode="Markdown")

    # --- لوحة التحكم الخاصة بالآدمن ---
    elif data == "admin_panel" and is_admin(user_id):
        await show_admin_panel(update, context)

    elif data == "adm_cashier" and is_admin(user_id):
        conn = get_db()
        cashier = conn.execute("SELECT balance FROM cashiers WHERE bot_id = ?", (BOT_ID,)).fetchone()
        conn.close()
        await update.effective_chat.send_message(f"🏦 **رصيد الكاشيرة الحالي:** `{cashier['balance'] if cashier else 0:.2f} NSP`", parse_mode="Markdown")

    elif data == "adm_edit_user_bal" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_ADD_BAL_ID'
        await update.effective_chat.send_message("👤 أدخل آيدي العميل المراد تعديل رصيده:")

    elif data == "adm_set_bonus" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BONUS_AMT'
        await update.effective_chat.send_message("🎁 أدخل قيمة البونص الترحيبي الجديد بـ NSP:")

    elif data == "adm_set_rtp" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_RTP'
        await update.effective_chat.send_message("🎯 أدخل نسبة فوز ألعاب الكازينو RTP (من 0 إلى 100):")

    elif data == "adm_pay_methods" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_PAY_METHOD'
        await update.effective_chat.send_message("💳 أدخل رقم سيريتل كاش / طريقة الدفع الجديدة:")

    elif data == "adm_requests" and is_admin(user_id):
        conn = get_db()
        reqs = conn.execute("SELECT * FROM transactions WHERE status = 'pending' ORDER BY id DESC LIMIT 10").fetchall()
        conn.close()
        if not reqs:
            await update.effective_chat.send_message("✅ لا يوجد طلبات معلقة حالياً.")
            return
        for r in reqs:
            btns = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ قبول", callback_data=f"app_req_{r['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"rej_req_{r['id']}")]
            ])
            await update.effective_chat.send_message(f"📥 **طلب {r['type']}**\n• العميل: `{r['telegram_id']}`\n• المبلغ: {r['amount']} NSP\n• الرقم/العملية: `{r['tx_number']}`", reply_markup=btns, parse_mode="Markdown")

    elif data.startswith("app_req_") and is_admin(user_id):
        req_id = int(data.split("_")[2])
        conn = get_db()
        r = conn.execute("SELECT * FROM transactions WHERE id = ?", (req_id,)).fetchone()
        if r and r['status'] == 'pending':
            conn.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (req_id,))
            if 'deposit' in r['type']:
                conn.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (r['amount'], r['telegram_id']))
                update_cashier(r['amount'])
            elif 'withdraw' in r['type']:
                update_cashier(r['amount']) # السحب يزيد رصيد الخزينة/الكاشيرة
            conn.commit()
            await context.bot.send_message(r['telegram_id'], f"✅ تم قبول طلب الـ {r['type']} بقيمة {r['amount']} NSP")
            await query.message.edit_text("✅ تم قبول الطلب وترحيله للكاشيرة.")
        conn.close()

    elif data.startswith("rej_req_") and is_admin(user_id):
        req_id = int(data.split("_")[2])
        conn = get_db()
        r = conn.execute("SELECT * FROM transactions WHERE id = ?", (req_id,)).fetchone()
        if r and r['status'] == 'pending':
            conn.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (req_id,))
            if 'withdraw' in r['type']:
                conn.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (r['amount'], r['telegram_id']))
            conn.commit()
            await context.bot.send_message(r['telegram_id'], f"❌ تم رفض طلب الـ {r['type']} بقيمة {r['amount']} NSP وإعادة الرصيد.")
            await query.message.edit_text("❌ تم رفض الطلب.")
        conn.close()

    elif data == "adm_gen_batch" and is_admin(user_id):
        context.user_data['state'] = 'ADM_GIFT_AMT'
        await update.effective_chat.send_message("أدخل قيمة الكود الواحد بـ NSP:")

    elif data == "adm_add_admin" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_NEW_ADMIN'
        await update.effective_chat.send_message("أدخل آيدي الآدمن الجديد المراد إضافته:")

    elif data == "adm_user_details" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_USER_DETAILS'
        await update.effective_chat.send_message("أدخل آيدي العميل أو اسم المستخدم لجلب كافة تفاصيله:")

    elif data == "adm_toggle_maint" and is_admin(user_id):
        curr = get_setting('maintenance', '0')
        new_val = '1' if curr == '0' else '0'
        set_setting('maintenance', new_val)
        status_txt = "تم تفعيل وضع الصيانة 🛠" if new_val == '1' else "تم إلغاء وضع الصيانة وتشغيل البوت 🚀"
        await update.effective_chat.send_message(status_txt)

    elif data == "adm_ban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد حظره:")

    elif data == "adm_unban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_UNBAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد إلغاء حظره:")

    elif data == "adm_disable_code" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_DISABLE_CODE'
        await update.effective_chat.send_message("أدخل الكود المراد إلغاء تفعيله:")

    elif data == "adm_broadcast" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BROADCAST'
        await update.effective_chat.send_message("📢 أدخل النص أو أرسل صورة مع شرح للإذاعة الجماعية:")

    elif data == "adm_private_msg" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_PRIV_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد مراسلته بشكل خاص:")

    elif data == "adm_stats" and is_admin(user_id):
        conn = get_db()
        tot = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        bal = conn.execute("SELECT SUM(balance) FROM users").fetchone()[0] or 0.0
        active_today = conn.execute("SELECT COUNT(*) FROM users WHERE datetime(last_active) >= datetime('now', '-1 day')").fetchone()[0]
        conn.close()
        await update.effective_chat.send_message(f"📊 **إحصائيات العملاء والمستخدمين:**\n\n• إجمالي المسجلين: `{tot}`\n• المتصلين/النشطين خلال 24 ساعة: `{active_today}`\n• إجمالي الأرصدة: `{bal:.2f} NSP`", parse_mode="Markdown")

    elif data.startswith("reply_support_") and is_admin(user_id):
        target = int(data.split("_")[2])
        context.user_data['support_target'] = target
        context.user_data['state'] = 'WAIT_ADMIN_REPLY_SUPP'
        await update.effective_chat.send_message(f"💬 اكتب الرد للعميل `{target}`:", parse_mode="Markdown")

    elif data == "main_menu":
        await show_main_menu(update, context)

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏦 رصيد الكاشيرة", callback_data="adm_cashier"), InlineKeyboardButton("📥📤 طلبات الشحن والسحب", callback_data="adm_requests")],
        [InlineKeyboardButton("💰 تعديل رصيد مستخدم", callback_data="adm_edit_user_bal"), InlineKeyboardButton("🎁 تعديل البونص الترحيبي", callback_data="adm_set_bonus")],
        [InlineKeyboardButton("🎯 تعديل نسبة الربح (RTP)", callback_data="adm_set_rtp"), InlineKeyboardButton("💳 حسابات سيريتل كاش", callback_data="adm_pay_methods")],
        [InlineKeyboardButton("🎁 توليد أكواد هدية", callback_data="adm_gen_batch"), InlineKeyboardButton("❌ إلغاء تفعيل كود", callback_data="adm_disable_code")],
        [InlineKeyboardButton("🔍 تفاصيل عميل", callback_data="adm_user_details"), InlineKeyboardButton("📊 المتصلين والأعداد", callback_data="adm_stats")],
        [InlineKeyboardButton("🛠 وضع الصيانة", callback_data="adm_toggle_maint"), InlineKeyboardButton("👮‍♂️ إضافة آدمن", callback_data="adm_add_admin")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_user"), InlineKeyboardButton("✅ إلغاء حظر", callback_data="adm_unban_user")],
        [InlineKeyboardButton("📢 إرسال جماعي", callback_data="adm_broadcast"), InlineKeyboardButton("✉️ إرسال خاص", callback_data="adm_private_msg")],
        [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
    ]
    await update.effective_chat.send_message("⚙️ **لوحة التحكم الشاملة للآدمن:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ================= =========================================
# 7. معالجة النصوص والرسائل الحرة (State Machine)
# ==========================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    state = context.user_data.get('state')

    if not state: return
    conn = get_db()
    cursor = conn.cursor()

    # إنشاء حساب الموقع
    if state == 'WAIT_SITE_USER':
        if not validate_username(text):
            await update.message.reply_text("❌ اسم المستخدم يجب أن يكون 4 أحرف على الأقل. أعد الإدخال:")
            return
        context.user_data['temp_site_user'] = text
        context.user_data['state'] = 'WAIT_SITE_PASS'
        await update.message.reply_text("أدخل كلمة المرور (4 عناصر على الأقل):")

    elif state == 'WAIT_SITE_PASS':
        if not validate_password(text):
            await update.message.reply_text("❌ كلمة المرور قصيرة! أعد الإدخال:")
            return
        site_u = context.user_data.get('temp_site_user')
        try:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?", (site_u, text, user_id))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text(f"✅ **تم ربط الحساب بنجاح!**\n👤 اسم المستخدم: `{site_u}`\n🔑 كلمة المرور: `{text}`", parse_mode="Markdown")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ اسم المستخدم مأخوذ بالفعل، أدخل اسم آخر:")
            context.user_data['state'] = 'WAIT_SITE_USER'

    # شحن الرصيد
    elif state == 'WAIT_DEP_AMT':
        try:
            amt = float(text)
            context.user_data['dep_amt'] = amt
            context.user_data['state'] = 'WAIT_DEP_TX'
            await update.message.reply_text("أدخل رقم إشعار/عملية التحويل أو رقم سيريتل كاش:")
        except ValueError: await update.message.reply_text("أدخل مبلغ صحيح.")

    elif state == 'WAIT_DEP_TX':
        amt, t_type = context.user_data.get('dep_amt'), context.user_data.get('target_type')
        cursor.execute("INSERT INTO transactions (telegram_id, type, amount, tx_number) VALUES (?, ?, ?, ?)", (user_id, f"deposit_{t_type}", amt, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ تم رفع طلب الشحن للإدارة بنجاح.")
        await context.bot.send_message(MAIN_ADMIN_ID, f"📥 **طلب شحن جديد ({t_type})!**\n• العميل: `{user_id}`\n• المبلغ: {amt} NSP\n• الرقم: `{text}`", parse_mode="Markdown")

    # سحب الرصيد
    elif state == 'WAIT_WITH_AMT':
        try:
            amt = float(text)
            u = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            if not u or u['balance'] < amt:
                await update.message.reply_text("❌ رصيدك الحالي لا يكفي لهذا السحب.")
                return
            context.user_data['with_amt'] = amt
            context.user_data['state'] = 'WAIT_WITH_TX'
            await update.message.reply_text("أدخل رقم حساب سيريتل كاش لاستلام المبلغ:")
        except ValueError: await update.message.reply_text("أدخل مبلغ صحيح.")

    elif state == 'WAIT_WITH_TX':
        amt, t_type = context.user_data.get('with_amt'), context.user_data.get('target_type')
        cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amt, user_id))
        cursor.execute("INSERT INTO transactions (telegram_id, type, amount, tx_number) VALUES (?, ?, ?, ?)", (user_id, f"withdraw_{t_type}", amt, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ تم خصم المبلغ ورفع طلب السحب للإدارة بنجاح.")
        await context.bot.send_message(MAIN_ADMIN_ID, f"📤 **طلب سحب جديد ({t_type})!**\n• العميل: `{user_id}`\n• المبلغ: {amt} NSP\n• حساب الاستلام: `{text}`", parse_mode="Markdown")

    # إضافة رصيد لعميل مباشرة (الآدمن)
    elif state == 'ADM_WAIT_ADD_BAL_ID' and is_admin(user_id):
        context.user_data['target_user'] = text
        context.user_data['state'] = 'ADM_WAIT_ADD_BAL_AMT'
        await update.message.reply_text("أدخل المبلغ المراد إضافته (أو خصمه بإضافة -):")

    elif state == 'ADM_WAIT_ADD_BAL_AMT' and is_admin(user_id):
        target = context.user_data.get('target_user')
        try:
            amt = float(text)
            cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ? OR site_username = ?", (amt, target, target))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تعديل رصيد العميل `{target}` بـ {amt} NSP بنجاح.", parse_mode="Markdown")
        except ValueError: await update.message.reply_text("أدخل مبلغ صحيح.")

    # تعديل البونص الترحيبي
    elif state == 'ADM_WAIT_BONUS_AMT' and is_admin(user_id):
        set_setting('welcome_bonus', text)
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم تعديل قيمة البونص الترحيبي إلى {text} NSP.")

    # تعديل نسبة الربح RTP
    elif state == 'ADM_WAIT_RTP' and is_admin(user_id):
        set_setting('win_rate', text)
        context.user_data.clear()
        await update.message.reply_text(f"🎯 تم تعديل خوارزمية RTP إلى {text}%.")

    # تعديل وسائل الدفع
    elif state == 'ADM_WAIT_PAY_METHOD' and is_admin(user_id):
        cursor.execute("INSERT INTO payment_methods (name, number) VALUES ('سيريتل كاش', ?)", (text,))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text(f"💳 تم إضافة حساب سيريتل كاش الجديد: `{text}`", parse_mode="Markdown")

    # توليد دفعة أكواد
    elif state == 'ADM_GIFT_AMT' and is_admin(user_id):
        try:
            context.user_data['gift_amt'] = float(text)
            context.user_data['state'] = 'ADM_GIFT_USES'
            await update.message.reply_text("أدخل عدد مرات الاستخدام المتاحة لكل كود:")
        except ValueError: await update.message.reply_text("أدخل رقم صحيح.")

    elif state == 'ADM_GIFT_USES' and is_admin(user_id):
        try:
            context.user_data['gift_uses'] = int(text)
            context.user_data['state'] = 'ADM_GIFT_COUNT'
            await update.message.reply_text("أدخل كمية الأكواد المراد توليدها (مثال: 1 أو 10 أو 50):")
        except ValueError: await update.message.reply_text("أدخل عدد صحيح.")

    elif state == 'ADM_GIFT_COUNT' and is_admin(user_id):
        try:
            count = int(text)
            amt = context.user_data.get('gift_amt')
            uses = context.user_data.get('gift_uses')
            total_cost = amt * uses * count

            cashier = cursor.execute("SELECT balance FROM cashiers WHERE bot_id = ?", (BOT_ID,)).fetchone()
            if not cashier or cashier['balance'] < total_cost:
                await update.message.reply_text(f"❌ **رصيد الكاشيرة غير كافٍ!**\nالمطلوب: {total_cost:.2f} NSP\nمتوفر: {cashier['balance'] if cashier else 0:.2f} NSP")
                context.user_data.clear()
                return

            generated = []
            update_cashier(-total_cost)
            
            for _ in range(count):
                code = "AUREX-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                cursor.execute("INSERT INTO gift_codes (code, amount, max_uses) VALUES (?, ?, ?)", (code, amt, uses))
                generated.append(code)

            conn.commit()
            context.user_data.clear()
            
            codes_txt = "\n".join([f"`{c}`" for c in generated])
            await update.message.reply_text(f"🎁 **تم إنشاء {count} كود هدية وخصم {total_cost:.2f} NSP من الكاشيرة:**\n\n{codes_txt}", parse_mode="Markdown")
        except ValueError: await update.message.reply_text("أدخل عدد صحيح.")

    # تفاصيل عميل
    elif state == 'ADM_WAIT_USER_DETAILS' and is_admin(user_id):
        context.user_data.clear()
        u = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR site_username = ?", (text, text)).fetchone()
        if u:
            await update.message.reply_text(f"👤 **تفاصيل العميل:**\n\n🆔 تلغرام: `{u['telegram_id']}`\n👤 الاسم: {u['username']}\n🌐 حساب الموقع: `{u['site_username']}`\n🔑 كلمة مرور الموقع: `{u['site_password']}`\n💎 الرصيد: `{u['balance']:.2f} NSP`\n🚫 حالة الحظر: {'محظور ❌' if u['is_banned'] else 'نشط ✅'}", parse_mode="Markdown")
        else: await update.message.reply_text("❌ العميل غير موجود.")

    # حظر وإلغاء حظر
    elif state == 'ADM_WAIT_BAN_ID' and is_admin(user_id):
        context.user_data.clear()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (text,))
        conn.commit()
        await update.message.reply_text(f"🚫 تم حظر العميل `{text}` بنجاح.", parse_mode="Markdown")

    elif state == 'ADM_WAIT_UNBAN_ID' and is_admin(user_id):
        context.user_data.clear()
        cursor.execute("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (text,))
        conn.commit()
        await update.message.reply_text(f"✅ تم إلغاء حظر العميل `{text}` بنجاح.", parse_mode="Markdown")

    # إضافة آدمن
    elif state == 'ADM_WAIT_NEW_ADMIN' and is_admin(user_id):
        context.user_data.clear()
        try:
            cursor.execute("INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)", (int(text),))
            conn.commit()
            await update.message.reply_text(f"👮‍♂️ تم إضافة الآدمن `{text}` بنجاح.", parse_mode="Markdown")
        except ValueError: await update.message.reply_text("آيدي غير صالح.")

    # إلغاء كود
    elif state == 'ADM_WAIT_DISABLE_CODE' and is_admin(user_id):
        context.user_data.clear()
        cursor.execute("UPDATE gift_codes SET is_active = 0 WHERE code = ?", (text,))
        conn.commit()
        await update.message.reply_text(f"❌ تم إلغاء تفعيل الكود `{text}` بنجاح.", parse_mode="Markdown")

    # إرسال خاص وإذاعة
    elif state == 'ADM_WAIT_PRIV_ID' and is_admin(user_id):
        context.user_data['target_priv'] = int(text)
        context.user_data['state'] = 'ADM_WAIT_PRIV_TEXT'
        await update.message.reply_text("اكتب النص المراد إرساله للعميل:")

    elif state == 'ADM_WAIT_PRIV_TEXT' and is_admin(user_id):
        target = context.user_data.get('target_priv')
        context.user_data.clear()
        try:
            await context.bot.send_message(target, f"💬 **رسالة خاصة من الإدارة:**\n\n{text}", parse_mode="Markdown")
            await update.message.reply_text("✅ تم إرسال الرسالة الخاص بنجاح.")
        except Exception as e: await update.message.reply_text(f"❌ تعذر الإرسال: {e}")

    elif state == 'ADM_WAIT_BROADCAST' and is_admin(user_id):
        context.user_data.clear()
        users = cursor.execute("SELECT telegram_id FROM users WHERE is_banned = 0").fetchall()
        count = 0
        for u in users:
            try:
                await context.bot.send_message(u['telegram_id'], f"📢 **تنبيه عام من الإدارة:**\n\n{text}", parse_mode="Markdown")
                count += 1
            except Exception: pass
        await update.message.reply_text(f"✅ تم بث الرسالة بنجاح لـ {count} عميل.")

    # الدعم الفني
    elif state == 'WAIT_SUPPORT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال رسالتك للدعم الفني وسيجيبك أحد ممثلينا قريباً.")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد على العميل", callback_data=f"reply_support_{user_id}")]])
        await context.bot.send_message(MAIN_ADMIN_ID, f"💬 **رسالة دعم من:** `{user_id}`\n\n{text}", reply_markup=btn, parse_mode="Markdown")

    elif state == 'WAIT_ADMIN_REPLY_SUPP' and is_admin(user_id):
        target = context.user_data.get('support_target')
        context.user_data.clear()
        try:
            await context.bot.send_message(target, f"💬 **رد الدعم الفني:**\n\n{text}", parse_mode="Markdown")
            await update.message.reply_text("✅ تم إرسال الرد للعميل.")
        except Exception as e: await update.message.reply_text(f"❌ خطأ بالإرسال: {e}")

    # إدخال كود هدية
    elif state == 'WAIT_GIFT_CODE':
        code_row = cursor.execute("SELECT * FROM gift_codes WHERE code = ? AND is_active = 1", (text,)).fetchone()
        if code_row and code_row['used_count'] < code_row['max_uses']:
            used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND code = ?", (user_id, text)).fetchone()
            if not used:
                cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (text,))
                cursor.execute("INSERT INTO used_codes VALUES (?, ?)", (user_id, text))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (code_row['amount'], user_id))
                conn.commit()
                await update.message.reply_text(f"🎉 **مبروك!** تم شحن رصيدك بـ **{code_row['amount']:.2f} NSP**")
            else: await update.message.reply_text("❌ لقد استخدمت هذا الكود سابقاً.")
        else: await update.message.reply_text("❌ الكود غير صالح، معطل، أو انتهت استخداماته.")
        context.user_data.clear()

    conn.close()

# معالجة الصور
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get('state')
    photo = update.message.photo[-1].file_id
    caption = update.message.caption or ""

    if state == 'WAIT_WIN_SHOT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال صورة الإصابة للإدارة بنجاح!")
        await context.bot.send_photo(MAIN_ADMIN_ID, photo, caption=f"📸 **صورة إصابة جديدة!**\n• العميل: `{user_id}`\n\n{caption}", parse_mode="Markdown")

    elif state == 'WAIT_SUPPORT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الصورة للدعم الفني.")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد على العميل", callback_data=f"reply_support_{user_id}")]])
        await context.bot.send_photo(MAIN_ADMIN_ID, photo, caption=f"💬 **صورة دعم من العميل:** `{user_id}`\n\n{caption}", reply_markup=btn, parse_mode="Markdown")

    elif state == 'ADM_WAIT_BROADCAST' and is_admin(user_id):
        context.user_data.clear()
        conn = get_db()
        users = conn.execute("SELECT telegram_id FROM users WHERE is_banned = 0").fetchall()
        conn.close()
        count = 0
        for u in users:
            try:
                await context.bot.send_photo(u['telegram_id'], photo, caption=caption)
                count += 1
            except Exception: pass
        await update.message.reply_text(f"✅ تم بث الصورة بنجاح لـ {count} عميل.")

# ================= =========================================
# 8. التشغيل والبدء
# ==========================================================
def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("🚀 تم تشغيل المنصة وتحديث البوت بنجاح...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
