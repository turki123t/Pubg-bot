#!/usr/bin/env python3
import logging
import uuid
import aiohttp
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import threading
import uvicorn

BOT_TOKEN       = "ضع_توكن_البوت_هنا"
ADMIN_ID        = 123456789
MOYASAR_API_KEY = "ضع_مفتاح_moyasar_هنا"
WEBHOOK_BASE    = "https://your-server.com"

PRODUCTS = {
    "60uc":   {"name": "60 UC",   "price": 10},
    "325uc":  {"name": "325 UC",  "price": 50},
    "660uc":  {"name": "660 UC",  "price": 100},
    "1800uc": {"name": "1800 UC", "price": 250},
    "3850uc": {"name": "3850 UC", "price": 500},
    "8100uc": {"name": "8100 UC", "price": 1000},
}

pending_orders = {}
SELECT_PRODUCT, ENTER_PLAYER_ID, WAITING_PAYMENT = range(3)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
webhook_app = FastAPI()
telegram_app = None
async def create_moyasar_payment(amount_riyals, description, order_id):
    url = "https://api.moyasar.com/v1/payments"
    payload = {
        "amount": amount_riyals * 100,
        "currency": "SAR",
        "description": description,
        "source": {"type": "paymentpage"},
        "callback_url": f"{WEBHOOK_BASE}/moyasar-webhook",
        "metadata": {"order_id": order_id}
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, auth=aiohttp.BasicAuth(MOYASAR_API_KEY, "")) as resp:
            data = await resp.json()
            return {
                "payment_id": data.get("id"),
                "payment_url": data.get("source", {}).get("transaction_url") or data.get("_links", {}).get("payment_page", {}).get("href", "")
            }

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🛒 اشتري شدات", callback_data="buy")]]
    await update.message.reply_text(
        "🎮 *أهلاً في متجر شدات ببجي*\n\nنوفر شحن UC بأسرع وقت!\nالدفع تلقائي عبر مدى / STC Pay / بطاقة 💳\n\nاضغط الزر لتبدأ 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton(f"{p['name']} - {p['price']} ريال", callback_data=f"product_{key}")]
        for key, p in PRODUCTS.items()
    ]
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    await query.edit_message_text("💎 *اختر الكمية:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_PRODUCT

async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace("product_", "")
    context.user_data["product"] = product_key
    product = PRODUCTS[product_key]
    await query.edit_message_text(
        f"✅ اخترت: *{product['name']}* بسعر *{product['price']} ريال*\n\n🎮 *أرسل لي ID لاعبك في ببجي:*\nمثال: `5123456789`",
        parse_mode="Markdown"
    )
    return ENTER_PLAYER_ID
async def enter_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player_id = update.message.text.strip()
    context.user_data["player_id"] = player_id
    user = update.effective_user
    product_key = context.user_data["product"]
    product = PRODUCTS[product_key]
    order_id = str(uuid.uuid4())
    context.user_data["order_id"] = order_id
    await update.message.reply_text("⏳ جاري إنشاء رابط الدفع...")
    try:
        result = await create_moyasar_payment(
            amount_riyals=product["price"],
            description=f"شحن {product['name']} - ببجي - ID: {player_id}",
            order_id=order_id
        )
        payment_id = result["payment_id"]
        payment_url = result["payment_url"]
        pending_orders[payment_id] = {
            "user_id": user.id,
            "username": user.username or user.full_name,
            "product_key": product_key,
            "player_id": player_id,
        }
        keyboard = [[InlineKeyboardButton("💳 ادفع الآن", url=payment_url)]]
        await update.message.reply_text(
            f"📋 *ملخص الطلب:*\n\n🎮 الكمية: {product['name']}\n💰 السعر: {product['price']} ريال\n🆔 ID اللاعب: `{player_id}`\n\nاضغط للدفع 👇",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Moyasar error: {e}")
        await update.message.reply_text("❌ حدث خطأ. تواصل مع الدعم.")
    return WAITING_PAYMENT

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ تم الإلغاء. اكتب /start للبدء من جديد.")
    else:
        await update.message.reply_text("❌ تم الإلغاء. اكتب /start للبدء من جديد.")
    return ConversationHandler.END

@webhook_app.post("/moyasar-webhook")
async def moyasar_webhook(request: Request):
    data = await request.json()
    payment_id = data.get("id")
    status = data.get("status")
    if status == "paid" and payment_id in pending_orders:
        order = pending_orders.pop(payment_id)
        product = PRODUCTS[order["product_key"]]
        await telegram_app.bot.send_message(
            chat_id=order["user_id"],
            text=f"🎉 *تم تأكيد دفعك تلقائياً!*\n\n✅ {product['name']} سيتم شحنها لـ `{order['player_id']}` خلال دقائق!\n\nشكراً لتسوقك معنا 🎮",
            parse_mode="Markdown"
        )
        await telegram_app.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💰 *دفع مؤكد!*\n\n👤 @{order['username']}\n🎮 {product['name']}\n🎯 `{order['player_id']}`",
            parse_mode="Markdown"
        )
    return {"status": "ok"}

@webhook_app.get("/")
async def health():
    return {"status": "running"}

def run_webhook_server():
    uvicorn.run(webhook_app, host="0.0.0.0", port=8000)

def main():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(show_products, pattern="^buy$")],
        states={
            SELECT_PRODUCT:  [CallbackQueryHandler(select_product, pattern="^product_")],
            ENTER_PLAYER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_player_id)],
            WAITING_PAYMENT: [],
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern="^cancel$")],
    )
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(conv_handler)
    server_thread = threading.Thread(target=run_webhook_server, daemon=True)
    server_thread.start()
    print("✅ البوت شغّال!")
    telegram_app.run_polling()

if __name__ == "__main__":
    main()
