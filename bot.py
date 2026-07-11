import os
import json
import tempfile
import urllib.parse

import fitz  # PyMuPDF
import httpx
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

PROMPT = """You are a freight dispatch assistant. Extract load data from this Rate Confirmation text.

Return ONLY a JSON object, no markdown, no explanation:
{
  "po_number": "...",
  "rate": 2850,
  "miles": 742,
  "pickup_date": "07/10/2026",
  "pickup_time": "08:00",
  "pickup_address": "1400 N MacArthur Dr, Tracy, CA 95376",
  "pickup_city": "Tracy, CA",
  "delivery_date": "07/13/2026",
  "delivery_time": "09:00",
  "delivery_address": "7148 W. Old Bingham Hwy, West Jordan, UT 84081",
  "delivery_city": "West Jordan, UT",
  "trailer": "Flatbed",
  "weight": "40,000 lb",
  "commodity": "Paper Rolls",
  "tarp_required": true
}

Rules:
- rate: the TOTAL carrier pay in USD as a number (no $ or commas). Look carefully for labels like "Rate", "Total Rate", "Total", "Carrier Pay", "Line Haul", "Amount", "Total Carrier Pay". Sum line haul + fuel surcharge + accessorials if listed separately. Only use null if there is truly no dollar amount for the carrier in the document.
- miles: ALWAYS return a number, never null. If miles are stated in the document, use them. Otherwise ESTIMATE realistic driving miles between pickup and delivery cities using your knowledge.
- pickup_address / delivery_address: full street address if present in the document, otherwise null.
- If any other field is missing, use null.
- tarp_required is true only if the document mentions tarp/tarping.

Rate Confirmation text:
"""


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


async def parse_with_ai(text: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 1000,
                "messages": [
                    {"role": "user", "content": PROMPT + text[:15000]}
                ],
            },
        )
    data = response.json()
    if "content" not in data:
        print(f"Anthropic API error: {data}")
        raise RuntimeError(data.get("error", {}).get("message", "Unknown API error"))
    answer = data["content"][0]["text"]
    answer = answer.replace("```json", "").replace("```", "").strip()
    return json.loads(answer)


def maps_place_link(place: str) -> str:
    q = urllib.parse.quote_plus(place)
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def maps_route_link(origin: str, destination: str) -> str:
    o = urllib.parse.quote_plus(origin)
    d = urllib.parse.quote_plus(destination)
    return f"https://www.google.com/maps/dir/?api=1&origin={o}&destination={d}"


def build_card(d: dict) -> str:
    rate = d.get("rate")
    miles = d.get("miles")
    per_mile = f"${rate / miles:.2f}" if rate and miles else "—"
    rate_str = f"${rate:,.0f}" if rate else "—"
    miles_str = f"{miles:,}" if miles else "—"

    pickup_place = d.get("pickup_address") or d.get("pickup_city")
    delivery_place = d.get("delivery_address") or d.get("delivery_city")

    line = "━" * 32
    card = (
        "🚛 <b>LOAD SUMMARY</b>\n\n"
        f"🔥 <b>PO: {d.get('po_number') or '—'}</b>\n\n"
        f"💰 <b>Rate:</b> {rate_str}\n"
        f"📏 <b>Miles:</b> {miles_str}\n"
        f"💵 <b>$/Mile:</b> {per_mile}\n\n"
        f"{line}\n\n"
        "📍 <b>PICKUP</b>\n\n"
        f"📅 <b>{d.get('pickup_date') or '—'}</b>\n"
        f"🕒 <b>{d.get('pickup_time') or '—'}</b>\n\n"
        f"📍 {pickup_place or '—'}\n"
    )
    if pickup_place:
        card += f"📋 <code>{pickup_place}</code>\n"
        card += f"🗺 <a href=\"{maps_place_link(pickup_place)}\">Open PICKUP in Google Maps</a>\n"

    card += (
        f"\n{line}\n\n"
        "📍 <b>DELIVERY</b>\n\n"
        f"📅 <b>{d.get('delivery_date') or '—'}</b>\n"
        f"🕒 <b>{d.get('delivery_time') or '—'}</b>\n\n"
        f"📍 {delivery_place or '—'}\n"
    )
    if delivery_place:
        card += f"📋 <code>{delivery_place}</code>\n"
        card += f"🗺 <a href=\"{maps_place_link(delivery_place)}\">Open DELIVERY in Google Maps</a>\n"

    if pickup_place and delivery_place:
        route_url = maps_route_link(pickup_place, delivery_place)
        card += f"🛣 <a href=\"{route_url}\">Full route PICKUP → DELIVERY</a>\n"

    card += (
        f"\n{line}\n\n"
        f"🚛 <b>Trailer:</b> {d.get('trailer') or '—'}\n"
        f"⚖️ <b>Weight:</b> {d.get('weight') or '—'}\n"
        f"📦 <b>Commodity:</b> {d.get('commodity') or '—'}\n"
    )

    if d.get("tarp_required"):
        card += f"\n{line}\n\n🚨 <b>TARP REQUIRED</b>"
    return card


async def pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name

    await file.download_to_drive(tmp_path)
    text = extract_pdf(tmp_path)
    os.remove(tmp_path)

    if not text.strip():
        await update.message.reply_text("PDF does not contain readable text.")
        return

    await update.message.reply_text("Analyzing Rate Confirmation...")

    try:
        data = await parse_with_ai(text)
        card = build_card(data)
        await update.message.reply_text(
            card,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_to_message_id=update.message.message_id,
        )
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text(
            "Could not parse this Rate Confirmation. Please try again."
        )


def main():
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.PDF, pdf_handler))

    print("DispatchPilot AI started")
    app.run_polling()


if __name__ == "__main__":
    main()
