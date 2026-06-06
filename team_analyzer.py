import json
import os
import threading
from datetime import datetime, timezone

PROFILES_PATH = os.path.join(os.path.dirname(__file__), "team_profiles.json")
_lock = threading.Lock()

NULL_ADDR = "0x0000000000000000000000000000000000000000"

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
    if not os.path.exists(PROFILES_PATH):
        return {}
    with open(PROFILES_PATH, "r") as f:
        return json.load(f)


def _save(data):
    with open(PROFILES_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_profile(address):
    addr = address.strip().lower()
    with _lock:
        db = _load()
        return db.get(addr)


def list_profiles():
    with _lock:
        return _load()


def _is_renounced(owner_addr):
    if not owner_addr:
        return True
    cleaned = owner_addr.strip().lower()
    return cleaned in ("", "0x", NULL_ADDR.lower())


def _calc_stability(token_data, profile):
    """
    Returns (score: int 1-10, signals: list[{label, status}]).
    status is one of: 'good' | 'caution' | 'bad' | 'neutral'
    """
    score   = 0
    signals = []

    # 1. Source code transparency (+2)
    if token_data.get("is_open_source") == "1":
        score += 2
        signals.append({"label": "Source code verified",   "status": "good"})
    else:
        signals.append({"label": "Source code unverified", "status": "bad"})

    # 2. LP lock (+2)
    lp_holders = token_data.get("lp_holders") or []
    lp_locked  = any(h.get("is_locked") == 1 for h in lp_holders)
    if lp_locked:
        score += 2
        signals.append({"label": "Liquidity locked",     "status": "good"})
    else:
        signals.append({"label": "Liquidity not locked", "status": "bad"})

    # 3. Creator concentration (+2 if <5%, +1 if <15%)
    creator_pct = None
    try:
        raw = token_data.get("creator_percent")
        if raw is not None:
            creator_pct = float(raw) * 100
    except (TypeError, ValueError):
        pass

    if creator_pct is None:
        signals.append({"label": "Creator holdings unknown", "status": "neutral"})
    elif creator_pct < 5:
        score += 2
        signals.append({"label": f"Creator holds {creator_pct:.1f}%",           "status": "good"})
    elif creator_pct < 15:
        score += 1
        signals.append({"label": f"Creator holds {creator_pct:.1f}% (elevated)", "status": "caution"})
    else:
        signals.append({"label": f"Creator holds {creator_pct:.1f}% (high)",     "status": "bad"})

    # 4. Ownership status (+2 renounced, +1 transparent)
    owner_addr = token_data.get("owner_address", "")
    renounced  = _is_renounced(owner_addr)
    if renounced:
        score += 2
        signals.append({"label": "Ownership renounced",          "status": "good"})
    elif (token_data.get("hidden_owner") == "0"
          and token_data.get("can_take_back_ownership") == "0"):
        score += 1
        signals.append({"label": "Ownership visible, no backdoor", "status": "good"})
    else:
        signals.append({"label": "Owner has elevated privileges",  "status": "bad"})

    # 5. Non-mintable supply (+1)
    if token_data.get("is_mintable") == "0":
        score += 1
        signals.append({"label": "Fixed supply (non-mintable)", "status": "good"})
    else:
        signals.append({"label": "Mintable supply",              "status": "caution"})

    # 6. No balance manipulation (+1)
    if token_data.get("owner_change_balance") == "0":
        score += 1
        signals.append({"label": "No balance manipulation",       "status": "good"})
    else:
        signals.append({"label": "Owner can alter balances",      "status": "bad"})

    # 7. Track-record modifier (only when creator has prior tokens)
    if profile:
        prev_count   = profile.get("token_count", 0) - 1  # exclude current scan
        prev_reds    = profile.get("red_count",   0)
        prev_greens  = profile.get("green_count", 0)
        if prev_count > 0:
            if prev_reds >= 2:
                score -= 3
                signals.append({"label": f"{prev_reds} previous RED deployments",     "status": "bad"})
            elif prev_reds == 1:
                score -= 1.5
                signals.append({"label": "1 previous RED deployment",                 "status": "bad"})
            if prev_greens >= 3:
                score += 1
                signals.append({"label": f"{prev_greens} previous clean deployments", "status": "good"})
            elif prev_greens >= 1:
                score += 0.5
                signals.append({"label": f"{prev_greens} previous GREEN deployment(s)","status": "good"})

    return max(1, min(10, round(score))), signals


def _calc_reputation(profile, is_known_scammer=False):
    if is_known_scammer:
        return "KNOWN SCAMMER"
    token_count = profile.get("token_count", 0)
    red_count   = profile.get("red_count",   0)
    green_count = profile.get("green_count", 0)
    prev_count  = token_count - 1

    if prev_count <= 0:
        return "NEW"
    if red_count == 0 and green_count >= 3:
        return "TRUSTED"
    if red_count == 0:
        return "CLEAN"
    if red_count >= 2 or (prev_count > 0 and red_count / prev_count > 0.5):
        return "SUSPICIOUS"
    return "MIXED"


def process_token_scan(token_address, chain_id, token_data, dex_data,
                       verdict, is_known_scammer=False):
    """
    Build / update the creator's team profile and return a team_analysis dict.
    """
    creator_addr = (token_data.get("creator_address") or "").strip().lower()
    owner_addr   = (token_data.get("owner_address")   or "").strip().lower()
    chain_name   = CHAIN_NAMES.get(str(chain_id), str(chain_id))

    # Prefer creator; fall back to owner as profile key
    team_addr = creator_addr
    if not team_addr or team_addr == NULL_ADDR.lower():
        team_addr = owner_addr
    if not team_addr or team_addr == NULL_ADDR.lower():
        # No usable address — return analysis without persisting
        score, signals = _calc_stability(token_data, None)
        return _build_result(score, "NEW", signals, creator_addr, owner_addr,
                             token_data, track_record=None)

    creator_pct = None
    try:
        raw = token_data.get("creator_percent")
        if raw is not None:
            creator_pct = round(float(raw) * 100, 2)
    except (TypeError, ValueError):
        pass

    lp_holders = token_data.get("lp_holders") or []
    lp_locked  = any(h.get("is_locked") == 1 for h in lp_holders)
    now        = datetime.now(timezone.utc).isoformat()

    with _lock:
        db = _load()

        if team_addr not in db:
            db[team_addr] = {
                "address":       team_addr,
                "first_seen":    now,
                "last_seen":     now,
                "token_count":   0,
                "red_count":     0,
                "yellow_count":  0,
                "green_count":   0,
                "reputation":    "NEW",
                "tokens":        [],
            }

        profile  = db[team_addr]
        token_key = f"{chain_id}:{token_address.lower()}"

        # Find if this token already exists in the profile
        existing = next(
            (t for t in profile["tokens"]
             if t.get("token_address") == token_address.lower()
             and t.get("chain_id") == chain_id),
            None
        )

        score, signals = _calc_stability(token_data, profile)

        if existing:
            old_verdict = existing.get("verdict", verdict)
            existing.update({
                "last_scan":       now,
                "verdict":         verdict,
                "stability_score": score,
                "lp_locked":       lp_locked,
                "is_open_source":  token_data.get("is_open_source") == "1",
            })
            if creator_pct is not None:
                existing["creator_pct"] = creator_pct
            # Update verdict counts if verdict changed
            if old_verdict != verdict:
                _dec_verdict(profile, old_verdict)
                _inc_verdict(profile, verdict)
        else:
            profile["tokens"].append({
                "token_address":  token_address.lower(),
                "token_name":     token_data.get("token_name",   "Unknown"),
                "token_symbol":   token_data.get("token_symbol", "???"),
                "chain":          chain_name,
                "chain_id":       chain_id,
                "first_scan":     now,
                "last_scan":      now,
                "verdict":        verdict,
                "stability_score": score,
                "creator_pct":    creator_pct,
                "lp_locked":      lp_locked,
                "is_open_source": token_data.get("is_open_source") == "1",
            })
            profile["token_count"] += 1
            _inc_verdict(profile, verdict)

        # Keep rolling window to avoid unbounded growth
        profile["tokens"]    = profile["tokens"][-50:]
        profile["last_seen"] = now
        profile["reputation"] = _calc_reputation(profile, is_known_scammer)
        _save(db)

        track_record = {
            "token_count":   profile["token_count"],
            "green_count":   profile["green_count"],
            "yellow_count":  profile["yellow_count"],
            "red_count":     profile["red_count"],
            "first_seen":    profile["first_seen"],
            "recent_tokens": list(reversed(profile["tokens"][-3:])),
        }

    return _build_result(score, profile["reputation"], signals,
                         creator_addr, owner_addr, token_data,
                         track_record=track_record)


def _inc_verdict(profile, verdict):
    if verdict == "RED":
        profile["red_count"]    += 1
    elif verdict == "YELLOW":
        profile["yellow_count"] += 1
    else:
        profile["green_count"]  += 1


def _dec_verdict(profile, verdict):
    if verdict == "RED":
        profile["red_count"]    = max(0, profile["red_count"]    - 1)
    elif verdict == "YELLOW":
        profile["yellow_count"] = max(0, profile["yellow_count"] - 1)
    else:
        profile["green_count"]  = max(0, profile["green_count"]  - 1)


def _build_result(score, reputation, signals, creator_addr, owner_addr,
                  token_data, track_record):
    lp_holders = token_data.get("lp_holders") or []
    return {
        "stability_score":    score,
        "reputation":         reputation,
        "creator_address":    creator_addr or "N/A",
        "owner_address":      owner_addr   or "N/A",
        "ownership_renounced": _is_renounced(owner_addr),
        "is_open_source":     token_data.get("is_open_source") == "1",
        "lp_locked":          any(h.get("is_locked") == 1 for h in lp_holders),
        "track_record":       track_record,
        "signals":            signals,
    }
