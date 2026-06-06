"""
Token Sniffer Bot — polls DEX Screener for new token profiles every 5 minutes,
auto-scans each new address, and logs GREEN verdicts to green_alerts.txt.
"""

import logging
import os
import threading
import time
from datetime import datetime

import requests

DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
GREEN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "green_alerts.txt")
POLL_INTERVAL = 300  # 5 minutes

logger = logging.getLogger("sniffer_bot")

_state = {
    "running": False,
    "last_scan": None,
    "seen": set(),
    "scan_fn": None,
    "thread": None,
    "total_scanned": 0,
    "green_count": 0,
}
_lock = threading.Lock()


def _fetch_profiles():
    try:
        resp = requests.get(DEXSCREENER_PROFILES, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Failed to fetch token profiles: %s", e)
        return []


def _log_green(address, chain, result):
    info = result.get("info", {})
    score = result.get("confidence_score", "?")
    name = info.get("name", "Unknown")
    symbol = info.get("symbol", "?")
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    line = (
        f"[{ts}] GREEN | Score:{score}/10 | "
        f"{name} ({symbol}) | Chain:{chain} | {address}\n"
    )
    with open(GREEN_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    with _lock:
        _state["green_count"] += 1
    logger.info("Green alert: %s (%s) on %s — score %s/10", name, symbol, chain, score)


def _worker():
    while True:
        with _lock:
            if not _state["running"]:
                break
            scan_fn = _state["scan_fn"]

        try:
            with _lock:
                _state["last_scan"] = datetime.utcnow().isoformat()

            profiles = _fetch_profiles()
            new_found = 0

            for p in profiles:
                addr = (p.get("tokenAddress") or "").strip()
                chain = (p.get("chainId") or "ethereum").lower()
                if not addr:
                    continue

                key = f"{chain}:{addr.lower()}"
                with _lock:
                    if key in _state["seen"]:
                        continue
                    _state["seen"].add(key)

                new_found += 1
                if scan_fn:
                    try:
                        result = scan_fn(addr, chain)
                        with _lock:
                            _state["total_scanned"] += 1
                        if result.get("verdict") == "GREEN":
                            _log_green(addr, chain, result)
                    except Exception as e:
                        logger.debug("Scan failed for %s: %s", addr, e)

            logger.info("Sniffer cycle: %d new tokens processed.", new_found)

        except Exception as e:
            logger.error("Sniffer worker error: %s", e)

        # Sleep in 1-second ticks so stop() takes effect quickly
        for _ in range(POLL_INTERVAL):
            with _lock:
                if not _state["running"]:
                    break
            time.sleep(1)

    logger.info("Sniffer bot stopped.")


def start(scan_fn):
    """Start the sniffer bot. Returns True if started, False if already running."""
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
        _state["scan_fn"] = scan_fn

    t = threading.Thread(target=_worker, name="sniffer-bot", daemon=True)
    with _lock:
        _state["thread"] = t
    t.start()
    logger.info("Sniffer bot started.")
    return True


def stop():
    """Signal the sniffer bot to stop."""
    with _lock:
        _state["running"] = False
    logger.info("Sniffer bot stop requested.")


def status():
    """Return current bot status dict."""
    with _lock:
        return {
            "running": _state["running"],
            "last_scan": _state["last_scan"],
            "total_scanned": _state["total_scanned"],
            "green_count": _state["green_count"],
            "seen_count": len(_state["seen"]),
        }


def recent_alerts(n: int = 20):
    """Return the last n lines from green_alerts.txt, newest first."""
    if not os.path.exists(GREEN_LOG):
        return []
    with open(GREEN_LOG, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return [line.rstrip() for line in lines[-n:]][::-1]
