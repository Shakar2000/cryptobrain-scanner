import threading
import time
from datetime import datetime, timezone

import requests as _req

import moralis_client
import market_pulse
import trade_journal
import telegram_bot
import whale_profiler

_lock    = threading.Lock()
_thread  = None
_running = False

_state = {
    # {whale_address: {chain: set_of_token_addresses}} — seeded on first check
    "whale_holdings":     {},
    # Macro
    "last_macro_verdict": None,
    "last_macro_ts":      None,
    # Stats
    "last_check_time":    None,
    "check_count":        0,
    "alert_count":        0,
    # Health flags
    "whale_check_ok":     True,
    "position_check_ok":  True,
    "macro_check_ok":     True,
    "errors":             [],   # rolling last-10
}

WHALE_INTERVAL = 300   # 5 min between full cycles
MACRO_INTERVAL = 3600  # 1 hr between macro refreshes

_DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens"


# ── Helpers ───────────────────────────────────────────────────────────────

def _ts_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_price(v):
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.8f}"


def _push_error(tag, msg):
    with _lock:
        _state["errors"].append(f"[{tag}] {str(msg)[:100]}")
        _state["errors"] = _state["errors"][-10:]


def _inc_alerts():
    with _lock:
        _state["alert_count"] += 1


# ── Check 1: Whale Movement ───────────────────────────────────────────────

def _check_whale_movements():
    if not moralis_client.is_available():
        return

    whales = whale_profiler.list_whales()
    if not whales:
        return

    for addr, profile in whales.items():
        label = (profile.get("label") or addr[:10] + "…")
        current = {}   # chain → frozenset of token_addresses

        for chain in moralis_client.ENRICH_CHAINS:
            try:
                raw      = moralis_client.get_current_holdings(addr, chain=chain)
                holdings = raw if isinstance(raw, list) else raw.get("result", [])
                tokens   = frozenset(
                    (h.get("token_address") or "").lower()
                    for h in holdings
                    if h.get("token_address")
                )
                current[chain] = tokens
                time.sleep(0.15)
            except Exception as e:
                _push_error("whale-holdings", e)
                current[chain] = frozenset()

        with _lock:
            prev_all = _state["whale_holdings"].get(addr)

        if prev_all is None:
            # First check for this wallet — seed, no alert
            with _lock:
                _state["whale_holdings"][addr] = {c: set(t) for c, t in current.items()}
            continue

        for chain, tokens in current.items():
            prev_tokens = prev_all.get(chain, set())
            new_tokens  = tokens - prev_tokens

            # Update state
            with _lock:
                _state["whale_holdings"].setdefault(addr, {})[chain] = set(tokens)

            for token_addr in new_tokens:
                chain_name = moralis_client.CHAIN_NAME_MAP.get(chain, chain)
                msg = (
                    f"🐋 <b>Whale Movement Detected</b>\n"
                    f"Wallet: <b>{label}</b>\n"
                    f"New position: <code>{token_addr}</code>\n"
                    f"Chain: {chain_name}\n"
                    f"Wallet address: <code>{addr}</code>"
                )
                if telegram_bot.send_message(msg):
                    _inc_alerts()


# ── Check 2: Position Monitor ─────────────────────────────────────────────

def _check_positions():
    open_pos = trade_journal.list_positions(status="open")
    if not open_pos:
        return

    for pos in open_pos:
        if pos.get("alerted"):
            continue

        token_addr = pos.get("token_address", "")
        if not token_addr:
            continue

        try:
            resp = _req.get(f"{_DEXSCREENER_API}/{token_addr}", timeout=10)
            resp.raise_for_status()
            pairs = resp.json().get("pairs") or []
            if not pairs:
                continue

            best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
            price_str = best.get("priceUsd")
            if not price_str:
                continue

            current_price = float(price_str)
            entry_price   = float(pos.get("entry_price_usd") or 0)
            if entry_price <= 0:
                continue

            multiplier  = current_price / entry_price
            target_mult = float(pos.get("target_multiplier", 2.5))

            if multiplier >= target_mult:
                pullout  = float(pos.get("pullout_amount", 0))
                deploy   = float(pos.get("deploy_amount",  0))
                # Pull-out covers original + profit; remainder keeps riding
                remainder = deploy

                msg = (
                    f"🎯 <b>Target Hit — Take Profit!</b>\n"
                    f"Token: <b>{pos.get('token_name','?')} ({pos.get('token_symbol','?')})</b>\n"
                    f"Current price: {_fmt_price(current_price)} "
                    f"(<b>{multiplier:.2f}×</b>)\n"
                    f"Entry: {_fmt_price(entry_price)}\n"
                    f"💰 Pull out: <b>${pullout:,.2f}</b>\n"
                    f"🏄 Let <b>${remainder:,.2f}</b> ride"
                )
                if telegram_bot.send_message(msg):
                    _inc_alerts()
                trade_journal.mark_alerted(pos["id"])

        except Exception as e:
            _push_error("position", e)


# ── Check 3: Macro Monitor ────────────────────────────────────────────────

def _check_macro():
    snap = market_pulse.fetch_snapshot(force=True)
    if not snap:
        return

    new_verdict = snap.get("macro_verdict")

    with _lock:
        last = _state["last_macro_verdict"]
        _state["last_macro_ts"] = _ts_now()

    if last is None:
        # Seed — no alert on first check
        with _lock:
            _state["last_macro_verdict"] = new_verdict
        return

    if new_verdict != last:
        icon = "✅" if new_verdict == "CLEAR" else "🛡️" if new_verdict == "DEFENSIVE" else "⚠️"
        msg = (
            f"{icon} <b>Macro Verdict Changed</b>\n"
            f"{last} → <b>{new_verdict}</b>\n\n"
            f"BTC: ${snap.get('btc_price', 0):,.0f}\n"
            f"BTC Dominance: {snap.get('btc_dominance', 0)}%\n"
            f"Fear &amp; Greed: {snap.get('fear_greed', 0)} "
            f"({snap.get('fear_greed_label', '')})"
        )
        if telegram_bot.send_message(msg):
            _inc_alerts()

    with _lock:
        _state["last_macro_verdict"] = new_verdict


# ── Monitor loop ──────────────────────────────────────────────────────────

def _monitor_loop():
    global _running
    _macro_last_run = 0.0

    while _running:
        tick_start = time.time()

        with _lock:
            _state["last_check_time"] = _ts_now()
            _state["check_count"]    += 1

        # Check 1 — Whale movements
        try:
            _check_whale_movements()
            with _lock:
                _state["whale_check_ok"] = True
        except Exception as e:
            _push_error("whale", e)
            with _lock:
                _state["whale_check_ok"] = False

        # Check 2 — Position targets
        try:
            _check_positions()
            with _lock:
                _state["position_check_ok"] = True
        except Exception as e:
            _push_error("position", e)
            with _lock:
                _state["position_check_ok"] = False

        # Check 3 — Macro (hourly)
        if tick_start - _macro_last_run >= MACRO_INTERVAL:
            try:
                _check_macro()
                _macro_last_run = tick_start
                with _lock:
                    _state["macro_check_ok"] = True
            except Exception as e:
                _push_error("macro", e)
                with _lock:
                    _state["macro_check_ok"] = False

        # Sleep in 1-second increments so stop() responds quickly
        for _ in range(WHALE_INTERVAL):
            if not _running:
                break
            time.sleep(1)


# ── Public API ────────────────────────────────────────────────────────────

def is_running():
    return _running


def status():
    with _lock:
        s = dict(_state)
        # Convert sets to counts for JSON serialisation
        whale_wallet_count = len(s["whale_holdings"])
    return {
        "running":               _running,
        "telegram_available":    telegram_bot.is_available(),
        "moralis_available":     moralis_client.is_available(),
        "last_check_time":       s["last_check_time"],
        "check_count":           s["check_count"],
        "alert_count":           s["alert_count"],
        "whale_check_ok":        s["whale_check_ok"],
        "position_check_ok":     s["position_check_ok"],
        "macro_check_ok":        s["macro_check_ok"],
        "last_macro_verdict":    s["last_macro_verdict"],
        "last_macro_ts":         s["last_macro_ts"],
        "tracked_whales_seeded": whale_wallet_count,
        "errors":                s["errors"],
    }


def start():
    global _thread, _running
    if _running:
        return False
    _running = True
    _thread  = threading.Thread(target=_monitor_loop, daemon=True)
    _thread.start()
    telegram_bot.send_message(
        "🤖 <b>CryptoBrain Alert Monitor started</b>\n"
        "Monitoring: whale movements · position targets · macro verdict changes"
    )
    return True


def stop():
    global _running
    _running = False
    telegram_bot.send_message("🛑 <b>CryptoBrain Alert Monitor stopped</b>")


def send_test():
    """Send a test message to verify Telegram is configured correctly."""
    return telegram_bot.send_message(
        "✅ <b>CryptoBrain Scanner — Telegram test</b>\n"
        "If you can read this, alerts are configured correctly."
    )
