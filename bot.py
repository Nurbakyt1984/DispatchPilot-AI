import os
import tempfile

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import fitz  # PyMuPDF



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет!\n\n"
        "Отправьте PDF или фотографию Rate Confirmation."
    )


async def extract_pdf(file_path):
    text = ""

    doc = fitz.open(file_path)

    for page in doc:
        page_text = page.get_text()

        if page_text.strip():
            text += page_text + "\n"

        else:
            pix = page.get_pixmap(dpi=300)

            img_path = file_path + ".png"

            pix.save(img_path)

            
            os.remove(img_path)

    doc.close()

    return text


async def pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        await file.download_to_drive(tmp.name)

        text = await extract_pdf(tmp.name)

    os.remove(tmp.name)

    if len(text.strip()) == 0:
        await update.message.reply_text("❌ Не удалось извлечь текст.")
    else:
        await update.message.reply_text(
            "✅ Текст извлечён:\n\n" + text[:4000]
        )




app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

app.add_handler(CommandHandler("start", start))

app.add_handler(
    MessageHandler(filters.Document.PDF, pdf_handler)


app.run_polling()
