import os
import requests

_TELEGRAM_API = "https://api.telegram.org"


def is_available():
    return bool(
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and
        os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
    )


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id():
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def send_message(text, parse_mode="HTML"):
    """Send a message to the configured Telegram chat. Returns True on success."""
    if not is_available():
        return False
    try:
        url  = f"{_TELEGRAM_API}/bot{_token()}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": _chat_id(), "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False
