import os
import re
import sqlite3
import logging
import random
import string
import html
import threading
import json
import asyncio
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes
)

# ==========================================================
# 0. تطبيق Flask وخادم عجلة الحظ وواجهات API
# ==========================================================
app = Flask(__name__)

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
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Cairo', 'Segoe UI', Tahoma, sans-serif;
            user-select: none;
        }

        body {
            background: radial-gradient(circle at center, #1b0203 0%, #080102 60%, #000000 100%);
            color: #ffffff;
            text-align: center;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 15px;
            overflow-x: hidden;
        }

        .header-brand {
            margin-bottom: 15px;
        }

        h1 {
            color: #e50914;
            font-size: 28px;
            font-weight: 900;
            letter-spacing: 2px;
            text-transform: uppercase;
            text-shadow: 0 0 15px rgba(229, 9, 20, 0.7), 0 0 30px rgba(212, 175, 55, 0.4);
        }

        p.subtitle {
            font-size: 13px;
            color: #d4af37;
            letter-spacing: 1px;
            margin-top: 3px;
        }

        .wheel-container {
            position: relative;
            width: 330px;
            height: 330px;
            margin: 15px auto;
            border-radius: 50%;
            box-shadow: 0 0 35px rgba(229, 9, 20, 0.5), inset 0 0 15px rgba(212, 175, 55, 0.5);
            padding: 5px;
            background: linear-gradient(145deg, #d4af37, #5c0206, #d4af37);
        }

        #canvas {
            width: 320px;
            height: 320px;
            border-radius: 50%;
            background-color: #0d0d0d;
            display: block;
        }

        .pointer {
            position: absolute;
            top: -12px;
            left: 50%;
            transform: translateX(-50%);
            width: 0;
            height: 0;
            border-left: 16px solid transparent;
            border-right: 16px solid transparent;
            border-top: 32px solid #d4af37;
            z-index: 10;
            filter: drop-shadow(0 4px 6px rgba(0,0,0,0.9));
        }

        .pointer::after {
            content: '';
            position: absolute;
            top: -34px;
            left: -8px;
            width: 16px;
            height: 16px;
            background: #e50914;
            border-radius: 50%;
            box-shadow: 0 0 8px #e50914;
        }

        .center-btn {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 70px;
            height: 70px;
            background: radial-gradient(circle, #e50914 0%, #700004 70%, #300000 100%);
            border: 3px solid #d4af37;
            border-radius: 50%;
            color: #ffffff;
            font-weight: 900;
            font-size: 16px;
            cursor: pointer;
            z-index: 5;
            box-shadow: 0 0 20px rgba(0,0,0,0.9), inset 0 0 10px rgba(212, 175, 55, 0.6);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .center-btn:active {
            transform: translate(-50%, -50%) scale(0.92);
        }

        .info-card {
            background: rgba(20, 5, 7, 0.85);
            border: 1px solid rgba(212, 175, 55, 0.4);
            border-radius: 16px;
            padding: 14px 20px;
            width: 100%;
            max-width: 330px;
            margin-top: 15px;
            display: flex;
            justify-content: space-around;
            backdrop-filter: blur(8px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.6);
        }

        .info-item {
            display: flex;
            flex-direction: column;
        }

        .info-item span.label {
            font-size: 11px;
            color: #aaa;
            margin-bottom: 2px;
        }

        .info-item span.val {
            font-size: 18px;
            font-weight: bold;
            color: #d4af37;
            text-shadow: 0 0 8px rgba(212, 175, 55, 0.3);
        }

        #result-modal {
            margin-top: 15px;
            font-size: 16px;
            font-weight: bold;
            min-height: 28px;
            transition: all 0.3s ease;
        }

        .win-msg {
            color: #2ecc71;
            text-shadow: 0 0 10px rgba(46, 204, 113, 0.5);
            animation: pulse 1s infinite alternate;
        }

        .lose-msg {
            color: #e74c3c;
            text-shadow: 0 0 10px rgba(231, 76, 60, 0.5);
        }

        @keyframes pulse {
            from { transform: scale(1); }
            to { transform: scale(1.05); }
        }
    </style>
</head>
<body>

    <div class="header-brand">
        <h1>AUREX WHEEL</h1>
        <p class="subtitle">عجلة الحظ التفاعلية الفاخرة</p>
    </div>

    <div class="wheel-container">
        <div class="pointer"></div>
        <canvas id="canvas" width="320" height="320"></canvas>
        <button class="center-btn" id="spinBtn" onclick="startSpin()">SPIN</button>
    </div>

    <div id="result-modal"></div>

    <div class="info-card">
        <div class="info-item">
            <span class="label">اللفات المتاحة</span>
            <span class="val" id="spinsCount">--</span>
        </div>
        <div class="info-item">
            <span class="label">رصيد البوت</span>
            <span class="val" id="userBal">--</span>
        </div>
    </div>

    <script>
        const tg = window.Telegram ? window.Telegram.WebApp : null;
        if (tg) tg.expand();

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

                ctx.fillStyle = (i % 2 === 0) ? "#120203" : "#80050a";
                ctx.fill();

                ctx.strokeStyle = "#d4af37";
                ctx.lineWidth = 1.5;
                ctx.stroke();

                ctx.save();
                ctx.translate(radius, radius);
                ctx.rotate(angle + sliceAngle / 2);
                ctx.textAlign = "right";
                ctx.fillStyle = (values[i] === 10000 || values[i] === 500) ? "#d4af37" : "#ffffff";
                ctx.font = "bold 13px Cairo, Arial";
                ctx.fillText(values[i] + " NSP", radius - 15, 5);
                ctx.restore();
            }
        }

        async function fetchUserData() {
            if (!userId) {
                document.getElementById("result-modal").innerText = "⚠️ افتح العجلة عبر البوت مباشرة.";
                return;
            }
            try {
                const res = await fetch('/api/get-spins?telegram_id=' + userId);
                const data = await res.json();
                if (data.status === 'success' || data.free_spins !== undefined) {
                    const spinsVal = (data.free_spins !== undefined) ? data.free_spins : (data.spins_count || 0);
                    const balVal = (data.bot_balance !== undefined) ? data.bot_balance : (data.balance || 0);
                    document.getElementById("spinsCount").innerText = spinsVal;
                    document.getElementById("userBal").innerText = parseFloat(balVal).toFixed(2) + " NSP";
                    document.getElementById("result-modal").innerText = "";
                }
            } catch (e) {
                document.getElementById("result-modal").innerText = "❌ تعذر الاتصال بالسيرفر";
            }
        }

        async function startSpin() {
            if (isSpinning || !userId) return;
            isSpinning = true;
            document.getElementById("spinBtn").disabled = true;
            document.getElementById("result-modal").innerText = "";

            try {
                const res = await fetch('/api/wheel/spin', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ telegram_id: userId, user_id: userId })
                });
                const data = await res.json();

                if (data.status !== 'success' && !data.prize_index && data.prize_index !== 0) {
                    document.getElementById("result-modal").innerHTML = `<span class="lose-msg">❌ ${data.error || "عذراً، حدث خطأ!"}</span>`;
                    isSpinning = false;
                    document.getElementById("spinBtn").disabled = false;
                    return;
                }

                const targetIndex = data.prize_index;
                const sliceAngle = (2 * Math.PI) / numSlices;
                
                const targetAngle = (1.5 * Math.PI) - (targetIndex * sliceAngle) - (sliceAngle / 2);
                const extraRounds = 7 * 2 * Math.PI;
                const startAngle = currentAngle;
                const normalizedCurrent = currentAngle % (2 * Math.PI);
                let diff = targetAngle - normalizedCurrent;
                if (diff < 0) diff += 2 * Math.PI;
                const finalAngle = startAngle + extraRounds + diff;

                let startTimestamp = null;
                const duration = 5000;

                function animate(timestamp) {
                    if (!startTimestamp) startTimestamp = timestamp;
                    const elapsed = timestamp - startTimestamp;
                    const progress = Math.min(elapsed / duration, 1);
                    const easeOut = 1 - Math.pow(1 - progress, 4);

                    currentAngle = startAngle + (finalAngle - startAngle) * easeOut;
                    drawWheel();

                    if (progress < 1) {
                        requestAnimationFrame(animate);
                    } else {
                        currentAngle = finalAngle;
                        drawWheel();
                        isSpinning = false;
                        document.getElementById("spinBtn").disabled = false;
                        const remSpins = (data.free_spins_left !== undefined) ? data.free_spins_left : data.remaining_spins;
                        const newBal = (data.new_bot_balance !== undefined) ? data.new_bot_balance : data.new_balance;
                        
                        document.getElementById("spinsCount").innerText = remSpins;
                        document.getElementById("userBal").innerText = parseFloat(newBal || 0).toFixed(2) + " NSP";

                        if (data.reward > 0) {
                            document.getElementById("result-modal").innerHTML = `<span class="win-msg">🎉 مبروك! فزت بـ ${data.reward} NSP</span>`;
                        } else {
                            document.getElementById("result-modal").innerHTML = `<span class="lose-msg">💔 حظ أوفر في المرة القادمة!</span>`;
                        }
                    }
                }
                requestAnimationFrame(animate);

            } catch (e) {
                isSpinning = false;
                document.getElementById("spinBtn").disabled = false;
                document.getElementById("result-modal").innerText = "❌ خطأ في الشبكة";
            }
        }

        drawWheel();
        fetchUserData();
    </script>
</body>
</html>
"""

@app.route('/', methods=['GET'])
def index():
    return "OK - AUREX BOT IS RUNNING", 200

@app.route('/wheel', methods=['GET'])
def wheel_page():
    return HTML_WHEEL_PAGE, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/api/get-spins', methods=['GET'])
@app.route('/api/user_info', methods=['GET'])
def api_get_spins():
    user_id_raw = request.args.get('telegram_id') or request.args.get('user_id')
    if user_id_raw and str(user_id_raw).isdigit():
        user_id = int(user_id_raw)
        conn = get_db()
        u = conn.execute("SELECT spins_count, free_spins, balance, bot_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if u:
            spins = u['free_spins'] if u['free_spins'] is not None else u['spins_count']
            bal = u['bot_balance'] if u['bot_balance'] is not None else u['balance']
            return jsonify({
                "status": "success", 
                "free_spins": spins, 
                "bot_balance": bal,
                "spins": spins, 
                "balance": bal
            })
        else:
            return jsonify({"status": "error", "error": "المستخدم غير موجود بالنظام", "message": "المستخدم غير موجود بالنظام"})
    return jsonify({"status": "error", "error": "معرف المستخدم غير صالح", "message": "معرف المستخدم غير صالح"})

@app.route('/api/users', methods=['GET'])
def api_users():
    auth_header = request.headers.get('X-Admin-Token')
    admin_token = os.environ.get("ADMIN_API_TOKEN", "INTERNAL_SECURE_TOKEN")
    if auth_header != admin_token:
        return "Unauthorized", 403
    conn = get_db()
    users = conn.execute("SELECT telegram_id, site_username, site_balance FROM users WHERE site_username IS NOT NULL").fetchall()
    conn.close()
    data = [dict(u) for u in users]
    return jsonify(data)

@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        data = request.get_json(force=True) or {}
        return jsonify({"status": "ok", "message": "Registered locally"})
    except Exception:
        return jsonify({"status": "error"}), 400

@app.route('/api/wheel/spin', methods=['POST'])
@app.route('/api/spin', methods=['POST'])
def api_wheel_spin():
    try:
        data = request.get_json(force=True) or {}
        user_id_raw = data.get('telegram_id') or data.get('user_id')
        
        if not user_id_raw or not str(user_id_raw).isdigit():
            return jsonify({"status": "error", "error": "معرف المستخدم غير صحيح!", "message": "معرف المستخدم غير صحيح!"})

        user_id = int(user_id_raw)
        
        conn = get_db()
        cursor = conn.cursor()
        u = cursor.execute("SELECT spins_count, free_spins, balance, bot_balance, username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        
        current_spins = u['free_spins'] if (u and u['free_spins'] is not None) else (u['spins_count'] if u else 0)
        current_bal = u['bot_balance'] if (u and u['bot_balance'] is not None) else (u['balance'] if u else 0.0)

        spin_price = float(get_setting('paid_spin_price', '10'))

        is_free_spin = current_spins > 0
        if not is_free_spin:
            if current_bal < spin_price:
                conn.close()
                return jsonify({"status": "error", "error": f"رصيدك غير كافٍ للعب المدفوع ({spin_price} NSP)!", "message": "رصيدك غير كافٍ للعب المدفوع!"})
            cursor.execute("UPDATE users SET balance = balance - ?, bot_balance = bot_balance - ? WHERE telegram_id = ?", (spin_price, spin_price, user_id))
            update_cashier(spin_price)
            current_bal -= spin_price
        else:
            cursor.execute("UPDATE users SET spins_count = spins_count - 1, free_spins = free_spins - 1 WHERE telegram_id = ?", (user_id,))

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
        
        new_bal = current_bal
        if prize > 0:
            before_cashier, after_cashier = update_cashier(-prize)
            cursor.execute("UPDATE users SET balance = balance + ?, bot_balance = bot_balance + ? WHERE telegram_id = ?", (prize, prize, user_id))
            conn.commit()
            new_bal += prize
            
            if MAIN_LOOP and MAIN_LOOP.is_running():
                asyncio.run_coroutine_threadsafe(
                    send_spin_notifications(user_id, prize, before_cashier, after_cashier),
                    MAIN_LOOP
                )
        else:
            conn.commit()

        rem_spins = current_spins - 1 if is_free_spin else current_spins
        conn.close()
        
        return jsonify({
            "status": "success",
            "prize_index": prize_index,
            "reward": prize,
            "prize_value": prize,
            "free_spins_left": rem_spins,
            "remaining_spins": rem_spins,
            "new_bot_balance": new_bal,
            "new_balance": new_bal
        })
    except Exception as e:
        logging.error(f"Spin API error: {e}")
        return jsonify({"status": "error", "error": "حدث خطأ أثناء التدوير.", "message": "حدث خطأ أثناء التدوير."})

# ==========================================================
# 1. الإعدادات الأساسية ودوال الإشعارات
# ==========================================================
MAIN_ADMIN_ID = int(os.environ.get("MAIN_ADMIN_ID", "7255100997"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8862010727:AAFOSpPZue_Ieec8WCVFqiHOrkh7HUq1ckI").strip() 
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
            conn = get_db()
            u = conn.execute("SELECT username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            conn.close()
            user_name = html.escape(u['username'] if (u and u['username']) else "غير معروف")

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
                f"• العميل: <b>{user_name}</b>\n"
                f"• الآيدي: <code>{user_id}</code>\n"
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
            payload = {
                "site_username": username,
                "site_password": password,
                "telegram_id": telegram_id
            }
            resp = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logging.warning(f"Note: Site API sync ({e}), local database managed.")
            return False
            
    return await asyncio.to_thread(_send)

# ==========================================================
# 2. إدارة قاعدة البيانات الموحدة والمتوافقة
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

    cursor.execute('''CREATE TABLE IF NOT EXISTS bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_name TEXT NOT NULL,
        bot_token TEXT UNIQUE,
        cashier_balance REAL DEFAULT 10000.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
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
    )''')

    existing_cols = [col[1] for col in cursor.execute("PRAGMA table_info(users)").fetchall()]
    required_cols = {
        'bot_id': 'INTEGER DEFAULT 1',
        'username': 'TEXT',
        'site_username': 'TEXT',
        'site_password': 'TEXT',
        'balance': 'REAL DEFAULT 0.0',
        'bot_balance': 'REAL DEFAULT 0.0',
        'site_balance': 'REAL DEFAULT 0.0',
        'total_spent': 'REAL DEFAULT 0.0',
        'deposit_count': 'INTEGER DEFAULT 0',
        'withdraw_count': 'INTEGER DEFAULT 0',
        'referrals_count': 'INTEGER DEFAULT 0',
        'spins_count': 'INTEGER DEFAULT 0',
        'free_spins': 'INTEGER DEFAULT 0',
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

    cursor.execute('''CREATE TABLE IF NOT EXISTS code_usages (
        telegram_id INTEGER PRIMARY KEY,
        last_used TIMESTAMP
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
        ('min_site_deposit', '50'),
        ('min_site_withdraw', '100'),
        ('paid_spin_price', '10'),
        ('cashier_balance', '10000.0'),
        ('forced_channels', '[]'),
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

def get_forced_channels_list():
    raw = get_setting('forced_channels', '[]')
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception:
        if raw and not raw.startswith('['):
            channels = [c.strip() for c in raw.split(',') if c.strip()]
            return [{"name": f"قناة {c}", "username": c, "link": f"https://t.me/{c.replace('@','')}"} for c in channels]
    return []

async def check_forced_sub(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = get_forced_channels_list()
    if not channels: 
        return True
        
    for ch in channels:
        ch_username = ch.get('username', '').strip()
        if not ch_username:
            continue
        if not ch_username.startswith('@') and not ch_username.startswith('-') and not ch_username.isdigit():
            ch_username = '@' + ch_username
            
        try:
            member = await context.bot.get_chat_member(chat_id=ch_username, user_id=user_id)
            if member.status not in ['creator', 'administrator', 'member']: 
                return False
        except Exception as e: 
            logging.error(f"Check forced sub error for {ch_username}: {e}")
            return False
    return True

# ==========================================================
# 3. معالجة كود الهدية مع زر الإلغاء وتقييد الـ 6 ساعات
# ==========================================================
async def redeem_gift_code(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_code: str):
    user = update.effective_user
    user_id = user.id
    user_name = html.escape(user.full_name or user.username or "مستخدم")
    code_clean = raw_code.strip()
    
    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_action")]])

    if not code_clean:
        await update.message.reply_text("❌ الكود غير صحيح", reply_markup=cancel_btn)
        return

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)", (user_id, user.username or user.first_name))
        conn.commit()

        now = datetime.now()

        usage_row = cursor.execute("SELECT last_used FROM code_usages WHERE telegram_id = ?", (user_id,)).fetchone()
        if usage_row and usage_row['last_used']:
            try:
                last_used_time = datetime.strptime(str(usage_row['last_used']).split('.')[0], '%Y-%m-%d %H:%M:%S')
                next_allowed_time = last_used_time + timedelta(hours=6)
                if now < next_allowed_time:
                    diff_seconds = int((next_allowed_time - now).total_seconds())
                    hours_left = diff_seconds // 3600
                    minutes_left = (diff_seconds % 3600) // 60
                    await update.message.reply_text(
                        f"⏳ <b>تنبيه تقييد استخدام الأكواد:</b>\n\n"
                        f"لا يمكنك استخدام كود جديد إلا مرة واحدة كل 6 ساعات.\n"
                        f"يرجى الانتظار: <b>{hours_left} ساعة و {minutes_left} دقيقة</b>.",
                        parse_mode="HTML",
                        reply_markup=cancel_btn
                    )
                    conn.close()
                    return
            except Exception as ex:
                logging.error(f"Error checking 6 hours restriction: {ex}")

        u = cursor.execute("SELECT code_restricted_until FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if u and u['code_restricted_until']:
            try:
                res_str = str(u['code_restricted_until'])
                res_time = None
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                    try:
                        res_time = datetime.strptime(res_str.split('.')[0], fmt)
                        break
                    except Exception:
                        pass
                if res_time and now < res_time:
                    diff = int((res_time - now).total_seconds())
                    await update.message.reply_text(
                        f"🚫 أنت محظور مؤقتاً من تجربة الأكواد بسبب المحاولات الخاطئة. المتبقي: {diff} ثانية.",
                        reply_markup=cancel_btn
                    )
                    conn.close()
                    return
            except Exception as e:
                logging.error(f"Restriction check error: {e}")

        code_obj = cursor.execute("SELECT * FROM gift_codes WHERE UPPER(code) = UPPER(?)", (code_clean,)).fetchone()

        if not code_obj:
            attempts = context.user_data.get('code_attempts', 0) + 1
            context.user_data['code_attempts'] = attempts
            if attempts >= 3:
                until_str = (now + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("UPDATE users SET code_restricted_until = ? WHERE telegram_id = ?", (until_str, user_id))
                conn.commit()
                context.user_data['code_attempts'] = 0
                context.user_data.pop('state', None)
                await update.message.reply_text("🚫 أدخلت كوداً خاطئاً 3 مرات! تم تقييدك من إدخال الأكواد لمدة 10 دقائق.")
            else:
                await update.message.reply_text("❌ الكود غير صحيح", reply_markup=cancel_btn)
            conn.close()
            return

        used_count = int(code_obj['used_count']) if (code_obj['used_count'] is not None) else 0
        max_uses = int(code_obj['max_uses']) if (code_obj['max_uses'] is not None) else 1
        is_active = code_obj['is_active'] if code_obj['is_active'] is not None else 1

        used_by_user = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND UPPER(code) = UPPER(?)", (user_id, code_clean)).fetchone()

        if used_by_user or used_count >= max_uses or is_active == 0:
            context.user_data.pop('state', None)
            await update.message.reply_text("❌ الكود مستخدم أو غير فعال", reply_markup=cancel_btn)
            conn.close()
            return

        amt = float(code_obj['amount']) if (code_obj['amount'] is not None) else 0.0
        actual_code = code_obj['code']
        new_used_count = used_count + 1
        new_is_active = 0 if new_used_count >= max_uses else 1

        cursor.execute("INSERT OR REPLACE INTO used_codes (telegram_id, code) VALUES (?, ?)", (user_id, actual_code))
        cursor.execute("UPDATE gift_codes SET used_count = ?, is_active = ? WHERE UPPER(code) = UPPER(?)", (new_used_count, new_is_active, actual_code))
        cursor.execute("UPDATE users SET balance = COALESCE(balance, 0) + ?, bot_balance = COALESCE(bot_balance, 0) + ? WHERE telegram_id = ?", (amt, amt, user_id))
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number, status) VALUES (?, 'gift_code', 'كود هدية', ?, ?, 'approved')", (user_id, amt, actual_code))
        
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT OR REPLACE INTO code_usages (telegram_id, last_used) VALUES (?, ?)", (user_id, now_str))

        conn.commit()
        conn.close()
        
        context.user_data.clear()

        await update.message.reply_text(f"🎉 <b>تم تفعيل الكود وإضافة الرصيد إلى محفظتك!</b> (+{amt:.2f} NSP)", parse_mode="HTML")
        
        await send_all_admins(
            context,
            f"🎁 <b>استخدام كود هدية:</b>\n"
            f"• العميل: <b>{user_name}</b>\n"
            f"• الآيدي: <code>{user_id}</code>\n"
            f"• الكود: <code>{actual_code}</code>\n"
            f"• القيمة: <b>{amt:.2f} NSP</b>"
        )
        await show_main_menu(update, context)
    except Exception as e:
        logging.error(f"Error in redeem_gift_code: {e}")
        try:
            conn.close()
        except Exception: pass
        await update.message.reply_text("❌ حدث خطأ غير متوقع عند معالجة كود الهدية. يرجى المحاولة مرة أخرى.", reply_markup=cancel_btn)

# ==========================================================
# 4. الأوامر والقوائم الرئيسية المحدثة
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if update.message:
        try:
            random_emoji = random.choice(["⚡", "🔥", "🎉"])
            await context.bot.set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                reaction=[{"type": "emoji", "emoji": random_emoji}]
            )
        except Exception as e:
            logging.error(f"Error setting reaction on /start: {e}")

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
        channels = get_forced_channels_list()
        btns = []
        for ch in channels:
            ch_name = ch.get('name', 'قناة الاشتراك')
            ch_link = ch.get('link', '#')
            btns.append([InlineKeyboardButton(f"📢 {ch_name}", url=ch_link)])
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
            user_name = html.escape(user.full_name or user.username or "مستخدم")
            await send_all_admins(
                context, 
                f"👤 <b>عضو جديد انضم للبوت!</b>\n\n• الاسم: <b>{user_name}</b>\n• المعرف: @{user.username or 'لا يوجد'}\n• الآيدي: <code>{user.id}</code>\n• الإحالة بواسطة: {ref_txt}"
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
    
    bot_bal = float(db_user['bot_balance'] if (db_user and db_user['bot_balance'] is not None) else ((db_user['balance'] if db_user else 0.0) or 0.0))
    site_bal = float(db_user['site_balance'] if (db_user and db_user['site_balance'] is not None) else 0.0)
    spins = int(db_user['free_spins'] if (db_user and db_user['free_spins'] is not None) else ((db_user['spins_count'] if db_user else 0) or 0))

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
        aurex_btn = InlineKeyboardButton("🌐 زيارة منصة AUREX", web_app=WebAppInfo(url=SERVER_URL))
    except Exception:
        aurex_btn = InlineKeyboardButton("🌐 زيارة منصة AUREX", url=SERVER_URL)

    try:
        wheel_btn = InlineKeyboardButton(f"🎡 عجلة الحظ والإحالات ({spins} فرص)", web_app=WebAppInfo(url=wheel_url))
    except Exception:
        wheel_btn = InlineKeyboardButton(f"🎡 عجلة الحظ والإحالات ({spins} فرص)", url=wheel_url)

    keyboard = [
        [aurex_btn],
        [wheel_btn],
        [InlineKeyboardButton("💳 شحن البوت", callback_data="dep_menu"), InlineKeyboardButton("💰 سحب الأرباح", callback_data="with_menu")],
        [InlineKeyboardButton("🔄 شحن رصيد للموقع", callback_data="transfer_to_site"), InlineKeyboardButton("↩️ سحب رصيد من الموقع", callback_data="transfer_from_site")],
        [InlineKeyboardButton("🔑 إنشاء حساب موقع", callback_data="create_site_account"), InlineKeyboardButton("🔐 بيانات حسابي", callback_data="my_account")],
        [InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="claim_gift")],
        [InlineKeyboardButton("📸 إرسال صورة إثبات", callback_data="send_win_shot"), InlineKeyboardButton("💬 الدعم الفني", callback_data="contact_support")],
        [InlineKeyboardButton("📜 السجل المالي", callback_data="my_logs")]
    ]

    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم الإدارية (الآدمن)", callback_data="admin_panel")])

    chat = update.effective_chat
    await chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ==========================================================
# 5. معالج التفاعلات والأزرار (Callback Router)
# ==========================================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    user_id = user.id
    user_name = html.escape(user.full_name or user.username or "مستخدم")

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_action")]])

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
        user_row = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        bonus_enabled = get_setting('welcome_bonus_enabled', '1') == '1'
        bonus_amt = float(get_setting('welcome_bonus', '500.0'))
        
        bonus_granted = False
        if bonus_enabled and bonus_amt > 0 and user_row and user_row['got_welcome_bonus'] == 0:
            cursor.execute(
                "UPDATE users SET security_passed = 1, got_welcome_bonus = 1, balance = balance + ?, bot_balance = bot_balance + ? WHERE telegram_id = ? AND got_welcome_bonus = 0",
                (bonus_amt, bonus_amt, user_id)
            )
            if cursor.rowcount > 0:
                bonus_granted = True
                conn.commit()
                before_cashier, after_cashier = update_cashier(-bonus_amt)
            else:
                cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user_id,))
                conn.commit()
        else:
            cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user_id,))
            conn.commit()
            
        conn.close()

        if bonus_granted:
            await query.message.edit_text(f"قلتلك حموية ماصدقتني! 🍯\n\n🎉 <b>لقد حصلت على بونص ترحيبي بقيمة {bonus_amt:.2f} NSP!</b>", parse_mode="HTML")
            
            await send_all_admins(
                context,
                f"⚠️ <b>إشعار خصم من الكاشيرة (بونص ترحيبي):</b>\n\n"
                f"تم خصم مبلغ <b>{bonus_amt:.2f} NSP</b> من الكاشيرة لدخول شخص جديد:\n"
                f"• العميل: <b>{user_name}</b>\n"
                f"• الآيدي: <code>{user_id}</code>\n\n"
                f"🏦 المبلغ القديم في الكاشيرة: <code>{before_cashier:.2f} NSP</code>\n"
                f"🏦 المبلغ الجديد في الكاشيرة: <code>{after_cashier:.2f} NSP</code>"
            )
        else:
            await query.message.edit_text("قلتلك حموية ماصدقتني! 🍯 تم توثيق حسابك بنجاح.")

        await show_main_menu(update, context)
        return

    if data == "cancel_action":
        context.user_data.clear()
        try:
            await query.message.delete()
        except Exception: pass
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
        await update.effective_chat.send_message(
            "🔑 <b>إنشاء حساب جديد للموقع:</b>\n\n"
            "✍️ أدخل اسم المستخدم الجديد (يتكون من 6 أحرف/أرقام إنجليزية على الأقل وبدون رموز):",
            reply_markup=cancel_btn,
            parse_mode="HTML"
        )

    elif data == "transfer_to_site":
        conn = get_db()
        u = conn.execute("SELECT site_username, balance, bot_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ يجب إنشاء حساب على الموقع أولاً!", parse_mode="HTML")
            return
        
        min_site_dep = get_setting('min_site_deposit', '50')
        bal = u['bot_balance'] if u['bot_balance'] is not None else u['balance']
        context.user_data['state'] = 'WAIT_TRANSFER_TO_SITE'
        await update.effective_chat.send_message(
            f"🔄 <b>شحن رصيد للموقع:</b>\n"
            f"💰 رصيد البوت المتوفر: <b>{bal:.2f} NSP</b>\n"
            f"📌 الحد الأدنى للتحويل للموقع: <b>{min_site_dep} NSP</b>\n\n"
            f"✍️ أدخل المبلغ المراد تحويله من البوت إلى حسابك بالموقع:",
            reply_markup=cancel_btn,
            parse_mode="HTML"
        )

    elif data == "transfer_from_site":
        conn = get_db()
        u = conn.execute("SELECT site_username, site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ يجب إنشاء حساب على الموقع أولاً!", parse_mode="HTML")
            return

        min_site_with = get_setting('min_site_withdraw', '100')
        context.user_data['state'] = 'WAIT_TRANSFER_FROM_SITE'
        await update.effective_chat.send_message(
            f"↩️ <b>سحب رصيد من الموقع:</b>\n"
            f"💎 رصيد الموقع المتوفر: <b>{u['site_balance']:.2f} NSP</b>\n"
            f"📌 الحد الأدنى للسحب من الموقع: <b>{min_site_with} NSP</b>\n\n"
            f"✍️ أدخل المبلغ المراد سحبه من الموقع إلى رصيد البوت:",
            reply_markup=cancel_btn,
            parse_mode="HTML"
        )

    elif data == "dep_menu":
        min_dep = get_setting('min_deposit', '50')
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="dep_method_سيريتل كاش")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="dep_method_شام كاش")],
            [InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_action")]
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
            reply_markup=cancel_btn,
            parse_mode="HTML"
        )

    elif data == "with_menu":
        min_with = get_setting('min_withdraw', '100')
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="with_method_سيريتل كاش")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="with_method_شام كاش")],
            [InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_action")]
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
            reply_markup=cancel_btn,
            parse_mode="HTML"
        )

    elif data == "my_ref":
        me = await context.bot.get_me()
        await update.effective_chat.send_message(f"🔗 <b>رابط إحالتي الشخصي:</b>\n<code>https://t.me/{me.username}?start={user_id}</code>\n\n📢 انشر رابطك! عند تسجيل صديقك وإنشاء حسابه بالموقع، ستحصل فوراً على 🎡 <b>فرصة تدوير مجانية</b> في عجلة الحظ!", parse_mode="HTML")

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await update.effective_chat.send_message("🎁 أدخل كود الهدية الآن:", reply_markup=cancel_btn)

    elif data == "send_win_shot":
        context.user_data['state'] = 'WAIT_WIN_SHOT'
        await update.effective_chat.send_message("📸 أرسل صورة الإصابة / الفوز الآن (يمكنك إرسال نص أو صورة):", reply_markup=cancel_btn)

    elif data == "contact_support":
        context.user_data['state'] = 'WAIT_SUPPORT'
        await update.effective_chat.send_message("💬 يمكنك كتابة رسالتك أو إرسال صورة مباشرة للدعم الفني:", reply_markup=cancel_btn)

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
        await update.effective_chat.send_message(f"🎯 أدخل الوزن النسبي الجديد لـ <b>{val} NSP</b> (مثال: 10 أو 5.5):", parse_mode="HTML", reply_markup=cancel_btn)

    elif data == "adm_grant_spins" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_SPINS_USER_ID'
        await update.effective_chat.send_message("👤 أدخل آيدي العميل أو اسم حساب الموقع لمنحه محاولات لعب مجانية:", reply_markup=cancel_btn)

    elif data == "adm_set_win_rate" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_WIN_RATE'
        await update.effective_chat.send_message("🎯 أدخل نسبة الفوز الجديدة في عجلة الحظ (من 0 إلى 100):", reply_markup=cancel_btn)

    elif data == "adm_cashier" and is_admin(user_id):
        bal = get_cashier_balance()
        await update.effective_chat.send_message(f"🏦 <b>رصيد الكاشيرة الحالي:</b> <code>{bal:.2f} NSP</code>", parse_mode="HTML")

    elif data == "adm_edit_user_bal" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_ADD_BAL_ID'
        await update.effective_chat.send_message("👤 أدخل آيدي العميل أو اسم حساب الموقع المراد تعديل رصيده:", reply_markup=cancel_btn)

    elif data == "adm_set_bonus" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BONUS_AMT'
        await update.effective_chat.send_message("🎁 أدخل قيمة البونص الترحيبي الجديد بـ NSP:", reply_markup=cancel_btn)

    elif data == "adm_toggle_bonus_state" and is_admin(user_id):
        curr = get_setting('welcome_bonus_enabled', '1')
        new_val = '0' if curr == '1' else '1'
        set_setting('welcome_bonus_enabled', new_val)
        txt = "❌ تم <b>تعطيل</b> البونص الترحيبي نهائياً." if new_val == '0' else "✅ تم <b>تفعيل</b> البونص الترحيبي للعملاء الجدد."
        await update.effective_chat.send_message(txt, parse_mode="HTML")

    elif data == "adm_set_limits" and is_admin(user_id):
        min_dep = get_setting('min_deposit', '50')
        min_with = get_setting('min_withdraw', '100')
        context.user_data['state'] = 'ADM_WAIT_MIN_DEP'
        await update.effective_chat.send_message(
            f"📉 <b>تعديل حدود البوت:</b>\n\n"
            f"• الحد الأدنى للشحن حالياً: <b>{min_dep} NSP</b>\n"
            f"• الحد الأدنى للسحب حالياً: <b>{min_with} NSP</b>\n\n"
            f"✍️ أدخل الحد الأدنى الجديد لشحن البوت بـ NSP:",
            reply_markup=cancel_btn,
            parse_mode="HTML"
        )

    elif data == "adm_site_limits" and is_admin(user_id):
        s_dep = get_setting('min_site_deposit', '50')
        s_with = get_setting('min_site_withdraw', '100')
        context.user_data['state'] = 'ADM_WAIT_SITE_MIN_DEP'
        await update.effective_chat.send_message(
            f"🌐 <b>تعديل حدود الشحن والسحب للموقع:</b>\n\n"
            f"• الحد الأدنى للشحن للموقع حالياً: <b>{s_dep} NSP</b>\n"
            f"• الحد الأدنى للسحب من الموقع حالياً: <b>{s_with} NSP</b>\n\n"
            f"✍️ أدخل الحد الأدنى الجديد للشحن للموقع بـ NSP:",
            reply_markup=cancel_btn,
            parse_mode="HTML"
        )

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
        await update.effective_chat.send_message(f"✍️ أدخل رقم/حساب {method_name} الجديد:", reply_markup=cancel_btn)

    elif data == "adm_requests" and is_admin(user_id):
        conn = get_db()
        reqs = conn.execute("SELECT * FROM transactions WHERE status = 'pending' ORDER BY id DESC LIMIT 10").fetchall()
        
        if not reqs:
            conn.close()
            await update.effective_chat.send_message("✅ لا يوجد طلبات معلقة حالياً.")
            return
        for r in reqs:
            u_row = conn.execute("SELECT username FROM users WHERE telegram_id = ?", (r['telegram_id'],)).fetchone()
            req_user_name = html.escape(u_row['username'] if (u_row and u_row['username']) else "غير معروف")
            
            type_title = "طلب شحن رصيد" if 'deposit' in r['type'] else "طلب سحب أرباح"
            
            btns = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ قبول", callback_data=f"app_req_{r['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"rej_req_{r['id']}")]
            ])
            await update.effective_chat.send_message(
                f"📥 <b>{type_title} معلق</b>\n"
                f"• العميل: <b>{req_user_name}</b>\n"
                f"• الآيدي: <code>{r['telegram_id']}</code>\n"
                f"• الوسيلة: <b>{r['method']}</b>\n"
                f"• المبلغ: <b>{r['amount']} NSP</b>\n"
                f"• الرقم/العملية: <code>{r['tx_number']}</code>", 
                reply_markup=btns, 
                parse_mode="HTML"
            )
        conn.close()

    elif data.startswith("app_req_") and is_admin(user_id):
        req_id = int(data.split("_")[2])
        conn = get_db()
        r = conn.execute("SELECT * FROM transactions WHERE id = ?", (req_id,)).fetchone()
        
        if r and r['status'] == 'pending':
            amt = float(r['amount'])
            user_target = r['telegram_id']
            u_row = conn.execute("SELECT username FROM users WHERE telegram_id = ?", (user_target,)).fetchone()
            target_name = html.escape(u_row['username'] if (u_row and u_row['username']) else "غير معروف")
            
            if 'deposit' in r['type']:
                before_cashier, after_cashier = update_cashier(-amt)
                conn.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (req_id,))
                conn.execute("UPDATE users SET balance = balance + ?, bot_balance = bot_balance + ?, deposit_count = deposit_count + 1 WHERE telegram_id = ?", (amt, amt, user_target))
                conn.commit()
                
                await context.bot.send_message(
                    user_target, 
                    f"✅ <b>تم قبول طلب الشحن!</b>\n\n"
                    f"• المبلغ لشحن: <b>+{amt:.2f} NSP</b>\n"
                    f"• طريقة الدفع: <b>{r['method']}</b>\n"
                    f"تمت إضافة الرصيد إلى محفظة البوت الخاصة بك بنجاح.", 
                    parse_mode="HTML"
                )
                
                msg_admin = (
                    f"✅ <b>تم قبول طلب الشحن وخصمه من الكاشيرة!</b>\n"
                    f"• العميل: <b>{target_name}</b>\n"
                    f"• الآيدي: <code>{user_target}</code>\n"
                    f"• المبلغ المُضاف للعميل: <b>+{amt:.2f} NSP</b>\n"
                    f"🏦 <b>رصيد الكاشيرة قبل:</b> <code>{before_cashier:.2f} NSP</code>\n"
                    f"🏦 <b>رصيد الكاشيرة بعد:</b> <code>{after_cashier:.2f} NSP</code>"
                )
                await query.message.edit_text(msg_admin, parse_mode="HTML")

            elif 'withdraw' in r['type']:
                before_cashier, after_cashier = update_cashier(amt)
                conn.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (req_id,))
                conn.execute("UPDATE users SET withdraw_count = withdraw_count + 1 WHERE telegram_id = ?", (user_target,))
                conn.commit()

                await context.bot.send_message(
                    user_target, 
                    f"✅ <b>تم قبول طلب سحب الأرباح!</b>\n\n"
                    f"• المبلغ المسحوب: <b>{amt:.2f} NSP</b>\n"
                    f"• حساب الاستلام: <b>{r['tx_number']}</b>\n"
                    f"تم تحويل المبلغ إليك بنجاح.", 
                    parse_mode="HTML"
                )
                
                msg_admin = (
                    f"✅ <b>تم قبول طلب السحب وإضافته للكاشيرة!</b>\n"
                    f"• العميل: <b>{target_name}</b>\n"
                    f"• الآيدي: <code>{user_target}</code>\n"
                    f"• المبلغ المضاف للكاشيرة: <b>+{amt:.2f} NSP</b>\n"
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
                conn.execute("UPDATE users SET balance = balance + ?, bot_balance = bot_balance + ? WHERE telegram_id = ?", (r['amount'], r['amount'], r['telegram_id']))
            conn.commit()
            
            req_type_ar = "الشحن" if 'deposit' in r['type'] else "السحب"
            
            await context.bot.send_message(
                r['telegram_id'], 
                f"❌ <b>تم رفض طلب {req_type_ar}!</b>\n\n"
                f"• المبلغ: <b>{r['amount']} NSP</b>\n"
                f"• الوسيلة: <b>{r['method']}</b>\n"
                f"{'تم إعادة الرصيد إلى محفظة البوت الخاصة بك.' if 'withdraw' in r['type'] else 'يرجى مراجعة بيانات التحويل وإعادة الطلب.'}",
                parse_mode="HTML"
            )
            await query.message.edit_text("❌ تم رفض الطلب وإبلاغ العميل بالإشعار المعرب بنجاح.")
        conn.close()

    elif data == "adm_gen_batch" and is_admin(user_id):
        context.user_data['state'] = 'ADM_GIFT_AMT'
        await update.effective_chat.send_message("✍️ <b>خطوة 1/3:</b> أدخل قيمة الكود الواحد بـ NSP:", reply_markup=cancel_btn, parse_mode="HTML")

    elif data == "adm_view_codes" and is_admin(user_id):
        conn = get_db()
        codes = conn.execute("SELECT * FROM gift_codes WHERE is_active = 1 AND used_count < max_uses LIMIT 50").fetchall()
        conn.close()
        if not codes:
            await update.effective_chat.send_message("❌ لا يوجد أكواد هدية مفعلة حالياً.")
            return
        txt = "🎁 <b>قائمة الأكواد النشطة:</b>\n\n"
        for c in codes:
            txt += f"• الكود: <code>{c['code']}</code> | القيمة: <code>{c['amount']} NSP</code> | الاستخدام: <code>{c['used_count']}/{c['max_uses']}</code>\n"
        await update.effective_chat.send_message(txt, parse_mode="HTML")

    elif data == "adm_disable_code" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_DISABLE_CODE'
        await update.effective_chat.send_message("✍️ أدخل الكود المراد إلغاء تفعيله بالضبط:", reply_markup=cancel_btn)

    elif data == "adm_edit_channels" and is_admin(user_id):
        channels = get_forced_channels_list()
        txt = "📢 <b>نظام الاشتراك الإجباري بالقنوات:</b>\n\n"
        if not channels:
            txt += "لا توجد قنوات إجبارية حالياً.\n"
        else:
            for idx, ch in enumerate(channels, 1):
                txt += f"{idx}. <b>{html.escape(ch.get('name',''))}</b>\n   • المعرف: <code>{html.escape(ch.get('username',''))}</code>\n   • الرابط: {html.escape(ch.get('link',''))}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ إضافة قناة إجبارية جديد", callback_data="adm_add_ch_start")],
            [InlineKeyboardButton("🗑 حذف قناة محدده", callback_data="adm_del_ch_menu")],
            [InlineKeyboardButton("🧹 مسح كافة القنوات", callback_data="adm_clear_all_channels")],
            [InlineKeyboardButton("⚙️ لوحة الآدمن", callback_data="admin_panel")]
        ]
        await update.effective_chat.send_message(txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "adm_add_ch_start" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_CH_USERNAME'
        await update.effective_chat.send_message("✍️ <b>الخطوة 1/3:</b> أدخل معرف القناة (مثال: <code>@channel_username</code>):", reply_markup=cancel_btn, parse_mode="HTML")

    elif data == "adm_del_ch_menu" and is_admin(user_id):
        channels = get_forced_channels_list()
        if not channels:
            await update.effective_chat.send_message("❌ لا توجد قنوات لحذفها.")
            return
        btns = []
        for idx, ch in enumerate(channels):
            btns.append([InlineKeyboardButton(f"🗑 حذف: {ch.get('name','')}", callback_data=f"adm_del_ch_{idx}")])
        btns.append([InlineKeyboardButton("↩️ إلغاء", callback_data="adm_edit_channels")])
        await update.effective_chat.send_message("اختر القناة المراد حذفها:", reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("adm_del_ch_") and is_admin(user_id):
        idx = int(data.replace("adm_del_ch_", ""))
        channels = get_forced_channels_list()
        if 0 <= idx < len(channels):
            removed = channels.pop(idx)
            set_setting('forced_channels', json.dumps(channels, ensure_ascii=False))
            await update.effective_chat.send_message(f"✅ تم حذف القناة <b>{html.escape(removed.get('name',''))}</b> بنجاح.", parse_mode="HTML")
        await show_admin_panel(update, context)

    elif data == "adm_clear_all_channels" and is_admin(user_id):
        set_setting('forced_channels', '[]')
        await update.effective_chat.send_message("✅ تم مسح جميع القنوات الإجبارية بنجاح.")

    elif data == "adm_add_admin" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_NEW_ADMIN'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد إضافته كـ آدمن:", reply_markup=cancel_btn)

    elif data == "adm_user_details" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_USER_DETAILS'
        await update.effective_chat.send_message("أدخل آيدي العميل أو اسم مستخدم الموقع لجلب كافة تفاصيله:", reply_markup=cancel_btn)

    elif data == "adm_toggle_maint" and is_admin(user_id):
        curr = get_setting('maintenance', '0')
        new_val = '1' if curr == '0' else '0'
        set_setting('maintenance', new_val)
        status_txt = "تم تفعيل وضع الصيانة 🛠" if new_val == '1' else "تم إلغاء وضع الصيانة وتشغيل البوت 🚀"
        await update.effective_chat.send_message(status_txt)

    elif data == "adm_ban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد حظره:", reply_markup=cancel_btn)

    elif data == "adm_unban_user" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_UNBAN_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد إلغاء حظره:", reply_markup=cancel_btn)

    elif data == "adm_broadcast" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BROADCAST'
        await update.effective_chat.send_message("📢 أدخل النص المراد إرساله لجميع مستخدمي البوت:", reply_markup=cancel_btn)

    elif data == "adm_private_msg" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_PRIV_ID'
        await update.effective_chat.send_message("أدخل آيدي العميل المراد مراسلته بشكل خاص:", reply_markup=cancel_btn)

    elif data == "adm_stats" and is_admin(user_id):
        conn = get_db()
        tot = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        bal = conn.execute("SELECT SUM(COALESCE(bot_balance, balance)) FROM users").fetchone()[0] or 0.0
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
        await update.effective_chat.send_message(f"💬 اكتب الرد للعميل <code>{target}</code>:", reply_markup=cancel_btn, parse_mode="HTML")

    elif data == "main_menu":
        await show_main_menu(update, context)

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bonus_state = "مفعل ✅" if get_setting('welcome_bonus_enabled', '1') == '1' else "معطل ❌"
    
    keyboard = [
        [InlineKeyboardButton("🏦 رصيد الكاشيرة", callback_data="adm_cashier"), InlineKeyboardButton("📥📤 طلبات الشحن والسحب", callback_data="adm_requests")],
        [InlineKeyboardButton("🎮 إعدادات لعبة الحظ", callback_data="adm_game_settings"), InlineKeyboardButton("🎡 منح لفات لعميل", callback_data="adm_grant_spins")],
        [InlineKeyboardButton("💳 تعديل حسابات الدفع", callback_data="adm_pay_methods"), InlineKeyboardButton("💰 تعديل رصيد مستخدم", callback_data="adm_edit_user_bal")],
        [InlineKeyboardButton(f"🎁 حالة البونص ({bonus_state})", callback_data="adm_toggle_bonus_state"), InlineKeyboardButton("🎁 قيمة البونص الترحيبي", callback_data="adm_set_bonus")],
        [InlineKeyboardButton("📉 تعديل حدود البوت", callback_data="adm_set_limits"), InlineKeyboardButton("🌐 تعديل حدود الموقع", callback_data="adm_site_limits")],
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
# 6. معالج النصوص والرسائل والعمليات الحسابية للكاشيرة
# ==========================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        msg_text = update.message.text.strip()
        if msg_text.startswith("برق"):
            try:
                await context.bot.set_message_reaction(
                    chat_id=update.effective_chat.id,
                    message_id=update.message.message_id,
                    reaction=[{"type": "emoji", "emoji": "⚡"}]
                )
            except Exception as e:
                logging.error(f"Error adding lightning reaction: {e}")
        elif msg_text.startswith("نار"):
            try:
                await context.bot.set_message_reaction(
                    chat_id=update.effective_chat.id,
                    message_id=update.message.message_id,
                    reaction=[{"type": "emoji", "emoji": "🔥"}]
                )
            except Exception as e:
                logging.error(f"Error adding fire reaction: {e}")

    user = update.effective_user
    user_id = user.id
    user_name = html.escape(user.full_name or user.username or "مستخدم")
    text = (update.message.text or update.message.caption or "").strip()
    state = context.user_data.get('state')

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_action")]])

    if not state and text:
        if text.upper().startswith("GIFT") or (len(text) >= 4 and not text.isdigit() and not text.startswith("/")):
            conn_check = get_db()
            c_check = conn_check.execute("SELECT 1 FROM gift_codes WHERE UPPER(code) = UPPER(?)", (text,)).fetchone()
            conn_check.close()
            if c_check or text.upper().startswith("GIFT"):
                state = 'WAIT_GIFT_CODE'
    if not state and update.message and update.message.photo:
        state = 'WAIT_WIN_SHOT'

    conn = get_db()
    cursor = conn.cursor()

    try:
        if state == 'WAIT_GIFT_CODE':
            conn.close()
            await redeem_gift_code(update, context, text)
            return

        elif state == 'WAIT_SITE_USER':
            if not validate_username(text):
                await update.message.reply_text("❌ اسم المستخدم غير صالح! يجب أن يتكون من 6 أحرف/أرقام إنجليزية على الأقل وبدون رموز وخالٍ من المسافات.", reply_markup=cancel_btn)
                conn.close()
                return
                
            check = cursor.execute("SELECT telegram_id FROM users WHERE site_username = ?", (text,)).fetchone()
            if check:
                await update.message.reply_text("❌ اسم المستخدم هذا محجوز لعميل آخر! يرجى اختيار اسم مختلف.", reply_markup=cancel_btn)
                conn.close()
                return

            context.user_data['temp_site_user'] = text
            context.user_data['state'] = 'WAIT_SITE_PASS'
            await update.message.reply_text(
                "🔑 <b>الخطوة الأخيرة:</b> أدخل كلمة المرور (يجب أن تحتوي على 6 أحرف وأرقام إنجليزية على الأقل):", 
                reply_markup=cancel_btn,
                parse_mode="HTML"
            )
            conn.close()
            return

        elif state == 'WAIT_SITE_PASS':
            if not validate_password(text):
                await update.message.reply_text("❌ كلمة المرور ضعيفة! يجب أن تكون 6 خانات على الأقل وتحتوي على أحرف وأرقام إنجليزية معاً.", reply_markup=cancel_btn)
                conn.close()
                return

            username = context.user_data.get('temp_site_user')
            password = text
            
            u_info = cursor.execute("SELECT referred_by FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            
            cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?", (username, password, user_id))
            
            if u_info and u_info['referred_by']:
                ref_id = u_info['referred_by']
                cursor.execute("UPDATE users SET spins_count = spins_count + 1, free_spins = free_spins + 1 WHERE telegram_id = ?", (ref_id,))
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
                min_site_dep = float(get_setting('min_site_deposit', '50'))
                if amt < min_site_dep:
                    await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى للشحن للموقع ({min_site_dep} NSP)!", reply_markup=cancel_btn)
                    conn.close()
                    return
            except ValueError:
                await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!", reply_markup=cancel_btn)
                conn.close()
                return

            u = cursor.execute("SELECT balance, bot_balance, site_username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            curr_bal = u['bot_balance'] if u['bot_balance'] is not None else u['balance']
            if curr_bal < amt:
                await update.message.reply_text("❌ رصيدك في البوت غير كافٍ لهذا التحويل!", reply_markup=cancel_btn)
                conn.close()
                return

            cursor.execute("UPDATE users SET balance = balance - ?, bot_balance = bot_balance - ?, site_balance = site_balance + ? WHERE telegram_id = ?", (amt, amt, amt, user_id))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحويل <b>{amt:.2f} NSP</b> بنجاح إلى حسابك بالموقع!", parse_mode="HTML")
            await show_main_menu(update, context)
            return

        elif state == 'WAIT_TRANSFER_FROM_SITE':
            try:
                amt = float(text)
                min_site_with = float(get_setting('min_site_withdraw', '100'))
                if amt < min_site_with:
                    await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى للسحب من الموقع ({min_site_with} NSP)!", reply_markup=cancel_btn)
                    conn.close()
                    return
            except ValueError:
                await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!", reply_markup=cancel_btn)
                conn.close()
                return

            u = cursor.execute("SELECT site_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            if u['site_balance'] < amt:
                await update.message.reply_text("❌ رصيدك في الموقع غير كافٍ لهذا السحب!", reply_markup=cancel_btn)
                conn.close()
                return

            cursor.execute("UPDATE users SET site_balance = site_balance - ?, balance = balance + ?, bot_balance = bot_balance + ? WHERE telegram_id = ?", (amt, amt, amt, user_id))
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
                    await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى للشحن ({min_dep} NSP)!", reply_markup=cancel_btn)
                    conn.close()
                    return
            except ValueError:
                await update.message.reply_text("❌ أدخل رقماً صحيحاً للمبلغ!", reply_markup=cancel_btn)
                conn.close()
                return

            context.user_data['dep_amt'] = amt
            context.user_data['state'] = 'WAIT_DEP_TX'
            method = context.user_data.get('selected_method')
            acc_num = get_payment_number(method)
            
            await update.message.reply_text(
                f"✍️ <b>الخطوة الثانية:</b> قم بتحويل مبلغ <b>{amt:.2f} NSP</b> إلى رقم الحساب <code>{acc_num}</code> ({method}).\n\n"
                f"ثم أرسل رقم العملية / رقم التحويل الآن للتأكيد:",
                reply_markup=cancel_btn,
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
                f"• العميل: <b>{user_name}</b>\n"
                f"• الآيدي: <code>{user_id}</code>\n"
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
                    await update.message.reply_text(f"❌ المبلغ أقل من الحد الأدنى للسحب ({min_with} NSP)!", reply_markup=cancel_btn)
                    conn.close()
                    return
            except ValueError:
                await update.message.reply_text("❌ أدخل رقماً صحيحاً للمبلغ!", reply_markup=cancel_btn)
                conn.close()
                return

            u = cursor.execute("SELECT balance, bot_balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            curr_bal = u['bot_balance'] if u['bot_balance'] is not None else u['balance']
            if curr_bal < amt:
                await update.message.reply_text("❌ رصيدك الحالي في البوت غير كافٍ للسحب!", reply_markup=cancel_btn)
                conn.close()
                return

            context.user_data['with_amt'] = amt
            context.user_data['state'] = 'WAIT_WITH_ACC'
            await update.message.reply_text("✍️ <b>الخطوة الثانية:</b> أرسل رقم حسابك / رقم محفظتك لاستلام المبلغ:", reply_markup=cancel_btn)
            conn.close()
            return

        elif state == 'WAIT_WITH_ACC':
            amt = context.user_data.get('with_amt')
            method = context.user_data.get('selected_method')
            acc_target = text

            cursor.execute("UPDATE users SET balance = balance - ?, bot_balance = bot_balance - ? WHERE telegram_id = ?", (amt, amt, user_id))
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
                f"• العميل: <b>{user_name}</b>\n"
                f"• الآيدي: <code>{user_id}</code>\n"
                f"• الوسيلة: <b>{method}</b>\n"
                f"• المبلغ: <b>{amt:.2f} NSP</b>\n"
                f"• رقم حساب المستلم: <code>{html.escape(acc_target)}</code>"
            )
            await show_main_menu(update, context)
            return

        elif state == 'WAIT_WIN_SHOT':
            msg_txt = f"📸 <b>صورة/إشعار فوز من عميل!</b>\n• العميل: <b>{user_name}</b>\n• الآيدي: <code>{user_id}</code>"
            if text:
                msg_txt += f"\n\nالرسالة:\n{html.escape(text)}"
            
            admins = cursor.execute("SELECT telegram_id FROM users WHERE is_admin = 1").fetchall()
            admin_ids = set([a['telegram_id'] for a in admins] + [MAIN_ADMIN_ID])

            if update.message.photo:
                photo_id = update.message.photo[-1].file_id
                for aid in admin_ids:
                    try:
                        await context.bot.send_photo(aid, photo=photo_id, caption=msg_txt, parse_mode="HTML")
                    except Exception: pass
            else:
                await send_all_admins(context, msg_txt)
                
            conn.close()
            context.user_data.clear()
            await update.message.reply_text("✅ تم إرسال الصورة/الإشعار للإدارة بنجاح.")
            await show_main_menu(update, context)
            return

        elif state == 'WAIT_SUPPORT':
            msg_txt = f"💬 <b>رسالة دعم جديدة من عميل!</b>\n• العميل: <b>{user_name}</b>\n• الآيدي: <code>{user_id}</code>"
            if text:
                msg_txt += f"\n\nالرسالة:\n{html.escape(text)}"

            reply_btn = InlineKeyboardMarkup([[InlineKeyboardButton(f"💬 الرد على العميل ({user_name})", callback_data=f"reply_support_{user_id}")]])

            admins = cursor.execute("SELECT telegram_id FROM users WHERE is_admin = 1").fetchall()
            admin_ids = set([a['telegram_id'] for a in admins] + [MAIN_ADMIN_ID])

            if update.message.photo:
                photo_id = update.message.photo[-1].file_id
                for aid in admin_ids:
                    try:
                        await context.bot.send_photo(aid, photo=photo_id, caption=msg_txt, parse_mode="HTML", reply_markup=reply_btn)
                    except Exception: pass
            else:
                await send_all_admins(context, msg_txt, reply_markup=reply_btn)

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
                    u = cursor.execute("SELECT telegram_id, site_username, spins_count, free_spins FROM users WHERE telegram_id = ? OR site_username = ?", (int(text), text)).fetchone()
                else:
                    u = cursor.execute("SELECT telegram_id, site_username, spins_count, free_spins FROM users WHERE site_username = ?", (text,)).fetchone()

                if not u:
                    await update.message.reply_text("❌ لم يتم العثور على عميل بهذا الآيدي أو اسم المستخدم!")
                    conn.close()
                    return
                context.user_data['target_spins_user'] = u['telegram_id']
                context.user_data['state'] = 'ADM_WAIT_SPINS_COUNT'
                curr_spins = u['free_spins'] if u['free_spins'] is not None else u['spins_count']
                await update.message.reply_text(f"👤 العميل: <code>{u['telegram_id']}</code>\n🎡 اللفات الحالية: <b>{curr_spins}</b>\n\n✍️ أدخل عدد اللفات المراد إضافتها:", reply_markup=cancel_btn, parse_mode="HTML")
                conn.close()
                return

            elif state == 'ADM_WAIT_SPINS_COUNT':
                try:
                    cnt = int(text)
                    if cnt <= 0: raise ValueError
                    t_user = context.user_data.get('target_spins_user')
                    cursor.execute("UPDATE users SET spins_count = spins_count + ?, free_spins = free_spins + ? WHERE telegram_id = ?", (cnt, cnt, t_user))
                    conn.commit()
                    await update.message.reply_text(f"✅ تم إضافة <b>{cnt}</b> محاولة لعب للعميل <code>{t_user}</code> بنجاح.", parse_mode="HTML")
                    try:
                        await context.bot.send_message(t_user, f"🎉 <b>تم منحك {cnt} محاولات لعب مجانية في عجلة الحظ من الإدارة!</b>", parse_mode="HTML")
                    except Exception: pass
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً لعدد اللفات!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_ADD_BAL_ID':
                if text.isdigit():
                    u = cursor.execute("SELECT telegram_id, site_username, balance, bot_balance FROM users WHERE telegram_id = ? OR site_username = ?", (int(text), text)).fetchone()
                else:
                    u = cursor.execute("SELECT telegram_id, site_username, balance, bot_balance FROM users WHERE site_username = ?", (text,)).fetchone()

                if not u:
                    await update.message.reply_text("❌ لم يتم العثور على عميل بهذا الآيدي أو اسم المستخدم!")
                    conn.close()
                    return
                context.user_data['target_bal_user'] = u['telegram_id']
                context.user_data['state'] = 'ADM_WAIT_ADD_BAL_AMT'
                curr_bal = u['bot_balance'] if u['bot_balance'] is not None else u['balance']
                await update.message.reply_text(f"👤 العميل: <code>{u['telegram_id']}</code>\n💰 الرصيد الحالي: <b>{curr_bal:.2f} NSP</b>\n\n✍️ أدخل المبلغ المراد إضافته (أو اطرح بإدخال قيم سالبة مثل -50):", reply_markup=cancel_btn, parse_mode="HTML")
                conn.close()
                return

            elif state == 'ADM_WAIT_ADD_BAL_AMT':
                try:
                    amt = float(text)
                    t_user = context.user_data.get('target_bal_user')
                    cursor.execute("UPDATE users SET balance = balance + ?, bot_balance = bot_balance + ? WHERE telegram_id = ?", (amt, amt, t_user))
                    conn.commit()
                    await update.message.reply_text(f"✅ تم تعديل رصيد العميل <code>{t_user}</code> بمقدار: <b>{amt:+.2f} NSP</b>", parse_mode="HTML")
                    try:
                        await context.bot.send_message(t_user, f"🔔 <b>تنبيه من الإدارة:</b>\nتم تعديل رصيد محفظتك بمقدار <b>{amt:+.2f} NSP</b>.", parse_mode="HTML")
                    except Exception: pass
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً للمبلغ!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_BONUS_AMT':
                try:
                    amt = float(text)
                    if amt < 0: raise ValueError
                    set_setting('welcome_bonus', str(amt))
                    await update.message.reply_text(f"🎉 تم تعديل البونص الترحيبي إلى: <b>{amt:.2f} NSP</b>", parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً للبونص!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_MIN_DEP':
                try:
                    amt = float(text)
                    if amt <= 0: raise ValueError
                    set_setting('min_deposit', str(amt))
                    context.user_data['state'] = 'ADM_WAIT_MIN_WITH'
                    await update.message.reply_text(f"✅ تم تحديث الحد الأدنى للشحن إلى <b>{amt} NSP</b>.\n\n✍️ الآن أدخل الحد الأدنى الجديد للسحب بـ NSP:", reply_markup=cancel_btn, parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
                conn.close()
                return

            elif state == 'ADM_WAIT_MIN_WITH':
                try:
                    amt = float(text)
                    if amt <= 0: raise ValueError
                    set_setting('min_withdraw', str(amt))
                    await update.message.reply_text(f"✅ تم تحديث الحد الأدنى للسحب إلى <b>{amt} NSP</b> بنجاح.", parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_SITE_MIN_DEP':
                try:
                    amt = float(text)
                    if amt <= 0: raise ValueError
                    set_setting('min_site_deposit', str(amt))
                    context.user_data['state'] = 'ADM_WAIT_SITE_MIN_WITH'
                    await update.message.reply_text(f"✅ تم تحديث الحد الأدنى للشحن للموقع إلى <b>{amt} NSP</b>.\n\n✍️ الآن أدخل الحد الأدنى الجديد للسحب من الموقع بـ NSP:", reply_markup=cancel_btn, parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
                conn.close()
                return

            elif state == 'ADM_WAIT_SITE_MIN_WITH':
                try:
                    amt = float(text)
                    if amt <= 0: raise ValueError
                    set_setting('min_site_withdraw', str(amt))
                    await update.message.reply_text(f"✅ تم تحديث الحد الأدنى للسحب من الموقع إلى <b>{amt} NSP</b> بنجاح.", parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_PAY_NUMBER':
                method_name = context.user_data.get('edit_pay_method')
                if method_name:
                    cursor.execute("UPDATE payment_methods SET number = ? WHERE name = ?", (text, method_name))
                    conn.commit()
                    await update.message.reply_text(f"✅ تم تحديث رقم حساب {method_name} إلى: <code>{text}</code>", parse_mode="HTML")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_GIFT_AMT':
                try:
                    amt = float(text)
                    if amt <= 0: raise ValueError
                    context.user_data['gift_amt'] = amt
                    context.user_data['state'] = 'ADM_GIFT_MAX_USES'
                    await update.message.reply_text("✍️ <b>خطوة 2/3:</b> أدخل عدد مرات استخدام الكود الواحدة (مثال: 1 للذكاء الفردي أو 100 للعام):", reply_markup=cancel_btn, parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل مبلغاً صحيحاً!")
                conn.close()
                return

            elif state == 'ADM_GIFT_MAX_USES':
                try:
                    uses = int(text)
                    if uses <= 0: raise ValueError
                    context.user_data['gift_max_uses'] = uses
                    context.user_data['state'] = 'ADM_GIFT_COUNT'
                    await update.message.reply_text("✍️ <b>خطوة 3/3:</b> أدخل عدد الأكواد المراد توليدها الآن (مثال: 1 أو 5):", reply_markup=cancel_btn, parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً!")
                conn.close()
                return

            elif state == 'ADM_GIFT_COUNT':
                try:
                    count = int(text)
                    if count <= 0 or count > 50: raise ValueError
                    amt = context.user_data.get('gift_amt')
                    uses = context.user_data.get('gift_max_uses')
                    
                    created_codes = []
                    for _ in range(count):
                        rnd_code = "GIFT-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                        cursor.execute("INSERT INTO gift_codes (code, amount, max_uses, used_count, is_active) VALUES (?, ?, ?, 0, 1)", (rnd_code, amt, uses))
                        created_codes.append(rnd_code)
                    conn.commit()
                    
                    res_txt = f"🎁 <b>تم توليد {count} أكواد هدية بنجاح:</b>\n\n"
                    for c in created_codes:
                        res_txt += f"• <code>{c}</code> (القيمة: {amt} NSP | الاستخدام: {uses})\n"
                    await update.message.reply_text(res_txt, parse_mode="HTML")
                except ValueError:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً بين 1 و 50!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_DISABLE_CODE':
                cursor.execute("UPDATE gift_codes SET is_active = 0 WHERE UPPER(code) = UPPER(?)", (text,))
                conn.commit()
                await update.message.reply_text(f"✅ تم إلغاء تفعيل الكود <code>{html.escape(text)}</code> بنجاح.", parse_mode="HTML")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_CH_USERNAME':
                ch_user = text.strip()
                if not ch_user.startswith('@') and not ch_user.startswith('-'):
                    ch_user = '@' + ch_user
                context.user_data['temp_ch_user'] = ch_user
                context.user_data['state'] = 'ADM_WAIT_CH_NAME'
                await update.message.reply_text("✍️ <b>الخطوة 2/3:</b> أدخل الاسم الظاهر للقناة (مثال: قناة الأخبار الرسمية):", reply_markup=cancel_btn, parse_mode="HTML")
                conn.close()
                return

            elif state == 'ADM_WAIT_CH_NAME':
                context.user_data['temp_ch_name'] = text.strip()
                context.user_data['state'] = 'ADM_WAIT_CH_LINK'
                ch_user = context.user_data.get('temp_ch_user', '')
                default_link = f"https://t.me/{ch_user.replace('@','')}"
                await update.message.reply_text(f"✍️ <b>الخطوة 3/3:</b> أدخل رابط القناة (أو أرسل '-' لاستخدام الرابط الافتراضي: <code>{default_link}</code>):", reply_markup=cancel_btn, parse_mode="HTML")
                conn.close()
                return

            elif state == 'ADM_WAIT_CH_LINK':
                ch_user = context.user_data.get('temp_ch_user')
                ch_name = context.user_data.get('temp_ch_name')
                ch_link = text.strip()
                if ch_link == '-' or not ch_link.startswith('http'):
                    ch_link = f"https://t.me/{ch_user.replace('@','')}"
                
                channels = get_forced_channels_list()
                channels.append({"name": ch_name, "username": ch_user, "link": ch_link})
                set_setting('forced_channels', json.dumps(channels, ensure_ascii=False))
                
                context.user_data.clear()
                await update.message.reply_text(f"✅ تم إضافة القناة <b>{html.escape(ch_name)}</b> لقائمة الاشتراك الإجباري بنجاح!", parse_mode="HTML")
                conn.close()
                return

            elif state == 'ADM_WAIT_NEW_ADMIN':
                if text.isdigit():
                    new_aid = int(text)
                    cursor.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (new_aid,))
                    cursor.execute("UPDATE users SET is_admin = 1 WHERE telegram_id = ?", (new_aid,))
                    conn.commit()
                    await update.message.reply_text(f"✅ تم تعيين المستخدم <code>{new_aid}</code> كـ آدمن في البوت بنجاح.", parse_mode="HTML")
                else:
                    await update.message.reply_text("❌ أدخل آيدي رقمي صحيح!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_USER_DETAILS':
                if text.isdigit():
                    u = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR site_username = ?", (int(text), text)).fetchone()
                else:
                    u = cursor.execute("SELECT * FROM users WHERE site_username = ?", (text,)).fetchone()

                if not u:
                    await update.message.reply_text("❌ لم يتم العثور على عميل بهذة البيانات!")
                else:
                    spins = u['free_spins'] if u['free_spins'] is not None else u['spins_count']
                    bot_bal = u['bot_balance'] if u['bot_balance'] is not None else u['balance']
                    site_usr = u['site_username'] or "غير مربوط"
                    site_pw = u['site_password'] or "غير متوفر"
                    ref_by = u['referred_by'] or "لا يوجد"
                    is_b = "نعم 🚫" if u['is_banned'] else "لا ✅"
                    is_adm = "نعم 👑" if u['is_admin'] else "لا"

                    info_txt = (
                        f"👤 <b>تفاصيل العميل الشاملة:</b>\n\n"
                        f"• الآيدي: <code>{u['telegram_id']}</code>\n"
                        f"• المعرف: @{u['username'] or 'لا يوجد'}\n"
                        f"• حساب الموقع: <code>{html.escape(site_usr)}</code>\n"
                        f"• كلمة السر: <code>{html.escape(site_pw)}</code>\n"
                        f"• رصيد البوت: <b>{bot_bal:.2f} NSP</b>\n"
                        f"• رصيد الموقع: <b>{u['site_balance']:.2f} NSP</b>\n"
                        f"• محاولات العجلة: <b>{spins}</b>\n"
                        f"• إجمالي مرات الشحن: <code>{u['deposit_count']}</code>\n"
                        f"• إجمالي مرات السحب: <code>{u['withdraw_count']}</code>\n"
                        f"• عدد الإحالات: <code>{u['referrals_count']}</code>\n"
                        f"• تم الإحالة بواسطة: <code>{ref_by}</code>\n"
                        f"• آدمن: {is_adm}\n"
                        f"• محظور: {is_b}\n"
                        f"• تاريخ الانضمام: <code>{u['created_at']}</code>"
                    )
                    await update.message.reply_text(info_txt, parse_mode="HTML")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_BAN_ID':
                if text.isdigit():
                    ban_id = int(text)
                    cursor.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (ban_id,))
                    conn.commit()
                    await update.message.reply_text(f"🚫 تم حظر المستخدم <code>{ban_id}</code> بنجاح.", parse_mode="HTML")
                else:
                    await update.message.reply_text("❌ أدخل آيدي رقمي صحيح!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_UNBAN_ID':
                if text.isdigit():
                    unban_id = int(text)
                    cursor.execute("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (unban_id,))
                    conn.commit()
                    await update.message.reply_text(f"✅ تم إلغاء حظر المستخدم <code>{unban_id}</code> بنجاح.", parse_mode="HTML")
                else:
                    await update.message.reply_text("❌ أدخل آيدي رقمي صحيح!")
                context.user_data.clear()
                conn.close()
                return

            elif state == 'ADM_WAIT_BROADCAST':
                users = cursor.execute("SELECT telegram_id FROM users WHERE is_banned = 0").fetchall()
                conn.close()
                context.user_data.clear()
                
                bc_msg = update.message.text or update.message.caption or ""
                success, failed = 0, 0
                
                await update.message.reply_text(f"📢 جاري جلب المستلمون وإرسال الإذاعة لـ <b>{len(users)}</b> عميل...", parse_mode="HTML")
                
                for u_row in users:
                    try:
                        if update.message.photo:
                            await context.bot.send_photo(u_row['telegram_id'], photo=update.message.photo[-1].file_id, caption=bc_msg, parse_mode="HTML")
                        else:
                            await context.bot.send_message(u_row['telegram_id'], text=bc_msg, parse_mode="HTML")
                        success += 1
                        await asyncio.sleep(0.04)
                    except Exception:
                        failed += 1
                        
                await update.message.reply_text(f"📊 <b>نتيجة الإذاعة العامة:</b>\n\n✅ نجاح الإرسال: {success}\n❌ فشل الإرسال (حظر البوت): {failed}", parse_mode="HTML")
                return

            elif state == 'ADM_WAIT_PRIV_ID':
                if text.isdigit():
                    priv_id = int(text)
                    u = cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (priv_id,)).fetchone()
                    if not u:
                        await update.message.reply_text("❌ هذا المستخدم غير موجود بالم قاعدة البيانات!")
                        conn.close()
                        return
                    context.user_data['target_priv_id'] = priv_id
                    context.user_data['state'] = 'ADM_WAIT_PRIV_MSG'
                    await update.message.reply_text(f"💬 أدخل نص أو صورة الرسالة المراد إرسالها للعميل <code>{priv_id}</code>:", reply_markup=cancel_btn, parse_mode="HTML")
                else:
                    await update.message.reply_text("❌ أدخل آيدي رقمي صحيح!")
                conn.close()
                return

            elif state == 'ADM_WAIT_PRIV_MSG':
                target_id = context.user_data.get('target_priv_id')
                p_text = f"📩 <b>رسالة خاصة من إدارة المنصة:</b>\n\n{html.escape(text)}" if text else "📩 <b>رسالة خاصة من إدارة المنصة:</b>"
                
                try:
                    if update.message.photo:
                        await context.bot.send_photo(target_id, photo=update.message.photo[-1].file_id, caption=p_text, parse_mode="HTML")
                    else:
                        await context.bot.send_message(target_id, text=p_text, parse_mode="HTML")
                    await update.message.reply_text(f"✅ تم إرسال الرسالة الخاصة للعميل <code>{target_id}</code> بنجاح.", parse_mode="HTML")
                except Exception as ex:
                    await update.message.reply_text(f"❌ تعذر إرسال الرسالة للعميل: {ex}")
                    
                context.user_data.clear()
                conn.close()
                return

            elif state == 'WAIT_ADMIN_REPLY_SUPP':
                target_id = context.user_data.get('support_target')
                reply_text = f"💬 <b>رد من الدعم الفني:</b>\n\n{html.escape(text)}" if text else "💬 <b>رد من الدعم الفني:</b>"
                
                try:
                    if update.message.photo:
                        await context.bot.send_photo(target_id, photo=update.message.photo[-1].file_id, caption=reply_text, parse_mode="HTML")
                    else:
                        await context.bot.send_message(target_id, text=reply_text, parse_mode="HTML")
                    await update.message.reply_text(f"✅ تم إرسال الرد للعميل <code>{target_id}</code> بنجاح.", parse_mode="HTML")
                except Exception as ex:
                    await update.message.reply_text(f"❌ تعذر إرسال الرد: {ex}")
                    
                context.user_data.clear()
                conn.close()
                return

    except Exception as e:
        logging.error(f"Error handling message state ({state}): {e}")
        try: conn.close()
        except Exception: pass
        await update.message.reply_text("❌ حدث خطأ غير متوقع. يرجى إعادة المحاولة.")
    finally:
        try: conn.close()
        except Exception: pass

# ==========================================================
# 7. تشغيل البوت مع تطبيق Flask و Gunicorn
# ==========================================================
def run_telegram_bot():
    global MAIN_LOOP, bot_app
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    MAIN_LOOP = loop
    
    init_db()
    
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(callback_router))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    
    logging.info("Starting AUREX Telegram Bot Polling...")
    bot_app.run_polling(close_loop=False)

# تشغيل خيط بوت التلغرام في الخلفية تلقائياً لتوافق Gunicorn و Python المباشر
bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
