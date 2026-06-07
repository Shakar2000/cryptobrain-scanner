import json
import os
import threading
from datetime import datetime, timezone

JOURNAL_PATH = os.path.join(os.path.dirname(__file__), "trade_journal.json")
_lock = threading.Lock()


def _load():
    if not os.path.exists(JOURNAL_PATH):
        return []
    with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def list_positions(status=None):
    with _lock:
        positions = _load()
    if status:
        positions = [p for p in positions if p.get("status") == status]
    return positions


def add_position(token_address, chain, token_name, token_symbol,
                 entry_price_usd, deploy_amount,
                 target_multiplier=2.5, stop_loss_pct=-20):
    entry = float(entry_price_usd)
    deploy = float(deploy_amount)
    target_price  = entry * float(target_multiplier)
    pullout_amount = deploy * float(target_multiplier)

    position = {
        "id":                datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17],
        "token_address":     token_address.strip().lower(),
        "chain":             chain.strip().lower(),
        "token_name":        token_name,
        "token_symbol":      token_symbol,
        "entry_price_usd":   entry,
        "deploy_amount":     deploy,
        "target_multiplier": float(target_multiplier),
        "target_price_usd":  round(target_price, 12),
        "pullout_amount":    round(pullout_amount, 2),
        "stop_loss_pct":     float(stop_loss_pct),
        "status":            "open",
        "opened_at":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alerted":           False,
    }

    with _lock:
        positions = _load()
        positions.insert(0, position)
        _save(positions)

    return {"ok": True, "position": position}


def close_position(position_id):
    with _lock:
        positions = _load()
        found = False
        for p in positions:
            if p.get("id") == position_id:
                p["status"]    = "closed"
                p["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                found = True
                break
        _save(positions)
    return {"ok": found}


def mark_alerted(position_id):
    with _lock:
        positions = _load()
        for p in positions:
            if p.get("id") == position_id:
                p["alerted"] = True
                break
        _save(positions)
