
import os

import tempfile

import fitz

from telegram import Update

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text(

" DispatchPilot Al\n\nSend PDF Rate Confirmation."

async def extract_pdf(file_path):

doc = fitz.open(file_path)

for page in doc:

page_text = page.get_text()
if page_text.strip():

text += page_text + "\n"

doc.close()

return text

async def pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): file = await update.message.document.get_file()

with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:

await file.download_to_drive(tmp.name)

text = await extract_pdf(tmp.name)

os.remove(tmp.name)

if not text.strip():

await update.message.reply_text(" PDF does not contain readable text.")

else:

await update.message.reply_text(" Text extracted:\n\n" + text[:4000])

app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

app.add_handler(CommandHandler("start", start)) app.add_handler(MessageHandler(filters.Document.PDF, pdf_handler))

print("DispatchPilot Al started") prin app.run_polling()
