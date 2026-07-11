import os
import json
import asyncio
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
ORS_API_KEY = os.getenv("ORS_API_KEY")

# Memory of loads by PO number (per chat), lives while the container runs
LOADS = {}

# Collector for PDF albums (several files sent as one message)
ALBUMS = {}

PROMPT = """You are a freight dispatch assistant. Extract load data from this Rate Confirmation text.

Return ONLY a JSON object, no markdown, no explanation:
{
  "po_number": "...",
  "broker": "TQL (Total Quality Logistics)",
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
- broker: the freight broker company name (e.g. TQL, CH Robinson, Coyote, Echo). Usually in the header/logo area of the document. Use null if not found.
- rate: the TOTAL carrier pay in USD as a number (no $ or commas). Look carefully for labels like "Rate", "Total Rate", "Total", "Carrier Pay", "Line Haul", "Amount", "Total Carrier Pay". Sum line haul + fuel surcharge + accessorials if listed separately. Only use null if there is truly no dollar amount for the carrier in the document.
- miles: number if stated in the document, otherwise ESTIMATE realistic driving miles between pickup and delivery cities using your knowledge. Use null only if both cities are unknown.
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


def merge_loads(old: dict, new: dict) -> dict:
    """Combine two documents for the same PO: new values fill gaps in old."""
    merged = dict(old)
    for key, value in new.items():
        if value is not None and value != "":
            merged[key] = value
    # Keep old values where new document had nothing
    for key, value in old.items():
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value
    return merged


def maps_place_link(place: str) -> str:
    q = urllib.parse.quote_plus(place)
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def maps_route_link(origin: str, destination: str) -> str:
    o = urllib.parse.quote_plus(origin)
    d = urllib.parse.quote_plus(destination)
    return f"https://www.google.com/maps/dir/?api=1&origin={o}&destination={d}"


async def ors_geocode(client: httpx.AsyncClient, place: str):
    """Turn an address into [lon, lat] via OpenRouteService."""
    r = await client.get(
        "https://api.openrouteservice.org/geocode/search",
        params={"api_key": ORS_API_KEY, "text": place, "size": 1,
                "boundary.country": "US"},
    )
    data = r.json()
    features = data.get("features") or []
    if not features:
        return None
    return features[0]["geometry"]["coordinates"]


async def real_route_miles(pickup: str, delivery: str):
    """Accurate driving miles via OpenRouteService. Returns int miles or None."""
    if not ORS_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            start = await ors_geocode(client, pickup)
            end = await ors_geocode(client, delivery)
            if not start or not end:
                return None
            r = await client.post(
                "https://api.openrouteservice.org/v2/directions/driving-hgv",
                headers={"Authorization": ORS_API_KEY},
                json={"coordinates": [start, end]},
            )
            data = r.json()
            meters = data["routes"][0]["summary"]["distance"]
            return round(meters / 1609.34)
    except Exception as e:
        print(f"ORS error: {e}")
        return None


def is_incomplete(d: dict) -> bool:
    """Load is incomplete if rate or a street address is missing."""
    no_rate = not d.get("rate")
    no_pickup_addr = not d.get("pickup_address")
    no_delivery_addr = not d.get("delivery_address")
    return no_rate or no_pickup_addr or no_delivery_addr


def build_card(d: dict, merged: bool = False) -> str:
    rate = d.get("rate")
    miles = d.get("miles")
    per_mile = f"${rate / miles:.2f}" if rate and miles else "—"
    rate_str = f"${rate:,.0f}" if rate else "—"
    miles_str = f"{miles:,}" if miles else "—"

    pickup_place = d.get("pickup_address") or d.get("pickup_city")
    delivery_place = d.get("delivery_address") or d.get("delivery_city")

    line = "━" * 32
    card = ""
    if merged:
        card += "🔄 <b>UPDATED — merged from 2 documents</b>\n\n"

    card += (
        "🚛 <b>LOAD SUMMARY</b>\n\n"
        f"🔥 <b>PO: {d.get('po_number') or '—'}</b>\n"
        f"🏢 <b>Broker:</b> {d.get('broker') or '—'}\n\n"
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
        card += (
            f"\n📋 Copy address:\n"
            f"<code>{pickup_place}</code>\n"
            f"────────────────────\n"
            f"🗺 <a href=\"{maps_place_link(pickup_place)}\">Open PICKUP in Google Maps</a>\n"
        )

    card += (
        f"\n{line}\n\n"
        "📍 <b>DELIVERY</b>\n\n"
        f"📅 <b>{d.get('delivery_date') or '—'}</b>\n"
        f"🕒 <b>{d.get('delivery_time') or '—'}</b>\n\n"
        f"📍 {delivery_place or '—'}\n"
    )
    if delivery_place:
        card += (
            f"\n📋 Copy address:\n"
            f"<code>{delivery_place}</code>\n"
            f"────────────────────\n"
            f"🗺 <a href=\"{maps_place_link(delivery_place)}\">Open DELIVERY in Google Maps</a>\n"
        )

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

    if not merged and is_incomplete(d):
        card += (
            f"\n\n⏳ <i>Some info is missing. "
            f"Send the second document with the same PO# and I will merge them.</i>"
        )
    return card


async def download_pdf_text(update: Update) -> str:
    file = await update.message.document.get_file()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name

    await file.download_to_drive(tmp_path)
    text = extract_pdf(tmp_path)
    os.remove(tmp_path)
    return text


async def process_text(update: Update, text: str):
    if not text.strip():
        await update.message.reply_text("PDF does not contain readable text.")
        return

    await update.message.reply_text("Analyzing Rate Confirmation...")

    try:
        data = await parse_with_ai(text)

        chat_id = update.message.chat_id
        po = data.get("po_number")
        merged = False

        if po:
            key = (chat_id, str(po))
            if key in LOADS:
                data = merge_loads(LOADS[key], data)
                merged = True
            LOADS[key] = data
            # Keep memory small: store at most 50 loads
            if len(LOADS) > 50:
                LOADS.pop(next(iter(LOADS)))

        # Accurate truck-route miles via OpenRouteService
        pickup_place = data.get("pickup_address") or data.get("pickup_city")
        delivery_place = data.get("delivery_address") or data.get("delivery_city")
        if pickup_place and delivery_place:
            route_miles = await real_route_miles(pickup_place, delivery_place)
            if route_miles:
                data["miles"] = route_miles
                if po:
                    LOADS[(chat_id, str(po))] = data

        card = build_card(data, merged=merged)
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


async def finish_album(group_key):
    """Wait until the album stops growing, then process all PDFs as one load."""
    while True:
        await asyncio.sleep(2)
        album = ALBUMS.get(group_key)
        if album is None:
            return
        if album["done_growing"]:
            break
        album["done_growing"] = True  # if no new file arrives in 2s, finish

    album = ALBUMS.pop(group_key, None)
    if not album:
        return
    combined = "\n\n===== NEXT DOCUMENT =====\n\n".join(album["texts"])
    await process_text(album["update"], combined)


async def pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.message.media_group_id

    if group_id:
        # Several PDFs sent as one message: collect them, answer once
        group_key = (update.message.chat_id, group_id)
        text = await download_pdf_text(update)

        if group_key not in ALBUMS:
            ALBUMS[group_key] = {
                "texts": [text],
                "update": update,
                "done_growing": False,
            }
            asyncio.create_task(finish_album(group_key))
        else:
            ALBUMS[group_key]["texts"].append(text)
            ALBUMS[group_key]["done_growing"] = False  # still growing, keep waiting
        return

    # Single PDF
    text = await download_pdf_text(update)
    await process_text(update, text)


def main():
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.PDF, pdf_handler))

    print("DispatchPilot AI started")
    app.run_polling()


if __name__ == "__main__":
    main()
