import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# رابط السيرفر وتوكن البوت يتم جلبهم من متغيرات البيئة تلقائياً
SERVER_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:5000")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# 1. امر البدء وتسجيل الحساب تلقائياً
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    telegram_id = user.id
    username = user.username or user.first_name

    # تسجيل الحساب في قاعدة البيانات عبر السيرفر
    try:
        requests.post(f"{SERVER_URL}/api/auth", json={
            "telegram_id": telegram_id,
            "username": username
        })
    except Exception as e:
        print(f"Error sync server: {e}")

    # رابط واجهة اللعبة والموقع داخل تلجرام Mini App
    web_app_url = f"{SERVER_URL}/static/index.html"
    
    keyboard = [
        [InlineKeyboardButton("🎮 فتح اللعبة والمنصة", web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton("💳 رصيدي الحالي", callback_data="check_balance")],
        [InlineKeyboardButton("📥 إيداع رصيد", callback_data="deposit"), InlineKeyboardButton("📤 سحب رصيد", callback_data="withdraw")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"أهلاً بك {user.first_name} في المنصة!\n"
        f"معرف حسابك: `{telegram_id}`\n\n"
        f"يمكنك فتح اللعبة وإدارة رصيدك مباشرة من الأزرار أدناه:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# 2. معالجة الأزرار التفاعلية
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    telegram_id = query.from_user.id

    if query.data == "check_balance":
        try:
            res = requests.get(f"{SERVER_URL}/api/user/{telegram_id}")
            if res.status_code == 200:
                balance = res.json().get("balance", 0)
                await query.message.reply_text(f"💰 رصيدك الحالي المتاح داخل الموقع: **{balance}** $", parse_mode="Markdown")
            else:
                await query.message.reply_text("❌ الحساب غير مسجل بعد، أرسل /start لتسجيل الحساب.")
        except Exception:
            await query.message.reply_text("⚠️ فشل الاتصال بالسيرفر الرئيسي.")

    elif query.data == "deposit":
        await query.message.reply_text(
            "📥 **طلب إيداع رصيد:**\n"
            "أرسل رسالة تحتوي على كلمة (إيداع) متبوعة بالمبلغ.\n"
            "مثال: `إيداع 50`",
            parse_mode="Markdown"
        )

    elif query.data == "withdraw":
        await query.message.reply_text(
            "📤 **طلب سحب رصيد:**\n"
            "أرسل رسالة تحتوي على كلمة (سحب) متبوعة بالمبلغ.\n"
            "مثال: `سحب 20`",
            parse_mode="Markdown"
        )

# 3. معالجة الرسائل النصية للإيداع والسحب التلقائي
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    telegram_id = update.effective_user.id

    if text.startswith("إيداع"):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            amount = float(parts[1])
            res = requests.post(f"{SERVER_URL}/api/transaction", json={
                "telegram_id": telegram_id,
                "type": "deposit",
                "amount": amount
            })
            if res.status_code == 200:
                await update.message.reply_text(f"✅ تم رفع طلب إيداع بمبلغ {amount} $ بنجاح، وهو قيد المعالجة من الكاشير.")
            else:
                await update.message.reply_text("❌ حدث خطأ أثناء رفع الطلب.")
        else:
            await update.message.reply_text("⚠️ يرجى كتابة الأمر بالشكل الصحيح. مثال: `إيداع 50`", parse_mode="Markdown")

    elif text.startswith("سحب"):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            amount = float(parts[1])
            res = requests.post(f"{SERVER_URL}/api/transaction", json={
                "telegram_id": telegram_id,
                "type": "withdraw",
                "amount": amount
            })
            if res.status_code == 200:
                await update.message.reply_text(f"✅ تم تجميد مبلغ {amount} $ ورفع طلب السحب إلى الكاشير بنجاح.")
            else:
                data = res.json()
                await update.message.reply_text(f"❌ {data.get('error', 'فشل في عملية السحب')}")
        else:
            await update.message.reply_text("⚠️ يرجى كتابة الأمر بالشكل الصحيح. مثال: `سحب 20`", parse_mode="Markdown")

def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("البوت يعمل الآن بنجاح...")
    app.run_polling()

if __name__ == '__main__':
    main()
