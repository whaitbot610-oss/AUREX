import os
import re
import sqlite3
import logging
import random
import string
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes
)

# ==========================================================
# 1. الإعدادات الأساسية
# ==========================================================
MAIN_ADMIN_ID = 7255100997  # الآدمن الرئيسي
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8948439052:AAHv-UWeTMQmHybxspFRVRpnjIqetmW8LbI").strip()
SERVER_URL = os.environ.get("SERVER_URL", "https://aurex-my-bot.onrender.com").strip()

if not SERVER_URL.startswith("https://"):
    SERVER_URL = "https://" + SERVER_URL.replace("http://", "")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================================
# 2. إدارة قاعدة البيانات الموحدة (WAL Mode + Auto Migration)
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
    
    # جدول المستخدمين
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
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
        is_admin INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        code_restricted_until TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # --- ميزة التحديث التلقائي للأعمدة المفقودة (Auto Migration) ---
    required_columns = {
        'site_username': 'TEXT',
        'site_password': 'TEXT',
        'balance': 'REAL DEFAULT 0.0',
        'total_spent': 'REAL DEFAULT 0.0',
        'deposit_count': 'INTEGER DEFAULT 0',
        'withdraw_count': 'INTEGER DEFAULT 0',
        'referrals_count': 'INTEGER DEFAULT 0',
        'referred_by': 'INTEGER',
        'got_welcome_bonus': 'INTEGER DEFAULT 0',
        'security_passed': 'INTEGER DEFAULT 0',
        'is_admin': 'INTEGER DEFAULT 0',
        'is_banned': 'INTEGER DEFAULT 0',
        'code_restricted_until': 'TIMESTAMP',
        'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        'last_active': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    }
    
    cursor.execute("PRAGMA table_info(users)")
    existing_cols = [col['name'] for col in cursor.fetchall()]
    
    for col_name, col_type in required_columns.items():
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                logging.error(f"Error adding column {col_name}: {e}")
    
    # جدول المعاملات المالية
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
    
    # جدول أكواد الهدايا
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (
        code TEXT PRIMARY KEY, 
        amount REAL, 
        max_uses INTEGER, 
        used_count INTEGER DEFAULT 0, 
        is_active INTEGER DEFAULT 1
    )''')
    
    # سجل الأكواد المستعملة
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_codes (
        telegram_id INTEGER, 
        code TEXT, 
        PRIMARY KEY (telegram_id, code)
    )''')
    
    # جدول حسابات الدفع
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        name TEXT UNIQUE, 
        number TEXT, 
        active INTEGER DEFAULT 1
    )''')
    
    # جدول الإعدادات العامة والخزينة
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, 
        value TEXT
    )''')

    # القيم الافتراضية للضبط
    defaults = [
        ('win_rate', '30'),
        ('maintenance', '0'),
        ('welcome_bonus', '500'),
        ('welcome_bonus_enabled', '1'),
        ('min_deposit', '50'),
        ('min_withdraw', '100'),
        ('cashier_balance', '10000.0'),
        ('jackpot_balance', '254005482.0'),
        ('forced_channels', '')
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(val)))
        
    cursor.execute("INSERT OR IGNORE INTO payment_methods (name, number) VALUES ('سيريتل كاش', '0987654321')")
    cursor.execute("INSERT OR IGNORE INTO payment_methods (name, number) VALUES ('شام كاش', 'test')")
    
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
    cursor.execute("SELECT value FROM settings WHERE key = 'cashier_balance'")
    row = cursor.fetchone()
    before_balance = float(row['value']) if row else 0.0
    after_balance = max(0.0, before_balance + amount_change)
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cashier_balance', ?)", (str(after_balance),))
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

# ==========================================================
# 3. التحقق ودوال الأمان (شروط الحساب الجديدة)
# ==========================================================
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
# 4. معالجة الأوامر والقوائم الرئيسية
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    cursor = conn.cursor()

    db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
    
    # فحص آمن للحظر لتفادي خطأ المفتاح
    if db_user and ('is_banned' in db_user.keys()) and db_user['is_banned']:
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
        await update.message.reply_text("🚨 **يجب عليك الاشتراك بجميع القنوات التالية أولاً لاستخدام البوت:**", reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
        return

    ref_by = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user.id else None
    
    if not db_user:
        is_main_admin = 1 if user.id == MAIN_ADMIN_ID else 0
        cursor.execute(
            "INSERT INTO users (telegram_id, username, referred_by, is_admin) VALUES (?, ?, ?, ?)", 
            (user.id, user.username or user.first_name, ref_by, is_main_admin)
        )
        conn.commit()
        
        try:
            ref_txt = f"`{ref_by}`" if ref_by else "بدون إحالة"
            await context.bot.send_message(
                MAIN_ADMIN_ID, 
                f"👤 **عضو جديد انضم للبوت!**\n\n• الاسم: {user.full_name}\n• المعرف: @{user.username or 'لا يوجد'}\n• الآيدي: `{user.id}`\n• الإحالة بواسطة: {ref_txt}", 
                parse_mode="Markdown"
            )
        except Exception: pass

        if ref_by:
            cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE telegram_id = ?", (ref_by,))
            conn.commit()
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

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ تم إلغاء العملية الحالية وتفريغ البيانات.")
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
        f"💎 رصيد الموقع الموحد: **{balance:.2f} NSP**\n"
        f"──────────────────"
    )

    keyboard = [
        [InlineKeyboardButton("🌐 فتح الكازينو (WebApp)", web_app=WebAppInfo(url=SERVER_URL))],
        [InlineKeyboardButton("🔑 إنشاء / تعديل حساب الموقع", callback_data="create_site_account"), InlineKeyboardButton("🔐 بيانات حسابي", callback_data="my_account")],
        [InlineKeyboardButton("📥 شحن رصيد الموقع", callback_data="dep_menu"), InlineKeyboardButton("📤 سحب رصيد الموقع", callback_data="with_menu")],
        [InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="claim_gift")],
        [InlineKeyboardButton("📸 إرسال صورة إصابة", callback_data="send_win_shot"), InlineKeyboardButton("💬 مراسلة الدعم", callback_data="contact_support")],
        [InlineKeyboardButton("📜 سجلاتي المالية", callback_data="my_logs")]
    ]

    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم الإدارية (الآدمن)", callback_data="admin_panel")])

    chat = update.effective_chat
    await chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ==========================================================
# 5. معالج التفاعلات والأزرار (Callback Router)
# ==========================================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    conn = get_db()
    cursor = conn.cursor()

    if data == "sec_correct":
        user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        bonus_enabled = get_setting('welcome_bonus_enabled', '1') == '1'
        bonus_amt = float(get_setting('welcome_bonus', '500.0'))
        
        if bonus_enabled and bonus_amt > 0 and user and user['got_welcome_bonus'] == 0:
            before_cashier, after_cashier = update_cashier(-bonus_amt)
            cursor.execute("UPDATE users SET security_passed = 1, got_welcome_bonus = 1, balance = balance + ? WHERE telegram_id = ?", (bonus_amt, user_id))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"كفو عليك! 🍯\n🎉 **تم توثيق حسابك بنجاح وحصلت على بونص ترحيبي بقيمة {bonus_amt:.2f} NSP!**", parse_mode="Markdown")
        else:
            cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("كفو عليك! 🍯 تم توثيق حسابك بنجاح.")

        await show_main_menu(update, context)
        return

    elif data == "sec_wrong":
        conn.close()
        await query.message.edit_text("إجابة خاطئة! حاول مرة أخرى بالضغط على /start ❌")
        return

    conn.close()

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
            await update.effective_chat.send_message(f"🔐 **بيانات حسابك المربوط في الموقع:**\n\n👤 اسم المستخدم: `{u['site_username']}`\n🔑 كلمة المرور: `{u['site_password']}`", parse_mode="Markdown")
        else:
            await update.effective_chat.send_message("❌ ليس لديك حساب مربوط بعد! استخدم زر (إنشاء / تعديل حساب الموقع).")

    elif data == "create_site_account":
        context.user_data['state'] = 'WAIT_SITE_USER'
        keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]]
        await update.effective_chat.send_message(
            "🔑 **إنشاء / ربط حساب جديد للموقع:**\n\n"
            "✍️ أدخل اسم المستخدم الجديد (يجب أن يتكون من 6 أحرف/أرقام إنجليزية على الأقل وبدون رموز):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "dep_menu":
        conn = get_db()
        u = conn.execute("SELECT site_username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ **تنبيه:** ينبغي عليك إنشاء أو ربط حساب موقع أولاً لتتمكن من الشحن والسحب!")
            return

        min_dep = get_setting('min_deposit', '50')
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="dep_method_سيريتل كاش")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="dep_method_شام كاش")],
            [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
        ]
        await update.effective_chat.send_message(f"📥 **اختر طريقة الشحن المناسبة:**\n📌 الحد الأدنى للشحن: `{min_dep} NSP`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("dep_method_"):
        method_name = data.replace("dep_method_", "")
        acc_num = get_payment_number(method_name)
        min_dep = get_setting('min_deposit', '50')
        context.user_data['selected_method'] = method_name
        context.user_data['state'] = 'WAIT_DEP_AMT'
        
        await update.effective_chat.send_message(
            f"💳 **طريقة الشحن:** {method_name}\n"
            f"📌 **رقم الحساب للتحويل:** `{acc_num}`\n"
            f"⚠️ **الحد الأدنى:** `{min_dep} NSP`\n\n"
            f"✍️ **الخطوة الأولى:** أرسل المبلغ المراد شحنه بعملة NSP الآن:",
            parse_mode="Markdown"
        )

    elif data == "with_menu":
        conn = get_db()
        u = conn.execute("SELECT site_username FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        if not u or not u['site_username']:
            await update.effective_chat.send_message("⚠️ **تنبيه:** ينبغي عليك إنشاء أو ربط حساب موقع أولاً لتتمكن من الشحن والسحب!")
            return

        min_with = get_setting('min_withdraw', '100')
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="with_method_سيريتل كاش")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="with_method_شام كاش")],
            [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
        ]
        await update.effective_chat.send_message(f"📤 **اختر طريقة السحب المناسبة:**\n📌 الحد الأدنى للسحب: `{min_with} NSP`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("with_method_"):
        method_name = data.replace("with_method_", "")
        min_with = get_setting('min_withdraw', '100')
        context.user_data['selected_method'] = method_name
        context.user_data['state'] = 'WAIT_WITH_AMT'
        
        await update.effective_chat.send_message(
            f"📤 **طريقة السحب:** {method_name}\n"
            f"📌 **الحد الأدنى للسحب:** `{min_with} NSP`\n\n"
            f"✍️ **الخطوة الأولى:** أرسل المبلغ المراد سحبه بعملة NSP:",
            parse_mode="Markdown"
        )

    elif data == "my_ref":
        me = await context.bot.get_me()
        await update.effective_chat.send_message(f"🔗 **رابط إحالتي الشخصي:**\n`https://t.me/{me.username}?start={user_id}`\n\n📢 سيصلك إشعار فوري عند دخول أي عميل جديد عبر رابطك!", parse_mode="Markdown")

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
        txt = "📜 **سجل آخر عملياتك:**\n\n"
        for l in logs:
            txt += f"• {l['type']} | الوسيلة: {l['method'] or 'عام'} | المبلغ: {l['amount']} NSP | الحالة: {l['status']}\n"
        await update.effective_chat.send_message(txt, parse_mode="Markdown")

    elif data == "admin_panel" and is_admin(user_id):
        await show_admin_panel(update, context)

    elif data == "adm_cashier" and is_admin(user_id):
        bal = get_cashier_balance()
        await update.effective_chat.send_message(f"🏦 **رصيد الكاشيرة الحالي:** `{bal:.2f} NSP`", parse_mode="Markdown")

    elif data == "adm_edit_user_bal" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_ADD_BAL_ID'
        await update.effective_chat.send_message("👤 أدخل آيدي العميل أو اسم المستخدم بالجروب المراد تعديل رصيده:")

    elif data == "adm_set_bonus" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_BONUS_AMT'
        await update.effective_chat.send_message("🎁 أدخل قيمة البونص الترحيبي الجديد بـ NSP:")

    elif data == "adm_toggle_bonus_state" and is_admin(user_id):
        curr = get_setting('welcome_bonus_enabled', '1')
        new_val = '0' if curr == '1' else '1'
        set_setting('welcome_bonus_enabled', new_val)
        txt = "❌ تم **تعطيل** البونص الترحيبي نهائياً." if new_val == '0' else "✅ تم **تفعيل** البونص الترحيبي للعملاء الجدد."
        await update.effective_chat.send_message(txt, parse_mode="Markdown")

    elif data == "adm_set_limits" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_MIN_DEP'
        await update.effective_chat.send_message("📥 أدخل الحد الأدنى للشحن بـ NSP:")

    elif data == "adm_set_rtp" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_RTP'
        await update.effective_chat.send_message("🎯 أدخل نسبة فوز ألعاب الكازينو RTP (من 0 إلى 100):")

    elif data == "adm_pay_methods" and is_admin(user_id):
        s_num = get_payment_number("سيريتل كاش")
        sh_num = get_payment_number("شام كاش")
        keyboard = [
            [InlineKeyboardButton("✏️ تعديل سيريتل كاش", callback_data="adm_edit_pay_سيريتل كاش")],
            [InlineKeyboardButton("✏️ تعديل شام كاش", callback_data="adm_edit_pay_شام كاش")],
            [InlineKeyboardButton("⚙️ لوحة الآدمن", callback_data="admin_panel")]
        ]
        await update.effective_chat.send_message(
            f"💳 **حسابات الدفع الحالية:**\n\n"
            f"📱 سيريتل كاش: `{s_num}`\n"
            f"💳 شام كاش: `{sh_num}`\n\nاختر الحساب المراد تعديله:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
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
                f"📥 **طلب {r['type']}**\n"
                f"• الوسيلة: **{r['method']}**\n"
                f"• العميل: `{r['telegram_id']}`\n"
                f"• المبلغ: **{r['amount']} NSP**\n"
                f"• الرقم/العملية: `{r['tx_number']}`", 
                reply_markup=btns, 
                parse_mode="Markdown"
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
                
                await context.bot.send_message(user_target, f"✅ **تم قبول طلب الشحن!**\nتم إضافة {amt:.2f} NSP إلى رصيد حسابك بنجاح.")
                await query.message.edit_text(
                    f"✅ **تم قبول طلب الإيداع!**\n"
                    f"• العميل: `{user_target}`\n"
                    f"• المبلغ: {amt} NSP\n"
                    f"🏦 **رصيد الكاشيرة قبل:** `{before_cashier:.2f} NSP`\n"
                    f"🏦 **رصيد الكاشيرة بعد:** `{after_cashier:.2f} NSP`",
                    parse_mode="Markdown"
                )

            elif 'withdraw' in r['type']:
                before_cashier, after_cashier = update_cashier(-amt)
                conn.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (req_id,))
                conn.execute("UPDATE users SET withdraw_count = withdraw_count + 1 WHERE telegram_id = ?", (user_target,))
                conn.commit()

                await context.bot.send_message(user_target, f"✅ **تم قبول طلب السحب!**\nتم تحويل {amt:.2f} NSP بنجاح إلى حسابك.")
                await query.message.edit_text(
                    f"✅ **تم قبول طلب السحب!**\n"
                    f"• العميل: `{user_target}`\n"
                    f"• المبلغ: {amt} NSP\n"
                    f"🏦 **رصيد الكاشيرة قبل:** `{before_cashier:.2f} NSP`\n"
                    f"🏦 **رصيد الكاشيرة بعد:** `{after_cashier:.2f} NSP`",
                    parse_mode="Markdown"
                )
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
            
            await context.bot.send_message(r['telegram_id'], f"❌ تم رفض طلب الـ {r['type']} بقيمة {r['amount']} NSP وتم إعادة الرصيد لحسابك.")
            await query.message.edit_text("❌ تم رفض الطلب وإبلاغ العميل.")
        conn.close()

    elif data == "adm_gen_batch" and is_admin(user_id):
        context.user_data['state'] = 'ADM_GIFT_AMT'
        await update.effective_chat.send_message("أدخل قيمة الكود الواحد بـ NSP:")

    elif data == "adm_view_codes" and is_admin(user_id):
        conn = get_db()
        codes = conn.execute("SELECT * FROM gift_codes WHERE is_active = 1 AND used_count < max_uses LIMIT 15").fetchall()
        conn.close()
        if not codes:
            await update.effective_chat.send_message("❌ لا يوجد أكواد هدية مفعالة وحالية.")
            return
        txt = "🎁 **قائمة الأكواد المفعالة:**\n\n"
        for c in codes:
            txt += f"• الكود: `{c['code']}` | القيمة: `{c['amount']} NSP` | الاستخدام: `{c['used_count']}/{c['max_uses']}`\n"
        await update.effective_chat.send_message(txt, parse_mode="Markdown")

    elif data == "adm_edit_channels" and is_admin(user_id):
        curr = get_setting('forced_channels', '')
        context.user_data['state'] = 'ADM_WAIT_CHANNELS'
        await update.effective_chat.send_message(f"📢 **القنوات الحالية:** `{curr or 'لا يوجد'}`\n\nأدخل معرّفات القنوات مفصولة بفاصلة (مثال: `@chan1,@chan2`):", parse_mode="Markdown")

    elif data == "adm_add_admin" and is_admin(user_id):
        context.user_data['state'] = 'ADM_WAIT_NEW_ADMIN'
        await update.effective_chat.send_message("أدخل آيدي الآدمن الجديد المراد إضافته:")

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
        await update.effective_chat.send_message(f"📊 **إحصائيات العملاء والمستخدمين:**\n\n• إجمالي المسجلين: `{tot}`\n• النشطين خلال 24 ساعة: `{active_today}`\n• إجمالي أرصدة الموقع: `{bal:.2f} NSP`", parse_mode="Markdown")

    elif data.startswith("reply_support_") and is_admin(user_id):
        target = int(data.split("_")[2])
        context.user_data['support_target'] = target
        context.user_data['state'] = 'WAIT_ADMIN_REPLY_SUPP'
        await update.effective_chat.send_message(f"💬 اكتب الرد للعميل `{target}`:", parse_mode="Markdown")

    elif data == "main_menu":
        await show_main_menu(update, context)

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bonus_state = "مفعل ✅" if get_setting('welcome_bonus_enabled', '1') == '1' else "معطل ❌"
    keyboard = [
        [InlineKeyboardButton("🏦 رصيد الكاشيرة", callback_data="adm_cashier"), InlineKeyboardButton("📥📤 طلبات الشحن والسحب", callback_data="adm_requests")],
        [InlineKeyboardButton("💳 تعديل حسابات الدفع", callback_data="adm_pay_methods"), InlineKeyboardButton("💰 تعديل رصيد مستخدم", callback_data="adm_edit_user_bal")],
        [InlineKeyboardButton(f"🎁 حالة البونص ({bonus_state})", callback_data="adm_toggle_bonus_state"), InlineKeyboardButton("🎁 قيمة البونص الترحيبي", callback_data="adm_set_bonus")],
        [InlineKeyboardButton("📉 تعديل حدود الشحن والسحب", callback_data="adm_set_limits"), InlineKeyboardButton("🎯 تعديل نسبة الربح (RTP)", callback_data="adm_set_rtp")],
        [InlineKeyboardButton("🎁 توليد أكواد هدية", callback_data="adm_gen_batch"), InlineKeyboardButton("📋 الأكواد النشطة", callback_data="adm_view_codes")],
        [InlineKeyboardButton("❌ إلغاء تفعيل كود", callback_data="adm_disable_code"), InlineKeyboardButton("📢 قنوات الاشتراك الإجباري", callback_data="adm_edit_channels")],
        [InlineKeyboardButton("🔍 تفاصيل عميل", callback_data="adm_user_details"), InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats")],
        [InlineKeyboardButton("🛠 وضع الصيانة", callback_data="adm_toggle_maint"), InlineKeyboardButton("👮‍♂️ إضافة آدمن", callback_data="adm_add_admin")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_user"), InlineKeyboardButton("✅ إلغاء حظر", callback_data="adm_unban_user")],
        [InlineKeyboardButton("📢 إرسال جماعي", callback_data="adm_broadcast"), InlineKeyboardButton("✉️ إرسال خاص", callback_data="adm_private_msg")],
        [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]
    ]
    await update.effective_chat.send_message("⚙️ **لوحة التحكم الشاملة للآدمن:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ==========================================================
# 6. معالجة النصوص والرسائل الحرة
# ==========================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    text = msg.text.strip() if msg.text else ""
    state = context.user_data.get('state')

    if is_admin(user_id) and msg.reply_to_message:
        replied_text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        match = re.search(r"🆔 العميل:\s*`?(\d+)`?", replied_text)
        if match:
            target_id = int(match.group(1))
            try:
                await context.bot.send_message(target_id, f"💬 **رد الدعم الفني:**\n\n{text}", parse_mode="Markdown")
                await msg.reply_text(f"✅ تم إرسال الرد بنجاح للعميل `{target_id}`", parse_mode="Markdown")
                return
            except Exception as e:
                await msg.reply_text(f"❌ تعذر إرسال الرد: {e}")
                return

    if not state: 
        return
        
    conn = get_db()
    cursor = conn.cursor()

    if state == 'WAIT_SITE_USER':
        if not validate_username(text):
            await update.message.reply_text("❌ اسم المستخدم يجب أن يكون 6 خانات على الأقل (أحرف إنجليزية وأرقام وبدون رموز). أعد الإدخال:")
            return
        context.user_data['temp_site_user'] = text
        context.user_data['state'] = 'WAIT_SITE_PASS'
        await update.message.reply_text("🔑 أدخل كلمة المرور (6 خانات على الأقل ويجب أن تحتوي أحرف وأرقام معاً):")

    elif state == 'WAIT_SITE_PASS':
        if not validate_password(text):
            await update.message.reply_text("❌ كلمة المرور غير صالحة! يجب أن تتكون من 6 خانات على الأقل وتدمج بين الأحرف والأرقام. أعد الإدخال:")
            return
        site_u = context.user_data.get('temp_site_user')
        try:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?", (site_u, str(text), user_id))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text(f"✅ **تم ربط حساب الموقع بنجاح!**\n👤 اسم المستخدم: `{site_u}`\n🔑 كلمة المرور: `{text}`", parse_mode="Markdown")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ اسم المستخدم مأخوذ بالفعل، أدخل اسم آخر:")
            context.user_data['state'] = 'WAIT_SITE_USER'

    elif state == 'WAIT_DEP_AMT':
        try:
            amt = float(text)
            min_dep = float(get_setting('min_deposit', '50'))
            if amt < min_dep:
                await update.message.reply_text(f"❌ الحد الأدنى للشحن هو {min_dep} NSP! أعد أدخل مبلغ ألمنشور:")
                return
            context.user_data['dep_amt'] = amt
            context.user_data['state'] = 'WAIT_DEP_TX'
            await update.message.reply_text("✍️ **الخطوة الثانية:** أدخل رقم عملية/إشعار التحويل الآن:")
        except ValueError: 
            await update.message.reply_text("❌ أدخل مبلغ صحيح أكبر من 0.")

    elif state == 'WAIT_DEP_TX':
        amt = context.user_data.get('dep_amt')
        method_name = context.user_data.get('selected_method', 'غير محدد')
        
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, ?, ?, ?, ?)", 
                       (user_id, "deposit", method_name, amt, text))
        conn.commit()
        context.user_data.clear()
        
        await update.message.reply_text("✅ **تم رفع طلب الشحن للإدارة بنجاح وسيتم معالجته قريباً.**")
        
        btns = InlineKeyboardMarkup([[InlineKeyboardButton("📥 عرض الطلبات", callback_data="adm_requests")]])
        await context.bot.send_message(
            MAIN_ADMIN_ID, 
            f"📥 **طلب شحن جديد!**\n\n• العميل: `{user_id}`\n• طريقة الدفع: **{method_name}**\n• المبلغ: **{amt} NSP**\n• رقم العملية: `{text}`", 
            reply_markup=btns,
            parse_mode="Markdown"
        )

    elif state == 'WAIT_WITH_AMT':
        try:
            amt = float(text)
            min_with = float(get_setting('min_withdraw', '100'))
            u = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            
            if amt < min_with:
                await update.message.reply_text(f"❌ الحد الأدنى للسحب هو {min_with} NSP!")
                return
            if not u or u['balance'] < amt or amt <= 0:
                await update.message.reply_text("❌ رصيدك الحالي لا يكفي لهذا السحب.")
                return
                
            context.user_data['with_amt'] = amt
            context.user_data['state'] = 'WAIT_WITH_TX'
            method_name = context.user_data.get('selected_method', 'غير محدد')
            await update.message.reply_text(f"✍️ **الخطوة الثانية:** أدخل رقم حسابك على ({method_name}) لاستلام المبلغ:")
        except ValueError: 
            await update.message.reply_text("❌ أدخل مبلغ صحيح.")

    elif state == 'WAIT_WITH_TX':
        amt = context.user_data.get('with_amt')
        method_name = context.user_data.get('selected_method', 'غير محدد')
        
        cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amt, user_id))
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, ?, ?, ?, ?)", 
                       (user_id, "withdraw", method_name, amt, text))
        conn.commit()
        context.user_data.clear()
        
        await update.message.reply_text("✅ **تم خصم المبلغ كمعلق ورفع طلب السحب للإدارة بنجاح.**")
        
        btns = InlineKeyboardMarkup([[InlineKeyboardButton("📤 عرض الطلبات", callback_data="adm_requests")]])
        await context.bot.send_message(
            MAIN_ADMIN_ID, 
            f"📤 **طلب سحب جديد!**\n\n• العميل: `{user_id}`\n• الوسيلة: **{method_name}**\n• المبلغ: **{amt} NSP**\n• حساب الاستلام: `{text}`", 
            reply_markup=btns,
            parse_mode="Markdown"
        )

    elif state == 'ADM_WAIT_MIN_DEP' and is_admin(user_id):
        set_setting('min_deposit', text)
        context.user_data['state'] = 'ADM_WAIT_MIN_WITH'
        await update.message.reply_text("📤 أدخل الحد الأدنى للسحب بـ NSP:")

    elif state == 'ADM_WAIT_MIN_WITH' and is_admin(user_id):
        set_setting('min_withdraw', text)
        context.user_data.clear()
        await update.message.reply_text("✅ تم تحديث حدود الشحن والسحب بنجاح.")

    elif state == 'ADM_WAIT_CHANNELS' and is_admin(user_id):
        set_setting('forced_channels', text)
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم تحديث قنوات الاشتراك الإجباري إلى:\n`{text}`", parse_mode="Markdown")

    elif state == 'ADM_WAIT_PAY_NUMBER' and is_admin(user_id):
        method_name = context.user_data.get('edit_pay_method')
        cursor.execute("INSERT OR REPLACE INTO payment_methods (name, number) VALUES (?, ?)", (method_name, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم تحديث رقم/حساب **{method_name}** إلى: `{text}`", parse_mode="Markdown")

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
        except ValueError: 
            await update.message.reply_text("أدخل مبلغ صحيح.")

    elif state == 'ADM_WAIT_BONUS_AMT' and is_admin(user_id):
        set_setting('welcome_bonus', text)
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم تعديل قيمة البونص الترحيبي إلى {text} NSP.")

    elif state == 'ADM_WAIT_RTP' and is_admin(user_id):
        set_setting('win_rate', text)
        context.user_data.clear()
        await update.message.reply_text(f"🎯 تم تعديل خوارزمية RTP إلى {text}%.")

    elif state == 'ADM_GIFT_AMT' and is_admin(user_id):
        try:
            context.user_data['gift_amt'] = float(text)
            context.user_data['state'] = 'ADM_GIFT_USES'
            await update.message.reply_text("أدخل عدد مرات الاستخدام المتاحة لكل كود:")
        except ValueError: 
            await update.message.reply_text("أدخل رقم صحيح.")

    elif state == 'ADM_GIFT_USES' and is_admin(user_id):
        try:
            context.user_data['gift_uses'] = int(text)
            context.user_data['state'] = 'ADM_GIFT_COUNT'
            await update.message.reply_text("أدخل كمية الأكواد المراد توليدها:")
        except ValueError: 
            await update.message.reply_text("أدخل عدد صحيح.")

    elif state == 'ADM_GIFT_COUNT' and is_admin(user_id):
        try:
            count = int(text)
            amt = context.user_data.get('gift_amt')
            uses = context.user_data.get('gift_uses')
            total_cost = amt * uses * count

            cashier_bal = get_cashier_balance()
            if cashier_bal < total_cost:
                await update.message.reply_text(f"❌ **رصيد الكاشيرة غير كافٍ!**\nالمطلوب: {total_cost:.2f} NSP\nمتوفر: {cashier_bal:.2f} NSP")
                context.user_data.clear()
                return

            before_cashier, after_cashier = update_cashier(-total_cost)
            generated = []
            
            for _ in range(count):
                code = "AUREX-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                cursor.execute("INSERT INTO gift_codes (code, amount, max_uses) VALUES (?, ?, ?)", (code, amt, uses))
                generated.append(code)

            conn.commit()
            context.user_data.clear()
            
            codes_txt = "\n".join([f"`{c}`" for c in generated])
            await update.message.reply_text(
                f"🎁 **تم إنشاء {count} كود هدية!**\n"
                f"• الخصم الإجمالي: `{total_cost:.2f} NSP`\n"
                f"• الكاشيرة قبل: `{before_cashier:.2f} NSP`\n"
                f"• الكاشيرة بعد: `{after_cashier:.2f} NSP`\n\n"
                f"📋 **الأكواد:**\n{codes_txt}", 
                parse_mode="Markdown"
            )
        except ValueError: 
            await update.message.reply_text("أدخل عدد صحيح.")

    elif state == 'ADM_WAIT_USER_DETAILS' and is_admin(user_id):
        context.user_data.clear()
        u = cursor.execute("SELECT * FROM users WHERE telegram_id = ? OR site_username = ?", (text, text)).fetchone()
        if u:
            await update.message.reply_text(
                f"👤 **تفاصيل العميل:**\n\n"
                f"🆔 تلغرام: `{u['telegram_id']}`\n"
                f"👤 الاسم: {u['username']}\n"
                f"🌐 حساب الموقع: `{u['site_username']}`\n"
                f"🔑 كلمة المرور: `{u['site_password']}`\n"
                f"💎 الرصيد: `{u['balance']:.2f} NSP`\n"
                f"🚫 حالة الحظر: {'محظور ❌' if u['is_banned'] else 'نشط ✅'}", 
                parse_mode="Markdown"
            )
        else: 
            await update.message.reply_text("❌ العميل غير موجود.")

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

    elif state == 'ADM_WAIT_NEW_ADMIN' and is_admin(user_id):
        context.user_data.clear()
        try:
            cursor.execute("UPDATE users SET is_admin = 1 WHERE telegram_id = ?", (int(text),))
            conn.commit()
            await update.message.reply_text(f"👮‍♂️ تم ترقية العميل `{text}` إلى آدمن بنجاح.", parse_mode="Markdown")
        except ValueError: 
            await update.message.reply_text("آيدي غير صالح.")

    elif state == 'ADM_WAIT_DISABLE_CODE' and is_admin(user_id):
        context.user_data.clear()
        cursor.execute("UPDATE gift_codes SET is_active = 0 WHERE code = ?", (text,))
        conn.commit()
        await update.message.reply_text(f"❌ تم إلغاء تفعيل الكود `{text}` بنجاح.", parse_mode="Markdown")

    elif state == 'ADM_WAIT_PRIV_ID' and is_admin(user_id):
        context.user_data['target_priv'] = int(text)
        context.user_data['state'] = 'ADM_WAIT_PRIV_TEXT'
        await update.message.reply_text("اكتب النص المراد إرساله للعميل:")

    elif state == 'ADM_WAIT_PRIV_TEXT' and is_admin(user_id):
        target = context.user_data.get('target_priv')
        context.user_data.clear()
        try:
            await context.bot.send_message(target, f"💬 **رسالة خاصة من الإدارة:**\n\n{text}", parse_mode="Markdown")
            await update.message.reply_text("✅ تم إرسال الرسالة بنجاح.")
        except Exception as e: 
            await update.message.reply_text(f"❌ تعذر الإرسال: {e}")

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

    elif state == 'WAIT_SUPPORT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال رسالتك للدعم الفني وسيجيبك أحد ممثلينا قريباً.")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد على العميل", callback_data=f"reply_support_{user_id}")]])
        await context.bot.send_message(
            MAIN_ADMIN_ID, 
            f"💬 **رسالة دعم جديدة!**\n🆔 العميل: `{user_id}`\n\n{text}", 
            reply_markup=btn, 
            parse_mode="Markdown"
        )

    elif state == 'WAIT_ADMIN_REPLY_SUPP' and is_admin(user_id):
        target = context.user_data.get('support_target')
        context.user_data.clear()
        try:
            await context.bot.send_message(target, f"💬 **رد الدعم الفني:**\n\n{text}", parse_mode="Markdown")
            await update.message.reply_text("✅ تم إرسال الرد للعميل.")
        except Exception as e: 
            await update.message.reply_text(f"❌ خطأ بالإرسال: {e}")

    elif state == 'WAIT_GIFT_CODE':
        code_row = cursor.execute("SELECT * FROM gift_codes WHERE code = ? AND is_active = 1", (text,)).fetchone()
        if code_row and code_row['used_count'] < code_row['max_uses']:
            used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND code = ?", (user_id, text)).fetchone()
            if not used:
                cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (text,))
                cursor.execute("INSERT INTO used_codes VALUES (?, ?)", (user_id, text))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (code_row['amount'], user_id))
                conn.commit()
                
                rem_uses = code_row['max_uses'] - (code_row['used_count'] + 1)
                await update.message.reply_text(f"🎉 **مبروك!** تم شحن رصيدك بـ **{code_row['amount']:.2f} NSP**")
                
                try:
                    await context.bot.send_message(
                        MAIN_ADMIN_ID,
                        f"🎁 **تم استخدام كود هدية!**\n\n"
                        f"• العميل: `{user_id}`\n"
                        f"• الكود: `{text}`\n"
                        f"• القيمة: **{code_row['amount']:.2f} NSP**\n"
                        f"• الاستخدامات المتبقية للكود: `{rem_uses}`",
                        parse_mode="Markdown"
                    )
                except Exception: pass
            else: 
                await update.message.reply_text("❌ لقد استخدمت هذا الكود سابقاً.")
        else: 
            await update.message.reply_text("❌ الكود غير صالح، معطل، أو انتهت استخداماته.")
        context.user_data.clear()

    conn.close()

# معالجة الصور
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    state = context.user_data.get('state')
    photo = msg.photo[-1].file_id
    caption = msg.caption or ""

    if is_admin(user_id) and msg.reply_to_message:
        replied_text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        match = re.search(r"🆔 العميل:\s*`?(\d+)`?", replied_text)
        if match:
            target_id = int(match.group(1))
            try:
                await context.bot.send_photo(target_id, photo, caption=f"💬 **رد الدعم الفني:**\n\n{caption}")
                await msg.reply_text(f"✅ تم إرسال الصورة للعميل `{target_id}`", parse_mode="Markdown")
                return
            except Exception as e:
                await msg.reply_text(f"❌ تعذر إرسال الصورة: {e}")
                return

    if state == 'WAIT_WIN_SHOT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال صورة الإصابة للإدارة بنجاح!")
        await context.bot.send_photo(
            MAIN_ADMIN_ID, photo, 
            caption=f"📸 **صورة إصابة جديدة!**\n🆔 العميل: `{user_id}`\n\n{caption}", 
            parse_mode="Markdown"
        )

    elif state == 'WAIT_SUPPORT':
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الصورة للدعم الفني.")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💬 رد على العميل", callback_data=f"reply_support_{user_id}")]])
        await context.bot.send_photo(
            MAIN_ADMIN_ID, photo, 
            caption=f"💬 **صورة دعم من العميل!**\n🆔 العميل: `{user_id}`\n\n{caption}", 
            reply_markup=btn, 
            parse_mode="Markdown"
        )

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

# ==========================================================
# 7. التشغيل والبدء
# ==========================================================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("🚀 تم تشغيل بوت تلجرام وتوصيله بقاعدة البيانات الموحدة بنجاح...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
