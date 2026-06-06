import json
import os
import threading
from datetime import datetime, timezone

WHALE_DB_PATH = os.path.join(os.path.dirname(__file__), "whale_profiles.json")
_lock = threading.Lock()

CHAIN_NAMES = {
    "1": "Ethereum",
    "56": "BNB Chain",
    "137": "Polygon",
    "42161": "Arbitrum",
    "10": "Optimism",
    "8453": "Base",
    "43114": "Avalanche",
    "solana": "Solana",
}


def _load():
    if not os.path.exists(WHALE_DB_PATH):
        return {}
    with open(WHALE_DB_PATH, "r") as f:
        return json.load(f)


def _save(data):
    with open(WHALE_DB_PATH, "w") as f:
        json.dump(data, f, indent=2)


def add_whale(address, label=""):
    addr = address.strip().lower()
    if not addr:
        return {"ok": False, "error": "Address required"}
    with _lock:
        db = _load()
        if addr in db:
            return {"ok": False, "error": "Already tracked"}
        display = label.strip() if label.strip() else (addr[:8] + "…" + addr[-4:])
        db[addr] = {
            "address": addr,
            "label": display,
            "added": datetime.now(timezone.utc).isoformat(),
            "last_activity": None,
            "entry_count": 0,
            "exit_count": 0,
            "current_holdings": {},
            "activity_log": [],
        }
        _save(db)
    return {"ok": True}


def remove_whale(address):
    addr = address.strip().lower()
    with _lock:
        db = _load()
        if addr not in db:
            return {"ok": False, "error": "Not found"}
        del db[addr]
        _save(db)
    return {"ok": True}


def list_whales():
    with _lock:
        return _load()


def get_profile(address):
    addr = address.strip().lower()
    with _lock:
        db = _load()
        return db.get(addr)


def process_token_scan(token_address, chain_id, token_data, dex_data):
    """
    Cross-reference GoPlus top holders against the whale registry.
    Logs ENTRY / EXIT / INCREASE / DECREASE / DETECTED events.
    Returns list of alert dicts to attach to the scan result.
    """
    holders_raw  = token_data.get("holders") or []
    token_name   = token_data.get("token_name",   "Unknown")
    token_symbol = token_data.get("token_symbol", "???")
    token_key    = f"{chain_id}:{token_address.lower()}"
    chain_name   = CHAIN_NAMES.get(str(chain_id), str(chain_id))

    price_usd = liquidity_usd = volume_h24 = 0.0
    if dex_data:
        try:
            price_usd     = float(dex_data.get("price_usd")     or 0)
            liquidity_usd = float(dex_data.get("liquidity_usd") or 0)
            volume_h24    = float(dex_data.get("volume_h24")    or 0)
        except (TypeError, ValueError):
            pass

    with _lock:
        db = _load()
        if not db:
            return []

        now     = datetime.now(timezone.utc).isoformat()
        alerts  = []
        changed = False

        # Build current holder address → pct map from GoPlus top-holders list
        current_addrs = {}
        for h in holders_raw:
            addr = (h.get("address") or "").lower()
            if not addr:
                continue
            try:
                pct = float(h.get("percent", 0)) * 100
            except (TypeError, ValueError):
                pct = 0.0
            current_addrs[addr] = round(pct, 2)

        # ── Step 1: tracked whales present in current top holders ──
        for whale_addr, whale in db.items():
            if whale_addr not in current_addrs:
                continue

            pct         = current_addrs[whale_addr]
            was_holding = token_key in whale["current_holdings"]

            if not was_holding:
                event_type = "ENTRY"
                whale["entry_count"] += 1
                whale["current_holdings"][token_key] = {
                    "token_name":          token_name,
                    "token_symbol":        token_symbol,
                    "chain":               chain_name,
                    "chain_id":            chain_id,
                    "token_address":       token_address.lower(),
                    "entry_timestamp":     now,
                    "entry_price_usd":     price_usd,
                    "entry_liquidity_usd": liquidity_usd,
                }
            else:
                prev = whale["current_holdings"][token_key].get("last_holdings_pct", 0)
                diff = pct - prev
                if diff > 0.5 and prev > 0 and pct > prev * 1.05:
                    event_type = "INCREASE"
                elif diff < -0.5 and prev > 0 and pct < prev * 0.95:
                    event_type = "DECREASE"
                else:
                    event_type = "DETECTED"

            holding = whale["current_holdings"][token_key]
            holding["last_seen"]         = now
            holding["last_holdings_pct"] = pct

            whale["activity_log"].insert(0, {
                "timestamp":     now,
                "event_type":    event_type,
                "chain":         chain_name,
                "chain_id":      chain_id,
                "token_address": token_address.lower(),
                "token_name":    token_name,
                "token_symbol":  token_symbol,
                "holdings_pct":  pct,
                "price_usd":     price_usd,
                "liquidity_usd": liquidity_usd,
                "volume_h24":    volume_h24,
            })
            whale["activity_log"] = whale["activity_log"][:100]
            whale["last_activity"] = now
            changed = True

            alerts.append({
                "whale_address": whale_addr,
                "whale_label":   whale["label"],
                "event_type":    event_type,
                "holdings_pct":  pct,
                "token_name":    token_name,
                "token_symbol":  token_symbol,
                "price_usd":     price_usd,
                "liquidity_usd": liquidity_usd,
            })

        # ── Step 2: exit detection — was holding, now absent ───────
        for whale_addr, whale in db.items():
            if token_key not in whale["current_holdings"]:
                continue
            if whale_addr in current_addrs:
                continue  # still present — handled above

            entry_price = whale["current_holdings"][token_key].get("entry_price_usd", 0)
            whale["activity_log"].insert(0, {
                "timestamp":       now,
                "event_type":      "EXIT",
                "chain":           chain_name,
                "chain_id":        chain_id,
                "token_address":   token_address.lower(),
                "token_name":      token_name,
                "token_symbol":    token_symbol,
                "holdings_pct":    0.0,
                "price_usd":       price_usd,
                "liquidity_usd":   liquidity_usd,
                "volume_h24":      volume_h24,
                "entry_price_usd": entry_price,
            })
            whale["activity_log"] = whale["activity_log"][:100]
            del whale["current_holdings"][token_key]
            whale["exit_count"] += 1
            whale["last_activity"] = now
            changed = True

        if changed:
            _save(db)

    return alerts


def get_recent_activity(limit=50):
    """Global activity feed across all whales, newest first."""
    with _lock:
        db = _load()

    events = []
    for addr, whale in db.items():
        for ev in whale["activity_log"]:
            events.append({**ev, "whale_address": addr, "whale_label": whale["label"]})

    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return events[:limit]
