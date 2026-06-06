import json
import os
import threading
from datetime import datetime, timezone

import wallet_tracker

SIGNALS_PATH = os.path.join(os.path.dirname(__file__), "smart_money_signals.json")
MAX_SIGNALS  = 500
_lock        = threading.Lock()

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
    if not os.path.exists(SIGNALS_PATH):
        return []
    with open(SIGNALS_PATH, "r") as f:
        return json.load(f)


def _save(data):
    with open(SIGNALS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _score(detected, holder_count, liquidity_usd, verdict, team_score):
    """Return (strength: int 1-10, label: str, types: list[str])."""
    n     = len(detected)
    score = min(n * 3, 6)   # 1 wallet → 3, 2+ → 6 (base cap)
    types = []

    if n >= 2:
        types.append("MULTI_WALLET")

    # Early-entry boost
    try:
        hc = int(holder_count) if holder_count else 0
    except (TypeError, ValueError):
        hc = 0
    if 0 < hc < 500:
        score += 2
        types.append("EARLY_ENTRY")
    elif 0 < hc < 1_500:
        score += 1

    # Liquidity sweet spot: early-stage but not microscopic dust
    try:
        liq = float(liquidity_usd) if liquidity_usd else 0.0
    except (TypeError, ValueError):
        liq = 0.0
    if 10_000 <= liq <= 500_000:
        score += 1

    # Verdict modifier
    if verdict == "GREEN":
        score += 1
        types.append("CLEAN_ENTRY")
    elif verdict == "RED":
        score -= 2

    # Team quality
    try:
        ts = int(team_score) if team_score else 0
    except (TypeError, ValueError):
        ts = 0
    if ts >= 7:
        score += 1

    score = max(1, min(10, score))

    if score >= 8:
        label = "ULTRA"
        types.append("HIGH_CONVICTION")
    elif score >= 6:
        label = "STRONG"
    elif score >= 4:
        label = "MODERATE"
    else:
        label = "WEAK"

    # Deduplicate while preserving order
    seen = set()
    types = [t for t in types if not (t in seen or seen.add(t))]

    return score, label, types


def process_token_scan(token_address, chain_id, token_data, dex_data,
                       verdict, team_score=None, confidence_score=None):
    """
    Detect smart money, score the signal, persist it, and return a result dict
    that gets embedded in the scan response as 'smart_money'.
    """
    detected = wallet_tracker.detect_in_scan(token_data)
    if not detected:
        return {"has_signal": False, "wallet_count": 0}

    token_name    = token_data.get("token_name",   "Unknown")
    token_symbol  = token_data.get("token_symbol", "???")
    holder_count  = token_data.get("holder_count")
    chain_name    = CHAIN_NAMES.get(str(chain_id), str(chain_id))
    liquidity_usd = (dex_data or {}).get("liquidity_usd")
    price_usd     = (dex_data or {}).get("price_usd")
    volume_h24    = (dex_data or {}).get("volume_h24")

    try:
        hc = int(holder_count) if holder_count else 0
    except (TypeError, ValueError):
        hc = 0

    strength, label, types = _score(
        detected, hc, liquidity_usd, verdict, team_score
    )

    token_key = f"{chain_id}:{token_address.lower()}"
    now       = datetime.now(timezone.utc).isoformat()

    signal = {
        "id":               token_key,         # dedup key: one record per token
        "timestamp":        now,
        "token_address":    token_address.lower(),
        "token_name":       token_name,
        "token_symbol":     token_symbol,
        "chain":            chain_name,
        "chain_id":         str(chain_id),
        "signal_strength":  strength,
        "signal_label":     label,
        "signal_types":     types,
        "wallets":          detected,
        "wallet_count":     len(detected),
        "price_usd":        price_usd,
        "liquidity_usd":    liquidity_usd,
        "volume_h24":       volume_h24,
        "holder_count":     hc,
        "verdict":          verdict,
        "team_score":       team_score,
        "confidence_score": confidence_score,
        "context": {
            "is_early":           0 < hc < 1_500,
            "is_clean":           verdict == "GREEN",
            "adequate_liquidity": bool(liquidity_usd
                                       and float(liquidity_usd or 0) >= 10_000),
        },
    }

    # Upsert: replace any prior signal for the same token
    with _lock:
        feed = _load()
        feed = [s for s in feed if s.get("id") != token_key]
        feed.insert(0, signal)
        feed = feed[:MAX_SIGNALS]
        _save(feed)

    wallet_tracker.bump_signal_count([w["address"] for w in detected])

    return {
        "has_signal":      True,
        "signal_strength": strength,
        "signal_label":    label,
        "signal_types":    types,
        "wallets":         detected,
        "wallet_count":    len(detected),
        "context":         signal["context"],
        "price_usd":       price_usd,
        "liquidity_usd":   liquidity_usd,
        "holder_count":    hc,
    }


def get_feed(limit=50, min_strength=1, chain_id=None, verdict_filter=None):
    """Recent signals, newest first, with optional filters."""
    with _lock:
        feed = _load()
    if chain_id:
        feed = [s for s in feed if str(s.get("chain_id", "")) == str(chain_id)]
    if verdict_filter:
        feed = [s for s in feed if s.get("verdict") == verdict_filter]
    if min_strength > 1:
        feed = [s for s in feed if (s.get("signal_strength") or 0) >= min_strength]
    return feed[:limit]


def get_token_signal(token_address, chain_id):
    """Latest signal for a specific token, or None."""
    key = f"{chain_id}:{token_address.lower()}"
    with _lock:
        feed = _load()
    return next((s for s in feed if s.get("id") == key), None)
