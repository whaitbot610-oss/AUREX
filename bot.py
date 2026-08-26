import os
import sqlite3
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes
)

# إعدادات البيئة
BOT_TOKEN = os.environ.get("8439192012:AAESCRJOdvE9VcECILq8Y_ZocIVwev6bnXk")
SERVER_URL = os.environ.get("SERVER_URL", "https://aurex-my-bot.onrender.com")
ADMIN_ID = 7255100997

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ---------------------------------------------------------
# وظائف قاعدة البيانات الأساسية
# ---------------------------------------------------------
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
# البداية وفحص الأمان والصيانة
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data.clear()

    # فحص وضع الصيانة
    maint = get_setting('maintenance', 'off')
    if maint == 'on' and user.id != ADMIN_ID:
        await update.message.reply_text("🚧 البوت والموقع حالياً في وضع الصيانة. يرجى المحاولة لاحقاً.")
        return

    conn = get_db()
    cursor = conn.cursor()
    
    # التعامل مع الإحالة
    ref_by = int(context.args[0]) if context.args and context.args[0].isdigit() and int(context.args[0]) != user.id else None
    
    db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
    if not db_user:
        cursor.execute("INSERT INTO users (telegram_id, username, referred_by) VALUES (?, ?, ?)", 
                       (user.id, user.username or user.first_name, ref_by))
        conn.commit()
        db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()

    conn.close()

    # سؤال الحماية الأولية
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
        await query.message.reply_text("❌ خطأ ياحبيب راجع معلوماتك وعيد!")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET security_passed = 1 WHERE telegram_id = ?", (user.id,))
    
    # تفعيل بونص الترحيب (مرة واحدة فقط)
    db_user = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,)).fetchone()
    welcome_val = float(get_setting('welcome_bonus', '0'))
    
    bonus_msg = ""
    if welcome_val > 0 and not db_user['got_welcome_bonus']:
        cursor.execute("UPDATE users SET balance = balance + ?, got_welcome_bonus = 1 WHERE telegram_id = ?", (welcome_val, user.id))
        bonus_msg = f"\n🎁 حصلت على بونص ترحيبي قدره **{welcome_val}** $"

    # إحالة ناجحة
    if db_user['referred_by']:
        ref_val = float(get_setting('referral_bonus', '0'))
        if ref_val > 0:
            cursor.execute("UPDATE users SET balance = balance + ?, referrals_count = referrals_count + 1 WHERE telegram_id = ?", 
                           (ref_val, db_user['referred_by']))
            await context.bot.send_message(
                chat_id=db_user['referred_by'],
                text=f"🎉 انضم شخص جديد عبر رابط إحالتك وحصلت على بونص بقيمة **{ref_val}** $!",
                parse_mode="Markdown"
            )

    conn.commit()
    conn.close()

    await notify_admin(context, f"👤 مستخدم جديد تجاوز اختبار الأمان:\n• الاسم: {user.first_name}\n• المعرف: `{user.id}`")
    await query.message.delete()
    if bonus_msg:
        await query.message.reply_text(bonus_msg, parse_mode="Markdown")
    await show_main_menu(update, context)

# ---------------------------------------------------------
# القائمة الرئيسية للعميل
# ---------------------------------------------------------
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    db_user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    conn.close()

    site_info = f"`{db_user['site_username']}`" if db_user['site_username'] else "غير مقتصر بعد"
    
    text = (
        f"🙋‍♂️ أهلاً بك عزيزي: **{db_user['username']}**\n"
        f"🆔 معرف الحساب: `{db_user['telegram_id']}`\n"
        f"🌐 حساب الموقع: {site_info}\n"
        f"💰 رصيدك الحالي: **{db_user['balance']:.2f}** $\n"
    )

    keyboard = [
        [InlineKeyboardButton("🌐 فتح موقع المنصة", web_app=WebAppInfo(url=SERVER_URL))],
        [InlineKeyboardButton("🔑 إنشاء / تعديل حساب الموقع", callback_data="create_site_account")],
        [InlineKeyboardButton("💳 شحن رصيد", callback_data="deposit_menu"), InlineKeyboardButton("📤 سحب رصيد", callback_data="withdraw_menu")],
        [InlineKeyboardButton("🎁 كود هدية", callback_data="claim_gift"), InlineKeyboardButton("🔗 رابط إحالتي", callback_data="my_ref")],
        [InlineKeyboardButton("📜 سجل الإيداع", callback_data="dep_history"), InlineKeyboardButton("📜 سجل السحب", callback_data="with_history")],
        [InlineKeyboardButton("💬 الدعم الفني", callback_data="support"), InlineKeyboardButton("🎯 إرسال صورة إصابة", callback_data="send_win_img")]
    ]

    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم الإدارية", callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

# ---------------------------------------------------------
# تفاعلات الأزرار والإدخالات النصية للعميل
# ---------------------------------------------------------
async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "create_site_account":
        context.user_data['state'] = 'WAIT_SITE_USER'
        await query.message.reply_text("🔑 أرسل الآن اسم المستخدم للموقع (يجب أن يكون من 6 أحرف على الأقل):")

    elif data == "deposit_menu":
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="dep_syriatel")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="dep_sham")]
        ]
        await query.message.reply_text("اختر طريقة الشحن المناسبة:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["dep_syriatel", "dep_sham"]:
        method = "سيريتل كاش" if data == "dep_syriatel" else "شام كاش"
        context.user_data['dep_method'] = method
        context.user_data['state'] = 'WAIT_DEP_AMT'
        
        conn = get_db()
        pm = conn.execute("SELECT number FROM payment_methods WHERE name = ?", (method,)).fetchone()
        conn.close()
        num_str = f"\nرقم التحويل الحالي: `{pm['number']}`" if pm else ""

        await query.message.reply_text(f"📥 اخترت الشحن عبر **{method}**.{num_str}\n\nأرسل الآن **المبلغ المراد شحنه** بالدولار:")

    elif data == "withdraw_menu":
        keyboard = [
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="with_syriatel")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="with_sham")]
        ]
        await query.message.reply_text("اختر طريقة السحب المناسبة:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["with_syriatel", "with_sham"]:
        method = "سيريتل كاش" if data == "with_syriatel" else "شام كاش"
        context.user_data['with_method'] = method
        context.user_data['state'] = 'WAIT_WITH_AMT'
        await query.message.reply_text(f"📤 اخترت السحب عبر **{method}**.\n\nأرسل الآن **المبلغ المراد سحبه**:")

    elif data == "claim_gift":
        context.user_data['state'] = 'WAIT_GIFT_CODE'
        await query.message.reply_text("🎁 أرسل الآن كود الهدية الذي حصلت عليه:")

    elif data == "my_ref":
        me = await context.bot.get_me()
        conn = get_db()
        u = conn.execute("SELECT referrals_count FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        conn.close()
        ref_link = f"https://t.me/{me.username}?start={user_id}"
        await query.message.reply_text(
            f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
            f"📊 عدد الإحالات الناجحة: **{u['referrals_count']}**\n"
            f"💡 شارك الرابط مع أصدقائك للحصول على مكافآت فورية عند انضمامهم!",
            parse_mode="Markdown"
        )

    elif data == "dep_history":
        conn = get_db()
        txs = conn.execute("SELECT * FROM transactions WHERE telegram_id = ? AND type = 'deposit' ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        conn.close()
        if not txs:
            await query.message.reply_text("📜 لا يوجد لديك سجل إيداعات سابق.")
            return
        msg = "📜 **سجل آخر عمليات الإيداع:**\n\n"
        for t in txs:
            msg += f"• المبلغ: {t['amount']}$ | الوسيلة: {t['method']} | الحالة: {t['status']}\n"
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "with_history":
        conn = get_db()
        txs = conn.execute("SELECT * FROM transactions WHERE telegram_id = ? AND type = 'withdraw' ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        conn.close()
        if not txs:
            await query.message.reply_text("📜 لا يوجد لديك سجل سحوبات سابق.")
            return
        msg = "📜 **سجل آخر عمليات السحب:**\n\n"
        for t in txs:
            msg += f"• المبلغ: {t['amount']}$ | الوسيلة: {t['method']} | الحالة: {t['status']}\n"
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "support":
        context.user_data['state'] = 'WAIT_SUPPORT_MSG'
        await query.message.reply_text("💬 اكتب الآن رسالتك أو استفسارك وسيتم توجيهها للدعم الفني مباشرة:")

    elif data == "send_win_img":
        context.user_data['state'] = 'WAIT_WIN_IMG'
        await query.message.reply_text("🎯 قم بإرسال صورة الإصابة أو الفوز الآن:")

# ---------------------------------------------------------
# لوحة الإدارة الكاملة (ADMIN PANEL)
# ---------------------------------------------------------
async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    await query.answer()
    data = query.data

    conn = get_db()
    cashier_bal = float(get_setting('cashier_balance', '1000'))
    maint = get_setting('maintenance', 'off')
    conn.close()

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
        await query.message.reply_text("👑 **لوحة إدارة البوت والكاشيرة:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "adm_add_bal":
        context.user_data['state'] = 'ADM_WAIT_ADD_USER'
        await query.message.reply_text("أرسل معرف (Telegram ID) المستخدم المراد إضافة رصيد له:")

    elif data == "adm_sub_bal":
        context.user_data['state'] = 'ADM_WAIT_SUB_USER'
        await query.message.reply_text("أرسل معرف (Telegram ID) المستخدم المراد خصم رصيد منه:")

    elif data == "adm_set_cashier":
        context.user_data['state'] = 'ADM_WAIT_CASHIER_BAL'
        await query.message.reply_text(f"رصيد الكاشيرة الحالي هو **{cashier_bal}** $.\nأرسل المبلغ الجديد لتعديله:")

    elif data == "adm_toggle_maint":
        new_m = "off" if maint == "on" else "on"
        set_setting('maintenance', new_m)
        await query.message.reply_text(f"✅ تم تغيير وضع الصيانة إلى: **{new_m.upper()}**", parse_mode="Markdown")

    elif data == "adm_user_info":
        context.user_data['state'] = 'ADM_WAIT_USER_INFO'
        await query.message.reply_text("أرسل معرف (Telegram ID) العميل لعرض تقريره الكامل:")

    elif data == "adm_gen_code":
        context.user_data['state'] = 'ADM_WAIT_GEN_CODE'
        await query.message.reply_text("أرسل بيانات الكود بالترتيب وبفراغات:\n`الكود المبلغ عدد_المستعملين`\nمثال:\n`GIFT100 10 50`", parse_mode="Markdown")

    elif data == "adm_broadcast":
        context.user_data['state'] = 'ADM_WAIT_BROADCAST'
        await query.message.reply_text("اكتب الرسالة الجماعية التي تريد إرسالها لكل مستخدمي البوت:")

    elif data == "adm_pm_user":
        context.user_data['state'] = 'ADM_WAIT_PM_USER'
        await query.message.reply_text("أرسل معرف (Telegram ID) العميل لتوجيه رسالة خاصة له:")

    elif data == "adm_set_welcome":
        context.user_data['state'] = 'ADM_WAIT_WELCOME_AMT'
        await query.message.reply_text("أرسل قيمة البونص الترحيبي بالدولار (أرسل 0 لإلغائه):")

    elif data == "adm_set_ref":
        context.user_data['state'] = 'ADM_WAIT_REF_AMT'
        await query.message.reply_text("أرسل قيمة بونص الإحالة لكل شخص ينضم بالدولار:")

    elif data == "adm_restrict_codes":
        conn = get_db()
        until = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE users SET code_restricted_until = ?", (until,))
        conn.commit()
        conn.close()
        await query.message.reply_text("⏳ تم تقييد استخدام الأكواد لجميع المستخدمين لمدة ساعة كاملة.")

    elif data == "adm_unrestrict_codes":
        conn = get_db()
        conn.execute("UPDATE users SET code_restricted_until = NULL")
        conn.commit()
        conn.close()
        await query.message.reply_text("🔓 تم إلغاء تقييد الأكواد عن جميع المستخدمين.")

    elif data == "adm_deps":
        conn = get_db()
        deps = conn.execute("SELECT * FROM transactions WHERE type = 'deposit' AND status = 'pending' ORDER BY id DESC LIMIT 5").fetchall()
        conn.close()
        if not deps:
            await query.message.reply_text("📩 لا توجد طلبات إيداع معلقة حالياً.")
            return
        for d in deps:
            keyboard = [
                [InlineKeyboardButton("✅ موافقة", callback_data=f"app_dep_{d['id']}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"rej_dep_{d['id']}")]
            ]
            await query.message.reply_text(
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
            await query.message.reply_text("📤 لا توجد طلبات سحب معلقة حالياً.")
            return
        for w in withs:
            keyboard = [
                [InlineKeyboardButton("✅ موافقة", callback_data=f"app_with_{w['id']}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"rej_with_{w['id']}")]
            ]
            await query.message.reply_text(
                f"📤 **طلب سحب رقم #{w['id']}**\n"
                f"👤 المستخدم: `{w['telegram_id']}`\n"
                f"💳 الوسيلة: {w['method']}\n"
                f"💰 المبلغ: **{w['amount']}** $\n"
                f"🔢 الحساب/المحفظة: `{w['tx_number']}`",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

# ---------------------------------------------------------
# الموافقة والرفض لعمليات السحب والإيداع بواسطة الأدمن
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
            await context.bot.send_message(chat_id=tx['telegram_id'], text=f"✅ تم قبول طلب الإيداع الخاص بك وشحن **{tx['amount']}** $ إلى رصيدك!", parse_mode="Markdown")
            await notify_admin(context, f"🏦 **تحديث رصيد الكاشيرة:**\nالسابق: {old_cashier:.2f}$\nالحالي: {new_cashier:.2f}$")

    elif data.startswith("rej_dep_"):
        tx_id = int(data.split("_")[2])
        tx = cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and tx['status'] == 'pending':
            cursor.execute("UPDATE transactions SET status = 'rejected' WHERE id = ?", (tx_id,))
            conn.commit()
            await query.message.edit_text(f"❌ تم رفض طلب الإيداع #{tx_id}.")
            await context.bot.send_message(chat_id=tx['telegram_id'], text=f"❌ تم رفض طلب الإيداع الخاص بك بمبلغ **{tx['amount']}** $.")

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
            await query.message.edit_text(f"✅ تم تأكيد السحب #{tx_id} وإرسال الأموال للعميل.")
            await context.bot.send_message(chat_id=tx['telegram_id'], text=f"✅ تم تحويل مبلغ السحب **{tx['amount']}** $ إلى حسابك بنجاح!", parse_mode="Markdown")
            await notify_admin(context, f"🏦 **تحديث رصيد الكاشيرة:**\nالسابق: {old_cashier:.2f}$\nالحالي: {new_cashier:.2f}$")

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
# معالجة الرسائل والمدخلات النصية الشاملة
# ---------------------------------------------------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    state = context.user_data.get('state')

    if not state:
        return

    conn = get_db()
    cursor = conn.cursor()

    # 1. إنشاء حساب الموقع
    if state == 'WAIT_SITE_USER':
        if len(text) < 6:
            await update.message.reply_text("⚠️ اسم المستخدم يجب أن يكون 6 أحرف على الأقل. أعد المحاولة:")
            return
        context.user_data['temp_site_user'] = text
        context.user_data['state'] = 'WAIT_SITE_PASS'
        await update.message.reply_text("أدخل الآن كلمة المرور للحساب (يجب أن تتكون من 6 أرقام على الأقل):")

    elif state == 'WAIT_SITE_PASS':
        if not text.isdigit() or len(text) < 6:
            await update.message.reply_text("⚠️ كلمة المرور يجب أن تكون 6 أرقام على الأقل. أعد المحاولة:")
            return
        site_user = context.user_data.get('temp_site_user')
        try:
            cursor.execute("UPDATE users SET site_username = ?, site_password = ? WHERE telegram_id = ?", (site_user, text, user_id))
            conn.commit()
            context.user_data.clear()
            await update.message.reply_text("✅ تم إنشاء وتحديث حساب الموقع بنجاح!")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ اسم المستخدم هذا مأخوذ من قبل، اختر اسماً آخر.")
            context.user_data['state'] = 'WAIT_SITE_USER'

    # 2. خطو شحن الرصيد
    elif state == 'WAIT_DEP_AMT':
        try:
            amt = float(text)
            if amt <= 0: raise ValueError
            context.user_data['dep_amt'] = amt
            context.user_data['state'] = 'WAIT_DEP_TX'
            await update.message.reply_text("أرسل الآن **رقم العملية** أو **رقم الإشعار** لربط الطلب:")
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إدخال مبلغ صحيح بالمنطق الرقمي.")

    elif state == 'WAIT_DEP_TX':
        amt = context.user_data.get('dep_amt')
        method = context.user_data.get('dep_method')
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'deposit', ?, ?, ?)",
                       (user_id, method, amt, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ تم رفع طلب الإيداع بنجاح، وهو قيد التثبيت من الكاشير.")
        await notify_admin(context, f"📥 **طلب إيداع جديد!**\n• العميل: `{user_id}`\n• المبلغ: {amt}$\n• الوسيلة: {method}\n• رقم العملية: `{text}`")

    # 3. خطو سحب الرصيد
    elif state == 'WAIT_WITH_AMT':
        try:
            amt = float(text)
            u = cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            if amt <= 0 or u['balance'] < amt:
                await update.message.reply_text("⚠️ رصيدك غير كافٍ لإنهاء عملية السحب.")
                return
            context.user_data['with_amt'] = amt
            context.user_data['state'] = 'WAIT_WITH_ACC'
            await update.message.reply_text("أرسل رقم المحفظة / الحساب الذي تريد استلام المبلغ عليه:")
        except ValueError:
            await update.message.reply_text("⚠️ يرجى كتابة مبلغ رقمي صحيح.")

    elif state == 'WAIT_WITH_ACC':
        amt = context.user_data.get('with_amt')
        method = context.user_data.get('with_method')
        # خصم وتجميد المبلغ من العميل فوراً
        cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amt, user_id))
        cursor.execute("INSERT INTO transactions (telegram_id, type, method, amount, tx_number) VALUES (?, 'withdraw', ?, ?, ?)",
                       (user_id, method, amt, text))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم تجميد مبلغ **{amt}** $ ورفع طلب السحب بنجاح إلى الإدارة.")
        await notify_admin(context, f"📤 **طلب سحب جديد!**\n• العميل: `{user_id}`\n• المبلغ: {amt}$\n• الوسيلة: {method}\n• الحساب: `{text}`")

    # 4. تفعيل كود الهدية
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
            await update.message.reply_text("❌ كود الهدية غير صحيح أو غير موجود.")
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
                await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة **{code_row['amount']}** $ لرصيدك!")
                await notify_admin(context, f"🎁 **استخدام كود هدية:**\n• العميل: `{user_id}`\n• الكود: `{text}`\n• المبلغ: {code_row['amount']}$\n• الكاشيرة سابقاً: {old_c:.2f}$\n• الكاشيرة الآن: {new_c:.2f}$")

        context.user_data.clear()

    # 5. الدعم الفني
    elif state == 'WAIT_SUPPORT_MSG':
        keyboard = [[InlineKeyboardButton("💬 رد على المستخدم", callback_data=f"adm_reply_{user_id}")]]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💬 **رسالة دعم جديدة من `{user_id}`:**\n\n{text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم وسيجري الرد عليك قريباً.")

    # 6. مدخلات الأدمن
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
            await update.message.reply_text(f"✅ تم إضافة {amt}$ إلى حساب `{target}` بنجاح.")
            await context.bot.send_message(chat_id=target, text=f"🎉 تم شحن رصيدك بقيمة **{amt}** $ بواسطة الإدارة!", parse_mode="Markdown")

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
            await update.message.reply_text(f"✅ تم خصم {amt}$ من حساب `{target}` بنجاح.")
            await context.bot.send_message(chat_id=target, text=f"⚠️ تم خصم مبلغ **{amt}** $ من رصيدك بواسطة الإدارة.", parse_mode="Markdown")

        elif state == 'ADM_WAIT_CASHIER_BAL':
            set_setting('cashier_balance', text)
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تعيين رصيد الكاشيرة إلى **{text}** $.")

        elif state == 'ADM_WAIT_USER_INFO':
            u = cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (int(text),)).fetchone()
            if u:
                msg = (
                    f"📊 **تفاصيل العميل `{u['telegram_id']}`:**\n\n"
                    f"• الاسم: {u['username']}\n"
                    f"• رصيده الحالي: **{u['balance']:.2f}** $\n"
                    f"• مجموع المصروف: {u['total_spent']:.2f} $\n"
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
            await update.message.reply_text(f"📢 تمت إرسال الرسالة الجماعية بنجاح إلى {cnt} مستخدم.")
            context.user_data.clear()

        elif state == 'ADM_WAIT_PM_USER':
            context.user_data['adm_target'] = int(text)
            context.user_data['state'] = 'ADM_WAIT_PM_TEXT'
            await update.message.reply_text("اكتب نص الرسالة الخاصة:")

        elif state == 'ADM_WAIT_PM_TEXT':
            target = context.user_data.get('adm_target')
            try:
                await context.bot.send_message(chat_id=target, text=f"✉️ **رسالة خاصة من الإدارة:**\n\n{text}", parse_mode="Markdown")
                await update.message.reply_text("✅ تم إرسال الرسالة بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"❌ فشل إرسال الرسالة: {e}")
            context.user_data.clear()

        elif state == 'ADM_WAIT_WELCOME_AMT':
            set_setting('welcome_bonus', text)
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحديد البونص الترحيبي بمبلغ **{text}** $.")

        elif state == 'ADM_WAIT_REF_AMT':
            set_setting('referral_bonus', text)
            context.user_data.clear()
            await update.message.reply_text(f"✅ تم تحديد بونص الإحالة بمبلغ **{text}** $ لكل شخص.")

    conn.close()

# ---------------------------------------------------------
# معالجة الصور (الدعم والصور الخاصة بالإصابات)
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
            caption=f"🎯 **صورة إصابة جديدة من العميل `{user_id}`:**\n{caption}",
            parse_mode="Markdown"
        )
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال صورة الإصابة للإدارة بنجاح!")

    elif state == 'WAIT_SUPPORT_MSG':
        keyboard = [[InlineKeyboardButton("💬 رد على المستخدم", callback_data=f"adm_reply_{user_id}")]]
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=f"💬 **رسالة دعم (صورة) من العميل `{user_id}`:**\n{caption}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الصورة للفريق الدعم بنجاح!")

# ---------------------------------------------------------
# تشغيل تطبيق البوت وتوجيه الأوامر
# ---------------------------------------------------------
def main():
    if not BOT_TOKEN:
        print("خطأ: يرجى كتابة BOT_TOKEN في متغيرات البيئة!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # معالجات الأوامر والـ Callback
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(security_check, pattern="^sec_"))
    app.add_handler(CallbackQueryHandler(admin_panel_handler, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(admin_action_handler, pattern="^(app_|rej_)"))
    app.add_handler(CallbackQueryHandler(user_callback_handler))

    # معالجات الرسائل النصية والصور
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    print("البوت المتكامل يعمل بدون أي أخطاء بنجاح...")
    app.run_polling()

if __name__ == '__main__':
    main()
