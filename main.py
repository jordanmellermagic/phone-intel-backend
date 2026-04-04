import os
import re
import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import PlainTextResponse, JSONResponse

app = FastAPI()

SEARCHBUG_ACCOUNT = os.environ.get("SEARCHBUG_ACCOUNT")
SEARCHBUG_PASS    = os.environ.get("SEARCHBUG_PASS")

# Per-user store keyed by token
# { "jordan": {"phone": "...", "name": "..."}, "sarah": {...} }
store: dict = {}


def clean_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


async def searchbug_lookup(phone_digits: str) -> str | None:
    url = "https://data.searchbug.com/api/search.aspx"
    encoded = f"CO_CODE={SEARCHBUG_ACCOUNT}&PASS={SEARCHBUG_PASS}&TYPE=api_ppl&F={phone_digits}&FORMAT=JSON&LIMIT=1"

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.post(
            url,
            content=encoded.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    print(f"[Searchbug] status={resp.status_code} body={resp.text[:300]}")

    try:
        data = resp.json()
    except Exception:
        print("[Searchbug] Failed to parse JSON")
        return None

    if data.get("Status") == "Error":
        print(f"[Searchbug] Error: {data.get('Error')}")
        return None

    try:
        people = data.get("people", {})
        persons = people.get("person", [])
        if not isinstance(persons, list):
            persons = [persons]
        if not persons:
            return None

        p = persons[0]
        names = p.get("names", {}).get("name", [])
        if not isinstance(names, list):
            names = [names]
        if not names:
            return None

        n = names[0]
        first = (n.get("firstName") or "").title().strip()
        last  = (n.get("lastName")  or "").title().strip()
        full  = f"{first} {last}".strip()
        return full if full else None

    except Exception as e:
        print(f"[Searchbug] Parse error: {e}")
        return None


# ── Twilio webhook ───────────────────────────────────────────────

@app.post("/twilio/incoming/{token}", response_class=PlainTextResponse)
async def twilio_incoming(
    token: str,
    From: str = Form(default=""),
    To:   str = Form(default=""),
):
    print(f"[Twilio] Incoming call for token={token} from {From}")

    digits = clean_phone(From)

    if len(digits) == 10:
        name = await searchbug_lookup(digits)
        store[token] = {
            "phone": digits,
            "name":  name,
        }
        print(f"[Stored] token={token} phone={digits} name={name}")
    else:
        print(f"[Twilio] Could not parse number: {From}")

    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
        media_type="application/xml"
    )


# ── Poll endpoint (iOS app) ──────────────────────────────────────

@app.get("/latest/{token}")
async def get_latest(token: str):
    data = store.get(token)
    if not data:
        return JSONResponse({"status": "empty"})
    return JSONResponse({
        "status": "found",
        "phone":  data.get("phone"),
        "name":   data.get("name"),
    })


# ── Glyphs endpoint ──────────────────────────────────────────────

@app.get("/glyphs/{token}")
async def glyphs_fetch(token: str):
    data = store.get(token)
    return JSONResponse({"value": data.get("name", "") if data else ""})


# ── Clear ────────────────────────────────────────────────────────

@app.post("/clear/{token}")
async def clear_latest(token: str):
    if token in store:
        del store[token]
    return JSONResponse({"status": "cleared"})


# ── Health check ─────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "PhoneIntel Backend"}
