import os
import time
import requests

BASE_URL = "https://deep-index.moralis.io/api/v2.2"

CHAINS        = ["eth", "bsc", "base", "polygon"]

# Moralis hex chain IDs (used as the `chain` query parameter)
# and their human-readable / decimal equivalents
CHAIN_ID_MAP = {
    "eth":    "1",    "0x1":    "1",
    "bsc":    "56",   "0x38":   "56",
    "base":   "8453", "0x2105": "8453",
    "polygon":"137",  "0x89":   "137",
}
CHAIN_NAME_MAP = {
    "eth":    "Ethereum", "0x1":    "Ethereum",
    "bsc":    "BNB Chain","0x38":   "BNB Chain",
    "base":   "Base",     "0x2105": "Base",
    "polygon":"Polygon",  "0x89":   "Polygon",
}

# Default chain order for wallet enrichment: BSC first (most meme-coin activity),
# then ETH, then Base.  Polygon omitted by default to limit latency.
ENRICH_CHAINS = ["0x38", "0x1", "0x2105"]

_MAX_DISCOVERY_PAGES = 5   # max pages when collecting token transfers for discovery
_NULL_ADDR = "0x0000000000000000000000000000000000000000"


def is_available():
    return bool(os.environ.get("MORALIS_API_KEY", "").strip())


def _api_key():
    return os.environ.get("MORALIS_API_KEY", "").strip()


def _headers():
    return {"X-API-Key": _api_key(), "Accept": "application/json"}


def _get(path, params=None, timeout=20):
    r = requests.get(
        BASE_URL + path,
        headers=_headers(),
        params=params or {},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _norm_ts(ts):
    """Normalise a Moralis timestamp to YYYY-MM-DDTHH:MM:SSZ."""
    if not ts:
        return ""
    return ts.replace("+00:00", "Z") if "+00:00" in ts else ts


# ── Raw Moralis endpoints ──────────────────────────────────────────────────

def get_wallet_history(address, chain="eth", limit=100):
    """GET /wallets/{address}/history — full wallet transaction history."""
    return _get(f"/wallets/{address.lower()}/history",
                {"chain": chain, "limit": limit})


def get_token_transfers(address, chain="eth", limit=100,
                        token_address=None, cursor=None):
    """GET /erc20/{address}/transfers — ERC20 transfers for a wallet."""
    params = {"chain": chain, "limit": limit}
    if token_address:
        params["contract_addresses[]"] = token_address.lower()
    if cursor:
        params["cursor"] = cursor
    return _get(f"/erc20/{address.lower()}/transfers", params)


def get_current_holdings(address, chain="eth"):
    """GET /{address}/erc20 — current ERC20 balances for a wallet."""
    return _get(f"/{address.lower()}/erc20", {"chain": chain})


# ── Whale profile enrichment ──────────────────────────────────────────────

def enrich_whale_profile(whale_address, chains=None):
    """
    Fetch multi-chain ERC20 transfer history for a tracked whale and merge
    the results into whale_profiler's activity_log as ENTRY / EXIT events.
    Returns {"added": N, "chains_scanned": [...], "error": str|None}.
    """
    import whale_profiler  # imported here to avoid circular dependency

    if not is_available():
        return {"added": 0, "chains_scanned": [], "error": "MORALIS_API_KEY not set"}

    addr = whale_address.strip().lower()
    if not whale_profiler.get_profile(addr):
        return {"added": 0, "chains_scanned": [], "error": "Wallet not in whale database"}

    if chains is None:
        chains = ENRICH_CHAINS  # BSC → ETH → Base

    new_events     = []
    chains_scanned = []

    for chain in chains:
        try:
            cursor     = None
            page_txns  = []
            for _ in range(2):     # up to 2 pages per chain to limit latency
                data      = get_token_transfers(addr, chain=chain, limit=100, cursor=cursor)
                page      = data.get("result", [])
                cursor    = data.get("cursor")
                page_txns.extend(page)
                if not cursor or not page:
                    break
                time.sleep(0.25)

            if page_txns:
                chains_scanned.append(chain)

            for t in page_txns:
                to_addr   = (t.get("to_address")   or "").lower()
                from_addr = (t.get("from_address") or "").lower()

                if to_addr == addr:
                    ev_type = "ENTRY"
                elif from_addr == addr:
                    ev_type = "EXIT"
                else:
                    continue

                new_events.append({
                    "timestamp":     _norm_ts(t.get("block_timestamp", "")),
                    "event_type":    ev_type,
                    "chain":         CHAIN_NAME_MAP.get(chain, chain),
                    "chain_id":      CHAIN_ID_MAP.get(chain, chain),
                    "token_address": (t.get("address") or "").lower(),
                    "token_name":    t.get("token_name",   "Unknown"),
                    "token_symbol":  t.get("token_symbol", "???"),
                    # fields not available from raw transfers — populated by real-time scans
                    "holdings_pct":  None,
                    "price_usd":     None,
                    "liquidity_usd": None,
                    "volume_h24":    None,
                    "holder_count":  None,
                    "market_cap":    None,
                    "fear_greed":    None,
                    "btc_dominance": None,
                    "macro_verdict": None,
                    "value_decimal": float(t.get("value_decimal") or 0),
                    "tx_hash":       t.get("transaction_hash"),
                    "source":        "moralis",
                })
        except Exception:
            continue

    if not new_events:
        return {"added": 0, "chains_scanned": chains_scanned, "error": None}

    # Merge into whale DB — dedup by tx_hash, sort newest-first, cap at 500
    with whale_profiler._lock:
        db = whale_profiler._load()
        if addr not in db:
            return {"added": 0, "chains_scanned": chains_scanned, "error": "Whale not found in DB"}

        existing_hashes = {
            e.get("tx_hash") for e in db[addr]["activity_log"] if e.get("tx_hash")
        }
        to_add = [e for e in new_events if e.get("tx_hash") not in existing_hashes]

        combined = db[addr]["activity_log"] + to_add
        combined.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        db[addr]["activity_log"] = combined[:500]

        for e in to_add:
            if e["event_type"] == "ENTRY":
                db[addr]["entry_count"] += 1
            elif e["event_type"] == "EXIT":
                db[addr]["exit_count"]  += 1

        whale_profiler._save(db)

    return {"added": len(to_add), "chains_scanned": chains_scanned, "error": None}


# ── Whale discovery ───────────────────────────────────────────────────────

def discover_early_buyers(token_address, chain="eth", max_enrich=30):
    """
    Pull ERC20 transfers of token_address, identify unique early buyers,
    score each 1-10, and return all candidates sorted by score desc.

    Scoring breakdown:
      entry_score     0-4  based on block-number percentile rank among all buyers
      size_score      0-2  buy size relative to median
      diversity_score 0-2  number of unique tokens in current wallet holdings
      holding_score   0-2  still holding this token at time of query
    """
    if not is_available():
        return {"error": "MORALIS_API_KEY not set", "candidates": []}

    # Collect all transfers of the token (oldest→newest after sort)
    all_transfers = []
    cursor = None
    for _ in range(_MAX_DISCOVERY_PAGES):
        try:
            params = {"chain": chain, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            data   = _get(f"/erc20/{token_address.lower()}/transfers", params)
            page   = data.get("result", [])
            cursor = data.get("cursor")
            all_transfers.extend(page)
            if not cursor or not page:
                break
            time.sleep(0.2)
        except Exception:
            break

    if not all_transfers:
        return {"error": "No transfer data found for this token", "candidates": []}

    all_transfers.sort(key=lambda t: int(t.get("block_number") or 0))

    # First buy per wallet (exclude null address and the contract itself)
    first_buys = {}
    for t in all_transfers:
        buyer = (t.get("to_address") or "").lower()
        if not buyer or buyer in (_NULL_ADDR, token_address.lower()):
            continue
        if buyer not in first_buys:
            first_buys[buyer] = {
                "address":       buyer,
                "first_block":   int(t.get("block_number") or 0),
                "first_ts":      _norm_ts(t.get("block_timestamp", "")),
                "token_name":    t.get("token_name",   "Unknown"),
                "token_symbol":  t.get("token_symbol", "???"),
                "value_decimal": float(t.get("value_decimal") or 0),
                "chain":         chain,
                "chain_id":      CHAIN_ID_MAP.get(chain, chain),
                "chain_name":    CHAIN_NAME_MAP.get(chain, chain),
            }

    if not first_buys:
        return {"error": "No buyer addresses found", "candidates": []}

    sorted_buyers = sorted(first_buys.values(), key=lambda b: b["first_block"])
    total_buyers  = len(sorted_buyers)

    # Median buy value across all buyers
    all_vals   = sorted(b["value_decimal"] for b in sorted_buyers if b["value_decimal"] > 0)
    median_val = all_vals[len(all_vals) // 2] if all_vals else 0

    # Pass-1 score (no extra API calls) for ALL buyers
    for i, buyer in enumerate(sorted_buyers):
        pct = i / total_buyers
        entry_score = 4 if pct <= 0.05 else 3 if pct <= 0.15 else 2 if pct <= 0.30 else 1
        val        = buyer["value_decimal"]
        size_score = 2 if (median_val > 0 and val >= median_val * 3) else \
                     1 if (median_val > 0 and val >= median_val)      else 0
        buyer["_partial"]       = entry_score + size_score
        buyer["entry_rank"]     = i + 1
        buyer["entry_rank_pct"] = round(pct * 100, 1)
        buyer["entry_score"]    = entry_score
        buyer["size_score"]     = size_score

    # Pass-2 enrichment for top candidates by partial score
    enrich_candidates = sorted(sorted_buyers, key=lambda b: -b["_partial"])[:max_enrich]

    for buyer in enrich_candidates:
        try:
            raw       = get_current_holdings(buyer["address"], chain=chain)
            holdings  = raw if isinstance(raw, list) else raw.get("result", [])
            still_holds = any(
                (h.get("token_address") or "").lower() == token_address.lower()
                for h in holdings
            )
            diversity = len({(h.get("token_address") or "").lower()
                             for h in holdings if h.get("token_address")})
        except Exception:
            still_holds = False
            diversity   = 0

        holding_score   = 2 if still_holds   else 0
        diversity_score = 2 if diversity >= 5 else 1 if diversity >= 2 else 0

        buyer["score"]           = min(10, buyer["_partial"] + holding_score + diversity_score)
        buyer["holding_score"]   = holding_score
        buyer["diversity_score"] = diversity_score
        buyer["still_holds"]     = still_holds
        buyer["portfolio_size"]  = diversity
        time.sleep(0.15)

    # Wallets not enriched get a capped score
    enriched_addrs = {b["address"] for b in enrich_candidates}
    for buyer in sorted_buyers:
        if buyer["address"] not in enriched_addrs:
            buyer.setdefault("score",           buyer["_partial"])
            buyer.setdefault("holding_score",   0)
            buyer.setdefault("diversity_score", 0)
            buyer.setdefault("still_holds",     False)
            buyer.setdefault("portfolio_size",  0)

    # Sort final list by score desc, include all buyers
    all_buyers = enrich_candidates + [b for b in sorted_buyers if b["address"] not in enriched_addrs]
    all_buyers.sort(key=lambda b: -b.get("score", 0))

    # Clean internal field
    for b in all_buyers:
        b.pop("_partial", None)

    return {
        "error":              None,
        "total_buyers":       total_buyers,
        "token_address":      token_address.lower(),
        "chain":              chain,
        "chain_id":           CHAIN_ID_MAP.get(chain, chain),
        "candidates":         all_buyers,
        "auto_add_threshold": 7,
    }
