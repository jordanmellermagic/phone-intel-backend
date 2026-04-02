import os
import re
from fastapi import FastAPI, Request, Form
from fastapi.responses import PlainTextResponse, JSONResponse

app = FastAPI()

# In-memory store — just the phone number, no Searchbug here
latest_phone: str | None = None


def clean_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


# ── Twilio webhook ───────────────────────────────────────────────
# Stores the caller's number. iOS app does the Searchbug lookup.

@app.post("/twilio/incoming", response_class=PlainTextResponse)
async def twilio_incoming(
    From: str = Form(default=""),
    To:   str = Form(default=""),
):
    global latest_phone

    print(f"[Twilio] Incoming call from {From} to {To}")

    digits = clean_phone(From)
    if len(digits) == 10:
        latest_phone = digits
        print(f"[Stored] {digits}")
    else:
        print(f"[Twilio] Could not parse number: {From}")

    # Silent hangup
    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
        media_type="application/xml"
    )


# ── Poll endpoint ────────────────────────────────────────────────
# iOS app hits this every 2s. Returns phone number only.
# App then calls Searchbug directly (already works in Swift).

@app.get("/latest")
async def get_latest():
    if latest_phone is None:
        return JSONResponse({"status": "empty"})
    return JSONResponse({"status": "found", "phone": latest_phone})


# ── Clear endpoint ───────────────────────────────────────────────

@app.post("/clear")
async def clear_latest():
    global latest_phone
    latest_phone = None
    return JSONResponse({"status": "cleared"})


# ── Health check ─────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "PhoneIntel Backend"}
