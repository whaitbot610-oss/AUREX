import os
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes
)

# ---------------------------------------------------------
# إعداد خادم الويب الوهمي لإرضاء خوادم Render
# ---------------------------------------------------------
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot Server is Online & Running Successfully!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# ---------------------------------------------------------
# إعدادات البيئة والتوقيع
# ---------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8948439052:AAHv-UWeTMQmHybxspFRVRpnjIqetmW8LbI").strip()
SERVER_URL = os.environ.get("SERVER_URL", "https://aurex-my-bot.onrender.com")
ADMIN_ID = 7255100997

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ---------------------------------------------------------
# إنشاء وتهيئة قاعدة البيانات تلقائياً
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0,
        total_spent REAL DEFAULT 0.0,
        deposit_count INTEGER DEFAULT 0,
        withdraw_count INTEGER DEFAULT 0,
        referrals_count INTEGER DEFAULT 0,
        site_username TEXT UNIQUE,
        site_password TEXT,
        security_passed INTEGER DEFAULT 0,
        got_welcome_bonus INTEGER DEFAULT 0,
        referred_by INTEGER,
        code_restricted_until TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        type TEXT,
        method TEXT,
        amount REAL,
        tx_number TEXT,
        status TEXT DEFAULT 'pending'
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_methods (
        name TEXT PRIMARY KEY,
        number TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS gift_codes (
        code TEXT PRIMARY KEY,
        amount REAL,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS used_codes (
        telegram_id INTEGER,
        code TEXT,
        PRIMARY KEY (telegram_id, code)
    )''')
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

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

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔔 **إشعار الإدارة:**\n\n{text}", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")

# ---------------------------------------------------------
# البداية وفحص الأمان
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data.clear()

    maint = get_setting('maintenance', 'off')
    if maint == 'on' and user.id != ADMIN_ID:
        await update.message.reply_text("🚧 البوت والموقع حالياً في وضع الصيانة. يرجى المحاولة لاحقاً.")
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
            [InlineKeyboardButton("حمصية", callback_data="sec_wrong")],
            [InlineKeyboardButton("حموية", callback_data="sec_correct")]
        ]
        await update.message.reply_text(
            "🔒 **سؤال حماية البوت:**\n\nحلاوة الجبن حمصية ولا حموية؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    await show_main_menu(update, context)

async def security_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == "sec_wrong":
        await update.effective_chat.send_message("❌ خطأ ياحبيب راجع معلوماتك وعيد!")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user.id,))
    
    db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
    welcome_val = float(get_setting('welcome_bonus', '0'))
    
    bonus_msg = ""
    if welcome_val > 0 and not db_user['got_welcome_bonus']:
        cursor.execute("UPDATE users SET balance = balance + ?, got_welcome_bonus = 1 WHERE telegram_id = ?", (welcome_val, user.id))
        bonus_msg = f"\n🎁 حصلت على بونص ترحيبي قدره **{welcome_val}** $"

    if db_user['referred_by']:
        ref_val = float(get_setting('referral_bonus', '0'))
        if ref_val > 0:
            cursor.execute("UPDATE users SET balance = balance + ?, referrals_count = referrals_count + 1 WHERE telegram_id = ?", 
                           (ref_val, db_user['referred_by']))
            try:
                await context.bot.send_message(
                    chat_id=db_user['referred_by'],
                    text=f"🎉 انضم شخص جديد عبر رابط إحالتك وحصلت على بونص بقيمة **{ref_val}** $!",
                    parse_mode="Markdown"
                )
            except: pass

    conn.commit()
    conn.close()

    await notify_admin(context, f"👤 مستخدم جديد تجاوز اختبار الأمان:\n• الاسم: {user.first_name}\n• المعرف: `{user.id}`")

    try:
        await query.message.delete()
    except Exception:
        pass

    if bonus_msg:
        await update.effective_chat.send_message(bonus_msg, parse_mode="Markdown")

    await show_main_menu(update, context)

# ---------------------------------------------------------
# القائمة الرئيسية
# ---------------------------------------------------------
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    db_user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    site_info = f"`{db_user['site_username']}`" if db_user and db_user['site_username'] else "غير مرتبط بعد"
    balance = db_user['balance'] if db_user else 0.0
    username = db_user['username'] if db_user else update.effective_user.first_name

    text = (
        f"🙋‍♂️ أهلاً بك عزيزي: **{username}**\n"
        f"🆔 معرف الحساب: `{user_id}`\n"
        f"🌐 حساب الموقع: {site_info}\n"
        f"💰 رصيدك الحالي: **{balance:.2f}** $\n"
    )

    clean_url = SERVER_URL.strip()
    if not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url.replace("http://", "")

    keyboard = [
        [InlineKeyboardButton("🌐 فتح موقع المنصة", web_app=WebAppInfo(url=clean_url))],
        [InlineKeyboardButton("🔑 إنشاء / تعديل حساب الموقع", callback_data="create_site_account")],
        [InlineKeyboardButton("💳 شحن رصيد", callback_data="deposit_menu"), InlineKeyboardButton("📤 سحب رصيد", callback_data="withdraw_menu")],
        [InlineKeyboardButton("🎁 كود هدية", callback_data="claim_gift"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref")],
        [InlineKeyboardButton("📜 سجل الإيداع", callback_data="dep_history"), InlineKeyboardButton("📜 سجل السحب", callback_data="with_history")],
        [InlineKeyboardButton("💬 الدعم الفني", callback_data="support"), InlineKeyboardButton("🎯 إرسال صورة إصابة", callback_data="send_win_img")]
    ]

    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم الإدارية", callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(keyboard)
    await update.effective_chat.send_message(text, reply_markup=markup, parse_mode="Markdown")

# ---------------------------------------------------------
# الأزرار التفاعلية للمستخدم
# ---------------------------------------------------------
async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "create_site_account":
        context.user_data['state'] = 'WAIT_SITE_USER'
        await update.effective_chat.send_message("🔑 أرسل الآن اسم المستخدم للموقع (6 أحرف على الأقل):")

    elif data == "deposit_menu":
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="dep_syriatel")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="dep_sham")]
        ]
        await update.effective_chat.send_message("اختر طريقة الشحن المناسبة:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["dep_syriatel", "dep_sham"]:
        method = "سيريتل كاش" if data == "dep_syriatel" else "شام كاش"
        context.user_data['dep_method'] = method
        context.user_data['state'] = 'WAIT_DEP_AMT'
        
        conn = get_db()
        pm = conn.execute("SELECT number FROM payment_methods WHERE name = ?", (method,)).fetchone()
        conn.close()
        num_str = f"\nرقم التحويل الحالي: `{pm['number']}`" if pm else ""

        await update.effective_chat.send_message(f"📥 اخترت الشحن عبر **{method}**.{num_str}\n\nأرسل الآن **المبلغ المراد شحنه** بالدولار:", parse_mode="Markdown")

    elif data == "withdraw_menu":
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="with_syriatel")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="with_sham")]
        ]
        await update.effective_chat.send_message("اختر طريقة السحب المناسبة:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["with_syriatel", "with_sham"]:
        method = "سيريتل كاش" if data == "with_syriatel" else "شام كاش"
        context.user_data['with_method'] = method
        context.user_data['state'] = 'WAIT_WITH_AMT'
        await update.effective_chat.send_message(f"📤 اخترت السحب عبر **{method}**.\n\nأرسل الآن **المبلغ المراد سحبه**:", parse_mode="Markdown")

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await update.effective_chat.send_message("🎁 أرسل الآن كود الهدية الذي حصلت عليه:")

    elif data == "my_ref":
        me = await context.bot.get_me()
        conn = get_db()
        u = conn.execute("SELECT referrals_count FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        ref_link = f"https://t.me/{me.username}?start={user_id}"
        await update.effective_chat.send_message(
            f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
            f"📊 عدد الإحالات الناجحة: **{u['referrals_count']}**\n"
            f"💡 شارك الرابط مع أصدقائك للحصول على مكافآت فورية!",
            parse_mode="Markdown"
        )

    elif data == "dep_history":
        conn = get_db()
        txs = conn.execute("SELECT * FROM transactions WHERE telegram_id = ? AND type = 'deposit' ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        conn.close()
        if not txs:
            await update.effective_chat.send_message("📜 لا يوجد لديك سجل إيداعات سابق.")
            return
        msg = "📜 **سجل آخر عمليات الإيداع:**\n\n"
        for t in txs:
            msg += f"• المبلغ: {t['amount']}$ | الوسيلة: {t['method']} | الحالة: {t['status']}\n"
        await update.effective_chat.send_message(msg, parse_mode="Markdown")

    elif data == "with_history":
        conn = get_db()
        txs = conn.execute("SELECT * FROM transactions WHERE telegram_id = ? AND type = 'withdraw' ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        conn.close()
        if not txs:
            await update.effective_chat.send_message("📜 لا يوجد لديك سجل سحوبات سابق.")
            return
        msg = "📜 **سجل آخر عمليات السحب:**\n\n"
        for t in txs:
            msg += f"• المبلغ: {t['amount']}$ | الوسيلة: {t['method']} | الحالة: {t['status']}\n"
        await update.effective_chat.send_message(msg, parse_mode="Markdown")

    elif data == "support":
        context.user_data['state'] = 'WAIT_SUPPORT_MSG'
        await update.effective_chat.send_message("💬 اكتب الآن رسالتك وسنرد عليك قريباً:")

    elif data == "send_win_img":
        context.user_data['state'] = 'WAIT_WIN_IMG'
        await update.effective_chat.send_message("🎯 قم بإرسال صورة الإصابة أو الفوز الآن:")

# ---------------------------------------------------------
# لوحة التحكم الإدارية
# ---------------------------------------------------------
async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    await query.answer()
    data = query.data

    cashier_bal = float(get_setting('cashier_balance', '1000'))
    maint = get_setting('maintenance', 'off')

    if data == "admin_panel":
        keyboard = [
            [InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal")],
            [InlineKeyboardButton(f"🏦 رصيد الكاشيرة: {cashier_bal:.2f} $", callback_data="adm_set_cashier")],
            [InlineKeyboardButton("📩 طلبات الشحن", callback_data="adm_deps"), InlineKeyboardButton("📤 طلبات السحب", callback_data="adm_withs")],
            [InlineKeyboardButton("🎁 توليد كود هدية", callback_data="adm_gen_code")],
            [InlineKeyboardButton("📊 تفاصيل عميل", callback_data="adm_user_info")],
            [InlineKeyboardButton("✉️ رسالة خاصة", callback_data="adm_pm_user"), InlineKeyboardButton("📢 رسالة جماعية", callback_data="adm_broadcast")],
            [InlineKeyboardButton(f"🛠️ وضع الصيانة: ({maint.upper()})", callback_data="adm_toggle_maint")],
            [InlineKeyboardButton("🎁 ضبط بونص الترحيب", callback_data="adm_set_welcome"), InlineKeyboardButton("🔗 ضبط بونص الإحالة", callback_data="adm_set_ref")],
            [InlineKeyboardButton("⏳ تقييد الكود (ساعة)", callback_data="adm_restrict_codes"), InlineKeyboardButton("🔓 إلغاء تقييد الكود", callback_data="adm_unrestrict_codes")]
        ]
        await update.effective_chat.send_message("👑 **لوحة إدارة البوت والكاشيرة:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "adm_add_bal":
        context.user_data['state'] = 'ADM_WAIT_ADD_USER'
        await update.effective_chat.send_message("أرسل معرف (Telegram ID) المستخدم المراد إضافة رصيد له:")

    elif data == "adm_sub_bal":
        context.user_data['state'] = 'ADM_WAIT_SUB_USER'
        await update.effective_chat.send_message("أرسل معرف (Telegram ID) المستخدم المراد خصم رصيد منه:")

    elif data == "adm_set_cashier":
        context.user_data['state'] = 'ADM_WAIT_CASHIER_BAL'
        await update.effective_chat.send_message(f"رصيد الكاشيرة الحالي هو **{cashier_bal}** $.\nأرسل المبلغ الجديد لتعديله:", parse_mode="Markdown")

    elif data == "adm_toggle_maint":
        new_m = "off" if maint == "on" else "on"
        set_setting('maintenance', new_m)
        await update.effective_chat.send_message(f"✅ تم تغيير وضع الصيانة إلى: **{new_m.upper()}**", parse_mode="Markdown")

    elif data == "adm_user_info":
        context.user_data['state'] = 'ADM_WAIT_USER_INFO'
        await update.effective_chat.send_message("أرسل معرف (Telegram ID) العميل لعرض تقريره:")

    elif data == "adm_gen_code":
        context.user_data['state'] = 'ADM_WAIT_GEN_CODE'
        await update.effective_chat.send_message("أرسل بيانات الكود بالشكل:\n`الكود المبلغ عدد_المستعملين`\nمثال:\n`GIFT100 10 50`", parse_mode="Markdown")

    elif data == "adm_broadcast":
        context.user_data['state'] = 'ADM_WAIT_BROADCAST'
        await update.effective_chat.send_message("اكتب الرسالة الجماعية المراد إرسالها للجميع:")

    elif data == "adm_pm_user":
        context.user_data['state'] = 'ADM_WAIT_PM_USER'
        await update.effective_chat.send_message("أرسل معرف (Telegram ID) العميل لتوجيه رسالة خاصة له:")

    elif data == "adm_set_welcome":
        context.user_data['state'] = 'ADM_WAIT_WELCOME_AMT'
        await update.effective_chat.send_message("أرسل قيمة البونص الترحيبي بالدولار:")

    elif data == "adm_set_ref":
        context.user_data['state'] = 'ADM_WAIT_REF_AMT'
        await update.effective_chat.send_message("أرسل قيمة بونص الإحالة لكل شخص بالدولار:")

    elif data == "adm_restrict_codes":
        conn = get_db()
        until = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE users SET code_restricted_until = ?", (until,))
        conn.commit()
        conn.close()
        await update.effective_chat.send_message("⏳ تم تقييد الأكواد لجميع المستخدمين لمدة ساعة.")

    elif data == "adm_unrestrict_codes":
        conn = get_db()
        conn.execute("UPDATE users SET code_restricted_until = NULL")
        conn.commit()
        conn.close()
        await update.effective_chat.send_message("🔓 تم إلغاء تقييد الأكواد عن الجميع.")

    elif data == "adm_deps":
        conn = get_db()
        deps = conn.execute("SELECT * FROM transactions WHERE type = 'deposit' AND status = 'pending' ORDER BY id DESC LIMIT 5").fetchall()
        conn.close()
        if not deps:
            await update.effective_chat.send_message("📩 لا توجد طلبات إيداع معلقة حالياً.")
            return
        for d in deps:
            keyboard = [
                [InlineKeyboardButton("✅ موافقة", callback_data=f"app_dep_{d['id']}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"rej_dep_{d['id']}")]
            ]
            await update.effective_chat.send_message(
                f"📥 **طلب إيداع رقم #{d['id']}**\n"
                f"👤 المستخدم: `{d['telegram_id']}`\n"
                f"💳 الوسيلة: {d['method']}\n"
                f"💰 المبلغ: **{d['amount']}** $\n"
                f"🔢 رقم العملية: `{d['tx_number']}`",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

    elif data == "adm_withs":
        conn = get_db()
        withs = conn.execute("SELECT * FROM transactions WHERE type = 'withdraw' AND status = 'pending' ORDER BY id DESC LIMIT 5").fetchall()
        conn.close()
        if not withs:
            await update.effective_chat.send_message("📤 لا توجد طلبات سحب معلقة حالياً.")
            return
        for w in withs:
            keyboard = [
                [InlineKeyboardButton("✅ موافقة", callback_data=f"app_with_{w['id']}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"rej_with_{w['id']}")]
            ]
            await update.effective_chat.send_message(
                f"📤 **طلب سحب رقم #{w['id']}**\n"
                f"👤 المستخدم: `{w['telegram_id']}`\n"
                f"💳 الوسيلة: {w['method']}\n"
                f"💰 المبلغ: **{w['amount']}** $\n"
                f"🔢 الحساب/المحفظة: `{w['tx_number']}`",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

# ---------------------------------------------------------
# إجراءات الموافقة والرفض
# ---------------------------------------------------------
async def admin_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    await query.answer()
    data = query.data

    conn = get_db()
    cursor = conn.cursor()

    if data.startswith("app_dep_"):
        tx_id = int(data.split("_")[2])
        tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and tx['status'] == 'pending':
            cursor.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (tx_id,))
            cursor.execute("UPDATE users SET balance = balance + ?, deposit_count = deposit_count + 1 WHERE telegram_id = ?", (tx['amount'], tx['telegram_id']))
            
            old_cashier = float(get_setting('cashier_balance', '1000'))
            new_cashier = old_cashier + tx['amount']
            set_setting('cashier_balance', new_cashier)

            conn.commit()
            await query.message.edit_text(f"✅ تم تأكيد الإيداع #{tx_id} وشحن {tx['amount']}$ للمستخدم.")
            await context.bot.send_message(chat_id=tx['telegram_id'], text=f"✅ تم قبول طلب الإيداع بقيمة **{tx['amount']}** $!", parse_mode="Markdown")

    elif data.startswith("rej_dep_"):
        tx_id = int(data.split("_")[2])
        tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and tx['status'] == 'pending':
            cursor.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (tx_id,))
            conn.commit()
            await query.message.edit_text(f"❌ تم رفض طلب الإيداع #{tx_id}.")
            await context.bot.send_message(chat_id=tx['telegram_id'], text=f"❌ تم رفض طلب الإيداع بمبلغ **{tx['amount']}** $.")

    elif data.startswith("app_with_"):
        tx_id = int(data.split("_")[2])
        tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and tx['status'] == 'pending':
            cursor.execute("UPDATE transactions SET status = 'approved' WHERE id = ?", (tx_id,))
            cursor.execute("UPDATE users SET withdraw_count = withdraw_count + 1 WHERE telegram_id = ?", (tx['telegram_id'],))
            
            old_cashier = float(get_setting('cashier_balance', '1000'))
            new_cashier = old_cashier - tx['amount']
            set_setting('cashier_balance', new_cashier)

            conn.commit()
            await query.message.edit_text(f"✅ تم تأكيد السحب #{tx_id}.")
            await context.bot.send_message(chat_id=tx['telegram_id'], text=f"✅ تم تحويل مبلغ السحب **{tx['amount']}** $ بنجاح!", parse_mode="Markdown")

    elif data.startswith("rej_with_"):
        tx_id = int(data.split("_")[2])
        tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and tx['status'] == 'pending':
            cursor.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (tx_id,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (tx['amount'], tx['telegram_id']))
            conn.commit()
            await query.message.edit_text(f"❌ تم رفض السحب #{tx_id} وإعادة المبلغ لرصيد المستخدم.")
            await context.bot.send_message(chat_id=tx['telegram_id'], text=f"❌ تم رفض طلب السحب وإعادة **{tx['amount']}** $ إلى رصيدك.")

    conn.close()

# ---------------------------------------------------------
# معالجة المدخلات النصية
# ---------------------------------------------------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    state = context.user_data.get('state')

    if not state:
        return

    conn = get_db()
    cursor = conn.cursor()

    if state == 'WAIT_SITE_USER':
        if len(text) < 6:
            await update.message.reply_text("⚠️ اسم المستخدم يجب أن يكون 6 أحرف على الأقل. أعد المحاولة:")
            return
        context.user_data['temp_site_user'] = text
        context.user_data['state'] = 'WAIT_SITE_PASS'
        await update.message.reply_text("أدخل الآن كلمة المرور للحساب (6 أرقام على الأقل):")

    elif state == 'WAIT_SITE_PASS':
        if not text.isdigit() or len(text) < 6:
            await update.message.reply_text("⚠️ كلمة المرور يجب أن تكون 6 أرقام على الأقل. أعد المحاولة:")
            return
        site_user = context.user_data.get('temp_site_user')
        try:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?", (site_user, text, user_id))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text("✅ تم ربط وتحديث حساب الموقع بنجاح!")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ اسم المستخدم هذا مأخوذ بالفعل، اختر اسماً آخر:")
            context.user_data['state'] = 'WAIT_SITE_USER'

    elif state == 'WAIT_DEP_AMT':
        try:
            amt = float(text)
            if amt <= 0: raise ValueError
            context.user_data['dep_amt'] = amt
            context.user_data['state'] = 'WAIT_DEP_TX'
            await update.message.reply_text("أرسل الآن **رقم العملية** أو **رقم الإشعار**:")
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إدخال مبلغ رقمي صحيح.")

    elif state == 'WAIT_DEP_TX':
        amt = context.user_data.get('dep_amt')
        method = context.user_data.get('dep_method')
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'deposit', ?, ?, ?)",
                       (user_id, method, amt, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ تم رفع طلب الإيداع وهو قيد المراجعة.")
        await notify_admin(context, f"📥 **طلب إيداع جديد!**\n• العميل: `{user_id}`\n• المبلغ: {amt}$\n• الوسيلة: {method}\n• رقم العملية: `{text}`")

    elif state == 'WAIT_WITH_AMT':
        try:
            amt = float(text)
            u = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            if amt <= 0 or u['balance'] < amt:
                await update.message.reply_text("⚠️ رصيدك غير كافٍ لإنهاء عملية السحب.")
                return
            context.user_data['with_amt'] = amt
            context.user_data['state'] = 'WAIT_WITH_ACC'
            await update.message.reply_text("أرسل رقم المحفظة / الحساب للتحويل:")
        except ValueError:
            await update.message.reply_text("⚠️ يرجى كتابة مبلغ رقمي صحيح.")

    elif state == 'WAIT_WITH_ACC':
        amt = context.user_data.get('with_amt')
        method = context.user_data.get('with_method')
        cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amt, user_id))
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'withdraw', ?, ?, ?)",
                       (user_id, method, amt, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم خصم **{amt}** $ ورفع طلب السحب إلى الإدارة.")
        await notify_admin(context, f"📤 **طلب سحب جديد!**\n• العميل: `{user_id}`\n• المبلغ: {amt}$\n• الوسيلة: {method}\n• الحساب: `{text}`")

    elif state == 'WAIT_GIFT_CODE':
        db_u = cursor.execute("SELECT code_restricted_until FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if db_u['code_restricted_until']:
            until = datetime.strptime(db_u['code_restricted_until'], '%Y-%m-%d %H:%M:%S')
            if datetime.now() < until:
                await update.message.reply_text(f"⏳ حسابك مقيد من استخدام الأكواد حتى: {db_u['code_restricted_until']}")
                conn.close()
                return

        code_row = cursor.execute("SELECT * FROM gift_codes WHERE code = ?", (text,)).fetchone()
        if not code_row:
            await update.message.reply_text("❌ كود الهدية غير صحيح.")
        elif code_row['used_count'] >= code_row['max_uses']:
            await update.message.reply_text("❌ انتهى عدد مرات استخدام هذا الكود.")
        else:
            used = cursor.execute("SELECT * FROM used_codes WHERE telegram_id = ? AND code = ?", (user_id, text)).fetchone()
            if used:
                await update.message.reply_text("⚠️ لقد قمت بطلب هذا الكود سابقاً!")
            else:
                cursor.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code = ?", (text,))
                cursor.execute("INSERT INTO used_codes (telegram_id, code) VALUES (?, ?)", (user_id, text))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (code_row['amount'], user_id))
                
                old_c = float(get_setting('cashier_balance', '1000'))
                new_c = old_c - code_row['amount']
                set_setting('cashier_balance', new_c)

                conn.commit()
                await update.message.reply_text(f"🎉 تم إضافة **{code_row['amount']}** $ لرصيدك!")
                await notify_admin(context, f"🎁 **استخدام كود هدية:**\n• العميل: `{user_id}`\n• الكود: `{text}`\n• المبلغ: {code_row['amount']}$")

        context.user_data.clear()

    elif state == 'WAIT_SUPPORT_MSG':
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💬 **رسالة دعم من `{user_id}`:**\n\n{text}",
            parse_mode="Markdown"
        )
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال رسالتك للدعم الفني بنجاح.")

    elif user_id == ADMIN_ID:
        if state == 'ADM_WAIT_ADD_USER':
            context.user_data['adm_target'] = int(text)
            context.user_data['state'] = 'ADM_WAIT_ADD_AMT'
            await update.message.reply_text("أدخل المبلغ المراد إضافته:")

        elif state == 'ADM_WAIT_ADD_AMT':
            target = context.user_data.get('adm_target')
            amt = float(text)
            cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amt, target))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم إضافة {amt}$ إلى `{target}`.")
            try:
                await context.bot.send_message(chat_id=target, text=f"🎉 تم شحن رصيدك بقيمة **{amt}** $!", parse_mode="Markdown")
            except: pass

        elif state == 'ADM_WAIT_SUB_USER':
            context.user_data['adm_target'] = int(text)
            context.user_data['state'] = 'ADM_WAIT_SUB_AMT'
            await update.message.reply_text("أدخل المبلغ المراد خصمه:")

        elif state == 'ADM_WAIT_SUB_AMT':
            target = context.user_data.get('adm_target')
            amt = float(text)
            cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amt, target))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم خصم {amt}$ من `{target}`.")

        elif state == 'ADM_WAIT_CASHIER_BAL':
            set_setting('cashier_balance', text)
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحديث رصيد الكاشيرة إلى **{text}** $.")

        elif state == 'ADM_WAIT_USER_INFO':
            u = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (int(text),)).fetchone()
            if u:
                msg = (
                    f"📊 **تفاصيل العميل `{u['telegram_id']}`:**\n\n"
                    f"• الاسم: {u['username']}\n"
                    f"• رصيده الحالي: **{u['balance']:.2f}** $\n"
                    f"• مرات الشحن: {u['deposit_count']}\n"
                    f"• مرات السحب: {u['withdraw_count']}\n"
                    f"• عدد الإحالات: {u['referrals_count']}\n"
                    f"• حساب الموقع: `{u['site_username'] or 'غير مسجل'}`"
                )
                await update.message.reply_text(msg, parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ لم يتم العثور على العميل.")
            context.user_data.clear()

        elif state == 'ADM_WAIT_GEN_CODE':
            parts = text.split()
            if len(parts) == 3:
                c_name, c_amt, c_uses = parts[0], float(parts[1]), int(parts[2])
                cursor.execute("INSERT OR REPLACE INTO gift_codes (code, amount, max_uses) VALUES (?, ?, ?)", (c_name, c_amt, c_uses))
                conn.commit()
                await update.message.reply_text(f"✅ تم إنشاء الكود `{c_name}` بمبلغ {c_amt}$ لعدد {c_uses} استخدام.")
            else:
                await update.message.reply_text("⚠️ تنسيق خاطئ! أرسل مثل: `GIFT100 10 50`")
            context.user_data.clear()

        elif state == 'ADM_WAIT_BROADCAST':
            users = cursor.execute("SELECT telegram_id FROM users").fetchall()
            cnt = 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u['telegram_id'], text=text, parse_mode="Markdown")
                    cnt += 1
                except: pass
            await update.message.reply_text(f"📢 تمت إرسال الرسالة إلى {cnt} مستخدم.")
            context.user_data.clear()

        elif state == 'ADM_WAIT_PM_USER':
            context.user_data['adm_target'] = int(text)
            context.user_data['state'] = 'ADM_WAIT_PM_TEXT'
            await update.message.reply_text("اكتب نص الرسالة الخاصة:")

        elif state == 'ADM_WAIT_PM_TEXT':
            target = context.user_data.get('adm_target')
            try:
                await context.bot.send_message(chat_id=target, text=f"✉️ **رسالة من الإدارة:**\n\n{text}", parse_mode="Markdown")
                await update.message.reply_text("✅ تم إرسال الرسالة بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"❌ فشل الإرسال: {e}")
            context.user_data.clear()

        elif state == 'ADM_WAIT_WELCOME_AMT':
            set_setting('welcome_bonus', text)
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحديد البونص الترحيبي بمبلغ **{text}** $.")

        elif state == 'ADM_WAIT_REF_AMT':
            set_setting('referral_bonus', text)
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحديد بونص الإحالة بمبلغ **{text}** $.")

    conn.close()

# ---------------------------------------------------------
# معالجة الصور
# ---------------------------------------------------------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get('state')
    photo_id = update.message.photo[-1].file_id
    caption = update.message.caption or ""

    if state == 'WAIT_WIN_IMG':
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=f"🎯 **صورة إصابة من العميل `{user_id}`:**\n{caption}",
            parse_mode="Markdown"
        )
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال صورة الإصابة بنجاح!")

    elif state == 'WAIT_SUPPORT_MSG':
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=f"💬 **رسالة دعم (صورة) من العميل `{user_id}`:**\n{caption}",
            parse_mode="Markdown"
        )
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الصورة للدعم بنجاح!")

# ---------------------------------------------------------
# نقطة التشغيل الرئيسية مع حماية الاتصال وخادم الويب
# ---------------------------------------------------------
def main():
    init_db()
    
    if not BOT_TOKEN or len(BOT_TOKEN) < 20:
        print("❌ خطأ: لم يتم التعرف على توكن البوت!")
        return

    threading.Thread(target=run_flask, daemon=True).start()

    try:
        app = Application.builder().token(BOT_TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(security_check, pattern="^sec_"))
        app.add_handler(CallbackQueryHandler(admin_panel_handler, pattern="^adm_"))
        app.add_handler(CallbackQueryHandler(admin_action_handler, pattern="^(app_|rej_)"))
        app.add_handler(CallbackQueryHandler(user_callback_handler))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

        print("🚀 جاري تشغيل البوت وخادم الويب بنجاح...")
        app.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"❌ خطأ أثناء تشغيل البوت: {e}")

if __name__ == '__main__':
    main()
