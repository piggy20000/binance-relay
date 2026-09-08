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
"""

import json
import os
import time
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
    return "Binance-relay actief. Gebruik /health om te testen."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
