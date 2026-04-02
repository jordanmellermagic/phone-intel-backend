import os
import re
import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import PlainTextResponse, JSONResponse

app = FastAPI()

SEARCHBUG_ACCOUNT = os.environ.get("SEARCHBUG_ACCOUNT")
SEARCHBUG_PASS    = os.environ.get("SEARCHBUG_PASS")

# In-memory store for the latest result
# Simple enough for a single performer — no database needed
latest_result: dict | None = None


def clean_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


async def searchbug_lookup(phone_digits: str) -> dict | None:
    url = "https://data.searchbug.com/api/search.aspx"
    payload = {
        "CO_CODE": SEARCHBUG_ACCOUNT,
        "PASS":    SEARCHBUG_PASS,
        "TYPE":    "api_ppl",
        "F":       phone_digits,
        "FORMAT":  "JSON",
        "LIMIT":   "1",
    }

    # Must send as URL-encoded string (not multipart form-data) to match what works in Swift
    encoded = "&".join(f"{k}={v}" for k, v in payload.items())
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.post(
            url,
            content=encoded.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    print(f"[Searchbug] status={resp.status_code} body={resp.text[:400]}")

    try:
        data = resp.json()
    except Exception:
        print("[Searchbug] Failed to parse JSON")
        return None

    if data.get("Status") == "Error":
        print(f"[Searchbug] Error: {data.get('Error')}")
        return None

    response = data.get("response", {})
    if not response:
        return None

    # Name
    name = None
    names = response.get("names", [])
    if names:
        n = names[0]
        first = n.get("firstName", "").title()
        last  = n.get("lastName",  "").title()
        name  = f"{first} {last}".strip() or None

    # DOB
    dob = None
    dobs = response.get("DOBs", [])
    if dobs:
        d     = dobs[0]
        month = str(d.get("month", "")).zfill(2)
        day   = str(d.get("day",   "")).zfill(2)
        year  = str(d.get("year",  ""))
        if year and month and month != "00" and day and day != "00":
            dob = f"{month}/{day}/{year}"
        elif year and month and month != "00":
            dob = f"{month}/{year}"
        elif year:
            dob = year

    # Address
    address = None
    addresses = response.get("addresses", [])
    if addresses:
        a     = addresses[0]
        parts = [p for p in [a.get("line1"), a.get("city"), a.get("state"), a.get("zip")] if p]
        address = ", ".join(parts) or None

    # Relatives
    relatives = []
    for rel in response.get("relatives", [])[:5]:
        rel_names = rel.get("names", [])
        if rel_names:
            rn   = rel_names[0]
            rf   = rn.get("firstName", "").title()
            rl   = rn.get("lastName",  "").title()
            full = f"{rf} {rl}".strip()
            if full:
                relatives.append(full)

    if not name and not dob and not address:
        return None

    return {
        "phone":     phone_digits,
        "name":      name,
        "dob":       dob,
        "address":   address,
        "relatives": relatives,
    }


# ── Twilio webhook ───────────────────────────────────────────────
# Twilio POSTs here when someone calls your Twilio number.
# Twilio expects a TwiML response — we return empty TwiML to hang up silently.

@app.post("/twilio/incoming", response_class=PlainTextResponse)
async def twilio_incoming(
    From: str = Form(default=""),
    To:   str = Form(default=""),
):
    global latest_result

    print(f"[Twilio] Incoming call from {From} to {To}")

    caller_digits = clean_phone(From)

    if len(caller_digits) == 10:
        try:
            result = await searchbug_lookup(caller_digits)
            if result:
                latest_result = result
                print(f"[Searchbug] Hit: {result}")
            else:
                latest_result = {
                    "phone":     caller_digits,
                    "name":      None,
                    "dob":       None,
                    "address":   None,
                    "relatives": [],
                }
                print("[Searchbug] No data found for this number")
        except Exception as e:
            print(f"[Searchbug] Exception: {e}")
            latest_result = {
                "phone":     caller_digits,
                "name":      None,
                "dob":       None,
                "address":   None,
                "relatives": [],
            }
    else:
        print(f"[Twilio] Could not parse caller number: {From}")

    # Return TwiML that hangs up immediately (silent, no ringing tone for caller)
    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
        media_type="application/xml"
    )


# ── Poll endpoint ────────────────────────────────────────────────
# iOS app hits this every 2 seconds to check for a new result.

@app.get("/latest")
async def get_latest():
    if latest_result is None:
        return JSONResponse({"status": "empty"})
    return JSONResponse({"status": "found", "data": latest_result})


# ── Clear endpoint ───────────────────────────────────────────────
# iOS app calls this after displaying a result, ready for next call.

@app.post("/clear")
async def clear_latest():
    global latest_result
    latest_result = None
    return JSONResponse({"status": "cleared"})


# ── Manual search endpoint ───────────────────────────────────────
# Keeps the existing manual search working through the backend.

@app.post("/search")
async def manual_search(request: Request):
    global latest_result
    body = await request.json()
    raw_phone = body.get("phone", "").strip()
    digits = clean_phone(raw_phone)

    if len(digits) != 10:
        return JSONResponse({"error": "Invalid US phone number"}, status_code=400)

    result = await searchbug_lookup(digits)

    if result is None:
        return JSONResponse({"status": "not_found", "phone": digits})

    return JSONResponse({"status": "found", "data": result})


# ── Health check ─────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "PhoneIntel Backend"}
