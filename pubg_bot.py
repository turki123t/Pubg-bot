import logging, uuid, os, aiohttp, threading, uvicorn
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
MOYASAR_API_KEY = os.environ.get("MOYASAR_API_KEY", "")
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE", "")

PRODUCTS = {
    "60uc": {"name": "60 UC", "price": 10},
    "325uc": {"name": "325 UC", "price": 50},
    "660uc": {"name": "660 UC", "price": 100},
    "1800uc": {"name": "1800 UC", "price": 250},
    "3850uc": {"name": "3850 UC", "price": 500},
    "8100uc": {"name": "8100 UC", "price": 1000},
}

pending_orders = {}
SELECT_PRODUCT, ENTER_PLAYER_ID, WAITING_PAYMENT = 0, 1, 2
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
webhook_app = FastAPI()
telegram_app = None

async def create_payment(amount, desc, oid):
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.moyasar.com/v1/payments", json={"amount": amount*100, "currency": "SAR", "description": desc, "source": {"type": "paymentpage"}, "callback_url": f"{WEBHOOK_BASE}/moyasar-webhook", "metadata": {"order_id": oid}}, auth=aiohttp.BasicAuth(MOYASAR_API_KEY, "")) as r:
            d = await r.json()
            return {"payment_id": d.get("id"), "payment_url": d.get("source", {}).get("transaction_url", "")}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎮 *أهلاً في متجر شدات ببجي*\n\nاضغط الزر لتبدأ 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 اشتري شدات", callback_data="buy")]]))

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    kb = [[InlineKeyboardButton(f"{p['name']} - {p['price']} ريال", callback_data=f"product_{k}")] for k, p in PRODUCTS.items()]
    kb.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    await update.callback_query.edit_message_text("💎 *اختر الكمية:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return SELECT_PRODUCT

async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    key = update.callback_query.data.replace("product_", "")
    context.user_data["product"] = key
    p = PRODUCTS[key]
    await update.callback_query.edit_message_text(f"✅ اخترت: *{p['name']}* - *{p['price']} ريال*\n\n🎮 أرسل ID لاعبك في ببجي:", parse_mode="Markdown")
    return ENTER_PLAYER_ID

async def enter_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = update.message.text.strip()
    context.user_data["player_id"] = pid
    user = update.effective_user
    key = context.user_data["product"]
    p = PRODUCTS[key]
    await update.message.reply_text("⏳ جاري إنشاء رابط الدفع...")
    try:
        r = await create_payment(p["price"], f"شحن {p['name']} ببجي ID:{pid}", str(uuid.uuid4()))
        pending_orders[r["payment_id"]] = {"user_id": user.id, "username": user.username or user.full_name, "product_key": key, "player_id": pid}
        await update.message.reply_text(f"📋 *{p['name']}* - *{p['price']} ريال*\n🆔 `{pid}`\n\nاضغط للدفع 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 ادفع الآن", url=r["payment_url"])]]))
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ خطأ، تواصل مع الدعم.")
    return WAITING_PAYMENT

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        await q.edit_message_text("❌ تم الإلغاء. /start للبدء.")
    else:
        await update.message.reply_text("❌ تم الإلغاء. /start للبدء.")
    return -1

@webhook_app.post("/moyasar-webhook")
async def webhook(request: Request):
    d = await request.json()
    pid, status = d.get("id"), d.get("status")
    if status == "paid" and pid in pending_orders:
        o = pending_orders.pop(pid)
        p = PRODUCTS[o["product_key"]]
        await telegram_app.bot.send_message(chat_id=o["user_id"], text=f"🎉 *تم تأكيد دفعك!*\n✅ {p['name']} سيتم شحنها لـ `{o['player_id']}` خلال دقائق!", parse_mode="Markdown")
        await telegram_app.bot.send_message(chat_id=ADMIN_ID, text=f"💰 دفع مؤكد!\n👤 @{o['username']}\n🎮 {p['name']}\n🎯 `{o['player_id']}`", parse_mode="Markdown")
    return {"status": "ok"}

@webhook_app.get("/")
async def health():
    return {"status": "running"}

def run_server():
    uvicorn.run(webhook_app, host="0.0.0.0", port=8000)

def main():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).updater(None).build()
    conv = ConversationHandler(entry_points=[CallbackQueryHandler(show_products, pattern="^buy$")], states={SELECT_PRODUCT: [CallbackQueryHandler(select_product, pattern="^product_")], ENTER_PLAYER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_player_id)], WAITING_PAYMENT: []}, fallbacks=[CallbackQueryHandler(cancel, pattern="^cancel$")])
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(conv)
    threading.Thread(target=run_server, daemon=True).start()
    print("✅ البوت شغّال!")
    telegram_app.run_polling()

if __name__ == "__main__":
    main()
