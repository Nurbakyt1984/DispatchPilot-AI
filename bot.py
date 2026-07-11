import os
import tempfile

import fitz  # PyMuPDF
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "DispatchPilot AI\n\nSend PDF Rate Confirmation."
    )


def extract_pdf(file_path: str) -> str:
    text = ""
    doc = fitz.open(file_path)
    for page in doc:
        page_text = page.get_text()
        if page_text.strip():
            text += page_text + "\n"
    doc.close()
    return text


async def pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name

    await file.download_to_drive(tmp_path)
    text = extract_pdf(tmp_path)
    os.remove(tmp_path)

    if not text.strip():
        await update.message.reply_text("PDF does not contain readable text.")
    else:
        await update.message.reply_text("Text extracted:\n\n" + text[:4000])


def main():
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.PDF, pdf_handler))

    print("DispatchPilot AI started")
    app.run_polling()


if __name__ == "__main__":
    main()
