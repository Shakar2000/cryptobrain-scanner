import os
import requests
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify

import scammer_db
import sniffer_bot
import whale_profiler

app = Flask(__name__)

GOPLUS_API_BASE = "https://api.gopluslabs.io/api/v1"

CHAIN_IDS = {
    "ethereum": "1",
    "eth": "1",
    "bsc": "56",
    "bnb": "56",
    "binance": "56",
    "polygon": "137",
    "matic": "137",
    "arbitrum": "42161",
    "arb": "42161",
    "optimism": "10",
    "op": "10",
    "base": "8453",
    "avalanche": "43114",
    "avax": "43114",
    "solana": "solana",
    "sol": "solana",
}

# Any single match → instant RED regardless of yellow count
HARD_REDS = [
    ("is_honeypot",             "1", "Honeypot detected"),
    ("hidden_owner",            "1", "Hidden owner detected"),
    ("can_take_back_ownership", "1", "Owner can take back ownership"),
    ("owner_change_balance",    "1", "Owner can change balances"),
    ("selfdestruct",            "1", "Self-destruct function present"),
]

# Accumulate: 3+ → RED, 1-2 → YELLOW
YELLOW_CHECKS = [
    ("is_mintable",           "1", "Token is mintable"),
    ("is_blacklisted",        "1", "Contract is blacklisted"),
    ("transfer_pausable",     "1", "Transfers can be paused"),
    ("slippage_modifiable",   "1", "Slippage is modifiable"),
    ("anti_whale_modifiable", "1", "Anti-whale limit modifiable"),
]

_MAX_GREEN_CONFIRMS = 8


# ── Data fetchers ─────────────────────────────────────────────────────────

def fetch_token_security(contract_address: str, chain_id: str) -> dict:
    url = f"{GOPLUS_API_BASE}/token_security/{chain_id}"
    params = {"contract_addresses": contract_address}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 1:
        raise ValueError(f"GoPlus API error: {data.get('message', 'Unknown error')}")
    result = data.get("result", {})
    token_data = result.get(contract_address.lower()) or result.get(contract_address)
    if not token_data:
        raise ValueError("No data returned for this contract address.")
    return token_data


DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens"


def fetch_dex_screener(contract_address: str) -> dict:
    try:
        resp = requests.get(f"{DEXSCREENER_API}/{contract_address}", timeout=15)
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []
        if not pairs:
            return {}
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        liq  = best.get("liquidity") or {}
        vol  = best.get("volume") or {}
        chg  = best.get("priceChange") or {}
        txns = (best.get("txns") or {}).get("h24") or {}
        return {
            "price_usd":        best.get("priceUsd"),
            "liquidity_usd":    liq.get("usd"),
            "volume_h24":       vol.get("h24"),
            "price_change_h24": chg.get("h24"),
            "dex_id":           best.get("dexId"),
            "pair_address":     best.get("pairAddress"),
            "quote_symbol":     (best.get("quoteToken") or {}).get("symbol"),
            "fdv":              best.get("fdv"),
            "market_cap":       best.get("marketCap"),
            "buys_h24":         txns.get("buys"),
            "sells_h24":        txns.get("sells"),
            "pair_url":         best.get("url"),
        }
    except Exception:
        return {}


# ── Confidence scoring ────────────────────────────────────────────────────

def _count_green_confirms(token_data: dict, dex_data: dict) -> int:
    """Count positive security signals (max = _MAX_GREEN_CONFIRMS)."""
    count = 0
    if token_data.get("is_open_source") == "1":
        count += 1
    if any(h.get("is_locked") == 1 for h in token_data.get("lp_holders", [])):
        count += 1
    try:
        if float(token_data.get("buy_tax", 0)) * 100 <= 5:
            count += 1
        if float(token_data.get("sell_tax", 0)) * 100 <= 5:
            count += 1
    except (TypeError, ValueError):
        pass
    try:
        if int(token_data.get("holder_count", 0)) > 200:
            count += 1
    except (TypeError, ValueError):
        pass
    holders = token_data.get("holders", [])
    if holders:
        try:
            if float(holders[0].get("percent", 1)) * 100 < 20:
                count += 1
        except (TypeError, ValueError):
            pass
    if dex_data:
        try:
            if float(dex_data.get("liquidity_usd") or 0) >= 50_000:
                count += 1
        except (TypeError, ValueError):
            pass
    if token_data.get("is_honeypot") == "0":
        count += 1
    return count


def calculate_confidence(verdict: str, red_findings: list,
                         yellow_findings: list, token_data: dict,
                         dex_data: dict) -> int:
    """Return conviction score 1 (worst) – 10 (best)."""
    green_confirms = _count_green_confirms(token_data, dex_data)
    raw = (green_confirms / _MAX_GREEN_CONFIRMS) * 10
    raw -= len(yellow_findings) * 1.0
    raw -= len(red_findings) * 3.0
    return max(1, min(10, round(raw)))


# ── Core analysis ─────────────────────────────────────────────────────────

def analyze_token(token_data: dict, dex_data: dict = None) -> dict:
    hard_reds    = []
    yellow_flags = []
    info         = {}

    info["name"]            = token_data.get("token_name",    "Unknown")
    info["symbol"]          = token_data.get("token_symbol",  "Unknown")
    info["total_supply"]    = token_data.get("total_supply",  "N/A")
    info["holder_count"]    = token_data.get("holder_count",  "N/A")
    info["lp_holder_count"] = token_data.get("lp_holder_count", "N/A")
    info["creator_address"] = token_data.get("creator_address", "N/A")
    info["owner_address"]   = token_data.get("owner_address",   "N/A")
    info["is_verified"]     = token_data.get("is_open_source") == "1"
    info["is_proxy"]        = token_data.get("is_proxy") == "1"

    try:
        buy_tax  = float(token_data.get("buy_tax",  0)) * 100
        sell_tax = float(token_data.get("sell_tax", 0)) * 100
        info["buy_tax"]  = f"{buy_tax:.1f}%"
        info["sell_tax"] = f"{sell_tax:.1f}%"
    except (TypeError, ValueError):
        buy_tax = sell_tax = None
        info["buy_tax"]  = "N/A"
        info["sell_tax"] = "N/A"

    if buy_tax is not None:
        if buy_tax > 10:
            hard_reds.append(f"Buy tax too high ({buy_tax:.1f}%)")
        if sell_tax > 10:
            hard_reds.append(f"Sell tax too high ({sell_tax:.1f}%)")
        if 5 < sell_tax <= 10:
            yellow_flags.append(f"Elevated sell tax ({sell_tax:.1f}%)")

    for field, bad_val, label in HARD_REDS:
        if token_data.get(field) == bad_val:
            hard_reds.append(label)

    for field, bad_val, label in YELLOW_CHECKS:
        if token_data.get(field) == bad_val:
            yellow_flags.append(label)

    holders = token_data.get("holders", [])
    if holders:
        try:
            top_pct = float(holders[0].get("percent", 0)) * 100
            info["top_holder_pct"] = f"{top_pct:.1f}%"
            if top_pct > 40:
                yellow_flags.append(f"Top holder owns {top_pct:.1f}% of supply")
        except (TypeError, ValueError):
            info["top_holder_pct"] = "N/A"

    lp_holders       = token_data.get("lp_holders", [])
    info["lp_locked"] = any(h.get("is_locked") == 1 for h in lp_holders)

    liq_val = None
    if dex_data:
        try:
            raw_liq = dex_data.get("liquidity_usd")
            liq_val = float(raw_liq) if raw_liq is not None else None
        except (TypeError, ValueError):
            pass

    if liq_val is not None:
        if liq_val < 1_000:
            hard_reds.append(f"Liquidity critically low (${liq_val:,.0f})")
        elif liq_val < 50_000:
            yellow_flags.append(f"Low liquidity (${liq_val:,.0f})")

    if hard_reds or len(yellow_flags) >= 3:
        verdict = "RED"
    elif yellow_flags:
        verdict = "YELLOW"
    else:
        verdict = "GREEN"

    return {
        "verdict":         verdict,
        "info":            info,
        "red_findings":    hard_reds,
        "yellow_findings": yellow_flags,
        "dex":             dex_data or {},
        "raw":             token_data,
    }


def resolve_chain(chain_input: str) -> str:
    cleaned = chain_input.strip().lower()
    if cleaned.isdigit():
        return cleaned
    return CHAIN_IDS.get(cleaned, "1")


def scan_token(contract_address: str, chain_input: str) -> dict:
    """Full scan: fetch → analyze → scammer check → confidence score."""
    chain_id = resolve_chain(chain_input)
    with ThreadPoolExecutor(max_workers=2) as pool:
        gf = pool.submit(fetch_token_security, contract_address, chain_id)
        df = pool.submit(fetch_dex_screener, contract_address)
        token_data = gf.result()
        dex_data   = df.result()

    result = analyze_token(token_data, dex_data)

    # Scammer DB cross-reference against creator and owner
    result["scammer_match"] = None
    for addr_key in ("creator_address", "owner_address"):
        addr = result["info"].get(addr_key, "")
        if addr and addr != "N/A":
            hit, label = scammer_db.is_scammer(addr)
            if hit:
                result["verdict"] = "RED"
                result["red_findings"].insert(
                    0, f"Scammer DB: {label} ({addr[:10]}...)"
                )
                result["scammer_match"] = {"address": addr, "label": label}
                break

    result["confidence_score"] = calculate_confidence(
        result["verdict"],
        result["red_findings"],
        result["yellow_findings"],
        token_data,
        dex_data,
    )

    result["whale_alerts"] = whale_profiler.process_token_scan(
        contract_address, chain_id, token_data, dex_data
    )

    return result


# ── Flask routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scan", methods=["POST"])
def scan():
    data = request.get_json(force=True)
    contract_address = (data.get("contract_address") or "").strip()
    chain_input      = (data.get("chain") or "ethereum").strip()

    if not contract_address:
        return jsonify({"error": "Contract address is required."}), 400

    try:
        result = scan_token(contract_address, chain_input)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"GoPlus API request failed: {e}"}), 502


# ── Scammer DB routes ─────────────────────────────────────────────────────

@app.route("/scammer/list")
def scammer_list():
    return jsonify(scammer_db.list_all())


@app.route("/scammer/add", methods=["POST"])
def scammer_add():
    data    = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    label   = (data.get("label")   or "Manual entry").strip()
    if not address:
        return jsonify({"error": "Address required"}), 400
    scammer_db.add(address, label)
    return jsonify({"ok": True})


@app.route("/scammer/remove", methods=["POST"])
def scammer_remove():
    data    = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"error": "Address required"}), 400
    scammer_db.remove(address)
    return jsonify({"ok": True})


# ── Sniffer Bot routes ────────────────────────────────────────────────────

@app.route("/sniffer/start", methods=["POST"])
def sniffer_start():
    started = sniffer_bot.start(scan_token)
    return jsonify({"ok": True, "already_running": not started})


@app.route("/sniffer/stop", methods=["POST"])
def sniffer_stop():
    sniffer_bot.stop()
    return jsonify({"ok": True})


@app.route("/sniffer/status")
def sniffer_status():
    return jsonify(sniffer_bot.status())


@app.route("/sniffer/alerts")
def sniffer_alerts():
    n = min(int(request.args.get("n", 20)), 100)
    return jsonify({"alerts": sniffer_bot.recent_alerts(n)})


# ── Whale Profiler routes ─────────────────────────────────────────────────

@app.route("/whale/list")
def whale_list():
    return jsonify(list(whale_profiler.list_whales().values()))


@app.route("/whale/add", methods=["POST"])
def whale_add():
    data    = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    label   = (data.get("label")   or "").strip()
    if not address:
        return jsonify({"ok": False, "error": "Address required"}), 400
    return jsonify(whale_profiler.add_whale(address, label))


@app.route("/whale/remove", methods=["POST"])
def whale_remove():
    data    = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"ok": False, "error": "Address required"}), 400
    return jsonify(whale_profiler.remove_whale(address))


@app.route("/whale/profile/<address>")
def whale_profile_route(address):
    profile = whale_profiler.get_profile(address)
    if not profile:
        return jsonify({"error": "Not found"}), 404
    return jsonify(profile)


@app.route("/whale/activity")
def whale_activity():
    limit = min(int(request.args.get("n", 50)), 200)
    return jsonify({"activity": whale_profiler.get_recent_activity(limit)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
