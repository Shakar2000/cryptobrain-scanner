import json
import os
import threading
from datetime import datetime, timezone

import requests

LOG_PATH = os.path.join(os.path.dirname(__file__), "market_pulse_log.json")
MAX_DAYS = 90
_lock    = threading.Lock()
_cache   = {}   # {date_str: snapshot_dict}

_CG_PRICE  = "https://api.coingecko.com/api/v3/simple/price"
_CG_GLOBAL = "https://api.coingecko.com/api/v3/global"
_FNG_URL   = "https://api.alternative.me/fng/?limit=1"


def _macro_verdict(btc_dominance, fear_greed):
    if btc_dominance > 65 or fear_greed > 80:
        return "DEFENSIVE"
    if btc_dominance < 60 and fear_greed < 40:
        return "CLEAR"
    return "CAUTION"


def _load():
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _fetch_fresh():
    """Hit CoinGecko (prices + global) and Alternative.me (Fear & Greed)."""
    price_resp = requests.get(
        _CG_PRICE,
        params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd"},
        timeout=10,
    )
    price_resp.raise_for_status()
    prices = price_resp.json()

    global_resp = requests.get(_CG_GLOBAL, timeout=10)
    global_resp.raise_for_status()
    global_data = global_resp.json().get("data", {})

    fg_resp  = requests.get(_FNG_URL, timeout=10)
    fg_resp.raise_for_status()
    fg_entry = fg_resp.json().get("data", [{}])[0]

    btc_price = float(prices.get("bitcoin",  {}).get("usd", 0))
    eth_price = float(prices.get("ethereum", {}).get("usd", 0))
    sol_price = float(prices.get("solana",   {}).get("usd", 0))
    btc_dom   = float(global_data.get("market_cap_percentage", {}).get("btc", 0))
    fg_value  = int(fg_entry.get("value", 50))
    fg_label  = fg_entry.get("value_classification", "Neutral")

    now = datetime.now(timezone.utc)
    return {
        "date":             now.strftime("%Y-%m-%d"),
        "timestamp":        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "btc_price":        btc_price,
        "eth_price":        eth_price,
        "sol_price":        sol_price,
        "btc_dominance":    round(btc_dom, 2),
        "fear_greed":       fg_value,
        "fear_greed_label": fg_label,
        "macro_verdict":    _macro_verdict(btc_dom, fg_value),
    }


def fetch_snapshot(force=False):
    """Return today's snapshot; fetches fresh only when not already cached today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not force:
        with _lock:
            if today in _cache:
                return _cache[today]
        log = _load()
        if log and log[0].get("date") == today:
            snap = log[0]
            with _lock:
                _cache[today] = snap
            return snap

    snapshot = _fetch_fresh()

    with _lock:
        log = _load()
        log = [s for s in log if s.get("date") != today]
        log.insert(0, snapshot)
        log = log[:MAX_DAYS]
        _save(log)
        _cache[today] = snapshot

    return snapshot


def get_today_snapshot():
    """Return today's snapshot, or None on any failure — never raises."""
    try:
        return fetch_snapshot(force=False)
    except Exception:
        return None


def get_log(limit=30):
    """Return the N most recent daily snapshots, newest first."""
    with _lock:
        log = _load()
    return log[:limit]
