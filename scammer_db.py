import json
import os
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scammer_db.json")


def _load():
    if not os.path.exists(DB_FILE):
        return {"addresses": {}}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)


def is_scammer(address: str):
    """Return (True, label) if address is in the DB, else (False, None)."""
    if not address or address in ("N/A", ""):
        return False, None
    db = _load()
    entry = db["addresses"].get(address.lower())
    if entry:
        return True, entry.get("label", "Known scammer")
    return False, None


def add(address: str, label: str = "Manual entry"):
    """Add or update a scammer address."""
    db = _load()
    db["addresses"][address.lower()] = {
        "label": label,
        "added": datetime.utcnow().isoformat(),
    }
    _save(db)


def remove(address: str):
    """Remove a scammer address."""
    db = _load()
    db["addresses"].pop(address.lower(), None)
    _save(db)


def list_all():
    """Return all scammer entries as {address: {label, added}}."""
    return _load()["addresses"]
