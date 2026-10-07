#!/usr/bin/env python3
"""
relay.py — piepklein tussenstation dat de Binance long/short-ratio doorgeeft.

Waarom: Binance weigert verzoeken vanaf Amerikaanse IP-adressen, en de standaardservers
van PythonAnywhere staan in de VS. Deze relay draait op een gratis host in Europa, haalt
de data daar op, en geeft hem door aan je bot. Je hoofdaccount hoeft dus niet te verhuizen.

De relay bewaart niets, kent je API-keys niet en kan niet handelen. Hij haalt alleen
publieke marktdata op. Het ergste dat kan gebeuren als iemand de URL vindt, is dat hij
diezelfde publieke cijfers ziet.

--------------------------------------------------------------------------------
DEPLOYEN — kies er één (alle drie gratis, kies een Europese regio)
--------------------------------------------------------------------------------

A. Render.com (eenvoudigst)
   1. Maak een publieke GitHub-repo met dit bestand en een `requirements.txt` met:
          flask
          gunicorn
   2. Render → New → Web Service → koppel de repo
   3. Region: **Frankfurt**
   4. Start command:  gunicorn relay:app
   5. Je krijgt een URL zoals https://mijn-relay.onrender.com
   Let op: gratis Render-services gaan na inactiviteit in slaapstand en hebben dan
   ~30 seconden nodig om wakker te worden. De bot houdt daar rekening mee (60s timeout)
   en de kwartiercheck houdt hem meestal wakker.

B. Fly.io
   1. `fly launch` in de map met dit bestand, kies regio **ams** (Amsterdam) of **fra**
   2. `fly deploy`

C. Een eigen VPS of Raspberry Pi thuis
   Draai `python3 relay.py` en zet er een reverse proxy met https voor. Een Pi in
   Nederland is qua locatie ideaal; hij moet dan wel altijd aan staan.

--------------------------------------------------------------------------------
GEBRUIKEN
--------------------------------------------------------------------------------
Zet in config.json van de bot:

    "trading": {
        ...
        "ls_relay_url": "https://mijn-relay.onrender.com"
    }

De bot probeert eerst Binance rechtstreeks; lukt dat niet, dan via de relay; lukt dat
ook niet, dan valt hij terug op de OKX-ratio. Test met:

    python3 tapebot.py --status

--------------------------------------------------------------------------------
BYBIT EN BITGET
--------------------------------------------------------------------------------
Ook Bybit weigert Amerikaanse IP-adressen. De route /beurs geeft twee publieke
endpoints van Bybit en Bitget door:

    /beurs?url=<volledige https-url van het endpoint>

Alleen de combinaties in BEURS_TOEGESTAAN mogen door; al het andere krijgt 403.
Testen in je browser:  https://<jouw-relay>.onrender.com/health/beurzen

--------------------------------------------------------------------------------
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, request

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

# Kleine cache: meerdere munten per kwartier hoeven Binance niet elke keer te bevragen.
_cache = {}
CACHE_SECONDS = 240

# Alleen deze paden mogen door. Zo is de relay geen open doorgeefluik naar het internet.
TOEGESTAAN = {
    "/futures/data/globalLongShortAccountRatio",
    "/futures/data/topLongShortAccountRatio",
    "/futures/data/topLongShortPositionRatio",
    "/futures/data/openInterestHist",
    "/futures/data/takerlongshortRatio",
}

# Voor /beurs: alleen deze combinaties van host en pad, alleen https.
# api.bytick.com is het tweede, officiële adres van Bybit.
BEURS_TOEGESTAAN = {
    ("api.bybit.com", "/v5/market/account-ratio"),
    ("api.bytick.com", "/v5/market/account-ratio"),
    ("api.bitget.com", "/api/v2/mix/market/account-long-short"),
}


def haal_op(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


@app.route("/ls")
def long_short():
    """
    /ls?symbol=BTCUSDT&period=2h&limit=360
    Geeft het antwoord van Binance ongewijzigd door.
    """
    symbol = (request.args.get("symbol") or "BTCUSDT").upper()
    period = request.args.get("period") or "2h"
    limit = min(int(request.args.get("limit", 360)), 500)
    pad = request.args.get("path") or "/futures/data/globalLongShortAccountRatio"
    if pad not in TOEGESTAAN:
        return jsonify({"error": "pad niet toegestaan"}), 400

    sleutel = (pad, symbol, period, limit)
    nu = time.time()
    if sleutel in _cache and nu - _cache[sleutel][0] < CACHE_SECONDS:
        return jsonify(_cache[sleutel][1])

    url = f"https://fapi.binance.com{pad}?symbol={symbol}&period={period}&limit={limit}"
    try:
        data = haal_op(url)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502

    if isinstance(data, dict) and data.get("msg"):
        # Ook deze host wordt geweigerd — dan staat hij blijkbaar niet in Europa.
        return jsonify({"error": data["msg"]}), 451

    _cache[sleutel] = (nu, data)
    return jsonify(data)


def haal_ruw(url):
    """Haalt een url op en geeft (statuscode, tekst). Fouten van de beurs gaan mee terug."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            tekst = e.read().decode("utf-8", errors="replace")
        except Exception:
            tekst = ""
        return e.code, tekst


def beurs_toegestaan(url):
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    return u.scheme == "https" and (u.netloc.lower(), u.path) in BEURS_TOEGESTAAN


@app.route("/beurs")
def beurs():
    """
    /beurs?url=https://api.bybit.com/v5/market/account-ratio?category=linear&...
    Geeft het antwoord van Bybit of Bitget ongewijzigd door.
    """
    doel = request.args.get("url", "")
    if not beurs_toegestaan(doel):
        host = urllib.parse.urlparse(doel).netloc or "?"
        return jsonify({"error": f"niet toegestaan: {host}{urllib.parse.urlparse(doel).path}"}), 403

    nu = time.time()
    if doel in _cache and nu - _cache[doel][0] < CACHE_SECONDS:
        return app.response_class(_cache[doel][1], mimetype="application/json")
    try:
        code, tekst = haal_ruw(doel)
    except Exception as e:
        return jsonify({"error": f"geen verbinding met de beurs: {str(e)[:150]}"}), 502
    if code != 200:
        # bijvoorbeeld als de beurs ook Frankfurt weert
        return jsonify({"error": f"beurs gaf HTTP {code}: {' '.join(tekst.split())[:150]}"}), 502
    _cache[doel] = (nu, tekst)
    return app.response_class(tekst, mimetype="application/json")


@app.route("/health/beurzen")
def health_beurzen():
    """Kan de relay Bybit en Bitget bereiken? Handig om in je browser te openen."""
    proeven = {
        "bybit": "https://api.bybit.com/v5/market/account-ratio"
                 "?category=linear&symbol=BTCUSDT&period=4h&limit=3",
        "bitget": "https://api.bitget.com/api/v2/mix/market/account-long-short"
                  "?symbol=BTCUSDT&productType=USDT-FUTURES&period=4h",
    }
    uit = {}
    for naam, url in proeven.items():
        try:
            code, tekst = haal_ruw(url)
            ok = code == 200 and ('"retCode":0' in tekst.replace(" ", "")
                                  or '"code":"00000"' in tekst.replace(" ", ""))
            uit[naam] = {"ok": ok, "http": code, "begin": " ".join(tekst.split())[:120]}
        except Exception as e:
            uit[naam] = {"ok": False, "error": str(e)[:150]}
    return jsonify(uit), (200 if all(v.get("ok") for v in uit.values()) else 502)


@app.route("/health")
def health():
    """Snelle controle of de relay Binance echt kan bereiken."""
    try:
        d = haal_op("https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
                    "?symbol=BTCUSDT&period=2h&limit=3")
        ok = isinstance(d, list) and len(d) > 0
        return jsonify({"ok": ok, "punten": len(d) if isinstance(d, list) else 0,
                        "laatste": d[-1] if ok else None})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 502


@app.route("/")
def index():
    return ("Relay actief. Test Binance met /health en Bybit/Bitget met /health/beurzen.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
