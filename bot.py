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
# 0. خادم صحة الخدمة وواجهة API وعجلة الحظ (Web App Server)
# ==========================================================
WHEEL_VALUES = [0, 5, 10, 15, 25, 50, 100, 500, 10000]
MAIN_LOOP = None

HTML_WHEEL_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>AUREX Lucky Wheel</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Cairo', sans-serif; user-select: none; }
        body { background: radial-gradient(circle at center, #1b0203 0%, #080102 60%, #000000 100%); color: #fff; text-align: center; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 15px; }
        h1 { color: #e50914; font-size: 28px; font-weight: 900; letter-spacing: 2px; text-shadow: 0 0 15px rgba(229, 9, 20, 0.7); }
        p.subtitle { font-size: 13px; color: #d4af37; margin-top: 3px; }
        .wheel-container { position: relative; width: 330px; height: 330px; margin: 15px auto; border-radius: 50%; box-shadow: 0 0 35px rgba(229, 9, 20, 0.5); padding: 5px; background: linear-gradient(145deg, #d4af37, #5c0206, #d4af37); }
        #canvas { width: 320px; height: 320px; border-radius: 50%; background-color: #0d0d0d; display: block; }
        .pointer { position: absolute; top: -12px; left: 50%; transform: translateX(-50%); width: 0; height: 0; border-left: 16px solid transparent; border-right: 16px solid transparent; border-top: 32px solid #d4af37; z-index: 10; }
        .center-btn { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 70px; height: 70px; background: radial-gradient(circle, #e50914 0%, #300000 100%); border: 3px solid #d4af37; border-radius: 50%; color: #fff; font-weight: 900; font-size: 16px; cursor: pointer; z-index: 5; }
        .info-card { background: rgba(20, 5, 7, 0.85); border: 1px solid rgba(212, 175, 55, 0.4); border-radius: 16px; padding: 14px 20px; width: 100%; max-width: 330px; margin-top: 15px; display: flex; justify-content: space-around; }
        .info-item { display: flex; flex-direction: column; }
        .info-item span.label { font-size: 11px; color: #aaa; }
        .info-item span.val { font-size: 18px; font-weight: bold; color: #d4af37; }
        #result-modal { margin-top: 15px; font-size: 16px; font-weight: bold; min-height: 28px; }
        .win-msg { color: #2ecc71; } .lose-msg { color: #e74c3c; }
    </style>
</head>
<body>
    <div class="header-brand"><h1>AUREX WHEEL</h1><p class="subtitle">عجلة الحظ التفاعلية الفاخرة</p></div>
    <div class="wheel-container">
        <div class="pointer"></div>
        <canvas id="canvas" width="320" height="320"></canvas>
        <button class="center-btn" id="spinBtn" onclick="startSpin()">SPIN</button>
    </div>
    <div id="result-modal"></div>
    <div class="info-card">
        <div class="info-item"><span class="label">اللفات المتاحة</span><span class="val" id="spinsCount">--</span></div>
        <div class="info-item"><span class="label">رصيد البوت</span><span class="val" id="userBal">--</span></div>
    </div>
    <script>
        const tg = window.Telegram ? window.Telegram.WebApp : null;
        if (tg) tg.expand();
        const urlParams = new URLSearchParams(window.location.search);
        let userId = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) ? tg.initDataUnsafe.user.id : null;
        if (!userId) userId = urlParams.get("telegram_id") || urlParams.get("user_id");
        if (userId === "null" || userId === "undefined") userId = null;

        const values = [0, 5, 10, 15, 25, 50, 100, 500, 10000];
        const numSlices = values.length;
        const canvas = document.getElementById("canvas");
        const ctx = canvas.getContext("2d");
        let currentAngle = 0, isSpinning = false;

        function drawWheel() {
            const radius = 160, sliceAngle = (2 * Math.PI) / numSlices;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            for (let i = 0; i < numSlices; i++) {
                const angle = currentAngle + i * sliceAngle;
                ctx.beginPath(); ctx.moveTo(radius, radius);
                ctx.arc(radius, radius, radius, angle, angle + sliceAngle);
                ctx.closePath();
                ctx.fillStyle = (i % 2 === 0) ? "#120203" : "#80050a"; ctx.fill();
                ctx.strokeStyle = "#d4af37"; ctx.lineWidth = 1.5; ctx.stroke();
                ctx.save(); ctx.translate(radius, radius); ctx.rotate(angle + sliceAngle / 2);
                ctx.textAlign = "right"; ctx.fillStyle = (values[i] >= 500) ? "#d4af37" : "#ffffff";
                ctx.font = "bold 13px Cairo, Arial"; ctx.fillText(values[i] + " NSP", radius - 15, 5);
                ctx.restore();
            }
        }

        async function fetchUserData() {
            if (!userId) return;
            try {
                const res = await fetch('/api/get-spins?telegram_id=' + userId);
                const data = await res.json();
                if (data.status === 'success' || data.free_spins !== undefined) {
                    document.getElementById("spinsCount").innerText = data.free_spins || 0;
                    document.getElementById("userBal").innerText = (data.bot_balance || 0).toFixed(2) + " NSP";
                }
            } catch (e) {}
        }

        async function startSpin() {
            if (isSpinning || !userId) return;
            isSpinning = true; document.getElementById("spinBtn").disabled = true;
            document.getElementById("result-modal").innerText = "";
            try {
                const res = await fetch('/api/wheel/spin', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ telegram_id: userId, user_id: userId })
                });
                const data = await res.json();
                if (data.status !== 'success') {
                    document.getElementById("result-modal").innerHTML = `<span class="lose-msg">❌ ${data.error || "خطأ"}</span>`;
                    isSpinning = false; document.getElementById("spinBtn").disabled = false;
                    return;
                }
                const targetIndex = data.prize_index, sliceAngle = (2 * Math.PI) / numSlices;
                const targetAngle = (1.5 * Math.PI) - (targetIndex * sliceAngle) - (sliceAngle / 2);
                const extraRounds = 7 * 2 * Math.PI, startAngle = currentAngle;
                let diff = targetAngle - (currentAngle % (2 * Math.PI));
                if (diff < 0) diff += 2 * Math.PI;
                const finalAngle = startAngle + extraRounds + diff;
                let startTimestamp = null;

                function animate(timestamp) {
                    if (!startTimestamp) startTimestamp = timestamp;
                    const elapsed = timestamp - startTimestamp, progress = Math.min(elapsed / 5000, 1);
                    const easeOut = 1 - Math.pow(1 - progress, 4);
                    currentAngle = startAngle + (finalAngle - startAngle) * easeOut;
                    drawWheel();
                    if (progress < 1) { requestAnimationFrame(animate); } else {
                        currentAngle = finalAngle; drawWheel(); isSpinning = false;
                        document.getElementById("spinBtn").disabled = false;
                        document.getElementById("spinsCount").innerText = data.free_spins_left;
                        document.getElementById("userBal").innerText = (data.new_bot_balance || 0).toFixed(2) + " NSP";
                        document.getElementById("result-modal").innerHTML = data.reward > 0 ? 
                            `<span class="win-msg">🎉 مبروك! فزت بـ ${data.reward} NSP</span>` : 
                            `<span class="lose-msg">💔 حظ أوفر في المرة القادمة!</span>`;
                    }
                }
                requestAnimationFrame(animate);
            } catch (e) {
                isSpinning = false; document.getElementById("spinBtn").disabled = false;
            }
        }
        drawWheel(); fetchUserData();
    </script>
</body>
</html>
"""

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/wheel":
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(HTML_WHEEL_PAGE.encode('utf-8'))
        elif parsed_path.path in ["/api/get-spins", "/api/user_info"]:
            qs = parse_qs(parsed_path.query)
            user_id_raw = qs.get('telegram_id', [None])[0] or qs.get('user_id', [None])[0]
            if user_id_raw and str(user_id_raw).isdigit():
                user_id = int(user_id_raw)
                conn = get_db()
                u = conn.execute("SELECT spins_count, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
                conn.close()
                if u:
                    self._send_json({"status": "success", "free_spins": u['spins_count'], "bot_balance": u['balance']})
                    return
            self._send_json({"status": "error", "error": "المستخدم غير موجود"})
        elif parsed_path.path == "/api/users":
            conn = get_db()
            users = conn.execute("SELECT telegram_id, site_username, site_balance FROM users WHERE site_username IS NOT NULL").fetchall()
            conn.close()
            self._send_json([dict(u) for u in users])
        else:
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"OK - AUREX BOT IS RUNNING")

    def do_POST(self):
        if self.path == "/api/register":
            self._send_json({"status": "ok", "message": "Registered locally"})
            return
        elif self.path in ["/api/wheel/spin", "/api/spin"]:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                user_id_raw = data.get('telegram_id') or data.get('user_id')
                if not user_id_raw or not str(user_id_raw).isdigit():
                    self._send_json({"status": "error", "error": "معرف المستخدم غير صحيح!"})
                    return

                user_id = int(user_id_raw)
                conn = get_db()
                cursor = conn.cursor()
                u = cursor.execute("SELECT spins_count, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
                if not u or u['spins_count'] <= 0:
                    conn.close()
                    self._send_json({"status": "error", "error": "ليس لديك محاولات لعب كافية!"})
                    return

                cursor.execute("UPDATE users SET spins_count = spins_count - 1 WHERE telegram_id = ?", (user_id,))
                win_rate = float(get_setting('game_win_rate', '30'))
                cashier_bal = get_cashier_balance()
                weights_raw = get_setting('wheel_weights', '')
                try: w_dict = json.loads(weights_raw) if weights_raw else {}
                except Exception: w_dict = {}

                roll = random.uniform(0, 100)
                prize = 0
                possible_prizes = [v for v in WHEEL_VALUES if 0 < v <= cashier_bal]
                
                if roll <= win_rate and possible_prizes:
                    prize_weights = [float(w_dict.get(str(v), 10)) for v in possible_prizes]
                    prize = random.choices(possible_prizes, weights=prize_weights, k=1)[0]

                prize_index = WHEEL_VALUES.index(prize)
                new_bal = u['balance']
                if prize > 0:
                    before_cashier, after_cashier = update_cashier(-prize)
                    cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (prize, user_id))
                    conn.commit()
                    new_bal += prize
                    if MAIN_LOOP and MAIN_LOOP.is_running():
                        asyncio.run_coroutine_threadsafe(
                            send_spin_notifications(user_id, prize, before_cashier, after_cashier),
                            MAIN_LOOP
                        )
                else:
                    conn.commit()

                rem_spins = u['spins_count'] - 1
                conn.close()
                self._send_json({
                    "status": "success", "prize_index": prize_index, "reward": prize,
                    "free_spins_left": rem_spins, "new_bot_balance": new_bal
                })
                return
            except Exception as e:
                logging.error(f"Spin API error: {e}")
                self._send_json({"status": "error", "error": "حدث خطأ أثناء التدوير."})
                return
        self.send_response(400)
        self.end_headers()

    def _send_json(self, data_dict):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data_dict).encode('utf-8'))

    def log_message(self, format, *args): return

def start_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ==========================================================
# 1. الإعدادات الأساسية
# ==========================================================
MAIN_ADMIN_ID = int(os.environ.get("MAIN_ADMIN_ID", "7255100997"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8948439052:AAHv-UWeTMQmHybxspFRVRpnjIqetmW8LbI").strip() 
SERVER_URL = os.environ.get("SERVER_URL", "https://aurex-my-bot.onrender.com").strip()
if not SERVER_URL.startswith("https://"): SERVER_URL = "https://" + SERVER_URL.replace("http://", "")

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
        except Exception: pass

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
                try: await bot_app.bot.send_message(aid, msg_adm, parse_mode="HTML")
                except Exception: pass
        except Exception as e: logging.error(f"Notification error: {e}")

# ==========================================================
# 2. إدارة قاعدة البيانات
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
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY, username TEXT, site_username TEXT UNIQUE, site_password TEXT, 
        balance REAL DEFAULT 0.0, site_balance REAL DEFAULT 0.0, total_spent REAL DEFAULT 0.0,
        deposit_count INTEGER DEFAULT 0, withdraw_count INTEGER DEFAULT 0, referrals_count INTEGER DEFAULT 0,
        spins_count INTEGER DEFAULT 0, referred_by INTEGER, got_welcome_bonus INTEGER DEFAULT 0,
        security_passed INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
        code_restricted_until TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER, type TEXT, method TEXT, amount REAL, tx_number TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL, max_uses INTEGER, used_count INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_codes (telegram_id INTEGER, code TEXT, PRIMARY KEY (telegram_id, code))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_methods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, number TEXT, active INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')

    defaults = [
        ('maintenance', '0'), ('welcome_bonus', '500'), ('welcome_bonus_enabled', '1'),
        ('min_deposit', '50'), ('min_withdraw', '100'), ('cashier_balance', '10000.0'),
        ('forced_channels', ''), ('game_win_rate', '30'),
        ('wheel_weights', '{"0": 50, "5": 20, "10": 12, "15": 8, "25": 5, "50": 3, "100": 1.5, "500": 0.4, "10000": 0.1}')
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(val)))
    cursor.execute("INSERT OR IGNORE INTO payment_methods (name, number) VALUES ('سيريتل كاش', '0987654321')")
    cursor.execute("INSERT OR IGNORE INTO payment_methods (name, number) VALUES ('شام كاش', '0912345678')")
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
    row = conn.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row and row['is_admin'])

def update_cashier(amount_change):
    conn = get_db()
    cursor = conn.cursor()
    row_before = cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'").fetchone()
    before_balance = float(row_before['value']) if row_before else 0.0
    cursor.execute("UPDATE settings SET value = CAST(MAX(0.0, CAST(value AS REAL) + ?) AS TEXT) WHERE key = 'cashier_balance'", (amount_change,))
    row_after = cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'").fetchone()
    after_balance = float(row_after['value']) if row_after else 0.0
    conn.commit()
    conn.close()
    return before_balance, after_balance

def get_cashier_balance(): return float(get_setting('cashier_balance', '0.0'))
def get_payment_number(method_name):
    conn = get_db()
    row = conn.execute("SELECT number FROM payment_methods WHERE name = ?", (method_name,)).fetchone()
    conn.close()
    return row['number'] if row else "غير متوفر"

def validate_username(username): return len(username) >= 6 and bool(re.match(r'^[a-zA-Z0-9_]+$', username))
def validate_password(password): return len(password) >= 6 and bool(re.search(r'[a-zA-Z]', password)) and bool(re.search(r'\d', password))

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

# ==========================================================
# 3. الأوامر والقوائم الرئيسية مع أنيميشن التفاعل عند Start
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # تأثير حركي تفاعلي يظهر على شاشة المستخدم فوراً عند الضغط على start
    loading_msg = await update.message.reply_text("⚡️ <b>جاري تشغيل محرك AUREX...</b> ▒▒▒▒▒▒▒▒▒▒ 0%", parse_mode="HTML")
    await asyncio.sleep(0.2)
    await loading_msg.edit_text("🔄 <b>جاري الاتصال بالسيرفر وقواعد البيانات...</b> █▒▒▒▒▒▒▒▒▒ 20%", parse_mode="HTML")
    await asyncio.sleep(0.2)
    await loading_msg.edit_text("⚙️ <b>جاري تحميل العجلة والنظام المالي...</b> ██████▒▒▒▒ 60%", parse_mode="HTML")
    await asyncio.sleep(0.2)
    await loading_msg.edit_text("💎 <b>جاري تجهيز بيانات حسابك...</b> ██████████ 100%", parse_mode="HTML")
    await asyncio.sleep(0.1)
    await loading_msg.delete()

    conn = get_db()
    cursor = conn.cursor()
    db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
    
    if db_user and db_user['is_banned']:
        conn.close()
        await update.message.reply_text("🚫 حسابك محظور من استخدام البوت.")
        return

    if get_setting('maintenance', '0') == '1' and not is_admin(user.id):
        conn.close()
        await update.message.reply_text("🛠 البوت والموقع حالياً في حالة صيانة وتحديث، يرجى المحاولة لاحقاً.")
        return

    if not await check_forced_sub(user.id, context):
        channels = get_setting('forced_channels', '')
        btns = [[InlineKeyboardButton(f"اشترك هنا: {ch}", url=f"https://t.me/{ch.replace('@','')}")] for ch in channels.split(',') if ch]
        btns.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub")])
        conn.close()
        await update.message.reply_text("🚨 <b>يجب عليك الاشتراك بجميع القنوات التالية أولاً لاستخدام البوت:</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")
        return

    ref_by = None
    if context.args and len(context.args) > 0 and context.args[0].isdigit():
        parsed_id = int(context.args[0])
        if parsed_id != user.id: ref_by = parsed_id

    if not db_user:
        is_main_admin = 1 if user.id == MAIN_ADMIN_ID else 0
        cursor.execute("INSERT INTO users (telegram_id, username, referred_by, is_admin) VALUES (?, ?, ?, ?)", (user.id, user.username or user.first_name, ref_by, is_main_admin))
        conn.commit()
        if user.id != MAIN_ADMIN_ID:
            ref_txt = f"<code>{ref_by}</code>" if ref_by else "بدون إحالة"
            await send_all_admins(context, f"👤 <b>عضو جديد انضم للبوت!</b>\n\n• الاسم: {html.escape(user.full_name or '')}\n• المعرف: @{user.username or 'لا يوجد'}\n• الآيدي: <code>{user.id}</code>\n• الإحالة بواسطة: {ref_txt}")
        if ref_by:
            cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE telegram_id = ?", (ref_by,))
            conn.commit()
            try: await context.bot.send_message(ref_by, f"🎉 <b>انضم عميل جديد عبر رابط إحالتك!</b>\n🆔 العميل: <code>{html.escape(user.first_name or '')}</code>\n📌 سيتم منحك فرصة لعب فورية بمجرد إنشاء هذا العميل لحسابه بالموقع!", parse_mode="HTML")
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
    if update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else: await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = html.escape(update.effective_user.first_name or "مستخدم")
    
    conn = get_db()
    db_user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    site_info = f"<code>{html.escape(db_user['site_username'])}</code>" if db_user and db_user['site_username'] else "❌ غير مربوط"
    bot_bal = db_user['balance'] if db_user else 0.0
    site_bal = db_user['site_balance'] if db_user else 0.0
    spins = db_user['spins_count'] if db_user else 0

    text = (
        f"👑 <b>منصة AUREX المتطورة والمحدثة</b> 👑\n"
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
    try: aurex_btn = InlineKeyboardButton("🌐 AUREX WebApp", web_app=WebAppInfo(url=SERVER_URL))
    except Exception: aurex_btn = InlineKeyboardButton("🌐 AUREX", url=SERVER_URL)

    try: wheel_btn = InlineKeyboardButton(f"🎡 عجلة الحظ والإحالات ({spins} فرص)", web_app=WebAppInfo(url=wheel_url))
    except Exception: wheel_btn = InlineKeyboardButton(f"🎡 عجلة الحظ والإحالات ({spins} فرص)", url=wheel_url)

    # جميع ازرار العميل القديمة مع إعادة الترتيب وإضافة ازرار جديدة
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
# 4. معالج التفاعلات للأزرار (Callback Router)
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
        await query.message.edit_text("غلط ياحبيب راجع معلوماتك ولا مابدك البونص الترحيبي؟", reply_markup=InlineKeyboardMarkup(keyboard))
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
        bonus_enabled = get_setting('welcome_bonus_enabled', '1') == '1'
        bonus_amt = float(get_setting('welcome_bonus', '500.0'))
        
        if bonus_enabled and bonus_amt > 0 and user and user['got_welcome_bonus'] == 0:
            before_cashier, after_cashier = update_cashier(-bonus_amt)
            cursor.execute("UPDATE users SET security_passed = 1, got_welcome_bonus = 1, balance = balance + ? WHERE telegram_id = ?", (bonus_amt, user_id))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"قلتلك حموية ماصدقتني! 🍯\n\n🎉 <b>لقد حصلت على بونص ترحيبي بقيمة {bonus_amt:.2f} NSP!</b>", parse_mode="HTML")
            await send_all_admins(context, f"⚠️ <b>خصم كاشيرة (بونص ترحيبي):</b>\nالمبلغ: <b>{bonus_amt:.2f} NSP</b>\nالعميل: <code>{user_id}</code>\n🏦 الكاشيرة قبل: <code>{before_cashier:.2f}</code> | بعد: <code>{after_cashier:.2f}</code>")
        else:
            cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("قلتلك حموية ماصدقتني! 🍯 تم توثيق حسابك بنجاح.")
        await show_main_menu(update, context)
        return

    elif data == "cancel_action":
        context.user_data.clear()
        await query.message.delete()
        await show_main_menu(update, context)
        return

    elif data == "check_sub":
        if await check_forced_sub(user_id, context):
            await query.message.delete()
            await show_main_menu(update, context)
        else:
            await update.effective_chat.send_message("❌ لم تشترك في كامل القنوات المطلوبة بعد.")

    elif data == "my_account":
        conn = get_db()
        u = conn.execute("SELECT site_username, site_password FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if u and u['site_username']:
            await update.effective_chat.send_message(f"🔐 <b>بيانات حسابك المربوط في الموقع:</b>\n\n👤 اسم المستخدم: <code>{html.escape(u['site_username'])}</code>\n🔑 كلمة المرور: <code>{html.escape(u['site_password'])}</code>", parse_mode="HTML")
        else:
            await update.effective_chat.send_message("❌ ليس لديك حساب مربوط بعد! استخدم زر (إنشاء حساب).")

    elif data == "create_site_account":
        conn = get_db()
        u = conn.execute("SELECT site_username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if u and u['site_username']:
            await update.effective_chat.send_message(f"⚠️ لديك حساب سابق بالفعل باسم: <code>{html.escape(u['site_username'])}</code>", parse_mode="HTML")
            return
        context.user_data['state'] = 'WAIT_SITE_USER'
        await update.effective_chat.send_message("🔑 <b>إنشاء حساب جديد للموقع:</b>\n\n✍️ أدخل اسم المستخدم الجديد (6 أحرف/أرقام إنجليزية على الأقل وبدون رموز):", parse_mode="HTML")

    elif data == "transfer_to_site":
        conn = get_db()
        u = conn.execute("SELECT site_username, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ يجب إنشاء حساب على الموقع أولاً!", parse_mode="HTML")
            return
        context.user_data['state'] = 'WAIT_TRANSFER_TO_SITE'
        await update.effective_chat.send_message(f"🔄 <b>شحن رصيد للموقع:</b>\n💰 رصيد البوت المتوفر: <b>{u['balance']:.2f} NSP</b>\n\n✍️ أدخل المبلغ المراد تحويله إلى الموقع:", parse_mode="HTML")

    elif data == "transfer_from_site":
        conn = get_db()
        u = conn.execute("SELECT site_username, site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ يجب إنشاء حساب على الموقع أولاً!", parse_mode="HTML")
            return
        context.user_data['state'] = 'WAIT_TRANSFER_FROM_SITE'
        await update.effective_chat.send_message(f"↩️ <b>سحب رصيد من الموقع:</b>\n💎 رصيد الموقع المتوفر: <b>{u['site_balance']:.2f} NSP</b>\n\n✍️ أدخل المبلغ المراد سحبه إلى البوت:", parse_mode="HTML")

    elif data == "dep_menu":
        min_dep = get_setting('min_deposit', '50')
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="dep_method_سيريتل كاش")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="dep_method_شام كاش")],
            [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
        ]
        await update.effective_chat.send_message(f"📥 <b>شحن البوت - اختر طريقة الدفع:</b>\n📌 الحد الأدنى للشحن: <code>{min_dep} NSP</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("dep_method_"):
        method_name = data.replace("dep_method_", "")
        acc_num = get_payment_number(method_name)
        min_dep = get_setting('min_deposit', '50')
        context.user_data['selected_method'] = method_name
        context.user_data['state'] = 'WAIT_DEP_AMT'
        await update.effective_chat.send_message(f"💳 <b>طريقة الشحن:</b> {method_name}\n📌 <b>رقم الحساب:</b> <code>{acc_num}</code>\n⚠️ <b>الحد الأدنى:</b> <code>{min_dep} NSP</code>\n\n✍️ أرسل المبلغ المراد شحنه بعملة NSP الآن:", parse_mode="HTML")

    elif data == "with_menu":
        min_with = get_setting('min_withdraw', '100')
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="with_method_سيريتل كاش")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="with_method_شام كاش")],
            [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
        ]
        await update.effective_chat.send_message(f"📤 <b>سحب أرباحك - اختر طريقة الاستلام:</b>\n📌 الحد الأدنى للسحب: <code>{min_with} NSP</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("with_method_"):
        method_name = data.replace("with_method_", "")
        min_with = get_setting('min_withdraw', '100')
        context.user_data['selected_method'] = method_name
        context.user_data['state'] = 'WAIT_WITH_AMT'
        await update.effective_chat.send_message(f"📤 <b>طريقة السحب:</b> {method_name}\n📌 <b>الحد الأدنى:</b> <code>{min_with} NSP</code>\n\n✍️ أرسل المبلغ المراد سحبه بعملة NSP:", parse_mode="HTML")

    elif data == "my_ref":
        me = await context.bot.get_me()
        await update.effective_chat.send_message(f"🔗 <b>رابط إحالتي الشخصي:</b>\n<code>https://t.me/{me.username}?start={user_id}</code>\n\n📢 انشر رابطك! عند تسجيل صديقك وإنشاء حسابه بالموقع، ستحصل فوراً على 🎡 <b>فرصة تدوير مجانية</b>!", parse_mode="HTML")

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await update.effective_chat.send_message("🎁 أدخل كود الهدية الآن:")

    elif data == "send_win_shot":
        context.user_data['state'] = 'WAIT_WIN_SHOT'
        await update.effective_chat.send_message("📸 أرسل صورة الإصابة / الفوز الآن:")

    elif data == "contact_support":
        context.user_data['state'] = 'WAIT_SUPPORT'
        await update.effective_chat.send_message("💬 اكتب رسالتك أو أرسل صورة مباشرة للدعم الفني:")

    elif data == "my_logs":
        conn = get_db()
        logs = conn.execute("SELECT * FROM transactions WHERE telegram_id = ? ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        conn.close()
        if not logs:
            await update.effective_chat.send_message("📜 ليس لديك سجلات سابقة.")
            return
        txt = "📜 <b>سجل آخر عملياتك:</b>\n\n"
        for l in logs: txt += f"• {l['type']} | الوسيلة: {l['method'] or 'عام'} | المبلغ: {l['amount']} NSP | الحالة: {l['status']}\n"
        await update.effective_chat.send_message(txt, parse_mode="HTML")

    # ----- لوحة الإدارة والأزرار الجديدة للمدير -----
    elif data == "admin_panel" and is_admin(user_id):
        await show_admin_panel(update, context)

    elif data == "adm_game_settings" and is_admin(user_id):
        wr = get_setting('game_win_rate', '30')
        keyboard = [
            [InlineKeyboardButton("🎯 تعديل نسبة الفوز %", callback_data="adm_set_win_rate")],
            [InlineKeyboardButton("📊 تعديل أوزان نسب الجوائز", callback_data="adm_slice_weights_menu")],
            [InlineKeyboardButton("⚙️ لوحة الآدمن", callback_data="admin_panel")]
        ]
        await update.effective_chat.send_message(f"🎮 <b>إعدادات خوارزمية عجلة الحظ:</b>\n\n• نسبة الفوز العامة: <b>{wr}%</b>\n📌 الخصم يتم تلقائياً من الكاشيرة.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "adm_slice_weights_menu" and is_admin(user_id):
        weights_raw = get_setting('wheel_weights', '{}')
        try: w_dict = json.loads(weights_raw)
        except Exception: w_dict = {}
        txt, btns, row = "📊 <b>أوزان ظهور الجوائز الحالية:</b>\n\n", [], []
        for v in WHEEL_VALUES:
            txt += f"• الجائزة <b>{v} NSP</b> 👈 الوزن: <code>{w_dict.get(str(v), 10)}</code>\n"
            row.append(InlineKeyboardButton(f"✏️ {v} NSP", callback_data=f"adm_sw_{v}"))
            if len(row) == 3: btns.append(row); row = []
        if row: btns.append(row)
        btns.append([InlineKeyboardButton("⚙️ إعدادات العجلة", callback_data="adm_game_settings")])
        await update.effective_chat.send_message(txt, reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

    elif data.startswith("adm_sw_") and is_admin(user_id):
        val = data.replace("adm_sw_", "")
        context.user_data['target_slice_val'] = val
        context.user_data['state'] = 'ADM_WAIT_SLICE_WEIGHT_AMT'
        await update.effective_chat.send_message(f"🎯 أدخل الوزن النسبي الجديد لـ <b>{val} NSP</b>:", parse_mode="HTML")

    elif data == "adm_grant_spins" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_SPINS_USER_ID'
        await update.effective_chat.send_message("👤 أدخل آيدي العميل أو اسم مستخدم الموقع لمنحه محاولات مجانية:")

    elif data == "adm_set_win_rate" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_WIN_RATE'
        await update.effective_chat.send_message("🎯 أدخل نسبة الفوز الجديدة في عجلة الحظ (من 0 إلى 100):")

    elif data == "adm_cashier" and is_admin(user_id):
        bal = get_cashier_balance()
        await update.effective_chat.send_message(f"🏦 <b>رصيد الكاشيرة الحالي:</b> <code>{bal:.2f} NSP</code>", parse_mode="HTML")

    elif data == "adm_edit_user_bal" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_ADD_BAL_ID'
        await update.effective_chat.send_message("👤 أدخل آيدي العميل أو اسم حساب الموقع المراد تعديل رصيده:")

    elif data == "adm_set_bonus" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BONUS_AMT'
        await update.effective_chat.send_message("🎁 أدخل قيمة البونص الترحيبي الجديد بـ NSP:")

    elif data == "adm_toggle_bonus_state" and is_admin(user_id):
        curr = get_setting('welcome_bonus_enabled', '1')
        new_val = '0' if curr == '1' else '1'
        set_setting('welcome_bonus_enabled', new_val)
        txt = "❌ تم <b>تعطيل</b> البونص الترحيبي." if new_val == '0' else "✅ تم <b>تفعيل</b> البونص الترحيبي."
        await update.effective_chat.send_message(txt, parse_mode="HTML")

    elif data == "adm_set_limits" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_MIN_DEP'
        await update.effective_chat.send_message("📥 أدخل الحد الأدنى للشحن بـ NSP:")

    elif data == "adm_pay_methods" and is_admin(user_id):
        s_num = get_payment_number("سيريتل كاش")
        sh_num = get_payment_number("شام كاش")
        keyboard = [
            [InlineKeyboardButton("✏️ تعديل سيريتل كاش", callback_data="adm_edit_pay_سيريتل كاش")],
            [InlineKeyboardButton("✏️ تعديل شام كاش", callback_data="adm_edit_pay_شام كاش")],
            [InlineKeyboardButton("⚙️ لوحة الآدمن", callback_data="admin_panel")]
        ]
        await update.effective_chat.send_message(f"💳 <b>حسابات الدفع الحالية:</b>\n\n📱 سيريتل كاش: <code>{s_num}</code>\n💳 شام كاش: <code>{sh_num}</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("adm_edit_pay_") and is_admin(user_id):
        method_name = data.replace("adm_edit_pay_", "")
        context.user_data['edit_pay_method'] = method_name
        context.user_data['state'] = 'ADM_WAIT_PAY_NUMBER'
        await update.effective_chat.send_message(f"✍️ أدخل رقم/حساب {method_name} الجديد:")

    elif data == "adm_requests" and is_admin(user_id):
        conn = get_db()
        reqs = conn.execute("SELECT * FROM transactions WHERE status = 'pending' ORDER BY id DESC LIMIT 10").fetchall()
        conn.close()
        if not reqs:
            await update.effective_chat.send_message("✅ لا يوجد طلبات معلقة حالياً.")
            return
        for r in reqs:
            btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ قبول", callback_data=f"app_req_{r['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"rej_req_{r['id']}")]])
            await update.effective_chat.send_message(f"📥 <b>طلب {r['type']}</b>\n• الوسيلة: <b>{r['method']}</b>\n• العميل: <code>{r['telegram_id']}</code>\n• المبلغ: <b>{r['amount']} NSP</b>\n• الرقم: <code>{r['tx_number']}</code>", reply_markup=btns, parse_mode="HTML")

    elif data.startswith("app_req_") and is_admin(user_id):
        req_id = int(data.split("_")[2])
        conn = get_db()
        r = conn.execute("SELECT * FROM transactions WHERE id = ?", (req_id,)).fetchone()
        if r and r['status'] == 'pending':
            amt = float(r['amount'])
            user_target = r['telegram_id']
            if 'deposit' in r['type']:
                before_cashier, after_cashier = update_cashier(amt)
                conn.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (req_id,))
                conn.execute("UPDATE users SET balance = balance + ?, deposit_count = deposit_count + 1 WHERE telegram_id = ?", (amt, user_target))
                conn.commit()
                await context.bot.send_message(user_target, f"✅ <b>تم قبول طلب الشحن!</b>\nتم إضافة {amt:.2f} NSP إلى رصيدك.", parse_mode="HTML")
                await query.message.edit_text(f"✅ <b>تم قبول طلب الشحن!</b>\n🏦 الكاشيرة قبل: <code>{before_cashier:.2f}</code> | بعد: <code>{after_cashier:.2f}</code>", parse_mode="HTML")
            elif 'withdraw' in r['type']:
                before_cashier, after_cashier = update_cashier(-amt)
                conn.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (req_id,))
                conn.execute("UPDATE users SET withdraw_count = withdraw_count + 1 WHERE telegram_id = ?", (user_target,))
                conn.commit()
                await context.bot.send_message(user_target, f"✅ <b>تم قبول طلب السحب!</b>\nتم تحويل {amt:.2f} NSP بنجاح.", parse_mode="HTML")
                await query.message.edit_text(f"✅ <b>تم قبول طلب السحب!</b>\n🏦 الكاشيرة قبل: <code>{before_cashier:.2f}</code> | بعد: <code>{after_cashier:.2f}</code>", parse_mode="HTML")
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
            await context.bot.send_message(r['telegram_id'], f"❌ تم رفض طلب {r['type']} بقيمة {r['amount']} NSP وإعادة الرصيد.")
            await query.message.edit_text("❌ تم رفض الطلب وإبلاغ العميل.")
        conn.close()

    elif data == "adm_gen_batch" and is_admin(user_id):
        context.user_data['state'] = 'ADM_GIFT_AMT'
        await update.effective_chat.send_message("✍️ <b>خطوة 1/3:</b> أدخل قيمة الكود الواحد بـ NSP:", parse_mode="HTML")

    elif data == "adm_view_codes" and is_admin(user_id):
        conn = get_db()
        codes = conn.execute("SELECT * FROM gift_codes WHERE is_active = 1 AND used_count < max_uses LIMIT 20").fetchall()
        conn.close()
        if not codes:
            await update.effective_chat.send_message("❌ لا يوجد أكواد هدية مفعلة حالياً.")
            return
        txt = "🎁 <b>قائمة الأكواد المفعلة:</b>\n\n"
        for c in codes: txt += f"• الكود: <code>{c['code']}</code> | القيمة: <code>{c['amount']} NSP</code> | الاستخدام: <code>{c['used_count']}/{c['max_uses']}</code>\n"
        await update.effective_chat.send_message(txt, parse_mode="HTML")

    elif data == "adm_disable_code" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_DISABLE_CODE'
        await update.effective_chat.send_message("أدخل الكود المراد إلغاء تفعيله بالضبط:")

    elif data == "adm_edit_channels" and is_admin(user_id):
        curr = get_setting('forced_channels', '')
        context.user_data['state'] = 'ADM_WAIT_CHANNELS'
        await update.effective_chat.send_message(f"📢 <b>القنوات الحالية:</b> <code>{curr or 'لا يوجد'}</code>\n\nأدخل المعرفات مفصولة بفاصلة (مثال: <code>@chan1,@chan2</code>):", parse_mode="HTML")

    elif data == "adm_add_admin" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_NEW_ADMIN'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد إضافته كـ آدمن:")

    elif data == "adm_user_details" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_USER_DETAILS'
        await update.effective_chat.send_message("أدخل آيدي العميل أو اسم مستخدم الموقع لجلب كافة تفاصيله:")

    elif data == "adm_toggle_maint" and is_admin(user_id):
        curr = get_setting('maintenance', '0')
        new_val = '1' if curr == '0' else '0'
        set_setting('maintenance', new_val)
        await update.effective_chat.send_message("تم تفعيل وضع الصيانة 🛠" if new_val == '1' else "تم إلغاء الصيانة وتشغيل البوت 🚀")

    elif data == "adm_ban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد حظره:")

    elif data == "adm_unban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_UNBAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد إلغاء حظره:")

    elif data == "adm_broadcast" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BROADCAST'
        await update.effective_chat.send_message("📢 أدخل النص المراد إرساله لجميع المستخدمين:")

    elif data == "adm_private_msg" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_PRIV_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد مراسلته بشكل خاص:")

    elif data == "adm_stats" and is_admin(user_id):
        conn = get_db()
        tot = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        bal = conn.execute("SELECT SUM(balance) FROM users").fetchone()[0] or 0.0
        s_bal = conn.execute("SELECT SUM(site_balance) FROM users").fetchone()[0] or 0.0
        active_today = conn.execute("SELECT COUNT(*) FROM users WHERE datetime(last_active) >= datetime('now', '-1 day')").fetchone()[0]
        conn.close()
        await update.effective_chat.send_message(f"📊 <b>إحصائيات المنصة والبوت:</b>\n\n• إجمالي المسجلين: <code>{tot}</code>\n• النشطين 24 ساعة: <code>{active_today}</code>\n• أرصدة البوت: <code>{bal:.2f} NSP</code>\n• أرصدة الموقع: <code>{s_bal:.2f} NSP</code>", parse_mode="HTML")

    elif data.startswith("reply_support_") and is_admin(user_id):
        target = int(data.split("_")[2])
        context.user_data['support_target'] = target
        context.user_data['state'] = 'WAIT_ADMIN_REPLY_SUPP'
        await update.effective_chat.send_message(f"💬 اكتب الرد للعميل <code>{target}</code>:", parse_mode="HTML")

    elif data == "main_menu":
        await show_main_menu(update, context)

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bonus_state = "مفعل ✅" if get_setting('welcome_bonus_enabled', '1') == '1' else "معطل ❌"
    keyboard = [
        [InlineKeyboardButton("🏦 رصيد الكاشيرة", callback_data="adm_cashier"), InlineKeyboardButton("📥📤 طلبات الشحن والسحب", callback_data="adm_requests")],
        [InlineKeyboardButton("🎮 إعدادات لعبة الحظ", callback_data="adm_game_settings"), InlineKeyboardButton("🎡 منح لفات لعميل", callback_data="adm_grant_spins")],
        [InlineKeyboardButton("💳 تعديل حسابات الدفع", callback_data="adm_pay_methods"), InlineKeyboardButton("💰 تعديل رصيد مستخدم", callback_data="adm_edit_user_bal")],
        [InlineKeyboardButton(f"🎁 حالة البونص ({bonus_state})", callback_data="adm_toggle_bonus_state"), InlineKeyboardButton("🎁 قيمة البونص الترحيبي", callback_data="adm_set_bonus")],
        [InlineKeyboardButton("📉 تعديل حدود الشحن والسحب", callback_data="adm_set_limits")],
        [InlineKeyboardButton("🎁 توليد أكواد هدية", callback_data="adm_gen_batch"), InlineKeyboardButton("📋 الأكواد النشطة", callback_data="adm_view_codes")],
        [InlineKeyboardButton("❌ إلغاء تفعيل كود", callback_data="adm_disable_code"), InlineKeyboardButton("📢 قنوات الاشتراك الإجباري", callback_data="adm_edit_channels")],
        [InlineKeyboardButton("🔍 تفاصيل عميل", callback_data="adm_user_details"), InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats")],
        [InlineKeyboardButton("🛠 وضع الصيانة", callback_data="adm_toggle_maint"), InlineKeyboardButton("👑 إضافة آدمن جديد", callback_data="adm_add_admin")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_user"), InlineKeyboardButton("✅ إلغاء حظر مستخدم", callback_data="adm_unban_user")],
        [InlineKeyboardButton("📢 إذاعة عامة (Broadcast)", callback_data="adm_broadcast"), InlineKeyboardButton("💬 رسالة خاصة لعميل", callback_data="adm_private_msg")],
        [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
    ]
    await update.effective_chat.send_message("⚙️ <b>لوحة التحكم الإدارية الكاملة (الآدمن):</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ==========================================================
# 5. معالج النصوص والرسائل والاستجابة للأكواد
# ==========================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    state = context.user_data.get('state')

    # الاستجابة الفورية والذكية للأكواد حتى دون وجود حالة سابقة
    if not state:
        if text.upper().startswith("GIFT-") or (len(text) >= 6 and text.isalnum() and not text.isdigit()):
            state = 'WAIT_GIFT_CODE'
        else: return

    conn = get_db()
    cursor = conn.cursor()

    try:
        if state == 'WAIT_SITE_USER':
            if not validate_username(text):
                await update.message.reply_text("❌ اسم المستخدم غير صالح! يجب أن يتكون من 6 أحرف/أرقام إنجليزية على الأقل وبدون رموز وخالٍ من المسافات.")
                conn.close()
                return
            check = cursor.execute("SELECT telegram_id FROM users WHERE site_username = ?", (text,)).fetchone()
            if check:
                await update.message.reply_text("❌ اسم المستخدم محجوز لعميل آخر!")
                conn.close()
                return
            context.user_data['temp_site_user'] = text
            context.user_data['state'] = 'WAIT_SITE_PASS'
            await update.message.reply_text("🔑 <b>الخطوة الأخيرة:</b> أدخل كلمة المرور (6 أحرف وأرقام إنجليزية على الأقل):", parse_mode="HTML")
            conn.close()
            return

        elif state == 'WAIT_SITE_PASS':
            if not validate_password(text):
                await update.message.reply_text("❌ كلمة المرور ضعيفة! يجب أن تكون 6 خانات وتحتوي على أحرف وأرقام معاً.")
                conn.close()
                return
            username = context.user_data.get('temp_site_user')
            password = text
            u_info = cursor.execute("SELECT referred_by FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?", (username, password, user_id))
            if u_info and u_info['referred_by']:
                ref_id = u_info['referred_by']
                cursor.execute("UPDATE users SET spins_count = spins_count + 1 WHERE telegram_id = ?", (ref_id,))
                conn.commit()
                try: await context.bot.send_message(ref_id, "🎉 <b>ربحت فرصة لعب مجانية!</b>\nقام صديقك بإنشاء حساب بالموقع بنجاح!", parse_mode="HTML")
                except Exception: pass
            else: conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(f"✅ <b>تم إنشاء حسابك بنجاح!</b>\n\n👤 اسم المستخدم: <code>{html.escape(username)}</code>\n🔑 كلمة المرور: <code>{html.escape(password)}</code>", parse_mode="HTML")
            await show_main_menu(update, context)
            return

        elif state == 'WAIT_TRANSFER_TO_SITE':
            try: amt = float(text); if amt <= 0: raise ValueError
            except ValueError: await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!"); conn.close(); return
            u = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            if u['balance'] < amt: await update.message.reply_text("❌ رصيدك غير كافٍ!"); conn.close(); return
            cursor.execute("UPDATE users SET balance = balance - ?, site_balance = site_balance + ? WHERE telegram_id = ?", (amt, amt, user_id))
            conn.commit(); conn.close(); context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحويل <b>{amt:.2f} NSP</b> بنجاح إلى حسابك بالموقع!", parse_mode="HTML")
            await show_main_menu(update, context)
            return

        elif state == 'WAIT_TRANSFER_FROM_SITE':
            try: amt = float(text); if amt <= 0: raise ValueError
            except ValueError: await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!"); conn.close(); return
            u = cursor.execute("SELECT site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            if u['site_balance'] < amt: await update.message.reply_text("❌ رصيدك في الموقع غير كافٍ!"); conn.close(); return
            cursor.execute("UPDATE users SET site_balance = site_balance - ?, balance = balance + ? WHERE telegram_id = ?", (amt, amt, user_id))
            conn.commit(); conn.close(); context.user_data.clear()
            await update.message.reply_text(f"↩️ تم سحب <b>{amt:.2f} NSP</b> بنجاح إلى البوت!", parse_mode="HTML")
            await show_main_menu(update, context)
            return

        elif state == 'WAIT_DEP_AMT':
            try:
                amt = float(text)
                min_dep = float(get_setting('min_deposit', '50'))
                if amt < min_dep: await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى ({min_dep} NSP)!"); conn.close(); return
            except ValueError: await update.message.reply_text("❌ أدخل رقماً صحيحاً!"); conn.close(); return
            context.user_data['dep_amt'] = amt
            context.user_data['state'] = 'WAIT_DEP_TX'
            method = context.user_data.get('selected_method')
            acc_num = get_payment_number(method)
            await update.message.reply_text(f"✍️ قم بتحويل مبلغ <b>{amt:.2f} NSP</b> إلى رقم الحساب <code>{acc_num}</code> ({method}).\n\nثم أرسل رقم العملية هنا للتأكيد:", parse_mode="HTML")
            conn.close(); return

        elif state == 'WAIT_DEP_TX':
            amt = context.user_data.get('dep_amt')
            method = context.user_data.get('selected_method')
            cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'deposit', ?, ?, ?)", (user_id, method, amt, text))
            conn.commit(); conn.close(); context.user_data.clear()
            await update.message.reply_text("✅ <b>تم إرسال طلب الشحن بنجاح!</b>", parse_mode="HTML")
            await send_all_admins(context, f"📥 <b>طلب شحن جديد!</b>\n• العميل: <code>{user_id}</code>\n• الوسيلة: <b>{method}</b>\n• المبلغ: <b>{amt:.2f} NSP</b>\n• الرقم: <code>{html.escape(text)}</code>")
            await show_main_menu(update, context); return

        elif state == 'WAIT_WITH_AMT':
            try:
                amt = float(text)
                min_with = float(get_setting('min_withdraw', '100'))
                if amt < min_with: await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى ({min_with} NSP)!"); conn.close(); return
            except ValueError: await update.message.reply_text("❌ أدخل رقماً صحيحاً!"); conn.close(); return
            u = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            if u['balance'] < amt: await update.message.reply_text("❌ رصيدك غير كافٍ!"); conn.close(); return
            context.user_data['with_amt'] = amt
            context.user_data['state'] = 'WAIT_WITH_ACC'
            await update.message.reply_text("✍️ أرسل رقم حسابك / محفظتك لاستلام المبلغ:")
            conn.close(); return

        elif state == 'WAIT_WITH_ACC':
            amt = context.user_data.get('with_amt')
            method = context.user_data.get('selected_method')
            cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amt, user_id))
            cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'withdraw', ?, ?, ?)", (user_id, method, amt, text))
            conn.commit(); conn.close(); context.user_data.clear()
            await update.message.reply_text("✅ <b>تم إرسال طلب السحب بنجاح!</b>", parse_mode="HTML")
            await send_all_admins(context, f"📤 <b>طلب سحب جديد!</b>\n• العميل: <code>{user_id}</code>\n• الوسيلة: <b>{method}</b>\n• المبلغ: <b>{amt:.2f} NSP</b>\n• الرقم: <code>{html.escape(text)}</code>")
            await show_main_menu(update, context); return

        elif state == 'WAIT_GIFT_CODE':
            code_clean = text.strip()
            code_obj = cursor.execute("SELECT * FROM gift_codes WHERE UPPER(code) = UPPER(?) AND is_active = 1", (code_clean,)).fetchone()
            if not code_obj or code_obj['used_count'] >= code_obj['max_uses']:
                await update.message.reply_text("❌ كود غير صحيح أو منتهي الفعالية!")
                conn.close(); return
            used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND UPPER(code) = UPPER(?)", (user_id, code_clean)).fetchone()
            if used:
                await update.message.reply_text("❌ لقد استخدمت هذا الكود سابقاً!")
                conn.close(); return
            amt = float(code_obj['amount'])
            actual_code = code_obj['code']
            new_used_count = code_obj['used_count'] + 1
            is_active = 0 if new_used_count >= code_obj['max_uses'] else 1
            cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (user_id, actual_code))
            cursor.execute("UPDATE gift_codes SET used_count = ?, is_active = ? WHERE code = ?", (new_used_count, is_active, actual_code))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amt, user_id))
            conn.commit(); conn.close(); context.user_data.clear()
            await update.message.reply_text(f"🎉 <b>تم شحن الكود بنجاح!</b>\nإضافة <b>+{amt:.2f} NSP</b> إلى رصيد بوتك.", parse_mode="HTML")
            await send_all_admins(context, f"🎁 <b>استخدام كود هدية:</b>\n• العميل: <code>{user_id}</code>\n• الكود: <code>{actual_code}</code>\n• القيمة: <b>{amt:.2f} NSP</b>")
            await show_main_menu(update, context); return

        elif state == 'WAIT_SUPPORT':
            await send_all_admins(context, f"💬 <b>رسالة دعم جديدة!</b>\n• العميل: <code>{user_id}</code>\n\nالرسالة:\n{html.escape(text)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 الرد على العميل", callback_data=f"reply_support_{user_id}")] introduce]]))
            conn.close(); context.user_data.clear()
            await update.message.reply_text("✅ تم إرسال رسالتك إلى الدعم بنجاح.")
            await show_main_menu(update, context); return

        elif is_admin(user_id):
            if state == 'ADM_WAIT_WIN_RATE':
                set_setting('game_win_rate', text)
                await update.message.reply_text(f"🎯 تم تعديل نسبة الفوز إلى: <b>{text}%</b>", parse_mode="HTML")
            elif state == 'ADM_WAIT_SLICE_WEIGHT_AMT':
                val = context.user_data.get('target_slice_val')
                weights_raw = get_setting('wheel_weights', '{}')
                try: w_dict = json.loads(weights_raw)
                except Exception: w_dict = {}
                w_dict[str(val)] = float(text)
                set_setting('wheel_weights', json.dumps(w_dict))
                await update.message.reply_text(f"✅ تم تعديل وزن الجائزة <b>{val} NSP</b> إلى <code>{text}</code>", parse_mode="HTML")
            elif state == 'ADM_WAIT_SPINS_USER_ID':
                u = cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ? OR site_username = ?", (int(text) if text.isdigit() else 0, text)).fetchone()
                if not u: await update.message.reply_text("❌ العميل غير موجود!"); conn.close(); return
                context.user_data['target_spins_user'] = u['telegram_id']
                context.user_data['state'] = 'ADM_WAIT_SPINS_COUNT'
                await update.message.reply_text("✍️ أدخل عدد اللفات المراد إضافتها:")
                conn.close(); return
            elif state == 'ADM_WAIT_SPINS_COUNT':
                cnt = int(text)
                t_user = context.user_data.get('target_spins_user')
                cursor.execute("UPDATE users SET spins_count = spins_count + ? WHERE telegram_id = ?", (cnt, t_user))
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة <b>{cnt}</b> محاولة للعميل <code>{t_user}</code>", parse_mode="HTML")
            elif state == 'ADM_WAIT_ADD_BAL_ID':
                u = cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ? OR site_username = ?", (int(text) if text.isdigit() else 0, text)).fetchone()
                if not u: await update.message.reply_text("❌ العميل غير موجود!"); conn.close(); return
                context.user_data['target_adm_user'] = u['telegram_id']
                context.user_data['state'] = 'ADM_WAIT_ADD_BAL_AMT'
                await update.message.reply_text("✍️ أدخل المبلغ المراد إضافته (+) أو خصمه (-):")
                conn.close(); return
            elif state == 'ADM_WAIT_ADD_BAL_AMT':
                amt = float(text)
                t_user = context.user_data.get('target_adm_user')
                before_cashier, after_cashier = update_cashier(-amt)
                cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amt, t_user))
                conn.commit()
                await update.message.reply_text(f"✅ تم تعديل رصيد العميل <code>{t_user}</code> بمقدار {amt:+.2f} NSP\n🏦 الكاشيرة قبل: {before_cashier:.2f} | بعد: {after_cashier:.2f}", parse_mode="HTML")
            elif state == 'ADM_WAIT_BONUS_AMT':
                set_setting('welcome_bonus', text)
                await update.message.reply_text(f"✅ تم تعديل البونص الترحيبي إلى: <b>{text} NSP</b>", parse_mode="HTML")
            elif state == 'ADM_WAIT_MIN_DEP':
                set_setting('min_deposit', text)
                context.user_data['state'] = 'ADM_WAIT_MIN_WITH'
                await update.message.reply_text("✍️ أدخل الحد الأدنى للسحب الآن:")
                conn.close(); return
            elif state == 'ADM_WAIT_MIN_WITH':
                set_setting('min_withdraw', text)
                await update.message.reply_text(f"✅ تم تحديث حدود العمليات.")
            elif state == 'ADM_WAIT_PAY_NUMBER':
                m_name = context.user_data.get('edit_pay_method')
                cursor.execute("UPDATE payment_methods SET number = ? WHERE name = ?", (text, m_name))
                conn.commit()
                await update.message.reply_text(f"✅ تم تحديث رقم {m_name} إلى: <code>{text}</code>", parse_mode="HTML")
            elif state == 'ADM_GIFT_AMT':
                context.user_data['gift_amt'] = float(text)
                context.user_data['state'] = 'ADM_GIFT_COUNT'
                await update.message.reply_text("✍️ أدخل عدد الأكواد:")
                conn.close(); return
            elif state == 'ADM_GIFT_COUNT':
                context.user_data['gift_count'] = int(text)
                context.user_data['state'] = 'ADM_GIFT_USES'
                await update.message.reply_text("✍️ أدخل مرات الاستخدام لكل كود:")
                conn.close(); return
            elif state == 'ADM_GIFT_USES':
                uses = int(text)
                amt, count = context.user_data.get('gift_amt'), context.user_data.get('gift_count')
                total_val = amt * count * uses
                before_cashier, after_cashier = update_cashier(-total_val)
                generated = []
                for _ in range(count):
                    c_str = "GIFT-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                    cursor.execute("INSERT INTO gift_codes (code, amount, max_uses) VALUES (?, ?, ?)", (c_str, amt, uses))
                    generated.append(c_str)
                conn.commit()
                txt = f"✅ <b>تم توليد الأكواد بنجاح وخصم {total_val:.2f} NSP من الكاشيرة!</b>\n\n"
                for code in generated: txt += f"• <code>{code}</code> ({amt} NSP)\n"
                await update.message.reply_text(txt, parse_mode="HTML")
            elif state == 'ADM_WAIT_DISABLE_CODE':
                cursor.execute("UPDATE gift_codes SET is_active = 0 WHERE code = ?", (text.strip(),))
                conn.commit()
                await update.message.reply_text("✅ تم إلغاء تفعيل الكود بنجاح.")
            elif state == 'ADM_WAIT_CHANNELS':
                set_setting('forced_channels', text)
                await update.message.reply_text("✅ تم تحديث قنوات الاشتراك الإجباري.")
            elif state == 'ADM_WAIT_NEW_ADMIN':
                cursor.execute("UPDATE users SET is_admin = 1 WHERE telegram_id = ?", (int(text),))
                conn.commit()
                await update.message.reply_text("✅ تم إضافة الآدمن الجديد.")
            elif state == 'ADM_WAIT_USER_DETAILS':
                u = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR site_username = ?", (int(text) if text.isdigit() else 0, text)).fetchone()
                if u: await update.message.reply_text(f"👤 <b>تفاصيل العميل:</b>\n🆔 <code>{u['telegram_id']}</code>\n🌐 حساب الموقع: <code>{u['site_username']}</code>\n💰 رصيد البوت: <b>{u['balance']}</b>\n💎 رصيد الموقع: <b>{u['site_balance']}</b>", parse_mode="HTML")
                else: await update.message.reply_text("❌ غير موجود.")
            elif state == 'ADM_WAIT_BAN_ID':
                cursor.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (int(text),))
                conn.commit()
                await update.message.reply_text("🚫 تم الحظر.")
            elif state == 'ADM_WAIT_UNBAN_ID':
                cursor.execute("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (int(text),))
                conn.commit()
                await update.message.reply_text("✅ تم فك الحظر.")
            elif state == 'ADM_WAIT_BROADCAST':
                users = cursor.execute("SELECT telegram_id FROM users WHERE is_banned = 0").fetchall()
                for u in users:
                    try: await context.bot.send_message(u['telegram_id'], text, parse_mode="HTML")
                    except Exception: pass
                await update.message.reply_text("📢 اكتملت الإذاعة بنجاح.")
            elif state == 'ADM_WAIT_PRIV_ID':
                context.user_data['target_priv_id'] = int(text)
                context.user_data['state'] = 'ADM_WAIT_PRIV_MSG'
                await update.message.reply_text("✍️ اكتب الرسالة:")
                conn.close(); return
            elif state == 'ADM_WAIT_PRIV_MSG':
                t_id = context.user_data.get('target_priv_id')
                await context.bot.send_message(t_id, f"📩 <b>رسالة خاصة من الإدارة:</b>\n\n{text}", parse_mode="HTML")
                await update.message.reply_text("✅ تم الإرسال.")
            elif state == 'WAIT_ADMIN_REPLY_SUPP':
                target = context.user_data.get('support_target')
                await context.bot.send_message(target, f"💬 <b>رد الدعم الفني:</b>\n\n{text}", parse_mode="HTML")
                await update.message.reply_text("✅ تم إرسال الرد للعميل.")

            context.user_data.clear()
            conn.close()
            return

    except Exception as e:
        logging.error(f"Error: {e}")
        try: conn.close()
        except Exception: pass

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get('state')
    if state in ['WAIT_WIN_SHOT', 'WAIT_SUPPORT']:
        photo = update.message.photo[-1]
        caption = update.message.caption or ""
        context.user_data.clear()
        await send_all_admins(context, f"📸 <b>صورة جديدة من عميل:</b> <code>{user_id}</code>\n{html.escape(caption)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 الرد", callback_data=f"reply_support_{user_id}")]]))
        conn = get_db()
        admins = conn.execute("SELECT telegram_id FROM users WHERE is_admin = 1").fetchall()
        conn.close()
        for aid in set([a['telegram_id'] for a in admins] + [MAIN_ADMIN_ID]):
            try: await context.bot.send_photo(aid, photo.file_id)
            except Exception: pass
        await update.message.reply_text("✅ تم إرسال الصورة بنجاح.")
        await show_main_menu(update, context)

# ==========================================================
# 6. التشغيل الرئيسي (Main Execution)
# ==========================================================
def main():
    global MAIN_LOOP, bot_app
    init_db()

    server_thread = threading.Thread(target=start_health_check_server, daemon=True)
    server_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()
    bot_app = application

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.get_event_loop()
    MAIN_LOOP = loop

    logging.info("🚀 AUREX Bot Started Successfully...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
