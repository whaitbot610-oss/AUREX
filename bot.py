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
MAIN_LOOP = None  # مرجع حلقة الأحداث الرئيسية لإرسال الإشعارات

HTML_WHEEL_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>AUREX Lucky Wheel</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: #0d0d0d; color: #ffffff; text-align: center; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 15px; }
        h1 { color: #e50914; font-size: 26px; margin-bottom: 5px; text-shadow: 0 0 10px rgba(229,9,20,0.5); }
        p.subtitle { font-size: 14px; color: #aaa; margin-bottom: 20px; }
        .wheel-container { position: relative; width: 320px; height: 320px; margin: 10px auto; }
        #canvas { width: 320px; height: 320px; border-radius: 50%; box-shadow: 0 0 25px rgba(229, 9, 20, 0.4); border: 4px solid #d4af37; }
        .pointer { position: absolute; top: -15px; left: 50%; transform: translateX(-50%); width: 0; height: 0; border-left: 15px solid transparent; border-right: 15px solid transparent; border-top: 30px solid #d4af37; z-index: 10; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.8)); }
        .center-btn { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 65px; height: 65px; background: radial-gradient(circle, #e50914, #800000); border: 3px solid #d4af37; border-radius: 50%; color: #fff; font-weight: bold; font-size: 16px; cursor: pointer; z-index: 5; box-shadow: 0 0 15px rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center; }
        .info-card { background: #1a1a1a; border: 1px solid #333; border-radius: 12px; padding: 12px 20px; width: 100%; max-width: 320px; margin-top: 20px; display: flex; justify-content: space-around; }
        .info-item { display: flex; flex-direction: column; }
        .info-item span.label { font-size: 12px; color: #888; }
        .info-item span.val { font-size: 18px; font-weight: bold; color: #d4af37; }
        #result-modal { margin-top: 15px; font-size: 16px; font-weight: bold; min-height: 25px; color: #e74c3c; }
    </style>
</head>
<body>
    <h1>🎡 عجلة الحظ AUREX</h1>
    <p class="subtitle">ادر العجلة واكسب جوائز فورية!</p>

    <div class="wheel-container">
        <div class="pointer"></div>
        <canvas id="canvas" width="320" height="320"></canvas>
        <button class="center-btn" id="spinBtn" onclick="startSpin()">Spin</button>
    </div>

    <div id="result-modal"></div>

    <div class="info-card">
        <div class="info-item">
            <span class="label">المحاولات</span>
            <span class="val" id="spinsCount">--</span>
        </div>
        <div class="info-item">
            <span class="label">رصيد البوت</span>
            <span class="val" id="userBal">--</span>
        </div>
    </div>

    <script>
        const tg = window.Telegram ? window.Telegram.WebApp : null;
        if(tg) tg.expand();

        const urlParams = new URLSearchParams(window.location.search);
        let userId = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) ? tg.initDataUnsafe.user.id : null;
        if (!userId) {
            userId = urlParams.get("telegram_id") || urlParams.get("user_id");
        }
        if (userId === "null" || userId === "undefined") userId = null;

        const values = [0, 5, 10, 15, 25, 50, 100, 500, 10000];
        const numSlices = values.length;
        const canvas = document.getElementById("canvas");
        const ctx = canvas.getContext("2d");
        let currentAngle = 0;
        let isSpinning = false;

        function drawWheel() {
            const radius = 160;
            const sliceAngle = (2 * Math.PI) / numSlices;

            ctx.clearRect(0, 0, canvas.width, canvas.height);

            for (let i = 0; i < numSlices; i++) {
                const angle = currentAngle + i * sliceAngle;
                ctx.beginPath();
                ctx.moveTo(radius, radius);
                ctx.arc(radius, radius, radius, angle, angle + sliceAngle);
                ctx.closePath();

                ctx.fillStyle = (i % 2 === 0) ? "#141414" : "#e50914";
                ctx.fill();
                ctx.strokeStyle = "#d4af37";
                ctx.lineWidth = 1.5;
                ctx.stroke();

                ctx.save();
                ctx.translate(radius, radius);
                ctx.rotate(angle + sliceAngle / 2);
                ctx.textAlign = "right";
                ctx.fillStyle = "#ffffff";
                ctx.font = "bold 13px Arial";
                ctx.fillText(values[i] + " NSP", radius - 15, 5);
                ctx.restore();
            }
        }

        async function fetchUserData() {
            if(!userId) {
                document.getElementById("result-modal").innerText = "⚠️ تعذر التعرف على حسابك، افتح العجلة من داخل البوت مباشرة.";
                return;
            }
            try {
                const res = await fetch('/api/user_info?telegram_id=' + userId + '&user_id=' + userId);
                const data = await res.json();
                if(data.status === 'ok') {
                    document.getElementById("spinsCount").innerText = data.spins;
                    document.getElementById("userBal").innerText = data.balance.toFixed(2) + " NSP";
                    document.getElementById("result-modal").innerText = "";
                } else {
                    document.getElementById("result-modal").innerText = "❌ " + (data.message || "خطأ في جلب البيانات");
                }
            } catch(e) {
                document.getElementById("result-modal").innerText = "❌ تعذر الاتصال بالسيرفر";
            }
        }

        async function startSpin() {
            if(isSpinning || !userId) return;
            isSpinning = true;
            document.getElementById("spinBtn").disabled = true;
            document.getElementById("result-modal").innerText = "";

            try {
                const res = await fetch('/api/spin', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ telegram_id: userId, user_id: userId })
                });
                const data = await res.json();

                if(data.status !== 'ok') {
                    document.getElementById("result-modal").innerText = "❌ " + (data.message || "عذراً، حدث خطأ!");
                    isSpinning = false;
                    document.getElementById("spinBtn").disabled = false;
                    return;
                }

                const targetIndex = data.prize_index;
                const sliceAngle = (2 * Math.PI) / numSlices;
                
                const targetAngle = (1.5 * Math.PI) - (targetIndex * sliceAngle) - (sliceAngle / 2);
                const extraRounds = 6 * 2 * Math.PI;
                const startAngle = currentAngle;
                const normalizedCurrent = currentAngle % (2 * Math.PI);
                let diff = targetAngle - normalizedCurrent;
                if (diff < 0) diff += 2 * Math.PI;
                const finalAngle = startAngle + extraRounds + diff;

                let startTimestamp = null;
                const duration = 4500;

                function animate(timestamp) {
                    if (!startTimestamp) startTimestamp = timestamp;
                    const elapsed = timestamp - startTimestamp;
                    const progress = Math.min(elapsed / duration, 1);
                    const easeOut = 1 - Math.pow(1 - progress, 3);

                    currentAngle = startAngle + (finalAngle - startAngle) * easeOut;
                    drawWheel();

                    if (progress < 1) {
                        requestAnimationFrame(animate);
                    } else {
                        currentAngle = finalAngle;
                        drawWheel();
                        isSpinning = false;
                        document.getElementById("spinBtn").disabled = false;
                        document.getElementById("spinsCount").innerText = data.remaining_spins;
                        document.getElementById("userBal").innerText = data.new_balance.toFixed(2) + " NSP";

                        if(data.prize_value > 0) {
                            document.getElementById("result-modal").innerHTML = `<span style="color:#2ecc71;">🎉 مبروك! فزت بـ ${data.prize_value} NSP</span>`;
                        } else {
                            document.getElementById("result-modal").innerHTML = `<span style="color:#e74c3c;">💔 حظ أوفر في المرة القادمة!</span>`;
                        }
                    }
                }
                requestAnimationFrame(animate);

            } catch(e) {
                isSpinning = false;
                document.getElementById("spinBtn").disabled = false;
                document.getElementById("result-modal").innerText = "❌ حدث خطأ في الاتصال بالشبكة";
            }
        }

        drawWheel();
        fetchUserData();
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
        elif parsed_path.path == "/api/user_info":
            qs = parse_qs(parsed_path.query)
            user_id_raw = qs.get('telegram_id', [None])[0] or qs.get('user_id', [None])[0]
            
            if user_id_raw and str(user_id_raw).isdigit():
                user_id = int(user_id_raw)
                conn = get_db()
                u = conn.execute("SELECT spins_count, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
                conn.close()
                if u:
                    res = {"status": "ok", "spins": u['spins_count'], "balance": u['balance']}
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
            users = conn.execute("SELECT telegram_id, site_username, site_balance FROM users WHERE site_username IS NOT NULL").fetchall()
            conn.close()
            data = [dict(u) for u in users]
            self._send_json(data)
        else:
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"OK - AUREX BOT IS RUNNING")

    def do_POST(self):
        if self.path == "/api/register":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                self._send_json({"status": "ok", "message": "Registered locally"})
            except Exception:
                self._send_json({"status": "error"})
            return

        elif self.path == "/api/spin":
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
                u = cursor.execute("SELECT spins_count, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
                
                if not u or u['spins_count'] <= 0:
                    conn.close()
                    self._send_json({"status": "error", "message": "ليس لديك محاولات لعب كافية!"})
                    return

                cursor.execute("UPDATE users SET spins_count = spins_count - 1 WHERE telegram_id = ?", (user_id,))
                
                win_rate = float(get_setting('game_win_rate', '30'))
                cashier_bal = get_cashier_balance()
                
                weights_raw = get_setting('wheel_weights', '')
                try:
                    w_dict = json.loads(weights_raw) if weights_raw else {}
                except Exception:
                    w_dict = {}

                roll = random.uniform(0, 100)
                prize = 0
                possible_prizes = [v for v in WHEEL_VALUES if v > 0 and v <= cashier_bal]
                
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
                    "status": "ok",
                    "prize_index": prize_index,
                    "prize_value": prize,
                    "remaining_spins": rem_spins,
                    "new_balance": new_bal
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
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

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

async def register_account_to_site_api_async(username, password, telegram_id):
    def _send():
        try:
            url = f"{SERVER_URL}/api/register"
            payload = json.dumps({
                "site_username": username,
                "site_password": password,
                "telegram_id": telegram_id
            }).encode('utf-8')
            
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            logging.warning(f"Note: Site API sync ({e}), local database managed.")
            return False
            
    return await asyncio.to_thread(_send)

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
        telegram_id INTEGER PRIMARY KEY, 
        username TEXT, 
        site_username TEXT UNIQUE, 
        site_password TEXT, 
        balance REAL DEFAULT 0.0,
        site_balance REAL DEFAULT 0.0,
        total_spent REAL DEFAULT 0.0,
        deposit_count INTEGER DEFAULT 0,
        withdraw_count INTEGER DEFAULT 0,
        referrals_count INTEGER DEFAULT 0,
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
        'username': 'TEXT',
        'site_username': 'TEXT',
        'site_password': 'TEXT',
        'balance': 'REAL DEFAULT 0.0',
        'site_balance': 'REAL DEFAULT 0.0',
        'total_spent': 'REAL DEFAULT 0.0',
        'deposit_count': 'INTEGER DEFAULT 0',
        'withdraw_count': 'INTEGER DEFAULT 0',
        'referrals_count': 'INTEGER DEFAULT 0',
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

    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        telegram_id INTEGER, 
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
        is_active INTEGER DEFAULT 1
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_codes (
        telegram_id INTEGER, 
        code TEXT, 
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

    defaults = [
        ('maintenance', '0'),
        ('welcome_bonus', '500'),
        ('welcome_bonus_enabled', '1'),
        ('min_deposit', '50'),
        ('min_withdraw', '100'),
        ('cashier_balance', '10000.0'),
        ('forced_channels', ''),
        ('game_win_rate', '30'),
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
    if user_id == MAIN_ADMIN_ID:
        return True
    conn = get_db()
    row = conn.execute("SELECT is_admin FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row and row['is_admin'])

def update_cashier(amount_change):
    conn = get_db()
    cursor = conn.cursor()
    row_before = cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'").fetchone()
    before_balance = float(row_before['value']) if row_before else 0.0
    
    cursor.execute(
        "UPDATE settings SET value = CAST(MAX(0.0, CAST(value AS REAL) + ?) AS TEXT) WHERE key = 'cashier_balance'", 
        (amount_change,)
    )
    row_after = cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'").fetchone()
    after_balance = float(row_after['value']) if row_after else 0.0
    
    conn.commit()
    conn.close()
    return before_balance, after_balance

def get_cashier_balance():
    return float(get_setting('cashier_balance', '0.0'))

def get_payment_number(method_name):
    conn = get_db()
    row = conn.execute("SELECT number FROM payment_methods WHERE name = ?", (method_name,)).fetchone()
    conn.close()
    return row['number'] if row else "غير متوفر"

def validate_username(username): 
    return len(username) >= 6 and bool(re.match(r'^[a-zA-Z0-9_]+$', username))

def validate_password(password): 
    return (len(password) >= 6 and 
            bool(re.search(r'[a-zA-Z]', password)) and 
            bool(re.search(r'\d', password)))

async def check_forced_sub(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels_str = get_setting('forced_channels', '')
    if not channels_str: 
        return True
    channels = [c.strip() for c in channels_str.split(',') if c.strip()]
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
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
            cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE telegram_id = ?", (ref_by,))
            conn.commit()
            try:
                await context.bot.send_message(ref_by, f"🎉 <b>انضم عميل جديد عبر رابط إحالتك!</b>\n🆔 العميل: <code>{html.escape(user.first_name or '')}</code>\n📌 سيتم منحك فرصة لعب فورية بمجرد إنشاء هذا العميل لحسابه بالموقع!", parse_mode="HTML")
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
    bot_bal = db_user['balance'] if db_user else 0.0
    site_bal = db_user['site_balance'] if db_user else 0.0
    spins = db_user['spins_count'] if db_user else 0

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
        bonus_enabled = get_setting('welcome_bonus_enabled', '1') == '1'
        bonus_amt = float(get_setting('welcome_bonus', '500.0'))
        
        if bonus_enabled and bonus_amt > 0 and user and user['got_welcome_bonus'] == 0:
            before_cashier, after_cashier = update_cashier(-bonus_amt)
            cursor.execute("UPDATE users SET security_passed = 1, got_welcome_bonus = 1, balance = balance + ? WHERE telegram_id = ?", (bonus_amt, user_id))
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

    if data == "cancel_action":
        context.user_data.clear()
        await query.message.delete()
        await show_main_menu(update, context)
        return

    if data == "check_sub":
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
            await update.effective_chat.send_message(f"⚠️ <b>تنبيه:</b> لديك حساب شخصي سابق بالفعل باسم: <code>{html.escape(u['site_username'])}</code>\nلا يُسمح للعميل بإنشاء أكثر من حساب واحد.", parse_mode="HTML")
            return

        context.user_data['state'] = 'WAIT_SITE_USER'
        keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]
        await update.effective_chat.send_message(
            "🔑 <b>إنشاء حساب جديد للموقع:</b>\n\n"
            "✍️ أدخل اسم المستخدم الجديد (يتكون من 6 أحرف/أرقام إنجليزية على الأقل وبدون رموز):",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data == "transfer_to_site":
        conn = get_db()
        u = conn.execute("SELECT site_username, balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ يجب إنشاء حساب على الموقع أولاً!", parse_mode="HTML")
            return
        
        context.user_data['state'] = 'WAIT_TRANSFER_TO_SITE'
        await update.effective_chat.send_message(
            f"🔄 <b>شحن رصيد للموقع:</b>\n"
            f"💰 رصيد البوت المتوفر: <b>{u['balance']:.2f} NSP</b>\n\n"
            f"✍️ أدخل المبلغ المراد تحويله من البوت إلى حسابك بالموقع:",
            parse_mode="HTML"
        )

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
            f"✍️ أدخل المبلغ المراد سحبه من الموقع إلى رصيد البوت:",
            parse_mode="HTML"
        )

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
        
        await update.effective_chat.send_message(
            f"💳 <b>طريقة الشحن:</b> {method_name}\n"
            f"📌 <b>رقم الحساب للتحويل:</b> <code>{acc_num}</code>\n"
            f"⚠️ <b>الحد الأدنى:</b> <code>{min_dep} NSP</code>\n\n"
            f"✍️ <b>الخطوة الأولى:</b> أرسل المبلغ المراد شحنه بعملة NSP الآن:",
            parse_mode="HTML"
        )

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
        
        await update.effective_chat.send_message(
            f"📤 <b>طريقة السحب:</b> {method_name}\n"
            f"📌 <b>الحد الأدنى للسحب:</b> <code>{min_with} NSP</code>\n\n"
            f"✍️ <b>الخطوة الأولى:</b> أرسل المبلغ المراد سحبه بعملة NSP من رصيد البوت:",
            parse_mode="HTML"
        )

    elif data == "my_ref":
        me = await context.bot.get_me()
        await update.effective_chat.send_message(f"🔗 <b>رابط إحالتي الشخصي:</b>\n<code>https://t.me/{me.username}?start={user_id}</code>\n\n📢 انشر رابطك! عند تسجيل صديقك وإنشاء حسابه بالموقع، ستحصل فوراً على 🎡 <b>فرصة تدوير مجانية</b> في عجلة الحظ!", parse_mode="HTML")

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await update.effective_chat.send_message("🎁 أدخل كود الهدية الآن:")

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
        txt = "📜 <b>سجل آخر عملياتك:</b>\n\n"
        for l in logs:
            txt += f"• {l['type']} | الوسيلة: {l['method'] or 'عام'} | المبلغ: {l['amount']} NSP | الحالة: {l['status']}\n"
        await update.effective_chat.send_message(txt, parse_mode="HTML")

    elif data == "admin_panel" and is_admin(user_id):
        await show_admin_panel(update, context)

    elif data == "adm_game_settings" and is_admin(user_id):
        wr = get_setting('game_win_rate', '30')
        keyboard = [
            [InlineKeyboardButton("🎯 تعديل نسبة الفوز %", callback_data="adm_set_win_rate")],
            [InlineKeyboardButton("📊 تعديل أوزان نسب الجوائز", callback_data="adm_slice_weights_menu")],
            [InlineKeyboardButton("⚙️ لوحة الآدمن", callback_data="admin_panel")]
        ]
        await update.effective_chat.send_message(
            f"🎮 <b>إعدادات خوارزمية عجلة الحظ:</b>\n\n"
            f"• نسبة الفوز العامة: <b>{wr}%</b>\n"
            f"• قيم الجوائز الثابتة بالعجلة: 0, 5, 10, 15, 25, 50, 100, 500, 10000 NSP\n"
            f"📌 الخصم يتم تلقائياً وفورياً من الكاشيرة عند فوز العميل بأي قيمة.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data == "adm_slice_weights_menu" and is_admin(user_id):
        weights_raw = get_setting('wheel_weights', '{}')
        try:
            w_dict = json.loads(weights_raw)
        except Exception:
            w_dict = {}
        
        txt = "📊 <b>أوزان ظهور الجوائز الحالية في عجلة الحظ:</b>\n\n"
        btns = []
        row = []
        for v in WHEEL_VALUES:
            w_val = w_dict.get(str(v), 10)
            txt += f"• الجائزة <b>{v} NSP</b> 👈 الوزن النسبى: <code>{w_val}</code>\n"
            row.append(InlineKeyboardButton(f"✏️ {v} NSP", callback_data=f"adm_sw_{v}"))
            if len(row) == 3:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        btns.append([InlineKeyboardButton("⚙️ إعدادات العجلة", callback_data="adm_game_settings")])
        await update.effective_chat.send_message(txt, reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

    elif data.startswith("adm_sw_") and is_admin(user_id):
        val = data.replace("adm_sw_", "")
        context.user_data['target_slice_val'] = val
        context.user_data['state'] = 'ADM_WAIT_SLICE_WEIGHT_AMT'
        await update.effective_chat.send_message(f"🎯 أدخل الوزن النسبي الجديد لـ <b>{val} NSP</b> (مثال: 10 أو 5.5):", parse_mode="HTML")

    elif data == "adm_grant_spins" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_SPINS_USER_ID'
        await update.effective_chat.send_message("👤 أدخل آيدي العميل أو اسم حساب الموقع لمنحه محاولات لعب مجانية:")

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
        txt = "❌ تم <b>تعطيل</b> البونص الترحيبي نهائياً." if new_val == '0' else "✅ تم <b>تفعيل</b> البونص الترحيبي للعملاء الجدد."
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
        await update.effective_chat.send_message(
            f"💳 <b>حسابات الدفع الحالية:</b>\n\n"
            f"📱 سيريتل كاش: <code>{s_num}</code>\n"
            f"💳 شام كاش: <code>{sh_num}</code>\n\nاختر الحساب المراد تعديله:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

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
            btns = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ قبول", callback_data=f"app_req_{r['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"rej_req_{r['id']}")]
            ])
            await update.effective_chat.send_message(
                f"📥 <b>طلب {r['type']}</b>\n"
                f"• الوسيلة: <b>{r['method']}</b>\n"
                f"• العميل: <code>{r['telegram_id']}</code>\n"
                f"• المبلغ: <b>{r['amount']} NSP</b>\n"
                f"• الرقم/العملية: <code>{r['tx_number']}</code>", 
                reply_markup=btns, 
                parse_mode="HTML"
            )

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
                
                await context.bot.send_message(user_target, f"✅ <b>تم قبول طلب الشحن!</b>\nتم إضافة {amt:.2f} NSP إلى رصيد البوت الخاص بك بنجاح.", parse_mode="HTML")
                
                msg_admin = (
                    f"✅ <b>تم قبول طلب الشحن وتحديث الكاشيرة!</b>\n"
                    f"• العميل: <code>{user_target}</code>\n"
                    f"• المبلغ المُضاف: <b>+{amt:.2f} NSP</b>\n"
                    f"🏦 <b>رصيد الكاشيرة قبل:</b> <code>{before_cashier:.2f} NSP</code>\n"
                    f"🏦 <b>رصيد الكاشيرة بعد:</b> <code>{after_cashier:.2f} NSP</code>"
                )
                await query.message.edit_text(msg_admin, parse_mode="HTML")

            elif 'withdraw' in r['type']:
                before_cashier, after_cashier = update_cashier(-amt)
                conn.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (req_id,))
                conn.execute("UPDATE users SET withdraw_count = withdraw_count + 1 WHERE telegram_id = ?", (user_target,))
                conn.commit()

                await context.bot.send_message(user_target, f"✅ <b>تم قبول طلب السحب!</b>\nتم تحويل {amt:.2f} NSP بنجاح.", parse_mode="HTML")
                
                msg_admin = (
                    f"✅ <b>تم قبول طلب السحب وخصمه من الكاشيرة!</b>\n"
                    f"• العميل: <code>{user_target}</code>\n"
                    f"• المبلغ المخصوم: <b>-{amt:.2f} NSP</b>\n"
                    f"🏦 <b>رصيد الكاشيرة قبل:</b> <code>{before_cashier:.2f} NSP</code>\n"
                    f"🏦 <b>رصيد الكاشيرة بعد:</b> <code>{after_cashier:.2f} NSP</code>"
                )
                await query.message.edit_text(msg_admin, parse_mode="HTML")
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
            
            await context.bot.send_message(r['telegram_id'], f"❌ تم رفض طلب {r['type']} بقيمة {r['amount']} NSP وتم إعادة الرصيد لبوتك.")
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
        for c in codes:
            txt += f"• الكود: <code>{c['code']}</code> | القيمة: <code>{c['amount']} NSP</code> | الاستخدام: <code>{c['used_count']}/{c['max_uses']}</code>\n"
        await update.effective_chat.send_message(txt, parse_mode="HTML")

    elif data == "adm_disable_code" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_DISABLE_CODE'
        await update.effective_chat.send_message("أدخل الكود المراد إلغاء تفعيله بالضبط:")

    elif data == "adm_edit_channels" and is_admin(user_id):
        curr = get_setting('forced_channels', '')
        context.user_data['state'] = 'ADM_WAIT_CHANNELS'
        await update.effective_chat.send_message(f"📢 <b>القنوات الحالية:</b> <code>{curr or 'لا يوجد'}</code>\n\nأدخل معرّفات القنوات مفصولة بفاصلة (مثال: <code>@chan1,@chan2</code>):", parse_mode="HTML")

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
        status_txt = "تم تفعيل وضع الصيانة 🛠" if new_val == '1' else "تم إلغاء وضع الصيانة وتشغيل البوت 🚀"
        await update.effective_chat.send_message(status_txt)

    elif data == "adm_ban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد حظره:")

    elif data == "adm_unban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_UNBAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد إلغاء حظره:")

    elif data == "adm_broadcast" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BROADCAST'
        await update.effective_chat.send_message("📢 أدخل النص المراد إرساله لجميع مستخدمي البوت:")

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
        await update.effective_chat.send_message(
            f"📊 <b>إحصائيات المنصة والبوت:</b>\n\n"
            f"• إجمالي المسجلين: <code>{tot}</code>\n"
            f"• النشطين خلال 24 ساعة: <code>{active_today}</code>\n"
            f"• إجمالي أرصدة البوت: <code>{bal:.2f} NSP</code>\n"
            f"• إجمالي أرصدة الموقع: <code>{s_bal:.2f} NSP</code>", 
            parse_mode="HTML"
        )

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
# 5. معالج النصوص والرسائل (Text Handling)
# ==========================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    state = context.user_data.get('state')

    if not state:
        return

    conn = get_db()
    cursor = conn.cursor()

    if state == 'WAIT_SITE_USER':
        if not validate_username(text):
            await update.message.reply_text("❌ اسم المستخدم غير صالح! يجب أن يتكون من 6 أحرف/أرقام إنجليزية على الأقل وبدون رموز وخالٍ من المسافات.")
            conn.close()
            return
            
        check = cursor.execute("SELECT telegram_id FROM users WHERE site_username = ?", (text,)).fetchone()
        if check:
            await update.message.reply_text("❌ اسم المستخدم هذا محجوز لعميل آخر! يرجى اختيار اسم مختلف.")
            conn.close()
            return

        context.user_data['temp_site_user'] = text
        context.user_data['state'] = 'WAIT_SITE_PASS'
        keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]
        await update.message.reply_text(
            "🔑 <b>الخطوة الأخيرة:</b> أدخل كلمة المرور (يجب أن تحتوي على 6 أحرف وأرقام إنجليزية على الأقل):", 
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        conn.close()
        return

    elif state == 'WAIT_SITE_PASS':
        if not validate_password(text):
            await update.message.reply_text("❌ كلمة المرور ضعيفة! يجب أن تكون 6 خانات على الأقل وتحتوي على أحرف وأرقام إنجليزية معاً.")
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
            try:
                await context.bot.send_message(ref_id, "🎉 <b>ربحت فرصة لعب مجانية!</b>\nقام صديقك بإنشاء حساب على الموقع بنجاح، تم إضافة فرصة لعب إلى حسابك في عجلة الحظ!", parse_mode="HTML")
            except Exception: pass
        else:
            conn.commit()
            
        conn.close()

        context.user_data.clear()
        
        asyncio.create_task(register_account_to_site_api_async(username, password, user_id))

        await update.message.reply_text(
            f"✅ <b>تم إنشاء وحفظ حسابك بنجاح!</b>\n\n"
            f"👤 اسم المستخدم: <code>{html.escape(username)}</code>\n"
            f"🔑 كلمة المرور: <code>{html.escape(password)}</code>",
            parse_mode="HTML"
        )
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_TRANSFER_TO_SITE':
        try:
            amt = float(text)
            if amt <= 0: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!")
            conn.close()
            return

        u = cursor.execute("SELECT balance, site_username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if u['balance'] < amt:
            await update.message.reply_text("❌ رصيدك في البوت غير كافٍ لهذا التحويل!")
            conn.close()
            return

        cursor.execute("UPDATE users SET balance = balance - ?, site_balance = site_balance + ? WHERE telegram_id = ?", (amt, amt, user_id))
        conn.commit()
        conn.close()
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم تحويل <b>{amt:.2f} NSP</b> بنجاح إلى حسابك بالموقع!", parse_mode="HTML")
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_TRANSFER_FROM_SITE':
        try:
            amt = float(text)
            if amt <= 0: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!")
            conn.close()
            return

        u = cursor.execute("SELECT site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if u['site_balance'] < amt:
            await update.message.reply_text("❌ رصيدك في الموقع غير كافٍ لهذا السحب!")
            conn.close()
            return

        cursor.execute("UPDATE users SET site_balance = site_balance - ?, balance = balance + ? WHERE telegram_id = ?", (amt, amt, user_id))
        conn.commit()
        conn.close()
        context.user_data.clear()
        await update.message.reply_text(f"↩️ تم سحب <b>{amt:.2f} NSP</b> بنجاح من رصيد الموقع إلى رصيد البوت!", parse_mode="HTML")
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_DEP_AMT':
        try:
            amt = float(text)
            min_dep = float(get_setting('min_deposit', '50'))
            if amt < min_dep:
                await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى للشحن ({min_dep} NSP)!")
                conn.close()
                return
        except ValueError:
            await update.message.reply_text("❌ أدخل رقماً صحيحاً للمبلغ!")
            conn.close()
            return

        context.user_data['dep_amt'] = amt
        context.user_data['state'] = 'WAIT_DEP_TX'
        method = context.user_data.get('selected_method')
        acc_num = get_payment_number(method)
        
        await update.message.reply_text(
            f"✍️ <b>الخطوة الثانية:</b> قم بتحويل مبلغ <b>{amt:.2f} NSP</b> إلى رقم الحساب <code>{acc_num}</code> ({method}).\n\n"
            f"ثم أرسل رقم العملية / رقم التحويل الآن للتأكيد:",
            parse_mode="HTML"
        )
        conn.close()
        return

    elif state == 'WAIT_DEP_TX':
        amt = context.user_data.get('dep_amt')
        method = context.user_data.get('selected_method')
        tx_num = text

        cursor.execute(
            "INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'deposit', ?, ?, ?)",
            (user_id, method, amt, tx_num)
        )
        conn.commit()
        conn.close()
        context.user_data.clear()

        await update.message.reply_text("✅ <b>تم إرسال طلب الشحن بنجاح!</b> وسيتم إشعارك فور مراجعته وقبوله.", parse_mode="HTML")
        
        await send_all_admins(
            context,
            f"📥 <b>طلب شحن جديد!</b>\n"
            f"• العميل: <code>{user_id}</code>\n"
            f"• الوسيلة: <b>{method}</b>\n"
            f"• المبلغ: <b>{amt:.2f} NSP</b>\n"
            f"• رقم العملية: <code>{html.escape(tx_num)}</code>"
        )
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_WITH_AMT':
        try:
            amt = float(text)
            min_with = float(get_setting('min_withdraw', '100'))
            if amt < min_with:
                await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى للسحب ({min_with} NSP)!")
                conn.close()
                return
        except ValueError:
            await update.message.reply_text("❌ أدخل رقماً صحيحاً للمبلغ!")
            conn.close()
            return

        u = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if u['balance'] < amt:
            await update.message.reply_text("❌ رصيدك الحالي في البوت غير كافٍ للسحب!")
            conn.close()
            return

        context.user_data['with_amt'] = amt
        context.user_data['state'] = 'WAIT_WITH_ACC'
        await update.message.reply_text("✍️ <b>الخطوة الثانية:</b> أرسل رقم حسابك / رقم محفظتك لاستلام المبلغ:")
        conn.close()
        return

    elif state == 'WAIT_WITH_ACC':
        amt = context.user_data.get('with_amt')
        method = context.user_data.get('selected_method')
        acc_target = text

        cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amt, user_id))
        cursor.execute(
            "INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'withdraw', ?, ?, ?)",
            (user_id, method, amt, acc_target)
        )
        conn.commit()
        conn.close()
        context.user_data.clear()

        await update.message.reply_text("✅ <b>تم إرسال طلب السحب بنجاح!</b> وخصم المبلغ مؤقتاً لحين معالجة الطلب.", parse_mode="HTML")
        
        await send_all_admins(
            context,
            f"📤 <b>طلب سحب أرباح جديد!</b>\n"
            f"• العميل: <code>{user_id}</code>\n"
            f"• الوسيلة: <b>{method}</b>\n"
            f"• المبلغ: <b>{amt:.2f} NSP</b>\n"
            f"• رقم حساب المستلم: <code>{html.escape(acc_target)}</code>"
        )
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_GIFT_CODE':
        now = datetime.now()
        u = cursor.execute("SELECT code_restricted_until FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if u and u['code_restricted_until']:
            try:
                res_time = datetime.strptime(u['code_restricted_until'], '%Y-%m-%d %H:%M:%S')
                if now < res_time:
                    diff = int((res_time - now).total_seconds())
                    await update.message.reply_text(f"🚫 أنت محظور مؤقتاً من تجربة الأكواد بسبب المحاولات الخاطئة. المتبقي: {diff} ثانية.")
                    conn.close()
                    return
            except Exception: pass

        code_clean = text.strip()
        code_obj = cursor.execute("SELECT * FROM gift_codes WHERE UPPER(code) = UPPER(?) AND is_active = 1", (code_clean,)).fetchone()
        
        if not code_obj or code_obj['used_count'] >= code_obj['max_uses']:
            attempts = context.user_data.get('code_attempts', 0) + 1
            context.user_data['code_attempts'] = attempts
            if attempts >= 3:
                cursor.execute("UPDATE users SET code_restricted_until = strftime('%Y-%m-%d %H:%M:%S', 'now', '+10 minutes') WHERE telegram_id = ?", (user_id,))
                conn.commit()
                context.user_data['code_attempts'] = 0
                await update.message.reply_text("🚫 أدخلت كوداً خاطئاً 3 مرات! تم تقييدك من إدخال الأكواد لمدة 10 دقائق.")
            else:
                await update.message.reply_text(f"❌ كود غير صحيح أو منتهي الفعالية! (المحاولة {attempts}/3)")
            conn.close()
            return

        used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND UPPER(code) = UPPER(?)", (user_id, code_clean)).fetchone()
        if used:
            await update.message.reply_text("❌ لقد استخدمت هذا الكود سابقاً!")
            conn.close()
            return

        amt = float(code_obj['amount'])
        actual_code = code_obj['code']
        new_used_count = code_obj['used_count'] + 1
        is_active = 0 if new_used_count >= code_obj['max_uses'] else 1

        cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (user_id, actual_code))
        cursor.execute("UPDATE gift_codes SET used_count = ?, is_active = ? WHERE code = ?", (new_used_count, is_active, actual_code))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amt, user_id))
        conn.commit()
        conn.close()
        context.user_data.clear()

        await update.message.reply_text(f"🎉 <b>تم شحن الكود بنجاح!</b>\nإضافة <b>+{amt:.2f} NSP</b> إلى رصيد بوتك.", parse_mode="HTML")
        
        await send_all_admins(
            context,
            f"🎁 <b>استخدام كود هدية:</b>\n"
            f"• العميل: <code>{user_id}</code>\n"
            f"• الكود: <code>{actual_code}</code>\n"
            f"• القيمة: <b>{amt:.2f} NSP</b>"
        )
        await show_main_menu(update, context)
        return

    elif state == 'WAIT_SUPPORT':
        await send_all_admins(
            context,
            f"💬 <b>رسالة دعم جديدة من عميل!</b>\n"
            f"• العميل: <code>{user_id}</code>\n\n"
            f"الرسالة:\n{html.escape(text)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 الرد على العميل", callback_data=f"reply_support_{user_id}")]])
        )
        conn.close()
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال رسالتك إلى فريق الدعم الفني بنجاح.")
        await show_main_menu(update, context)
        return

    elif is_admin(user_id):
        if state == 'ADM_WAIT_WIN_RATE':
            try:
                rate = float(text)
                if not (0 <= rate <= 100): raise ValueError
                set_setting('game_win_rate', str(rate))
                await update.message.reply_text(f"🎯 تم تعديل نسبة الفوز العامة في عجلة الحظ إلى: <b>{rate}%</b>", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ أدخل نسبة مئوية صحيحة من 0 إلى 100!")
            context.user_data.clear()
            conn.close()
            return

        elif state == 'ADM_WAIT_SLICE_WEIGHT_AMT':
            try:
                w_num = float(text)
                if w_num < 0: raise ValueError
                val = context.user_data.get('target_slice_val')
                weights_raw = get_setting('wheel_weights', '{}')
                try:
                    w_dict = json.loads(weights_raw)
                except Exception:
                    w_dict = {}
                w_dict[str(val)] = w_num
                set_setting('wheel_weights', json.dumps(w_dict))
                await update.message.reply_text(f"✅ تم تعديل وزن الجائزة <b>{val} NSP</b> إلى: <code>{w_num}</code>", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ أدخل رقماً صحيحاً للوزن!")
            context.user_data.clear()
            conn.close()
            return

        elif state == 'ADM_WAIT_SPINS_USER_ID':
            if text.isdigit():
                u = cursor.execute("SELECT telegram_id, site_username, spins_count FROM users WHERE telegram_id = ? OR site_username = ?", (int(text), text)).fetchone()
            else:
                u = cursor.execute("SELECT telegram_id, site_username, spins_count FROM users WHERE site_username = ?", (text,)).fetchone()

            if not u:
                await update.message.reply_text("❌ لم يتم العثور على عميل بهذا الآيدي أو اسم المستخدم!")
                conn.close()
                return
            context.user_data['target_spins_user'] = u['telegram_id']
            context.user_data['state'] = 'ADM_WAIT_SPINS_COUNT'
            await update.message.reply_text(f"👤 العميل: <code>{u['telegram_id']}</code>\n🎡 اللفات الحالية: <b>{u['spins_count']}</b>\n\n✍️ أدخل عدد اللفات المراد إضافتها:", parse_mode="HTML")
            conn.close()
            return

        elif state == 'ADM_WAIT_SPINS_COUNT':
            try:
                cnt = int(text)
                if cnt <= 0: raise ValueError
                t_user = context.user_data.get('target_spins_user')
                cursor.execute("UPDATE users SET spins_count = spins_count + ? WHERE telegram_id = ?", (cnt, t_user))
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة <b>{cnt}</b> محاولة لعب للعميل <code>{t_user}</code> بنجاح.", parse_mode="HTML")
                try:
                    await context.bot.send_message(t_user, f"🎉 <b>تم منحك {cnt} محاولات لعب مجانية في عجلة الحظ من الإدارة!</b>", parse_mode="HTML")
                except Exception: pass
            except ValueError:
                await update.message.reply_text("❌ أدخل عدداً صحيحاً أكبر من 0!")
            context.user_data.clear()
            conn.close()
            return

        elif state == 'ADM_WAIT_ADD_BAL_ID':
            if text.isdigit():
                u = cursor.execute("SELECT telegram_id, site_username FROM users WHERE telegram_id = ? OR site_username = ?", (int(text), text)).fetchone()
            else:
                u = cursor.execute("SELECT telegram_id, site_username FROM users WHERE site_username = ?", (text,)).fetchone()

            if not u:
                await update.message.reply_text("❌ لم يتم العثور على عميل بهذا الآيدي أو اسم المستخدم!")
                conn.close()
                return
            context.user_data['target_adm_user'] = u['telegram_id']
            context.user_data['state'] = 'ADM_WAIT_ADD_BAL_AMT'
            await update.message.reply_text(f"👤 العميل: <code>{u['telegram_id']}</code> ({u['site_username'] or 'غير مربوط'})\n\n✍️ أدخل المبلغ المراد إضافته (+) أو خصمه (-):", parse_mode="HTML")
            conn.close()
            return

        elif state == 'ADM_WAIT_ADD_BAL_AMT':
            try:
                amt = float(text)
                t_user = context.user_data.get('target_adm_user')
                if amt > 0:
                    cashier_bal = get_cashier_balance()
                    if cashier_bal < amt:
                        await update.message.reply_text(f"⚠️ <b>تحذير:</b> رصيد الكاشيرة الحالي ({cashier_bal:.2f} NSP) أقل من المبلغ المطلوب إضافته ({amt:.2f} NSP)!", parse_mode="HTML")
                        conn.close()
                        return
                before_cashier, after_cashier = update_cashier(-amt)
                cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amt, t_user))
                conn.commit()
                
                await update.message.reply_text(
                    f"✅ تم تعديل رصيد العميل <code>{t_user}</code> بمقدار {amt:+.2f} NSP بنجاح.\n"
                    f"🏦 الكاشيرة قبل: <code>{before_cashier:.2f} NSP</code>\n"
                    f"🏦 الكاشيرة بعد: <code>{after_cashier:.2f} NSP</code>",
                    parse_mode="HTML"
                )
                try:
                    await context.bot.send_message(t_user, f"🔔 تم تعديل رصيد بوتك بواسطة الإدارة بمقدار: <b>{amt:+.2f} NSP</b>", parse_mode="HTML")
                except Exception: pass
            except ValueError:
                await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
            context.user_data.clear()
            conn.close()
            return

        elif state == 'ADM_WAIT_BONUS_AMT':
            try:
                amt = float(text)
                set_setting('welcome_bonus', str(amt))
                await update.message.reply_text(f"✅ تم تعديل قيمة البونص الترحيبي إلى: <b>{amt:.2f} NSP</b>", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!")
            context.user_data.clear()
            conn.close()
            return

        elif state == 'ADM_WAIT_MIN_DEP':
            try:
                amt = float(text)
                set_setting('min_deposit', str(amt))
                context.user_data['state'] = 'ADM_WAIT_MIN_WITH'
                await update.message.reply_text(f"✅ تم تحديد الحد الأدنى للشحن: <b>{amt:.2f} NSP</b>\n\n✍️ أدخل الحد الأدنى للسحب الآن بـ NSP:", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
            conn.close()
            return

        elif state == 'ADM_WAIT_MIN_WITH':
            try:
                amt = float(text)
                set_setting('min_withdraw', str(amt))
                await update.message.reply_text(f"✅ تم تحديد الحد الأدنى للسحب: <b>{amt:.2f} NSP</b>", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
            context.user_data.clear()
            conn.close()
            return

        elif state == 'ADM_WAIT_PAY_NUMBER':
            m_name = context.user_data.get('edit_pay_method')
            cursor.execute("UPDATE payment_methods SET number = ? WHERE name = ?", (text, m_name))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحديث رقم/حساب {m_name} إلى: <code>{text}</code>", parse_mode="HTML")
            return

        elif state == 'ADM_GIFT_AMT':
            try:
                amt = float(text)
                if amt <= 0: raise ValueError
                context.user_data['gift_amt'] = amt
                context.user_data['state'] = 'ADM_GIFT_COUNT'
                await update.message.reply_text("✍️ <b>خطوة 2/3:</b> أدخل عدد الأكواد المراد توليدها:")
            except ValueError:
                await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
            conn.close()
            return

        elif state == 'ADM_GIFT_COUNT':
            try:
                count = int(text)
                if count <= 0: raise ValueError
                context.user_data['gift_count'] = count
                context.user_data['state'] = 'ADM_GIFT_USES'
                await update.message.reply_text("✍️ <b>خطوة 3/3:</b> أدخل عدد مرات الاستخدام المسموحة لكل كود:")
            except ValueError:
                await update.message.reply_text("❌ أدخل عدداً صحيحاً!")
            conn.close()
            return

        elif state == 'ADM_GIFT_USES':
            try:
                uses = int(text)
                if uses <= 0: raise ValueError
                amt = context.user_data.get('gift_amt')
                count = context.user_data.get('gift_count')
                
                total_value = amt * count * uses
                cashier_bal = get_cashier_balance()
                
                if cashier_bal < total_value:
                    await update.message.reply_text(
                        f"❌ <b>رصيد الكاشيرة غير كافٍ لتوليد هذه الأكواد!</b>\n\n"
                        f"• القيمة الكلية المطلوبة: <b>{total_value:.2f} NSP</b>\n"
                        f"• المتاح بالكاشيرة: <code>{cashier_bal:.2f} NSP</code>",
                        parse_mode="HTML"
                    )
                    conn.close()
                    context.user_data.clear()
                    return

                before_cashier, after_cashier = update_cashier(-total_value)

                generated = []
                for _ in range(count):
                    c_str = "GIFT-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                    cursor.execute("INSERT INTO gift_codes (code, amount, max_uses) VALUES (?, ?, ?)", (c_str, amt, uses))
                    generated.append(c_str)
                conn.commit()
                
                txt = (
                    f"✅ <b>تم توليد {count} أكواد بنجاح وخصم قيمتها من الكاشيرة!</b>\n\n"
                    f"💰 القيمة المحجوزة الكلية: <b>{total_value:.2f} NSP</b>\n"
                    f"🏦 الكاشيرة قبل: <code>{before_cashier:.2f} NSP</code>\n"
                    f"🏦 الكاشيرة بعد: <code>{after_cashier:.2f} NSP</code>\n\n"
                )
                for code in generated:
                    txt += f"• <code>{code}</code> (القيمة: {amt} NSP | الاستخدامات: {uses})\n"
                await update.message.reply_text(txt, parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"❌ حدث خطأ أثناء التوليد: {e}")
            conn.close()
            context.user_data.clear()
            return

        elif state == 'ADM_WAIT_DISABLE_CODE':
            code_clean = text.strip()
            code_row = cursor.execute("SELECT * FROM gift_codes WHERE UPPER(code) = UPPER(?) AND is_active = 1", (code_clean,)).fetchone()
            if code_row:
                unused_uses = code_row['max_uses'] - code_row['used_count']
                refund = unused_uses * float(code_row['amount'])
                cursor.execute("UPDATE gift_codes SET is_active = 0 WHERE code = ?", (code_row['code'],))
                conn.commit()
                
                before_cashier, after_cashier = update_cashier(refund)
                await update.message.reply_text(
                    f"✅ تم تعطيل الكود <code>{code_row['code']}</code> بنجاح.\n"
                    f"💰 تم إرجاع المتبقي (<b>{refund:.2f} NSP</b>) للكاشيرة.\n"
                    f"🏦 الكاشيرة قبل: <code>{before_cashier:.2f} NSP</code>\n"
                    f"🏦 الكاشيرة بعد: <code>{after_cashier:.2f} NSP</code>",
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text("❌ الكود غير موجود أو معطل سابقاً!")
            conn.close()
            context.user_data.clear()
            return

        elif state == 'ADM_WAIT_CHANNELS':
            set_setting('forced_channels', text)
            conn.close()
            context.user_data.clear()
            await update.message.reply_text("✅ تم تحديث قائمة قنوات الاشتراك الإجباري.")
            return

        elif state == 'ADM_WAIT_NEW_ADMIN':
            if text.isdigit():
                cursor.execute("UPDATE users SET is_admin = 1 WHERE telegram_id = ?", (int(text),))
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة العميل <code>{text}</code> كـ آدمن بنجاح.", parse_mode="HTML")
            else:
                await update.message.reply_text("❌ أدخل آيدي عددي صحيح!")
            conn.close()
            context.user_data.clear()
            return

        elif state == 'ADM_WAIT_USER_DETAILS':
            if text.isdigit():
                u = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR site_username = ?", (int(text), text)).fetchone()
            else:
                u = cursor.execute("SELECT * FROM users WHERE site_username = ?", (text,)).fetchone()

            conn.close()
            if not u:
                await update.message.reply_text("❌ العميل غير موجود!")
                return
            txt = (
                f"👤 <b>تفاصيل العميل كاملة:</b>\n\n"
                f"• الآيدي: <code>{u['telegram_id']}</code>\n"
                f"• اسم مستخدم البوت: @{u['username'] or 'لا يوجد'}\n"
                f"• حساب الموقع: <code>{u['site_username'] or 'غير مربوط'}</code>\n"
                f"• كلمة سر الموقع: <code>{u['site_password'] or 'غير محددة'}</code>\n"
                f"• رصيد البوت: <b>{u['balance']:.2f} NSP</b>\n"
                f"• رصيد الموقع: <b>{u['site_balance']:.2f} NSP</b>\n"
                f"• الإحالات الناجحة: <code>{u['referrals_count']}</code>\n"
                f"• محاولات العجلة المتاحة: <code>{u['spins_count']}</code>\n"
                f"• محظور: {'نعم 🚫' if u['is_banned'] else 'لا ✅'}\n"
                f"• تاريخ التسجيل: <code>{u['created_at']}</code>"
            )
            await update.message.reply_text(txt, parse_mode="HTML")
            context.user_data.clear()
            return

        elif state == 'ADM_WAIT_BAN_ID':
            if text.isdigit():
                cursor.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (int(text),))
                conn.commit()
                await update.message.reply_text(f"🚫 تم حظر العميل <code>{text}</code> بنجاح.", parse_mode="HTML")
            conn.close()
            context.user_data.clear()
            return

        elif state == 'ADM_WAIT_UNBAN_ID':
            if text.isdigit():
                cursor.execute("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (int(text),))
                conn.commit()
                await update.message.reply_text(f"✅ تم إلغاء حظر العميل <code>{text}</code>.", parse_mode="HTML")
            conn.close()
            context.user_data.clear()
            return

        elif state == 'ADM_WAIT_BROADCAST':
            users = cursor.execute("SELECT telegram_id FROM users").fetchall()
            conn.close()
            sent, failed = 0, 0
            safe_text = html.escape(text)
            for u in users:
                try:
                    await context.bot.send_message(u['telegram_id'], f"📢 <b>تنويه من الإدارة:</b>\n\n{safe_text}", parse_mode="HTML")
                    sent += 1
                except Exception:
                    failed += 1
            await update.message.reply_text(f"✅ تم الانتهاء من الإذاعة!\n• نجاح الإرسال: {sent}\n• فشل الإرسال: {failed}")
            context.user_data.clear()
            return

        elif state == 'ADM_WAIT_PRIV_ID':
            if text.isdigit():
                context.user_data['priv_target'] = int(text)
                context.user_data['state'] = 'ADM_WAIT_PRIV_TXT'
                await update.message.reply_text(f"✍️ اكتب النص المراد إرساله للعميل <code>{text}</code>:", parse_mode="HTML")
            conn.close()
            return

        elif state == 'ADM_WAIT_PRIV_TXT':
            target = context.user_data.get('priv_target')
            safe_text = html.escape(text)
            try:
                await context.bot.send_message(target, f"📩 <b>رسالة خاصة من الإدارة:</b>\n\n{safe_text}", parse_mode="HTML")
                await update.message.reply_text("✅ تم إرسال الرسالة الخاصة بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"❌ تعذر إرسال الرسالة: {e}")
            conn.close()
            context.user_data.clear()
            return

        elif state == 'WAIT_ADMIN_REPLY_SUPP':
            target = context.user_data.get('support_target')
            safe_text = html.escape(text)
            try:
                await context.bot.send_message(target, f"💬 <b>رد الدعم الفني:</b>\n\n{safe_text}", parse_mode="HTML")
                await update.message.reply_text("✅ تم إرسال الرد للعميل بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"❌ تعذر الإرسال: {e}")
            conn.close()
            context.user_data.clear()
            return

    conn.close()

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    photo = update.message.photo[-1]

    if state == 'WAIT_WIN_SHOT':
        context.user_data.clear()
        await update.message.reply_text("✅ <b>تم استلام صورة الإصابة!</b> وسيتم مراجعتها من قبل الإدارة.", parse_mode="HTML")
        await send_all_admins(
            context,
            f"📸 <b>صورة إصابة جديدة من عميل!</b>\n• العميل: <code>{user_id}</code>"
        )
        conn = get_db()
        admins = conn.execute("SELECT telegram_id FROM users WHERE is_admin = 1").fetchall()
        conn.close()
        admin_ids = set([a['telegram_id'] for a in admins] + [MAIN_ADMIN_ID])
        for aid in admin_ids:
            try:
                await context.bot.send_photo(aid, photo.file_id)
            except Exception: pass
        await show_main_menu(update, context)

    elif state == 'WAIT_SUPPORT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الصورة للدعم الفني.")
        await send_all_admins(
            context,
            f"💬 <b>صورة موجهة للدعم الفني!</b>\n• العميل: <code>{user_id}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 الرد على العميل", callback_data=f"reply_support_{user_id}")]])
        )
        conn = get_db()
        admins = conn.execute("SELECT telegram_id FROM users WHERE is_admin = 1").fetchall()
        conn.close()
        admin_ids = set([a['telegram_id'] for a in admins] + [MAIN_ADMIN_ID])
        for aid in admin_ids:
            try:
                await context.bot.send_photo(aid, photo.file_id)
            except Exception: pass
        await show_main_menu(update, context)

# ==========================================================
# 6. النقطة الرئيسية للتشغيل Main Engine
# ==========================================================
async def post_init(application: Application):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()

def main():
    global bot_app
    init_db()
    
    t = threading.Thread(target=start_health_check_server, daemon=True)
    t.start()
    logging.info("Health check server and WebApp wheel server started successfully.")

    builder = Application.builder().token(BOT_TOKEN).post_init(post_init)
    bot_app = builder.build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(callback_router))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logging.info("AUREX bot is running...")
    
    bot_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
