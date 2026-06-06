import json
import os
import threading
from datetime import datetime, timezone

WALLETS_PATH = os.path.join(os.path.dirname(__file__), "smart_wallets.json")
_lock        = threading.Lock()


def _load():
    if not os.path.exists(WALLETS_PATH):
        return {}
    with open(WALLETS_PATH, "r") as f:
        return json.load(f)


def _save(data):
    with open(WALLETS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def add_wallet(address, label="", category="custom", notes=""):
    addr = address.strip().lower()
    if not addr:
        return {"ok": False, "error": "Address required"}
    with _lock:
        db = _load()
        if addr in db:
            return {"ok": False, "error": "Already tracked"}
        display = label.strip() if label.strip() else (addr[:8] + "…" + addr[-4:])
        db[addr] = {
            "address":      addr,
            "label":        display,
            "category":     (category or "custom").strip(),
            "notes":        (notes    or "").strip(),
            "added":        datetime.now(timezone.utc).isoformat(),
            "signal_count": 0,
            "last_signal":  None,
        }
        _save(db)
    return {"ok": True}


def remove_wallet(address):
    addr = address.strip().lower()
    with _lock:
        db = _load()
        if addr not in db:
            return {"ok": False, "error": "Not found"}
        del db[addr]
        _save(db)
    return {"ok": True}


def list_wallets():
    with _lock:
        return _load()


def get_wallet(address):
    addr = address.strip().lower()
    with _lock:
        return _load().get(addr)


def detect_in_scan(token_data):
    """
    Compare GoPlus top holders against the smart money registry.
    Returns list of match dicts. Does NOT update stats — signal_feed handles that.
    """
    holders_raw = token_data.get("holders") or []

    with _lock:
        db = _load()

    if not db:
        return []

    hits = []
    for h in holders_raw:
        addr = (h.get("address") or "").lower()
        if addr not in db:
            continue
        try:
            pct = float(h.get("percent", 0)) * 100
        except (TypeError, ValueError):
            pct = 0.0
        w = db[addr]
        hits.append({
            "address":      addr,
            "label":        w["label"],
            "category":     w.get("category", "custom"),
            "holdings_pct": round(pct, 2),
        })

    return hits


def bump_signal_count(addresses):
    """Increment signal_count and update last_signal for each address."""
    if not addresses:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        db      = _load()
        changed = False
        for addr in addresses:
            if addr in db:
                db[addr]["signal_count"] = db[addr].get("signal_count", 0) + 1
                db[addr]["last_signal"]  = now
                changed = True
        if changed:
            _save(db)
