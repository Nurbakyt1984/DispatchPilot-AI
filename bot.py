from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправьте мне PDF Rate Confirmation, и я начну его распознавать."
    )

async def pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📄 PDF получен. Скоро я извлеку данные и создам карточку груза."
    )

app = ApplicationBuilder().token("YOUR_BOT_TOKEN").build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.Document.PDF, pdf_handler))

app.run_polling()
