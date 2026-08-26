import os
import re
import sqlite3
import logging
import threading
import random
from flask import Flask, render_template_string, jsonify, request, session, redirect, url_for
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes
)

# ================= =========================================
# 1. الإعدادات الأساسية والمعاملات
# ==========================================================
ADMIN_ID = 7255100997  # ضع آيدي تلغرام الخاص بك هنا
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8948439052:AAHv-UWeTMQmHybxspFRVRpnjIqetmW8LbI").strip()
SERVER_URL = os.environ.get("SERVER_URL", "https://aurex-my-bot.onrender.com").strip()
BOT_ID = "bot_main" # معرف البوت لربطه بنظام الكاشيرة الموحد

if not SERVER_URL.startswith("https://"):
    SERVER_URL = "https://" + SERVER_URL.replace("http://", "")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ================= =========================================
# 2. خادم الويب (Flask) + لوحة إدارة الموقع + WebApp الكازينو
# ==========================================================
web_app = Flask(__name__)
web_app.secret_key = "aurex_secret_admin_key_secure"

# واجهة لوحة إدارة الموقع (Admin Panel)
HTML_ADMIN = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>لوحة إدارة منصة AUREX</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, sans-serif; padding: 20px; margin: 0; }
        .container { max-width: 1000px; margin: 0 auto; }
        .card { background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        h2, h3 { color: #38bdf8; margin-top: 0; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; }
        .stat-box { background: #334155; padding: 15px; border-radius: 8px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #4ade80; margin-top: 5px; }
        input, select, button { width: 100%; padding: 10px; margin: 8px 0; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #fff; box-sizing: border-box; }
        button { background: #0284c7; font-weight: bold; cursor: pointer; border: none; }
        button:hover { background: #0369a1; }
        .alert { padding: 10px; background: #22c55e; color: #fff; border-radius: 6px; margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🌐 لوحة التحكم المركزية - AUREX Admin</h2>
        {% if msg %}<div class="alert">{{ msg }}</div>{% endif %}
        
        <div class="grid card">
            <div class="stat-box">إجمالي اللاعبين<div class="stat-value">{{ total_users }}</div></div>
            <div class="stat-box">إجمالي أرباح/أرصدة اللاعبين<div class="stat-value">{{ total_balance }} NSP</div></div>
            <div class="stat-box">رصيد كاشيرة البوت<div class="stat-value">{{ cashier_balance }} NSP</div></div>
            <div class="stat-box">نسبة الربح (RTP)<div class="stat-value">{{ win_rate }}%</div></div>
        </div>

        <div class="card">
            <h3>💰 شحن رصيد كاشيرة البوت</h3>
            <form method="POST" action="/admin/add_cashier">
                <label>اختر البوت:</label>
                <select name="bot_id"><option value="bot_main">البوت الرئيسي (bot_main)</option></select>
                <label>المبلغ المراد إضافته (NSP):</label>
                <input type="number" step="0.01" name="amount" required>
                <button type="submit">إضافة رصيد للكاشيرة</button>
            </form>
        </div>

        <div class="card">
            <h3>🎰 التحكم بخوارزمية الألعاب (نسبة الربح والخسارة)</h3>
            <form method="POST" action="/admin/set_algorithm">
                <label>نسبة فوز اللاعبين % (مثال: 20 تعني 20% فوز و 80% خسارة):</label>
                <input type="number" min="0" max="100" name="win_rate" value="{{ win_rate }}" required>
                <button type="submit">حفظ الخوارزمية</button>
            </form>
        </div>

        <div class="card">
            <h3>📢 قنوات الاشتراك الإجباري</h3>
            <form method="POST" action="/admin/set_channels">
                <label>القنوات (مفصولة بفاصلة مثال: @channel1, @channel2):</label>
                <input type="text" name="channels" value="{{ channels }}">
                <button type="submit">تحديث القنوات</button>
            </form>
        </div>
        
        <a href="/admin/logout" style="color: #ef4444; text-decoration: none;">تسجيل الخروج</a>
    </div>
</body>
</html>
"""

# واجهة الكازينو WebApp
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
        <input type="password" id="password" placeholder="كلمة المرور (أحرف وأرقام)">
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

# مسارات لوحة الإدارة
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
    channels = get_setting('forced_channels', '')
    conn.close()
    
    return render_template_string(HTML_ADMIN, total_users=total_users, total_balance=f"{total_bal:.2f}",
                                 cashier_balance=f"{cashier_bal:.2f}", win_rate=win_rate, channels=channels, msg=request.args.get('msg'))

@web_app.route('/admin/add_cashier', methods=['POST'])
def admin_add_cashier():
    if not session.get('admin_logged'): return redirect('/admin/login')
    bot_id = request.form.get('bot_id')
    amt = float(request.form.get('amount', 0))
    conn = get_db()
    conn.execute("UPDATE cashiers SET balance = balance + ? WHERE bot_id = ?", (amt, bot_id))
    conn.commit()
    conn.close()
    return redirect('/admin?msg=تم+إضافة+الرصيد+للكاشيرة+بنجاح')

@web_app.route('/admin/set_algorithm', methods=['POST'])
def admin_set_algorithm():
    if not session.get('admin_logged'): return redirect('/admin/login')
    rate = request.form.get('win_rate')
    set_setting('win_rate', rate)
    return redirect('/admin?msg=تم+تحديث+خوارزمية+الفوز+والخسارة')

@web_app.route('/admin/set_channels', methods=['POST'])
def admin_set_channels():
    if not session.get('admin_logged'): return redirect('/admin/login')
    set_setting('forced_channels', request.form.get('channels', ''))
    return redirect('/admin?msg=تم+تحديث+قنوات+الاشتراك+الإجباري')

@web_app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect('/admin/login')

# مسارات الموقع و WebApp API
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
    u, p = data.get('username'), data.get('password')
    conn = get_db()
    row = conn.execute("SELECT telegram_id FROM users WHERE site_username = ? AND site_password = ?", (u, p)).fetchone()
    conn.close()
    if row: return jsonify({"success": True, "telegram_id": row['telegram_id']})
    return jsonify({"success": False, "message": "بيانات الدخول غير صحيحة"})

@web_app.route('/api/spin', methods=['POST'])
def api_spin():
    data = request.json or {}
    user_id = data.get('telegram_id')
    conn = get_db()
    u = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    
    if not u or u['balance'] < 1.0:
        conn.close()
        return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب! شحن رصيدك عبر البوت."})

    win_rate = int(get_setting('win_rate', '30'))
    symbols = ["🍎", "🍋", "🍇", "🔔", "💎", "7️⃣"]
    
    # تطبيق الخوارزمية الذكية بناءً على نسبة RTP المحددة من الموقع
    is_win = random.randint(1, 100) <= win_rate
    if is_win:
        s = random.choice(symbols)
        r1 = r2 = r3 = s
        win = 5.0
        msg = f"🎉 فوز كبير! ربحت 5.00 NSP"
    else:
        r1, r2, r3 = random.choice(symbols), random.choice(symbols), random.choice(symbols)
        while r1 == r2 == r3: r3 = random.choice(symbols) # ضمان عدم الفوز المزدوج
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
# 3. إدارة قاعدة البيانات
# ==========================================================
def get_db():
    conn = sqlite3.connect("database.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0.0,
        site_username TEXT UNIQUE, site_password TEXT, security_passed INTEGER DEFAULT 0, referred_by INTEGER
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER, type TEXT, method TEXT, amount REAL, tx_number TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL, max_uses INTEGER, used_count INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_codes (telegram_id INTEGER, code TEXT, PRIMARY KEY (telegram_id, code))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS cashiers (bot_id TEXT PRIMARY KEY, balance REAL DEFAULT 0.0)''')
    
    # القيم الافتراضية
    cursor.execute("INSERT OR IGNORE INTO cashiers (bot_id, balance) VALUES (?, 0.0)", (BOT_ID,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('win_rate', '30')")
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

# ================= =========================================
# 4. فحص الاشتراك الإجباري والتحقق من الحسابات
# ==========================================================
def validate_username(username):
    return len(username) >= 6

def validate_password(password):
    has_letter = bool(re.search(r'[a-zA-Z]', password))
    has_digit = bool(re.search(r'\d', password))
    return len(password) >= 6 and has_letter and has_digit

async def check_forced_sub(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels_str = get_setting('forced_channels', '')
    if not channels_str: return True
    
    channels = [c.strip() for c in channels_str.split(',') if c.strip()]
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ['left', 'kicked']: return False
        except Exception:
            return False
    return True

# ================= =========================================
# 5. معالجة أوامر البوت والتفاعل
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # فحص الاشتراك الإجباري بصرامة
    if not await check_forced_sub(user.id, context):
        channels = get_setting('forced_channels', '')
        btns = [[InlineKeyboardButton(f"الاشتراك بالقناة: {ch}", url=f"https://t.me/{ch.replace('@','')}")] for ch in channels.split(',') if ch]
        btns.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub")])
        await update.message.reply_text("🚨 **يجب عليك الاشتراك بقنوات البوت أولاً لاستخدام الخدمات:**", reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
        return

    conn = get_db()
    cursor = conn.cursor()
    ref_by = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user.id else None
    
    db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
    if not db_user:
        cursor.execute("INSERT INTO users (telegram_id, username, referred_by) VALUES (?, ?, ?)", 
                       (user.id, user.username or user.first_name, ref_by))
        conn.commit()
        db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
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
        f"👑 **منصة AUREX الاحترافية** 👑\n"
        f"──────────────────\n"
        f"👤 العميل: **{update.effective_user.first_name}**\n"
        f"🆔 المعرف: `{user_id}`\n"
        f"🌐 حساب الموقع: {site_info}\n"
        f"💎 الرصيد: **{balance:.2f} NSP**\n"
        f"──────────────────"
    )

    keyboard = [
        [InlineKeyboardButton("🎰 فتح منصة الألعاب (الكازينو)", web_app=WebAppInfo(url=SERVER_URL))],
        [InlineKeyboardButton("🔑 إنشاء / تعديل حساب الموقع", callback_data="create_site_account")],
        [InlineKeyboardButton("💳 شحن رصيد", callback_data="deposit_menu"), InlineKeyboardButton("📤 سحب رصيد", callback_data="withdraw_menu")],
        [InlineKeyboardButton("🎁 كود هدية", callback_data="claim_gift"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref")],
        [InlineKeyboardButton("💬 الدعم الفني المباشر", callback_data="support")]
    ]

    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم الإدارية (الآدمن)", callback_data="admin_panel")])

    await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "check_sub":
        if await check_forced_sub(user_id, context):
            await query.message.delete()
            await show_main_menu(update, context)
        else:
            await update.effective_chat.send_message("❌ لم تشترك في جميع القنوات بعد!")

    elif data == "create_site_account":
        context.user_data['state'] = 'WAIT_SITE_USER'
        await update.effective_chat.send_message("🔑 اكتب اسم المستخدم للموقع (يتطلب 6 أحرف على الأقل):")

    elif data == "deposit_menu":
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="dep_syriatel")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="dep_sham")],
            [InlineKeyboardButton("↩️ إلغاء", callback_data="main_menu")]
        ]
        await update.effective_chat.send_message("اختر طريقة الشحن:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["dep_syriatel", "dep_sham"]:
        context.user_data['dep_method'] = "سيريتل كاش" if data == "dep_syriatel" else "شام كاش"
        context.user_data['state'] = 'WAIT_DEP_AMT'
        await update.effective_chat.send_message(f"📥 الشحن عبر **{context.user_data['dep_method']}**.\nأرسل المبلغ المراد شحنه بعملة NSP:", parse_mode="Markdown")

    elif data == "withdraw_menu":
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="with_syriatel")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="with_sham")],
            [InlineKeyboardButton("↩️ إلغاء", callback_data="main_menu")]
        ]
        await update.effective_chat.send_message("اختر طريقة السحب:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["with_syriatel", "with_sham"]:
        context.user_data['with_method'] = "سيريتل كاش" if data == "with_syriatel" else "شام كاش"
        context.user_data['state'] = 'WAIT_WITH_AMT'
        await update.effective_chat.send_message(f"📤 السحب عبر **{context.user_data['with_method']}**.\nأرسل المبلغ المراد سحبه بعملة NSP:", parse_mode="Markdown")

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await update.effective_chat.send_message("🎁 أدخل كود الهدية:")

    elif data == "my_ref":
        me = await context.bot.get_me()
        await update.effective_chat.send_message(f"🔗 **رابط إحالتك الشخصي:**\n`https://t.me/{me.username}?start={user_id}`", parse_mode="Markdown")

    elif data == "support":
        context.user_data['state'] = 'WAIT_SUPPORT'
        await update.effective_chat.send_message("💬 اكتب رسالتك للدعم الفني مباشرة:")

    elif data.startswith("reply_user_"):
        target_id = int(data.split("_")[2])
        context.user_data['reply_target'] = target_id
        context.user_data['state'] = 'WAIT_ADMIN_REPLY'
        await update.effective_chat.send_message(f"💬 اكتب الرد للعميل `{target_id}`:", parse_mode="Markdown")

    elif data == "main_menu":
        await show_main_menu(update, context)

# ================= =========================================
# 6. لوحة الآدمن المتقدمة داخل البوت
# ==========================================================
async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID: return
    await query.answer()
    data = query.data

    if data == "admin_panel":
        conn = get_db()
        cashier = conn.execute("SELECT balance FROM cashiers WHERE bot_id = ?", (BOT_ID,)).fetchone()
        cashier_bal = cashier['balance'] if cashier else 0.0
        conn.close()

        keyboard = [
            [InlineKeyboardButton("➕ إضافة رصيد لعميل", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
            [InlineKeyboardButton("🎁 إنشاء كود هدية (خصم من الكاشيرة)", callback_data="adm_gen_code")],
            [InlineKeyboardButton("📢 إرسال جماعي (إذاعة)", callback_data="adm_broadcast")],
            [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
        ]
        text = (
            f"⚙️ **لوحة التحكم الإدارية (داخل البوت)**\n\n"
            f"🏦 **رصيد الكاشيرة الحالي:** `{cashier_bal:.2f} NSP`\n"
            f"*(ملاحظة: يمكنك زيادة رصيد الكاشيرة من لوحة التحكم في الموقع)*"
        )
        await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "adm_gen_code":
        context.user_data['state'] = 'ADM_GIFT_AMT'
        await update.effective_chat.send_message("أدخل قيمة الكود الفردي بعملة NSP:")

    elif data == "adm_broadcast":
        context.user_data['state'] = 'ADM_WAIT_BROADCAST'
        await update.effective_chat.send_message("📢 اكتب النص المُراد إرساله لجميع مستخدمي البوت:")

    elif data == "adm_add_bal":
        context.user_data['state'] = 'ADM_WAIT_ADD_USER'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد إضافة رصيد له:")

# ================= =========================================
# 7. معالج النصوص والعمليات المتقدمة
# ==========================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = context.user_data.get('state')

    if not state: return

    conn = get_db()
    cursor = conn.cursor()

    # إنشاء حساب الموقع بشرط 6 أحرف وكلمة مرور معقدة
    if state == 'WAIT_SITE_USER':
        if not validate_username(text):
            await update.message.reply_text("❌ يجب أن يكون اسم المستخدم 6 أحرف على الأقل. أعد المحاولة:")
            return
        context.user_data['temp_site_user'] = text
        context.user_data['state'] = 'WAIT_SITE_PASS'
        await update.message.reply_text("أدخل كلمة المرور (يجب أن تحتوي على أحرف وأرقام و6 عناصر على الأقل):")

    elif state == 'WAIT_SITE_PASS':
        if not validate_password(text):
            await update.message.reply_text("❌ كلمة المرور ضعيفة! يجب أن تتكون من أحرف وأرقام معاً وتكون 6 عناصر على الأقل:")
            return
        site_user = context.user_data.get('temp_site_user')
        try:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?", (site_user, text, user_id))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text(f"✅ **تم ربط حساب الموقع بنجاح!**\n👤 اسم المستخدم: `{site_user}`\n🔑 كلمة المرور: `{text}`", parse_mode="Markdown")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ اسم المستخدم مأخوذ بالفعل، أدخل اسم آخر:")
            context.user_data['state'] = 'WAIT_SITE_USER'

    # الدعم الفني الفوري ورد الآدمن
    elif state == 'WAIT_SUPPORT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم الفني، سيصلك الرد هنا مباشرة.")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد على العميل", callback_data=f"reply_user_{user_id}")]])
        await context.bot.send_message(ADMIN_ID, f"📩 **رسالة دعم جديدة!**\n• العميل: `{user_id}`\n• النص: {text}", reply_markup=btn, parse_mode="Markdown")

    elif state == 'WAIT_ADMIN_REPLY' and user_id == ADMIN_ID:
        target = context.user_data.get('reply_target')
        context.user_data.clear()
        try:
            await context.bot.send_message(target, f"💬 **رد من الدعم الفني:**\n\n{text}", parse_mode="Markdown")
            await update.message.reply_text("✅ تم إرسال الرد بنجاح.")
        except Exception as e:
            await update.message.reply_text(f"❌ تعذر الإرسال: {e}")

    # خصم إنشاء الكود من الكاشيرة
    elif state == 'ADM_GIFT_AMT' and user_id == ADMIN_ID:
        try:
            amt = float(text)
            context.user_data['gift_amt'] = amt
            context.user_data['state'] = 'ADM_GIFT_USES'
            await update.message.reply_text("أدخل عدد مرات الاستخدام المتاحة للكود:")
        except ValueError: await update.message.reply_text("أدخل رقم صحيح.")

    elif state == 'ADM_GIFT_USES' and user_id == ADMIN_ID:
        try:
            uses = int(text)
            amt = context.user_data.get('gift_amt')
            total_cost = amt * uses
            
            cashier = cursor.execute("SELECT balance FROM cashiers WHERE bot_id = ?", (BOT_ID,)).fetchone()
            if not cashier or cashier['balance'] < total_cost:
                await update.message.reply_text(f"❌ **رصيد الكاشيرة غير كافٍ!**\nالتكلفة المطلوبة: {total_cost} NSP\nرصيد الكاشيرة: {cashier['balance'] if cashier else 0} NSP")
                context.user_data.clear()
                return

            import string
            code = "AUREX-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            cursor.execute("UPDATE cashiers SET balance = balance - ? WHERE bot_id = ?", (total_cost, BOT_ID))
            cursor.execute("INSERT INTO gift_codes (code, amount, max_uses) VALUES (?, ?, ?)", (code, amt, uses))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text(f"🎁 **تم إنشاء كود الهدية وخصم قيمته من الكاشيرة:**\n\nالكود: `{code}`\nالقيمة: {amt} NSP\nالاستخدامات: {uses}", parse_mode="Markdown")
        except ValueError: await update.message.reply_text("أدخل عدد صحيح.")

    # الإرسال الجماعي (إذاعة)
    elif state == 'ADM_WAIT_BROADCAST' and user_id == ADMIN_ID:
        context.user_data.clear()
        users = cursor.execute("SELECT telegram_id FROM users").fetchall()
        count = 0
        for u in users:
            try:
                await context.bot.send_message(u['telegram_id'], f"📢 **تنبيه هام:**\n\n{text}", parse_mode="Markdown")
                count += 1
            except Exception: pass
        await update.message.reply_text(f"✅ تم الإرسال بنجاح لـ {count} مستخدم.")

    # شحن وسحب وإدخال كود
    elif state == 'WAIT_DEP_AMT':
        try:
            amt = float(text)
            context.user_data['dep_amt'] = amt
            context.user_data['state'] = 'WAIT_DEP_TX'
            await update.message.reply_text("أرسل الآن رقم العملية/الإشعار:")
        except ValueError: await update.message.reply_text("أدخل رقم صحيح.")

    elif state == 'WAIT_DEP_TX':
        amt, method = context.user_data.get('dep_amt'), context.user_data.get('dep_method')
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'deposit', ?, ?, ?)", (user_id, method, amt, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال طلب الشحن للإدارة بنجاح.")
        await context.bot.send_message(ADMIN_ID, f"📥 **طلب إيداع جديد!**\n• العميل: `{user_id}`\n• المبلغ: {amt} NSP\n• الوسيلة: {method}\n• العملية: `{text}`", parse_mode="Markdown")

    elif state == 'WAIT_GIFT_CODE':
        code_row = cursor.execute("SELECT * FROM gift_codes WHERE code = ?", (text,)).fetchone()
        if code_row and code_row['used_count'] < code_row['max_uses']:
            used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND code = ?", (user_id, text)).fetchone()
            if not used:
                cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (text,))
                cursor.execute("INSERT INTO used_codes VALUES (?, ?)", (user_id, text))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (code_row['amount'], user_id))
                conn.commit()
                await update.message.reply_text(f"🎉 **مبروك!** تم شحن رصيدك بـ **{code_row['amount']} NSP**")
            else: await update.message.reply_text("❌ استخدمت هذا الكود سابقاً.")
        else: await update.message.reply_text("❌ الكود غير صالح أو انتهى.")
        context.user_data.clear()

    conn.close()

# ================= =========================================
# 8. التشغيل
# ==========================================================
def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_panel_handler, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(user_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("🚀 تم تشغيل المنصة بنجاح...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
