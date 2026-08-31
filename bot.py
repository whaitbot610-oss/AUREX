import os
import re
import sqlite3
import logging
import random
import string
import html
import threading
import json
import urllib.request
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes
)

# ==========================================================
# 0. خادم صحة الخدمة وواجهة API التزامنية لـ Web App
# ==========================================================
WHEEL_VALUES = [0, 5, 10, 15, 25, 50, 100, 500, 10000]
MAIN_LOOP = None

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/wheel":
            # توجيه الطلب إلى السيرفر الرئيسي إن وجد أو تقديم استجابة صحة
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"<h1>AUREX Wheel Service is Active</h1>")
        elif parsed_path.path in ["/api/user_info", "/api/get-spins"]:
            qs = parse_qs(parsed_path.query)
            user_id_raw = qs.get('telegram_id', [None])[0] or qs.get('user_id', [None])[0]
            
            if user_id_raw and str(user_id_raw).isdigit():
                user_id = int(user_id_raw)
                conn = get_db()
                u = conn.execute("SELECT free_spins, spins_count, bot_balance, balance, site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
                conn.close()
                if u:
                    spins = u['free_spins'] if u['free_spins'] is not None else (u['spins_count'] or 0)
                    bal = u['bot_balance'] if u['bot_balance'] is not None else (u['balance'] or 0.0)
                    res = {
                        "status": "success",
                        "free_spins": spins,
                        "spins": spins,
                        "bot_balance": bal,
                        "site_balance": u['site_balance'] or 0.0
                    }
                    self._send_json(res)
                    return
                else:
                    self._send_json({"status": "error", "message": "المستخدم غير موجود بالنظام"})
                    return
            self._send_json({"status": "error", "message": "معرف المستخدم غير صالح"})
            
        elif parsed_path.path == "/api/users":
            auth_header = self.headers.get('X-Admin-Token')
            admin_token = os.environ.get("ADMIN_API_TOKEN", "INTERNAL_SECURE_TOKEN")
            if auth_header != admin_token:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            conn = get_db()
            users = conn.execute("SELECT telegram_id, site_username, bot_balance, site_balance FROM users WHERE site_username IS NOT NULL").fetchall()
            conn.close()
            data = [dict(u) for u in users]
            self._send_json(data)
        else:
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"OK - AUREX BOT IS RUNNING")

    def do_POST(self):
        if self.path in ["/api/spin", "/api/wheel/spin"]:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                user_id_raw = data.get('telegram_id') or data.get('user_id')
                
                if not user_id_raw or not str(user_id_raw).isdigit():
                    self._send_json({"status": "error", "message": "معرف المستخدم غير صحيح!"})
                    return

                user_id = int(user_id_raw)
                
                conn = get_db()
                cursor = conn.cursor()
                u = cursor.execute("SELECT free_spins, spins_count, bot_balance, balance, site_balance, bot_id FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
                
                if not u:
                    conn.close()
                    self._send_json({"status": "error", "message": "حساب المستخدم غير موجود"})
                    return

                spins_left = u['free_spins'] if u['free_spins'] is not None else (u['spins_count'] or 0)
                bot_bal = u['bot_balance'] if u['bot_balance'] is not None else (u['balance'] or 0.0)
                is_free_spin = False

                if spins_left > 0:
                    is_free_spin = True
                    spins_left -= 1
                    cursor.execute("UPDATE users SET free_spins = ?, spins_count = ? WHERE telegram_id = ?", (spins_left, spins_left, user_id))
                elif bot_bal >= 10.0:
                    bot_bal -= 10.0
                    cursor.execute("UPDATE users SET bot_balance = ?, balance = ? WHERE telegram_id = ?", (bot_bal, bot_bal, user_id))
                    update_cashier(10.0, conn=conn)
                else:
                    conn.close()
                    self._send_json({"status": "error", "message": "ليس لديك لفات مجانية أو رصيد كافٍ لتدوير العجلة (10 NSP)"})
                    return

                win_rate = float(get_setting('game_win_rate', '30', conn=conn))
                cashier_bal = get_cashier_balance(conn=conn)
                
                probs_raw = get_setting('wheel_probabilities', '', conn=conn)
                try:
                    w_dict = json.loads(probs_raw) if probs_raw else {}
                except Exception:
                    w_dict = {}

                roll = random.uniform(0, 100)
                prize = 0
                possible_prizes = [v for v in WHEEL_VALUES if v > 0 and v <= cashier_bal]
                
                if roll <= win_rate and possible_prizes:
                    prize_weights = [float(w_dict.get(str(v), 10)) for v in possible_prizes]
                    prize = random.choices(possible_prizes, weights=prize_weights, k=1)[0]

                prize_index = WHEEL_VALUES.index(prize)
                
                if prize > 0:
                    before_cashier, after_cashier = update_cashier(-prize, conn=conn)
                    bot_bal += prize
                    cursor.execute("UPDATE users SET bot_balance = ?, balance = ? WHERE telegram_id = ?", (bot_bal, bot_bal, user_id))
                else:
                    before_cashier, after_cashier = get_cashier_balance(conn=conn), get_cashier_balance(conn=conn)

                conn.commit()
                conn.close()

                if prize > 0 and MAIN_LOOP and MAIN_LOOP.is_running():
                    asyncio.run_coroutine_threadsafe(
                        send_spin_notifications(user_id, prize, before_cashier, after_cashier),
                        MAIN_LOOP
                    )

                self._send_json({
                    "status": "success",
                    "prize_index": prize_index,
                    "reward": prize,
                    "is_free_spin": is_free_spin,
                    "free_spins_left": spins_left,
                    "new_bot_balance": bot_bal,
                    "new_site_balance": u['site_balance'] or 0.0
                })
                return
            except Exception as e:
                logging.error(f"Spin API error: {e}")
                self._send_json({"status": "error", "message": "حدث خطأ أثناء التدوير."})
                return
        
        self.send_response(400)
        self.end_headers()

    def _send_json(self, data_dict):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data_dict).encode('utf-8'))

    def log_message(self, format, *args):
        return

def start_health_check_server():
    port = int(os.environ.get("BOT_HTTP_PORT", 8081))
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logging.warning(f"Health check server port warning: {e}")

# ==========================================================
# 1. الإعدادات الأساسية
# ==========================================================
MAIN_ADMIN_ID = int(os.environ.get("MAIN_ADMIN_ID", "7255100997"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8948439052:AAHv-UWeTMQmHybxspFRVRpnjIqetmW8LbI").strip() 
SERVER_URL = os.environ.get("SERVER_URL", "https://aurex-my-bot.onrender.com").strip()

if not SERVER_URL.startswith("https://"):
    SERVER_URL = "https://" + SERVER_URL.replace("http://", "")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

bot_app = None

async def send_all_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    conn = get_db()
    admins = conn.execute("SELECT telegram_id FROM users WHERE is_admin = 1").fetchall()
    conn.close()
    
    admin_ids = set([a['telegram_id'] for a in admins] + [MAIN_ADMIN_ID])
    for aid in admin_ids:
        try:
            await context.bot.send_message(chat_id=aid, text=text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as e:
            logging.error(f"Failed to send admin notification to {aid}: {e}")

async def send_spin_notifications(user_id, prize, before_cashier, after_cashier):
    if bot_app:
        try:
            await bot_app.bot.send_message(
                user_id, 
                f"🎡 <b>إشعار عجلة الحظ!</b>\n\n🎉 مبروك! فزت بـ <b>{prize:.2f} NSP</b> وتم إضافتها لرصيد بوتك مباشرة.", 
                parse_mode="HTML"
            )
            
            conn = get_db()
            admins = conn.execute("SELECT telegram_id FROM users WHERE is_admin = 1").fetchall()
            conn.close()
            admin_ids = set([a['telegram_id'] for a in admins] + [MAIN_ADMIN_ID])
            
            msg_adm = (
                f"🎰 <b>خصم كاشيرة (فوز بعجلة الحظ):</b>\n"
                f"• العميل: <code>{user_id}</code>\n"
                f"• الجائزة: <b>{prize:.2f} NSP</b>\n"
                f"🏦 الكاشيرة قبل: <code>{before_cashier:.2f} NSP</code>\n"
                f"🏦 الكاشيرة بعد: <code>{after_cashier:.2f} NSP</code>"
            )
            for aid in admin_ids:
                try:
                    await bot_app.bot.send_message(aid, msg_adm, parse_mode="HTML")
                except Exception: pass
        except Exception as e:
            logging.error(f"Notification error: {e}")

async def register_account_to_site_api_async(username, password, telegram_id, referred_by=None):
    def _send():
        try:
            url = f"{SERVER_URL}/api/register_site"
            payload = json.dumps({
                "site_user": username,
                "site_pass": password,
                "telegram_id": telegram_id,
                "referred_by": referred_by
            }).encode('utf-8')
            
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            logging.warning(f"Note: Site API sync ({e}), local database managed.")
            return False
            
    return await asyncio.to_thread(_send)

# ==========================================================
# 2. إدارة قاعدة البيانات والتوافق الموحد
# ==========================================================
def get_db():
    conn = sqlite3.connect("database.db", check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            bot_token TEXT UNIQUE,
            cashier_balance REAL DEFAULT 10000.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY, 
        bot_id INTEGER DEFAULT 1,
        username TEXT, 
        site_username TEXT UNIQUE, 
        site_password TEXT, 
        bot_balance REAL DEFAULT 0.0,
        balance REAL DEFAULT 0.0,
        site_balance REAL DEFAULT 0.0,
        total_spent REAL DEFAULT 0.0,
        deposit_count INTEGER DEFAULT 0,
        withdraw_count INTEGER DEFAULT 0,
        referrals_count INTEGER DEFAULT 0,
        free_spins INTEGER DEFAULT 0,
        spins_count INTEGER DEFAULT 0,
        referred_by INTEGER,
        got_welcome_bonus INTEGER DEFAULT 0,
        security_passed INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        code_restricted_until TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    existing_cols = [col[1] for col in cursor.execute("PRAGMA table_info(users)").fetchall()]
    required_cols = {
        'bot_id': 'INTEGER DEFAULT 1',
        'username': 'TEXT',
        'site_username': 'TEXT',
        'site_password': 'TEXT',
        'bot_balance': 'REAL DEFAULT 0.0',
        'balance': 'REAL DEFAULT 0.0',
        'site_balance': 'REAL DEFAULT 0.0',
        'total_spent': 'REAL DEFAULT 0.0',
        'deposit_count': 'INTEGER DEFAULT 0',
        'withdraw_count': 'INTEGER DEFAULT 0',
        'referrals_count': 'INTEGER DEFAULT 0',
        'free_spins': 'INTEGER DEFAULT 0',
        'spins_count': 'INTEGER DEFAULT 0',
        'referred_by': 'INTEGER',
        'got_welcome_bonus': 'INTEGER DEFAULT 0',
        'security_passed': 'INTEGER DEFAULT 0',
        'is_admin': 'INTEGER DEFAULT 0',
        'is_banned': 'INTEGER DEFAULT 0',
        'code_restricted_until': 'TIMESTAMP',
        'created_at': 'TIMESTAMP',
        'last_active': 'TIMESTAMP'
    }
    for col_name, col_type in required_cols.items():
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                logging.error(f"Error adding column {col_name}: {e}")

    # التزامن الداخلي بين أسماء الحقول لضمان التوافق التام
    cursor.execute("UPDATE users SET bot_balance = balance WHERE bot_balance = 0.0 AND balance > 0.0")
    cursor.execute("UPDATE users SET balance = bot_balance WHERE balance = 0.0 AND bot_balance > 0.0")
    cursor.execute("UPDATE users SET free_spins = spins_count WHERE free_spins = 0 AND spins_count > 0")
    cursor.execute("UPDATE users SET spins_count = free_spins WHERE spins_count = 0 AND free_spins > 0")

    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        telegram_id INTEGER, 
        bot_id INTEGER DEFAULT 1,
        type TEXT, 
        method TEXT, 
        amount REAL, 
        tx_number TEXT, 
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (
        code TEXT PRIMARY KEY, 
        amount REAL, 
        max_uses INTEGER, 
        used_count INTEGER DEFAULT 0, 
        active INTEGER DEFAULT 1,
        bot_id INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_codes (
        telegram_id INTEGER, 
        code TEXT, 
        used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (telegram_id, code)
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        name TEXT UNIQUE, 
        number TEXT, 
        active INTEGER DEFAULT 1
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, 
        value TEXT
    )''')

    default_wheel_probs = {
        "0": 50.0, "5": 20.0, "10": 12.0, "15": 8.0, 
        "25": 5.0, "50": 3.0, "100": 1.5, "500": 0.4, "10000": 0.1
    }

    defaults = [
        ('maintenance', 'off'),
        ('welcome_bonus', '10.0'),
        ('welcome_bonus_enabled', '1'),
        ('min_deposit', '10'),
        ('min_withdraw', '10'),
        ('cashier_balance', '10000.0'),
        ('forced_channels', ''),
        ('game_win_rate', '30'),
        ('wheel_probabilities', json.dumps(default_wheel_probs))
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(val)))
        
    cursor.execute("SELECT * FROM bots WHERE id = 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO bots (id, bot_name, cashier_balance) VALUES (1, 'AUREX Main Bot', 10000.0)")

    cursor.execute("INSERT OR IGNORE INTO payment_methods (name, number) VALUES ('سيريتل كاش', '0987654321')")
    cursor.execute("INSERT OR IGNORE INTO payment_methods (name, number) VALUES ('شام كاش', '0912345678')")
    
    conn.commit()
    conn.close()

def get_setting(key, default="0", conn=None):
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else default
    finally:
        if close_conn:
            conn.close()

def set_setting(key, value, conn=None):
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    finally:
        if close_conn:
            conn.close()

def is_admin(user_id):
    if user_id == MAIN_ADMIN_ID:
        return True
    conn = get_db()
    row = conn.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row and row['is_admin'])

def update_cashier(amount_change, bot_id=1, conn=None):
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    try:
        cursor = conn.cursor()
        row_before = cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'").fetchone()
        before_balance = float(row_before['value']) if row_before else 0.0
        
        after_balance = max(0.0, before_balance + amount_change)
        cursor.execute("UPDATE settings SET value = ? WHERE key = 'cashier_balance'", (str(after_balance),))
        cursor.execute("UPDATE bots SET cashier_balance = ? WHERE id = ?", (after_balance, bot_id))
        
        if close_conn:
            conn.commit()
        return before_balance, after_balance
    finally:
        if close_conn:
            conn.close()

def get_cashier_balance(bot_id=1, conn=None):
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    try:
        row = conn.execute("SELECT cashier_balance FROM bots WHERE id = ?", (bot_id,)).fetchone()
        if row and row['cashier_balance'] is not None:
            return float(row['cashier_balance'])
        return float(get_setting('cashier_balance', '0.0', conn=conn))
    finally:
        if close_conn:
            conn.close()

def get_payment_number(method_name):
    conn = get_db()
    row = conn.execute("SELECT number FROM payment_methods WHERE name = ?", (method_name,)).fetchone()
    conn.close()
    return row['number'] if row else "غير متوفر"

def validate_username(username): 
    return len(username) >= 3 and bool(re.match(r'^[a-zA-Z0-9_]+$', username))

def validate_password(password): 
    return len(password) >= 3

async def check_forced_sub(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels_str = get_setting('forced_channels', '')
    if not channels_str: 
        return True
    channels = [c.strip() for c in channels_str.split(',') if c.strip()]
    for ch in channels:
        ch_target = ch if (ch.startswith('@') or ch.startswith('-')) else f"@{ch}"
        try:
            member = await context.bot.get_chat_member(chat_id=ch_target, user_id=user_id)
            if member.status in ['left', 'kicked']: 
                return False
        except Exception: 
            return False
    return True

# ==========================================================
# 3. الأوامر والقوائم الرئيسية
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

    maint = get_setting('maintenance', 'off')
    if (maint == 'on' or maint == '1') and not is_admin(user.id):
        conn.close()
        await update.message.reply_text("🛠 البوت والموقع حالياً في حالة صيانة وتحديث، يرجى المحاولة لاحقاً.")
        return

    if not await check_forced_sub(user.id, context):
        channels = get_setting('forced_channels', '')
        btns = [[InlineKeyboardButton(f"اشترك هنا: {ch}", url=f"https://t.me/{ch.replace('@','')}") ] for ch in channels.split(',') if ch]
        btns.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub")])
        conn.close()
        await update.message.reply_text("🚨 <b>يجب عليك الاشتراك بجميع القنوات التالية أولاً لاستخدام البوت:</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")
        return

    ref_by = None
    if context.args and len(context.args) > 0 and context.args[0].isdigit():
        parsed_id = int(context.args[0])
        if parsed_id != user.id:
            ref_by = parsed_id

    if not db_user:
        is_main_admin = 1 if user.id == MAIN_ADMIN_ID else 0
        cursor.execute(
            "INSERT INTO users (telegram_id, username, referred_by, is_admin) VALUES (?, ?, ?, ?)", 
            (user.id, user.username or user.first_name, ref_by, is_main_admin)
        )
        conn.commit()
        
        if user.id != MAIN_ADMIN_ID:
            ref_txt = f"<code>{ref_by}</code>" if ref_by else "بدون إحالة"
            await send_all_admins(
                context, 
                f"👤 <b>عضو جديد انضم للبوت!</b>\n\n• الاسم: {html.escape(user.full_name or '')}\n• المعرف: @{user.username or 'لا يوجد'}\n• الآيدي: <code>{user.id}</code>\n• الإحالة بواسطة: {ref_txt}"
            )

        if ref_by:
            cursor.execute("""
                UPDATE users 
                SET referrals_count = COALESCE(referrals_count, 0) + 1,
                    free_spins = COALESCE(free_spins, 0) + 1,
                    spins_count = COALESCE(spins_count, 0) + 1
                WHERE telegram_id = ?
            """, (ref_by,))
            conn.commit()
            try:
                await context.bot.send_message(ref_by, f"🎉 <b>انضم عميل جديد عبر رابط إحالتك!</b>\n🆔 العميل: <code>{html.escape(user.first_name or '')}</code>\n📌 تم منحك لفة مجانية جديدة بعجلة الحظ!", parse_mode="HTML")
            except Exception: pass
            
        db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()

    cursor.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE telegram_id = ?", (user.id,))
    conn.commit()
    conn.close()

    if not db_user['security_passed']:
        await send_security_question(update)
        return

    await show_main_menu(update, context)

async def send_security_question(update: Update):
    keyboard = [
        [InlineKeyboardButton("حمصية 🌺", callback_data="sec_wrong")],
        [InlineKeyboardButton("حموية 🍯", callback_data="sec_correct")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    text = "🔒 <b>سؤال حماية البوت:</b>\n\nحلاوة الجبن حمصية ولا حموية؟"
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = html.escape(update.effective_user.first_name or "مستخدم")
    
    conn = get_db()
    db_user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    site_info = f"<code>{html.escape(db_user['site_username'])}</code>" if db_user and db_user['site_username'] else "❌ غير مربوط"
    bot_bal = db_user['bot_balance'] if (db_user and db_user['bot_balance'] is not None) else (db_user['balance'] if db_user else 0.0)
    site_bal = db_user['site_balance'] if db_user else 0.0
    spins = db_user['free_spins'] if (db_user and db_user['free_spins'] is not None) else (db_user['spins_count'] if db_user else 0)

    text = (
        f"👑 <b>منصة AUREX المتطورة</b> 👑\n"
        f"──────────────────\n"
        f"👤 العميل: <b>{first_name}</b>\n"
        f"🆔 المعرف: <code>{user_id}</code>\n"
        f"🌐 حساب الموقع: {site_info}\n"
        f"💰 رصيد البوت: <b>{bot_bal:.2f} NSP</b>\n"
        f"💎 رصيد الموقع: <b>{site_bal:.2f} NSP</b>\n"
        f"🎡 فرص اللعب المتاحة: <b>{spins} محاولة</b>\n"
        f"──────────────────"
    )

    wheel_url = f"{SERVER_URL}/wheel?telegram_id={user_id}&user_id={user_id}"
    
    try:
        aurex_btn = InlineKeyboardButton("🌐 AUREX", web_app=WebAppInfo(url=SERVER_URL))
    except Exception:
        aurex_btn = InlineKeyboardButton("🌐 AUREX", url=SERVER_URL)

    try:
        wheel_btn = InlineKeyboardButton(f"🎡 عجلة الحظ والإحالات ({spins} فرص)", web_app=WebAppInfo(url=wheel_url))
    except Exception:
        wheel_btn = InlineKeyboardButton(f"🎡 عجلة الحظ والإحالات ({spins} فرص)", url=wheel_url)

    keyboard = [
        [aurex_btn],
        [wheel_btn],
        [InlineKeyboardButton("💳 شحن البوت", callback_data="dep_menu"), InlineKeyboardButton("💰 سحب ارباحك", callback_data="with_menu")],
        [InlineKeyboardButton("🔄 شحن رصيد للموقع", callback_data="transfer_to_site"), InlineKeyboardButton("↩️ سحب رصيد من الموقع", callback_data="transfer_from_site")],
        [InlineKeyboardButton("🔑 إنشاء حساب", callback_data="create_site_account"), InlineKeyboardButton("🔐 بيانات حسابي", callback_data="my_account")],
        [InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="claim_gift")],
        [InlineKeyboardButton("📸 إرسال صورة إصابة", callback_data="send_win_shot"), InlineKeyboardButton("💬 مراسلة الدعم", callback_data="contact_support")],
        [InlineKeyboardButton("📜 سجلاتي المالية", callback_data="my_logs")]
    ]

    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم الإدارية (الآدمن)", callback_data="admin_panel")])

    chat = update.effective_chat
    await chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ==========================================================
# 4. معالج التفاعلات والأزرار (Callback Router)
# ==========================================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "sec_wrong":
        keyboard = [
            [InlineKeyboardButton("مابدي البونص", callback_data="sec_no_bonus")],
            [InlineKeyboardButton("لا بدي ارجع لحط حموية", callback_data="sec_back")]
        ]
        await query.message.edit_text(
            "غلط ياحبيب راجع معلوماتك ولا مابدك البونص الترحيبي؟", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data == "sec_back":
        await send_security_question(update)
        return

    elif data == "sec_no_bonus":
        conn = get_db()
        conn.execute("UPDATE users SET security_passed = 1, got_welcome_bonus = -1 WHERE telegram_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("تم توثيق حسابك بنجاح دون الحصول على البونص الترحيبي.")
        await show_main_menu(update, context)
        return

    elif data == "sec_correct":
        conn = get_db()
        cursor = conn.cursor()
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        bonus_enabled = get_setting('welcome_bonus_enabled', '1', conn=conn) == '1'
        bonus_amt = float(get_setting('welcome_bonus', '10.0', conn=conn))
        
        if bonus_enabled and bonus_amt > 0 and user and user['got_welcome_bonus'] == 0:
            before_cashier, after_cashier = update_cashier(-bonus_amt, conn=conn)
            cursor.execute("""
                UPDATE users 
                SET security_passed = 1, got_welcome_bonus = 1, 
                    bot_balance = COALESCE(bot_balance, 0.0) + ?,
                    balance = COALESCE(balance, 0.0) + ? 
                WHERE telegram_id = ?
            """, (bonus_amt, bonus_amt, user_id))
            conn.commit()
            conn.close()
            
            await query.message.edit_text(f"قلتلك حموية ماصدقتني! 🍯\n\n🎉 <b>لقد حصلت على بونص ترحيبي بقيمة {bonus_amt:.2f} NSP!</b>", parse_mode="HTML")
            
            await send_all_admins(
                context,
                f"⚠️ <b>إشعار خصم من الكاشيرة (بونص ترحيبي):</b>\n\n"
                f"تم خصم مبلغ <b>{bonus_amt:.2f} NSP</b> من الكاشيرة لدخول شخص جديد (🆔 <code>{user_id}</code>) وحصوله على البونص.\n\n"
                f"🏦 المبلغ القديم في الكاشيرة: <code>{before_cashier:.2f} NSP</code>\n"
                f"🏦 المبلغ الجديد في الكاشيرة: <code>{after_cashier:.2f} NSP</code>"
            )
        else:
            cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("قلتلك حموية ماصدقتني! 🍯 تم توثيق حسابك بنجاح.")

        await show_main_menu(update, context)
        return

    elif data == "cancel_action":
        context.user_data.clear()
        try:
            await query.message.delete()
        except Exception:
            pass
        await show_main_menu(update, context)
        return

    elif data == "check_sub":
        if await check_forced_sub(user_id, context):
            try:
                await query.message.delete()
            except Exception:
                pass
            await show_main_menu(update, context)
        else:
            await update.effective_chat.send_message("❌ لم تشترك في كامل القنوات المطلوبة بعد.")
        return

    elif data == "my_account":
        conn = get_db()
        u = conn.execute("SELECT site_username, site_password FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if u and u['site_username']:
            await update.effective_chat.send_message(f"🔐 <b>بيانات حسابك المربوط في الموقع:</b>\n\n👤 اسم المستخدم: <code>{html.escape(u['site_username'])}</code>\n🔑 كلمة المرور: <code>{html.escape(u['site_password'])}</code>", parse_mode="HTML")
        else:
            await update.effective_chat.send_message("❌ ليس لديك حساب مربوط بعد! استخدم زر (إنشاء حساب).")
        return

    elif data == "create_site_account":
        conn = get_db()
        u = conn.execute("SELECT site_username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if u and u['site_username']:
            await update.effective_chat.send_message(f"⚠️ <b>تنبيه:</b> لديك حساب شخصي سابق بالفعل باسم: <code>{html.escape(u['site_username'])}</code>\nلا يُسمح للعميل بإنشاء أكثر من حساب واحد.", parse_mode="HTML")
            return

        context.user_data['state'] = 'WAIT_SITE_USER'
        keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]
        await update.effective_chat.send_message(
            "🔑 <b>إنشاء حساب جديد للموقع:</b>\n\n"
            "✍️ أدخل اسم المستخدم الجديد (يتكون من 3 أحرف/أرقام إنجليزية على الأقل وبدون رموز):",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    elif data == "transfer_to_site":
        conn = get_db()
        u = conn.execute("SELECT site_username, bot_balance, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ يجب إنشاء حساب على الموقع أولاً!", parse_mode="HTML")
            return
        
        bot_bal = u['bot_balance'] if u['bot_balance'] is not None else (u['balance'] or 0.0)
        context.user_data['state'] = 'WAIT_TRANSFER_TO_SITE'
        await update.effective_chat.send_message(
            f"🔄 <b>شحن رصيد للموقع:</b>\n"
            f"💰 رصيد البوت المتوفر: <b>{bot_bal:.2f} NSP</b>\n\n"
            f"✍️ أدخل المبلغ المراد تحويله إلى حساب الموقع:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]),
            parse_mode="HTML"
        )
        return

    elif data == "transfer_from_site":
        conn = get_db()
        u = conn.execute("SELECT site_username, site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ يجب إنشاء حساب على الموقع أولاً!", parse_mode="HTML")
            return

        context.user_data['state'] = 'WAIT_TRANSFER_FROM_SITE'
        await update.effective_chat.send_message(
            f"↩️ <b>سحب رصيد من الموقع:</b>\n"
            f"💎 رصيد الموقع المتوفر: <b>{u['site_balance']:.2f} NSP</b>\n\n"
            f"✍️ أدخل المبلغ المراد سحبه من الموقع إلى محفظة البوت:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]),
            parse_mode="HTML"
        )
        return

    elif data == "dep_menu":
        conn = get_db()
        methods = conn.execute("SELECT * FROM payment_methods WHERE active = 1").fetchall()
        conn.close()
        
        btns = []
        for m in methods:
            btns.append([InlineKeyboardButton(f"💳 {m['name']}", callback_data=f"dep_method_{m['id']}")])
        btns.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")])
        
        await update.effective_chat.send_message("💳 <b>اختر طريقة الشحن المناسبة:</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")
        return

    elif data.startswith("dep_method_"):
        method_id = int(data.split("_")[2])
        conn = get_db()
        m = conn.execute("SELECT * FROM payment_methods WHERE id = ?", (method_id,)).fetchone()
        conn.close()
        
        if not m:
            await update.effective_chat.send_message("❌ طريقة الدفع غير متاحة حالياً.")
            return

        context.user_data['dep_method'] = m['name']
        context.user_data['state'] = 'WAIT_DEP_AMOUNT'
        
        min_dep = float(get_setting('min_deposit', '10'))
        await update.effective_chat.send_message(
            f"💳 <b>شحن عن طريق {m['name']}:</b>\n\n"
            f"📌 الحساب/الرقم المحول إليه: <code>{m['number']}</code>\n"
            f"🔻 الحد الأدنى للشحن: <b>{min_dep:.2f} NSP</b>\n\n"
            f"✍️ يرجى كتابة مبلغ الشحن الآن:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]),
            parse_mode="HTML"
        )
        return

    elif data == "with_menu":
        conn = get_db()
        u = conn.execute("SELECT bot_balance, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        methods = conn.execute("SELECT * FROM payment_methods WHERE active = 1").fetchall()
        conn.close()

        bot_bal = u['bot_balance'] if (u and u['bot_balance'] is not None) else (u['balance'] if u else 0.0)
        min_with = float(get_setting('min_withdraw', '10'))

        if bot_bal < min_with:
            await update.effective_chat.send_message(f"❌ رصيدك الحالي ({bot_bal:.2f} NSP) أصل من الحد الأدنى للسحب ({min_with:.2f} NSP).")
            return

        btns = []
        for m in methods:
            btns.append([InlineKeyboardButton(f"💰 {m['name']}", callback_data=f"with_method_{m['id']}")])
        btns.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")])

        await update.effective_chat.send_message("💰 <b>اختر طريقة سحب الأرباح:</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")
        return

    elif data.startswith("with_method_"):
        method_id = int(data.split("_")[2])
        conn = get_db()
        m = conn.execute("SELECT * FROM payment_methods WHERE id = ?", (method_id,)).fetchone()
        conn.close()

        if not m:
            await update.effective_chat.send_message("❌ طريقة السحب غير متاحة.")
            return

        context.user_data['with_method'] = m['name']
        context.user_data['state'] = 'WAIT_WITH_AMOUNT'
        await update.effective_chat.send_message(
            f"💰 <b>سحب الأرباح عبر {m['name']}:</b>\n\n"
            f"✍️ أدخل المبلغ المراد سحبه:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]),
            parse_mode="HTML"
        )
        return

    elif data == "my_ref":
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
        conn = get_db()
        u = conn.execute("SELECT referrals_count, free_spins, spins_count FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        
        count = u['referrals_count'] if u else 0
        spins = u['free_spins'] if (u and u['free_spins'] is not None) else (u['spins_count'] if u else 0)

        msg = (
            f"🔗 <b>رابط الإحالة الخاص بك:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"📊 <b>إحصائيات الإحالة:</b>\n"
            f"• عدد الأشخاص المنضمين عبرك: <b>{count}</b>\n"
            f"• اللفات المجانية المكتسبة: <b>{spins}</b>\n\n"
            f"🎁 <b>المكافأة:</b> تكسب لفة مجانية بعجلة الحظ لكل شخص يقوم بالتسجيل عن طريق رابطك!"
        )
        await update.effective_chat.send_message(msg, parse_mode="HTML")
        return

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await update.effective_chat.send_message(
            "🎁 <b>تفعيل كود هدية:</b>\n\n"
            "✍️ أدخل رمز الكود الخاص بك هنا:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]),
            parse_mode="HTML"
        )
        return

    elif data == "send_win_shot":
        context.user_data['state'] = 'WAIT_WIN_SHOT'
        await update.effective_chat.send_message(
            "📸 <b>إرسال صورة إثبات الفوز/الإصابة:</b>\n\n"
            "قم بإرسال صورة الشاشة الآن مع وصف بسيط لمشاركتها مع الإدارة.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]),
            parse_mode="HTML"
        )
        return

    elif data == "contact_support":
        context.user_data['state'] = 'WAIT_SUPPORT_MSG'
        await update.effective_chat.send_message(
            "💬 <b>مراسلة الدعم الفني:</b>\n\n"
            "اكتب رسالتك أو استفسارك هنا وسيتم إرسالها لمشرفي المنصة مباشرة:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]),
            parse_mode="HTML"
        )
        return

    elif data == "my_logs":
        conn = get_db()
        txs = conn.execute("SELECT * FROM transactions WHERE telegram_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,)).fetchall()
        conn.close()

        if not txs:
            await update.effective_chat.send_message("📜 ليس لديك سجل معاملات سابقة.")
            return

        text = "📜 <b>آخر 10 معاملات مالية خاصة بك:</b>\n\n"
        for t in txs:
            st = "⏳ قيد الانتظار" if t['status'] == 'pending' else ("✅ مقبول" if t['status'] in ['approved', 'approve'] else "❌ مرفوض")
            tp = "شحن" if t['type'] == 'deposit' else "سحب"
            text += f"• [{t['created_at']}] {tp} - <b>{t['amount']:.2f} NSP</b> ({t['method']}) -> {st}\n"

        await update.effective_chat.send_message(text, parse_mode="HTML")
        return

    elif data == "admin_panel":
        if not is_admin(user_id):
            return
        
        conn = get_db()
        c_bal = get_cashier_balance(conn=conn)
        pending_count = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE status = 'pending'").fetchone()['c']
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()['c']
        conn.close()

        msg = (
            f"⚙️ <b>لوحة التحكم الإدارية (ADMIN):</b>\n\n"
            f"🏦 رصيد كاشيرة البوت: <b>{c_bal:.2f} NSP</b>\n"
            f"👥 إجمالي المستخدمين: <b>{total_users}</b>\n"
            f"⏳ المعاملات المعلقة: <b>{pending_count}</b>"
        )

        keyboard = [
            [InlineKeyboardButton("📥 طلبات الشحن والسحب المعلقة", callback_data="adm_pending_txs")],
            [InlineKeyboardButton("💰 إضافة رصيد للكاشيرة", callback_data="adm_add_cashier"), InlineKeyboardButton("🎁 إنشاء كود هدية", callback_data="adm_gen_code")],
            [InlineKeyboardButton("🎡 منح لفات للمستخدمين", callback_data="adm_grant_spins"), InlineKeyboardButton("📢 إذاعة عامة", callback_data="adm_broadcast")],
            [InlineKeyboardButton("🛠 تبديل وضع الصيانة", callback_data="adm_toggle_maint"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
        ]
        await update.effective_chat.send_message(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    elif data == "adm_pending_txs":
        if not is_admin(user_id): return
        conn = get_db()
        pending = conn.execute("SELECT * FROM transactions WHERE status = 'pending' ORDER BY created_at ASC LIMIT 5").fetchall()
        conn.close()

        if not pending:
            await update.effective_chat.send_message("✅ لا توجد طلبات معلقة حالياً.")
            return

        for p in pending:
            tp = "إيداع/شحن" if p['type'] == 'deposit' else "سحب"
            txt = (
                f"🆔 <b>طلب رقم #{p['id']}</b>\n"
                f"👤 المستخدم: <code>{p['telegram_id']}</code>\n"
                f"📌 النوع: <b>{tp}</b>\n"
                f"💳 الطريقة: {p['method']}\n"
                f"💰 المبلغ: <b>{p['amount']:.2f} NSP</b>\n"
                f"🔢 الرقم المرجعي/المحول منه: <code>{p['tx_number']}</code>"
            )
            btns = [
                [InlineKeyboardButton("✅ موافقة", callback_data=f"adm_tx_approve_{p['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"adm_tx_reject_{p['id']}")]
            ]
            await update.effective_chat.send_message(txt, reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")
        return

    elif data.startswith("adm_tx_approve_") or data.startswith("adm_tx_reject_"):
        if not is_admin(user_id): return
        parts = data.split("_")
        action = parts[2]
        tx_id = int(parts[3])

        conn = get_db()
        cursor = conn.cursor()
        tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()

        if not tx or tx['status'] != 'pending':
            conn.close()
            await query.message.edit_text("❌ الطلب غير موجود أو تم معالجته سابقاً.")
            return

        t_uid = tx['telegram_id']
        t_amt = tx['amount']
        t_type = tx['type']

        if action == "approve":
            cursor.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (tx_id,))
            if t_type == 'deposit':
                cursor.execute("""
                    UPDATE users 
                    SET bot_balance = COALESCE(bot_balance, 0.0) + ?,
                        balance = COALESCE(balance, 0.0) + ?,
                        deposit_count = COALESCE(deposit_count, 0) + 1
                    WHERE telegram_id = ?
                """, (t_amt, t_amt, t_uid))
                update_cashier(t_amt, conn=conn)
                try: await context.bot.send_message(t_uid, f"✅ <b>تم قبول طلب الشحن الخاص بك بقيمة {t_amt:.2f} NSP!</b>", parse_mode="HTML")
                except Exception: pass

            elif t_type == 'withdraw':
                cursor.execute("""
                    UPDATE users 
                    SET withdraw_count = COALESCE(withdraw_count, 0) + 1
                    WHERE telegram_id = ?
                """, (t_uid,))
                update_cashier(-t_amt, conn=conn)
                try: await context.bot.send_message(t_uid, f"✅ <b>تم تنفيذ طلب سحب الأرباح بقيمة {t_amt:.2f} NSP بنجاح!</b>", parse_mode="HTML")
                except Exception: pass
            
            await query.message.edit_text(f"✅ تم القبول والموافقة على الطلب #{tx_id}")

        else:
            cursor.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (tx_id,))
            if t_type == 'withdraw':
                cursor.execute("""
                    UPDATE users 
                    SET bot_balance = COALESCE(bot_balance, 0.0) + ?,
                        balance = COALESCE(balance, 0.0) + ? 
                    WHERE telegram_id = ?
                """, (t_amt, t_amt, t_uid))
            
            try: await context.bot.send_message(t_uid, f"❌ <b>تم رفض طلب {t_type} بقيمة {t_amt:.2f} NSP.</b>", parse_mode="HTML")
            except Exception: pass
            
            await query.message.edit_text(f"❌ تم رفض الطلب #{tx_id}")

        conn.commit()
        conn.close()
        return

    elif data == "adm_toggle_maint":
        if not is_admin(user_id): return
        curr = get_setting('maintenance', 'off')
        nxt = 'off' if (curr == 'on' or curr == '1') else 'on'
        set_setting('maintenance', nxt)
        await update.effective_chat.send_message(f"🛠 تم تغيير وضع الصيانة إلى: <b>{nxt.upper()}</b>", parse_mode="HTML")
        return

    elif data == "adm_add_cashier":
        if not is_admin(user_id): return
        context.user_data['state'] = 'WAIT_ADM_CASHIER_AMT'
        await update.effective_chat.send_message("💰 اكتب المبلغ المراد إضافته لكاشيرة البوت:")
        return

    elif data == "adm_gen_code":
        if not is_admin(user_id): return
        context.user_data['state'] = 'WAIT_ADM_CODE_DETAILS'
        await update.effective_chat.send_message("🎁 أدخل التفاصيل بالشكل (الكود المبلغ عدد_الاستخدامات) مثال:\n<code>AUREX100 10 5</code>", parse_mode="HTML")
        return

    elif data == "adm_grant_spins":
        if not is_admin(user_id): return
        context.user_data['state'] = 'WAIT_ADM_SPINS_GRANT'
        await update.effective_chat.send_message("🎡 أدخل البيانات بالشكل (ID_المستخدم عدد_اللفات) أو (all عدد_اللفات) لمنح الجميع:\nمثال: <code>7255100997 3</code>", parse_mode="HTML")
        return

# ==========================================================
# 5. معالج الرسائل وتتابع الإدخالات (Message Handler)
# ==========================================================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get('state')
    text = update.message.text.strip() if update.message.text else ""

    if not state:
        return

    if state == 'WAIT_SITE_USER':
        if not validate_username(text):
            await update.message.reply_text("❌ اسم المستخدم يجب أن يحتوي على 3 أحرف/أرقام إنجليزية على الأقل وبدون رموز خاصة.")
            return
        
        context.user_data['reg_user'] = text
        context.user_data['state'] = 'WAIT_SITE_PASS'
        await update.message.reply_text("🔑 رائع! الآن أدخل كلمة المرور للحساب:")
        return

    elif state == 'WAIT_SITE_PASS':
        if not validate_password(text):
            await update.message.reply_text("❌ كلمة المرور يجب أن تتكون من 3 خانات على الأقل.")
            return

        reg_user = context.user_data.get('reg_user')
        reg_pass = text
        
        conn = get_db()
        cursor = conn.cursor()
        
        ref_user = cursor.execute("SELECT referred_by FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        referred_by = ref_user['referred_by'] if ref_user else None

        try:
            cursor.execute("""
                UPDATE users 
                SET site_username = ?, site_password = ? 
                WHERE telegram_id = ?
            """, (reg_user, reg_pass, user_id))
            conn.commit()
            conn.close()

            await register_account_to_site_api_async(reg_user, reg_pass, user_id, referred_by)

            context.user_data.clear()
            await update.message.reply_text(
                f"🎉 <b>تم إنشاء الحساب وربطه بنجاح!</b>\n\n"
                f"👤 اسم المستخدم: <code>{html.escape(reg_user)}</code>\n"
                f"🔑 كلمة المرور: <code>{html.escape(reg_pass)}</code>",
                parse_mode="HTML"
            )
            await show_main_menu(update, context)
        except sqlite3.IntegrityError:
            conn.close()
            await update.message.reply_text("❌ اسم المستخدم هذا مأخوذ بالفعل، اختر اسماً آخر.")
        return

    elif state == 'WAIT_TRANSFER_TO_SITE':
        try:
            amt = float(text)
            if amt <= 0: raise ValueError()
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال مبلغ صحيح أكبر من الصفر.")
            return

        conn = get_db()
        cursor = conn.cursor()
        u = cursor.execute("SELECT bot_balance, balance, site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()

        bot_bal = u['bot_balance'] if u['bot_balance'] is not None else (u['balance'] or 0.0)

        if bot_bal < amt:
            conn.close()
            await update.message.reply_text(f"❌ رصيد البوت غير كافٍ! المتاح: {bot_bal:.2f} NSP")
            return

        new_bot = bot_bal - amt
        new_site = (u['site_balance'] or 0.0) + amt

        cursor.execute("UPDATE users SET bot_balance = ?, balance = ?, site_balance = ? WHERE telegram_id = ?", (new_bot, new_bot, new_site, user_id))
        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text(f"✅ تم تحويل {amt:.2f} NSP من رصيد البوت إلى رصيد الموقع بنجاح!")
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_TRANSFER_FROM_SITE':
        try:
            amt = float(text)
            if amt <= 0: raise ValueError()
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال مبلغ صحيح أكبر من الصفر.")
            return

        conn = get_db()
        cursor = conn.cursor()
        u = cursor.execute("SELECT bot_balance, balance, site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()

        site_bal = u['site_balance'] or 0.0

        if site_bal < amt:
            conn.close()
            await update.message.reply_text(f"❌ رصيد الموقع غير كافٍ! المتاح: {site_bal:.2f} NSP")
            return

        bot_bal = u['bot_balance'] if u['bot_balance'] is not None else (u['balance'] or 0.0)
        new_site = site_bal - amt
        new_bot = bot_bal + amt

        cursor.execute("UPDATE users SET bot_balance = ?, balance = ?, site_balance = ? WHERE telegram_id = ?", (new_bot, new_bot, new_site, user_id))
        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text(f"✅ تم سحب {amt:.2f} NSP من رصيد الموقع إلى رصيد البوت بنجاح!")
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_DEP_AMOUNT':
        try:
            amt = float(text)
            min_d = float(get_setting('min_deposit', '10'))
            if amt < min_d:
                await update.message.reply_text(f"❌ الحد الأدنى للشحن هو {min_d:.2f} NSP.")
                return
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال مبلغ صحيح.")
            return

        context.user_data['dep_amount'] = amt
        context.user_data['state'] = 'WAIT_DEP_PROOF'
        await update.message.reply_text("📸 قم بإرسال رقم التحويل أو الصورة/الوصل لتأكيد عملية الدفع:")
        return

    elif state == 'WAIT_DEP_PROOF':
        amt = context.user_data.get('dep_amount')
        method = context.user_data.get('dep_method', 'Manual')
        proof = text if text else "مرفق صورة"

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transactions (telegram_id, type, method, amount, tx_number, status)
            VALUES (?, 'deposit', ?, ?, ?, 'pending')
        """, (user_id, method, amt, proof))
        tx_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text("✅ <b>تم تقديم طلب الشحن بنجاح وهو قيد المراجعة الآن!</b>", parse_mode="HTML")
        
        await send_all_admins(
            context,
            f"📥 <b>طلب شحن رصيد جديد (#{tx_id}):</b>\n\n"
            f"👤 العميل: <code>{user_id}</code>\n"
            f"💳 الطريقة: {method}\n"
            f"💰 المبلغ: <b>{amt:.2f} NSP</b>\n"
            f"🧾 الإثبات: <code>{proof}</code>"
        )
        return

    elif state == 'WAIT_WITH_AMOUNT':
        try:
            amt = float(text)
            min_w = float(get_setting('min_withdraw', '10'))
            if amt < min_w:
                await update.message.reply_text(f"❌ الحد الأدنى للسحب هو {min_w:.2f} NSP.")
                return
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال مبلغ صحيح.")
            return

        conn = get_db()
        u = conn.execute("SELECT bot_balance, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        
        bot_bal = u['bot_balance'] if (u and u['bot_balance'] is not None) else (u['balance'] if u else 0.0)

        if bot_bal < amt:
            await update.message.reply_text(f"❌ رصيد البوت الخاص بك لا يكفي ({bot_bal:.2f} NSP).")
            return

        context.user_data['with_amount'] = amt
        context.user_data['state'] = 'WAIT_WITH_DETAILS'
        await update.message.reply_text("✍️ أدخل رقم المحفظة أو العنوان الذي تريد استقبال الأموال عليه:")
        return

    elif state == 'WAIT_WITH_DETAILS':
        amt = context.user_data.get('with_amount')
        method = context.user_data.get('with_method', 'Manual')
        details = text

        conn = get_db()
        cursor = conn.cursor()
        u = cursor.execute("SELECT bot_balance, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        bot_bal = u['bot_balance'] if u['bot_balance'] is not None else (u['balance'] or 0.0)

        if bot_bal < amt:
            conn.close()
            await update.message.reply_text("❌ تراجع الرصيد، تعذر إكمال الطلب.")
            return

        new_bal = bot_bal - amt
        cursor.execute("UPDATE users SET bot_balance = ?, balance = ? WHERE telegram_id = ?", (new_bal, new_bal, user_id))
        cursor.execute("""
            INSERT INTO transactions (telegram_id, type, method, amount, tx_number, status)
            VALUES (?, 'withdraw', ?, ?, ?, 'pending')
        """, (user_id, method, amt, details))
        tx_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text("✅ <b>تم تقديم طلب السحب وخصم المبلغ مؤقتاً لحين الاعتماد!</b>", parse_mode="HTML")

        await send_all_admins(
            context,
            f"📤 <b>طلب سحب أرباح جديد (#{tx_id}):</b>\n\n"
            f"👤 العميل: <code>{user_id}</code>\n"
            f"💳 الطريقة: {method}\n"
            f"💰 المبلغ: <b>{amt:.2f} NSP</b>\n"
            f"📌 التفاصيل: <code>{details}</code>"
        )
        return

    elif state == 'WAIT_GIFT_CODE':
        code_str = text.strip().upper()
        conn = get_db()
        cursor = conn.cursor()

        code_obj = cursor.execute("SELECT * FROM gift_codes WHERE UPPER(code) = ? AND active = 1", (code_str,)).fetchone()
        if not code_obj:
            conn.close()
            await update.message.reply_text("❌ الكود غير صالح أو منتهي الصلاحية.")
            return

        used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND UPPER(code) = ?", (user_id, code_str)).fetchone()
        if used:
            conn.close()
            await update.message.reply_text("❌ لقد قمت باستخدام هذا الكود من قبل!")
            return

        max_uses = code_obj['max_uses'] or 1
        used_count = (code_obj['used_count'] or 0) + 1
        amt = float(code_obj['amount'] or 0)

        is_active = 0 if used_count >= max_uses else 1

        cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (user_id, code_obj['code']))
        cursor.execute("UPDATE gift_codes SET used_count = ?, active = ? WHERE code = ?", (used_count, is_active, code_obj['code']))
        
        cursor.execute("""
            UPDATE users 
            SET bot_balance = COALESCE(bot_balance, 0.0) + ?,
                balance = COALESCE(balance, 0.0) + ? 
            WHERE telegram_id = ?
        """, (amt, amt, user_id))

        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text(f"🎉 <b>مبروك! تم شحن {amt:.2f} NSP لرصيد بوتك بنجاح.</b>", parse_mode="HTML")
        await send_all_admins(context, f"🎟 <b>استخدام كود هدية:</b>\n👤 المستخدم: <code>{user_id}</code>\n🔑 الكود: <code>{code_str}</code>\n💰 القيمة: <b>{amt:.2f} NSP</b>")
        return

    elif state == 'WAIT_SUPPORT_MSG':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال رسالتك إلى فريق الدعم بنجاح.")
        await send_all_admins(context, f"💬 <b>رسالة دعم جديدة:</b>\n👤 من: <code>{user_id}</code>\n\n{html.escape(text)}")
        return

    elif state == 'WAIT_WIN_SHOT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الصورة للإدارة بنجاح، شكراً لمشاركتك!")
        await send_all_admins(context, f"📸 <b>إثبات إصابة/فوز جديد:</b>\n👤 من: <code>{user_id}</code>\n📝 النص: {html.escape(text)}")
        return

    elif state == 'WAIT_ADM_CASHIER_AMT':
        if not is_admin(user_id): return
        try:
            amt = float(text)
            update_cashier(amt)
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم إضافة {amt:.2f} NSP إلى كاشيرة البوت بنجاح!")
        except ValueError:
            await update.message.reply_text("❌ مبلغ غير صالح.")
        return

    elif state == 'WAIT_ADM_CODE_DETAILS':
        if not is_admin(user_id): return
        parts = text.split()
        if len(parts) < 3:
            await update.message.reply_text("❌ الصيغة غير صحيحة. استخدم: الكود المبلغ الاستخدامات")
            return
        code = parts[0].upper()
        try:
            amt = float(parts[1])
            uses = int(parts[2])
            cost = amt * uses

            conn = get_db()
            c_bal = get_cashier_balance(conn=conn)
            if c_bal < cost:
                conn.close()
                await update.message.reply_text(f"❌ رصيد الكاشيرة لا يكفي ({c_bal:.2f} NSP) لتغطية تكلفة الكود ({cost:.2f} NSP).")
                return

            update_cashier(-cost, conn=conn)
            conn.execute("INSERT INTO gift_codes (code, amount, max_uses, used_count, active) VALUES (?, ?, ?, 0, 1)", (code, amt, uses))
            conn.commit()
            conn.close()

            context.user_data.clear()
            await update.message.reply_text(f"✅ تم توليد الكود <code>{code}</code> بقيمة {amt} لعدد {uses} استخدام وتم خصم {cost} من الكاشيرة بنجاح!", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ: {e}")
        return

    elif state == 'WAIT_ADM_SPINS_GRANT':
        if not is_admin(user_id): return
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text("❌ الصيغة غير صحيحة.")
            return
        target = parts[0]
        try:
            spins = int(parts[1])
            conn = get_db()
            cursor = conn.cursor()

            if target.lower() == 'all':
                cursor.execute("""
                    UPDATE users 
                    SET free_spins = COALESCE(free_spins, 0) + ?,
                        spins_count = COALESCE(spins_count, 0) + ?
                """, (spins, spins))
                msg = f"✅ تم منح {spins} لفة مجانية لجميع المستخدمين بنجاح!"
            else:
                cursor.execute("""
                    UPDATE users 
                    SET free_spins = COALESCE(free_spins, 0) + ?,
                        spins_count = COALESCE(spins_count, 0) + ?
                    WHERE telegram_id = ? OR site_username = ?
                """, (spins, spins, target, target))
                msg = f"✅ تم منح {spins} لفة مجانية للمستخدم {target} بنجاح!"

            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(msg)
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ: {e}")
        return

# ==========================================================
# 6. التشغيل الرئيسي
# ==========================================================
def main():
    global bot_app, MAIN_LOOP
    init_db()

    threading.Thread(target=start_health_check_server, daemon=True).start()

    bot_app = Application.builder().token(BOT_TOKEN).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(callback_router))
    bot_app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, message_handler))

    logging.info("Starting AUREX Telegram Bot Polling...")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    MAIN_LOOP = loop

    bot_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
