import json
import os
import statistics
import threading
from datetime import datetime, timezone

WHALE_DB_PATH = os.path.join(os.path.dirname(__file__), "whale_profiles.json")
_lock = threading.Lock()

CHAIN_NAMES = {
    "1":      "Ethereum",
    "56":     "BNB Chain",
    "137":    "Polygon",
    "42161":  "Arbitrum",
    "10":     "Optimism",
    "8453":   "Base",
    "43114":  "Avalanche",
    "solana": "Solana",
}


# ── helpers ───────────────────────────────────────────────────────

def _sf(val, default=None):
    """Safe float — None/invalid/zero → default."""
    try:
        f = float(val)
        return f if f > 0 else default
    except (TypeError, ValueError):
        return default


def _si(val, default=None):
    """Safe int — None/invalid/negative → default."""
    try:
        i = int(val)
        return i if i >= 0 else default
    except (TypeError, ValueError):
        return default


def _avg(lst):
    clean = [x for x in lst if x is not None]
    return round(sum(clean) / len(clean), 2) if clean else None


def _pct_range(lst):
    """25th–75th percentile range, or [min, max] for tiny lists."""
    clean = sorted(x for x in lst if x is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return [clean[0], clean[0]]
    lo = clean[max(0, len(clean) // 4)]
    hi = clean[min(len(clean) - 1, 3 * len(clean) // 4)]
    return [lo, hi]


def _ts(iso):
    """Parse ISO timestamp to datetime (handles both Z and +00:00 suffixes)."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# ── CRUD ──────────────────────────────────────────────────────────

def _load():
    if not os.path.exists(WHALE_DB_PATH):
        return {}
    with open(WHALE_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(WHALE_DB_PATH, "w", encoding="utf-8") as f:
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
            "address":          addr,
            "label":            display,
            "added":            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_activity":    None,
            "entry_count":      0,
            "exit_count":       0,
            "current_holdings": {},
            "activity_log":     [],
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


# ── behavioral profile computation ────────────────────────────────

def _compute_entry_profile(entry_events):
    n = len(entry_events)
    if n == 0:
        return {"sample_count": 0}

    fgs   = [_sf(e.get("fear_greed"))    for e in entry_events]
    doms  = [_sf(e.get("btc_dominance")) for e in entry_events]
    liqs  = [_sf(e.get("liquidity_usd")) for e in entry_events]
    hcs   = [_si(e.get("holder_count"))  for e in entry_events]
    mcaps = [_sf(e.get("market_cap"))    for e in entry_events]

    return {
        "sample_count":         n,
        "avg_fear_greed":       _avg(fgs),
        "avg_btc_dominance":    _avg(doms),
        "avg_liquidity_usd":    _avg(liqs),
        "avg_holder_count":     _avg(hcs),
        "avg_market_cap":       _avg(mcaps),
        "typical_fg_range":     _pct_range(fgs),
        "typical_liq_range":    _pct_range(liqs),
        "typical_holder_range": _pct_range(hcs),
        "typical_mcap_range":   _pct_range(mcaps),
        "dca_rate":             None,  # filled by _build_behavioral_profile
    }


def _compute_exit_profile(log):
    """log is newest-first."""
    exits = [e for e in log if e.get("event_type") == "EXIT"]
    n     = len(exits)
    if n == 0:
        return {"sample_count": 0}

    # Per-token event lists, oldest → newest
    by_token = {}
    for ev in reversed(log):
        tk = ev.get("token_address")
        if tk:
            by_token.setdefault(tk, []).append(ev)

    hold_days   = []
    multipliers = []
    staged      = 0
    exit_fgs    = []

    for exit_ev in exits:
        tk = exit_ev.get("token_address")
        fg = _sf(exit_ev.get("fear_greed"))
        if fg is not None:
            exit_fgs.append(fg)

        tk_events = by_token.get(tk, [])  # oldest → newest

        entry_ev = next(
            (e for e in tk_events if e.get("event_type") == "ENTRY"), None
        )

        # Hold duration
        if entry_ev:
            try:
                days = (_ts(exit_ev["timestamp"]) - _ts(entry_ev["timestamp"])).total_seconds() / 86400
                if 0 <= days < 3650:
                    hold_days.append(round(days, 1))
            except Exception:
                pass

        # Exit multiplier (entry_price_usd stored on EXIT events by scan pipeline)
        ep = _sf(exit_ev.get("entry_price_usd"))
        xp = _sf(exit_ev.get("price_usd"))
        if ep and xp:
            m = xp / ep
            if 0 < m < 10_000:
                multipliers.append(round(m, 2))

        # Staged exit: any DECREASE before this EXIT on the same token
        exit_idx = next(
            (i for i, e in enumerate(tk_events) if e is exit_ev), None
        )
        if exit_idx is not None:
            for e in tk_events[:exit_idx]:
                if e.get("event_type") == "DECREASE":
                    staged += 1
                    break

    return {
        "sample_count":        n,
        "avg_hold_days":       _avg(hold_days),
        "avg_exit_multiplier": _avg(multipliers),
        "staged_exit_rate":    round(staged / n, 2),
        "avg_exit_fg":         _avg(exit_fgs),
    }


def _compute_shakeout_profile(log):
    """Detect DECREASE → INCREASE/ENTRY sequences (dump then rebuy)."""
    by_token = {}
    for ev in reversed(log):   # oldest → newest
        tk = ev.get("token_address")
        if tk:
            by_token.setdefault(tk, []).append(ev)

    shakeouts = []
    for tk, events in by_token.items():
        for i, ev in enumerate(events):
            if ev.get("event_type") != "DECREASE":
                continue
            for j in range(i + 1, len(events)):
                nxt = events[j]
                if nxt.get("event_type") in ("INCREASE", "ENTRY"):
                    prev_pct = float(events[i - 1].get("holdings_pct") or 0) if i > 0 else 0
                    curr_pct = float(ev.get("holdings_pct") or 0)
                    depth    = max(0.0, prev_pct - curr_pct)
                    days     = 0.0
                    try:
                        days = max(0.0, round(
                            (_ts(nxt["timestamp"]) - _ts(ev["timestamp"])).total_seconds() / 86400, 1
                        ))
                    except Exception:
                        pass
                    shakeouts.append({
                        "token":          tk,
                        "dump_depth_pct": round(depth, 2),
                        "reaccum_days":   days,
                    })
                    break
                elif nxt.get("event_type") == "EXIT":
                    break  # real exit, not a shakeout

    return {
        "shakeout_count":     len(shakeouts),
        "avg_dump_depth_pct": _avg([s["dump_depth_pct"] for s in shakeouts]),
        "avg_reaccum_days":   _avg([s["reaccum_days"]   for s in shakeouts]),
    }


def _compute_pattern_score(entry_profile, exit_profile, log):
    entries_n = entry_profile.get("sample_count", 0)
    exits_n   = exit_profile.get("sample_count",  0)

    if entries_n == 0:
        return {
            "score": 1, "grade": "INSUFFICIENT_DATA",
            "consistent_signals": [], "entries_analyzed": 0, "exits_analyzed": 0,
        }

    entry_events = [e for e in log if e.get("event_type") == "ENTRY"]

    # Data richness: 0–4 pts
    data_pts = min(4, entries_n + exits_n)

    # F&G consistency: 0–2 pts
    fg_pts = 0
    fgs = [x for x in [_sf(e.get("fear_greed")) for e in entry_events] if x is not None]
    if len(fgs) >= 2:
        try:
            std = statistics.stdev(fgs)
            fg_pts = 2 if std < 15 else 1 if std < 30 else 0
        except Exception:
            pass

    # Liquidity consistency: 0–2 pts
    liq_pts = 0
    liqs = [x for x in [_sf(e.get("liquidity_usd")) for e in entry_events] if x is not None]
    if len(liqs) >= 2:
        try:
            ml = statistics.mean(liqs)
            cv = statistics.stdev(liqs) / ml if ml > 0 else 99
            liq_pts = 2 if cv < 0.4 else 1 if cv < 0.8 else 0
        except Exception:
            pass

    # Exit data bonus: 0–2 pts
    exit_pts = min(2, exits_n)

    score = min(10, data_pts + fg_pts + liq_pts + exit_pts)
    grade = (
        "ORACLE"            if score >= 8 else
        "RELIABLE"          if score >= 6 else
        "EMERGING"          if score >= 4 else
        "UNRELIABLE"
    )

    # Consistent signals — condition must hold for ≥ 70 % of entry events
    signals = []
    if entry_events:
        n = len(entry_events)
        def rate(fn):
            return sum(1 for e in entry_events if fn(e)) / n

        if rate(lambda e: (_sf(e.get("fear_greed"), 50) or 50) < 40) >= 0.70:
            signals.append("Enters in Fear territory (F&G < 40)")
        elif rate(lambda e: (_sf(e.get("fear_greed"), 50) or 50) > 60) >= 0.70:
            signals.append("Enters in Greed territory (F&G > 60)")

        if rate(lambda e: (_sf(e.get("liquidity_usd"), 0) or 0) < 500_000) >= 0.70:
            signals.append("Early-stage liquidity (< $500K)")

        if rate(lambda e: (_si(e.get("holder_count"), 9999) or 9999) < 500) >= 0.70:
            signals.append("Ultra-early entry (< 500 holders)")
        elif rate(lambda e: (_si(e.get("holder_count"), 9999) or 9999) < 2_000) >= 0.70:
            signals.append("Early-stage entry (< 2000 holders)")

        if rate(lambda e: (_sf(e.get("btc_dominance"), 60) or 60) < 55) >= 0.70:
            signals.append("Enters when BTC dom < 55%")

        if rate(lambda e: e.get("macro_verdict") == "CLEAR") >= 0.70:
            signals.append("Prefers CLEAR macro conditions")

    return {
        "score":              score,
        "grade":              grade,
        "consistent_signals": signals,
        "entries_analyzed":   entries_n,
        "exits_analyzed":     exits_n,
    }


def _build_behavioral_profile(whale_record):
    """Pure computation — no I/O, no locks."""
    log          = whale_record.get("activity_log", [])
    entry_events = [e for e in log if e.get("event_type") == "ENTRY"]

    # DCA rate: fraction of tokens where ENTRY was followed by ≥ 1 INCREASE
    by_token_types = {}
    for ev in log:
        tk = ev.get("token_address")
        if tk:
            by_token_types.setdefault(tk, set()).add(ev.get("event_type"))

    dca_count = sum(
        1 for types in by_token_types.values()
        if "ENTRY" in types and "INCREASE" in types
    )

    ep = _compute_entry_profile(entry_events)
    if entry_events:
        ep["dca_rate"] = round(dca_count / len(entry_events), 2)

    xp = _compute_exit_profile(log)
    sp = _compute_shakeout_profile(log)
    ps = _compute_pattern_score(ep, xp, log)

    return {
        "entry_profile":    ep,
        "exit_profile":     xp,
        "shakeout_profile": sp,
        "pattern_score":    ps,
        "last_computed":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _moby_dick_match(current_ctx, entry_profile):
    """
    Compare current scan context against this whale's historical entry pattern.
    Returns (match_score 0–4, fired: bool). Fires when score >= 3.
    Requires entry_profile.sample_count >= 3 to guard against noise.
    """
    if entry_profile.get("sample_count", 0) < 3:
        return 0, False

    score = 0

    # 1. Fear & Greed within whale's typical entry F&G range (generous margin)
    fg_curr  = _sf(current_ctx.get("fear_greed"))
    fg_range = entry_profile.get("typical_fg_range")
    if fg_curr is not None and fg_range:
        lo, hi = fg_range
        margin = max(20, (hi - lo) * 1.5)
        if (lo - margin) <= fg_curr <= (hi + margin):
            score += 1

    # 2. Liquidity within 5× of whale's typical entry liquidity
    liq_curr = _sf(current_ctx.get("liquidity_usd"))
    liq_avg  = entry_profile.get("avg_liquidity_usd")
    if liq_curr and liq_avg and liq_avg > 0:
        r = liq_curr / liq_avg
        if 0.2 <= r <= 5.0:
            score += 1

    # 3. Holder count within whale's typical holder range (generous margin)
    hc_curr  = _si(current_ctx.get("holder_count"))
    hc_range = entry_profile.get("typical_holder_range")
    if hc_curr and hc_range:
        lo, hi = hc_range
        margin = max(500, (hi - lo) * 2)
        if (lo - margin) <= hc_curr <= (hi + margin):
            score += 1

    # 4. Market cap within an order of magnitude of whale's typical entry mcap
    mc_curr  = _sf(current_ctx.get("market_cap"))
    mc_range = entry_profile.get("typical_mcap_range")
    if mc_curr and mc_range and mc_range[0] and mc_range[0] > 0:
        lo, hi = mc_range
        if (lo / 10) <= mc_curr <= (hi * 10):
            score += 1

    return score, score >= 3


def get_behavioral_profile(address):
    """Public API — load whale and return computed behavioral profile dict."""
    profile = get_profile(address)
    if not profile:
        return None
    beh = _build_behavioral_profile(profile)
    return {
        "address": address.strip().lower(),
        "label":   profile.get("label"),
        **beh,
    }


# ── scan processing ───────────────────────────────────────────────

def process_token_scan(token_address, chain_id, token_data, dex_data,
                       macro_pulse=None):
    """
    Cross-reference GoPlus top holders against the whale registry.
    Returns {"alerts": [...], "moby_alerts": [...]}.
    """
    holders_raw  = token_data.get("holders") or []
    token_name   = token_data.get("token_name",   "Unknown")
    token_symbol = token_data.get("token_symbol", "???")
    token_key    = f"{chain_id}:{token_address.lower()}"
    chain_name   = CHAIN_NAMES.get(str(chain_id), str(chain_id))

    price_usd = liquidity_usd = volume_h24 = market_cap = 0.0
    if dex_data:
        try:
            price_usd     = float(dex_data.get("price_usd")     or 0)
            liquidity_usd = float(dex_data.get("liquidity_usd") or 0)
            volume_h24    = float(dex_data.get("volume_h24")    or 0)
            market_cap    = float(
                dex_data.get("market_cap") or dex_data.get("fdv") or 0
            )
        except (TypeError, ValueError):
            pass

    try:
        holder_count = int(token_data.get("holder_count") or 0)
    except (TypeError, ValueError):
        holder_count = 0

    fear_greed    = macro_pulse.get("fear_greed")    if macro_pulse else None
    btc_dominance = macro_pulse.get("btc_dominance") if macro_pulse else None
    macro_verdict = macro_pulse.get("macro_verdict") if macro_pulse else None

    with _lock:
        db = _load()
        if not db:
            return {"alerts": [], "moby_alerts": []}

        now         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        alerts      = []
        moby_alerts = []
        changed     = False

        # Current holder address → pct map from GoPlus top-holders
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
                    "entry_holder_count":  holder_count,
                    "entry_market_cap":    market_cap,
                    "entry_fear_greed":    fear_greed,
                    "entry_btc_dominance": btc_dominance,
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

            holding                      = whale["current_holdings"][token_key]
            holding["last_seen"]         = now
            holding["last_holdings_pct"] = pct

            whale["activity_log"].insert(0, {
                "timestamp":      now,
                "event_type":     event_type,
                "chain":          chain_name,
                "chain_id":       chain_id,
                "token_address":  token_address.lower(),
                "token_name":     token_name,
                "token_symbol":   token_symbol,
                "holdings_pct":   pct,
                "price_usd":      price_usd,
                "liquidity_usd":  liquidity_usd,
                "volume_h24":     volume_h24,
                # Phase 6 — behavioral context
                "holder_count":   holder_count,
                "market_cap":     market_cap,
                "fear_greed":     fear_greed,
                "btc_dominance":  btc_dominance,
                "macro_verdict":  macro_verdict,
            })
            whale["activity_log"] = whale["activity_log"][:100]
            whale["last_activity"] = now
            changed = True

            alert = {
                "whale_address": whale_addr,
                "whale_label":   whale["label"],
                "event_type":    event_type,
                "holdings_pct":  pct,
                "token_name":    token_name,
                "token_symbol":  token_symbol,
                "price_usd":     price_usd,
                "liquidity_usd": liquidity_usd,
                "moby_dick":     False,
            }

            # ── Moby Dick check: ENTRY whose conditions match history ──
            if event_type == "ENTRY" and whale.get("entry_count", 0) > 1:
                beh = _build_behavioral_profile(whale)
                ep  = beh["entry_profile"]

                match_score, fired = _moby_dick_match(
                    {
                        "fear_greed":    fear_greed,
                        "liquidity_usd": liquidity_usd,
                        "holder_count":  holder_count,
                        "market_cap":    market_cap,
                    },
                    ep,
                )

                if fired:
                    alert["moby_dick"] = True
                    moby_alerts.append({
                        "whale_address":       whale_addr,
                        "whale_label":         whale["label"],
                        "holdings_pct":        pct,
                        "token_name":          token_name,
                        "token_symbol":        token_symbol,
                        "price_usd":           price_usd,
                        "liquidity_usd":       liquidity_usd,
                        "match_score":         match_score,
                        "pattern_grade":       beh["pattern_score"].get("grade"),
                        "avg_exit_multiplier": beh["exit_profile"].get("avg_exit_multiplier"),
                        "consistent_signals":  beh["pattern_score"].get("consistent_signals", []),
                    })

            alerts.append(alert)

        # ── Step 2: exit detection — was holding, now absent ───────
        for whale_addr, whale in db.items():
            if token_key not in whale["current_holdings"]:
                continue
            if whale_addr in current_addrs:
                continue

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
                # Phase 6 — behavioral context
                "holder_count":    holder_count,
                "market_cap":      market_cap,
                "fear_greed":      fear_greed,
                "btc_dominance":   btc_dominance,
                "macro_verdict":   macro_verdict,
            })
            whale["activity_log"] = whale["activity_log"][:100]
            del whale["current_holdings"][token_key]
            whale["exit_count"] += 1
            whale["last_activity"] = now
            changed = True

        if changed:
            _save(db)

    return {"alerts": alerts, "moby_alerts": moby_alerts}


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
