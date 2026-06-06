# CryptoBrain Scanner — Project State

**Date:** 2026-06-06  
**Branch:** master  
**Commits:** 9 (initial + 7 feature phases + 1 bugfix)

---

## What the app does

A Flask web app that scans any EVM or Solana token contract and returns a
multi-layered intelligence report:

1. **Security verdict** (RED / YELLOW / GREEN) powered by the GoPlus Security API
2. **Market data** pulled from DEX Screener
3. **Whale Intelligence** — tracks specific wallets across scans, logs entry/exit events
4. **Team Stability** — profiles token creators, scores their contract practices, tracks their deployment history
5. **Smart Money Signal** — detects pre-registered "smart money" wallets in top holders and scores the signal 1–10
6. **Market Pulse** — daily macro snapshot (BTC/ETH/SOL prices, BTC dominance, Fear & Greed) with CLEAR/CAUTION/DEFENSIVE verdict stamped on every scan
7. **AI Analysis Engine** — sends the complete scan data to Claude (claude-sonnet-4-5) and returns a structured trade verdict with layer, deploy amount, target price, pull-out amount, stop loss, and 3-sentence reasoning
8. **Moby Dick Whale Profiler** — builds full behavioral profiles per tracked whale wallet: Entry/Exit/Shakeout profiles with historical averages and a Pattern Score (UNRELIABLE → EMERGING → RELIABLE → ORACLE); fires a Moby Dick Alert when a whale's new entry conditions match their historical pattern
9. **Moralis Integration** — cross-chain wallet enrichment (ETH/BSC/Base/Polygon) and whale discovery: paste any successful token to pull its early buyers, score them 1–10 by entry timing/size/holdings, auto-add wallets scoring 7+ to the whale database

A background sniffer bot can poll DEX Screener every 5 minutes for new token
listings and auto-scan them, logging any GREEN verdicts.

---

## File inventory

### Python — core application

| File | Lines | Purpose |
|------|------:|---------|
| `app.py` | ~570 | Flask application. Registers all routes. `scan_token()` is the main pipeline. |
| `scammer_db.py` | 50 | CRUD wrapper around `scammer_db.json`. Blacklist checked against creator/owner on every scan. |
| `sniffer_bot.py` | 153 | Background daemon. Polls DEX Screener every 5 minutes, auto-scans, logs GREEN verdicts. |
| `whale_profiler.py` | ~390 | Manages `whale_profiles.json`. Tracks user-added wallets; classifies ENTRY/EXIT/INCREASE/DECREASE/DETECTED. Phase 6: builds Entry/Exit/Shakeout/Pattern behavioral profiles; fires Moby Dick Alerts. |
| `moralis_client.py` | ~230 | Moralis API wrapper. Raw endpoints: wallet history, token transfers, current holdings. Higher-level: `enrich_whale_profile()` (multi-chain enrichment), `discover_early_buyers()` (token-to-whale discovery with scoring). |
| `team_analyzer.py` | 310 | Manages `team_profiles.json`. Scores creator contract practices 1–10; assigns TRUSTED/CLEAN/NEW/MIXED/SUSPICIOUS/KNOWN SCAMMER reputation. |
| `wallet_tracker.py` | 113 | Manages `smart_wallets.json`. CRUD for smart money wallet registry; detects in GoPlus holders. |
| `signal_feed.py` | 198 | Manages `smart_money_signals.json`. Scores smart money hits 1–10; type tags MULTI_WALLET/EARLY_ENTRY/CLEAN_ENTRY/HIGH_CONVICTION. |
| `market_pulse.py` | 97 | Manages `market_pulse_log.json`. Fetches BTC/ETH/SOL prices (CoinGecko), BTC dominance (CoinGecko /global `market_cap_percentage.btc`), Fear & Greed (Alternative.me). Caches once per day; max 90-day rolling window. |
| `ai_analyst.py` | ~110 | Calls `claude-sonnet-4-5` with the full scan result. Builds a structured prompt, parses the response for verdict/layer/deploy/target/pullout/stop_loss/reasoning. Returns `None` if `ANTHROPIC_API_KEY` not set. |

### HTML / templates

| File | Lines | Purpose |
|------|-------|---------|
| `templates/index.html` | ~3200 | Single-page UI. Dark theme, no external frameworks. Vanilla JS. Renders all scan result cards and hosts eight collapsible management panels. |

### Data files (auto-created, git-tracked)

| File | Purpose |
|------|---------|
| `scammer_db.json` | Known bad-actor addresses |
| `whale_profiles.json` | User-tracked wallet profiles with rolling 100-event activity logs (500 for Moralis-enriched) + Phase 6 behavioral context fields |
| `team_profiles.json` | Auto-built creator profiles (rolling 50 tokens per address) |
| `smart_wallets.json` | Smart money wallet registry with signal counts |
| `smart_money_signals.json` | Scored signal feed, one record per token (max 500) |
| `market_pulse_log.json` | Daily macro snapshots, newest first (max 90 days) |

### Deployment / config

| File | Purpose |
|------|---------|
| `Procfile` | Heroku: `web: gunicorn app:app --workers 2` |
| `requirements.txt` | `Flask`, `requests`, `gunicorn`, `anthropic` |
| `README.md` | Usage guide |

---

## scan_token() pipeline

```
fetch_token_security(GoPlus)  ─┐
                                ├─ parallel (ThreadPoolExecutor)
fetch_dex_screener()           ─┘
         │
         ▼
analyze_token()          → verdict, info, red_findings, yellow_findings
         │
         ▼
scammer_db check         → may escalate verdict to RED, sets scammer_match
         │
         ▼
calculate_confidence()   → confidence_score  1–10
         │
         ▼
market_pulse             → market_pulse      btc/eth/sol prices, dominance, F&G, macro_verdict
         │                                   (runs FIRST so macro context is available to whale_profiler)
         ▼
whale_profiler           → whale_alerts      list of tracked-wallet hits (with moby_dick flag)
                           moby_dick_alerts  high-conviction pattern-match alerts
         │
         ▼
team_analyzer            → team_analysis     stability_score, reputation, signals, track_record
         │
         ▼
signal_feed              → smart_money       has_signal, strength, label, types, wallets, context
         │
         ▼
ai_analyst               → brain_verdict     verdict, layer, deploy, target_price, pullout,
                                             stop_loss, reasoning (Claude API — skipped if no key)
         │
         ▼
return full result dict  → serialised as JSON to the browser
```

---

## All API routes

### Core

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Serve `index.html` |
| POST | `/scan` | Full token scan. Body: `{ contract_address, chain }`. Returns complete result dict. |

### Scammer Database

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/scammer/list` | All tracked scammer addresses |
| POST | `/scammer/add` | Add address. Body: `{ address, label }` |
| POST | `/scammer/remove` | Remove address. Body: `{ address }` |

### Sniffer Bot

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/sniffer/start` | Start the background polling daemon |
| POST | `/sniffer/stop` | Stop the daemon |
| GET | `/sniffer/status` | `{ running, last_scan, total_scanned, green_count, seen_count }` |
| GET | `/sniffer/alerts?n=20` | Last N lines from `green_alerts.txt` |

### Whale Profiler

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/whale/list` | All tracked whale profiles |
| POST | `/whale/add` | Add wallet. Body: `{ address, label }` |
| POST | `/whale/remove` | Remove wallet. Body: `{ address }` |
| GET | `/whale/profile/<address>` | Full profile + computed behavioral_profile for one wallet |
| GET | `/whale/moby/<address>` | Behavioral profile only (entry/exit/shakeout/pattern_score) |
| GET | `/whale/activity?n=50` | Global activity feed, newest first |
| POST | `/whale/enrich/<address>` | Trigger Moralis cross-chain enrichment for a tracked whale. Body: `{ chains: ["eth","bsc",...] }` (optional — defaults to all 4 chains). |
| POST | `/whale/discover` | Discover early buyers of a token. Body: `{ token_address, chain, auto_add }`. Scores candidates 1-10, auto-adds ≥7 to whale DB. |

### Team Analyzer

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/team/profiles` | All creator profiles (auto-built from scans) |
| GET | `/team/profile/<address>` | Full profile for one creator address |

### Smart Money

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/signals/feed?n=50&min_strength=1&chain_id=&verdict=` | Signal feed, filterable |
| GET | `/signals/token/<chain_id>/<address>` | Latest signal for a specific token |
| GET | `/smartwallet/list` | All smart money wallets |
| POST | `/smartwallet/add` | Add wallet. Body: `{ address, label, category }` |
| POST | `/smartwallet/remove` | Remove wallet. Body: `{ address }` |

### Market Pulse

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/snapshot` | Today's macro snapshot (fetches fresh if not cached; `?refresh=1` to force) |
| GET | `/snapshot/log?n=30` | Historical daily snapshots, newest first |

### Moralis

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/moralis/status` | `{ available: true/false }` — whether MORALIS_API_KEY is set |

---

## Scoring systems

### Security verdict (analyze_token)

- Any **HARD_RED** flag → RED immediately  
  (honeypot, hidden owner, owner can take back ownership, owner can change balances, selfdestruct, buy/sell tax > 10%, liquidity < $1K)
- **3+ YELLOW flags** → RED  
- **1–2 YELLOW flags** → YELLOW  
- **0 flags** → GREEN

### Confidence score (1–10)

Starts from up to 8 "green confirms" (open source, LP locked, buy tax ≤5%, sell tax ≤5%, holders > 200, top holder < 20%, liquidity ≥$50K, not honeypot), scaled to 10, then subtracts 1.0 per yellow finding and 3.0 per red finding.

### Team Stability score (1–10)

| Signal | Max pts |
|--------|---------|
| Source code verified | +2 |
| Liquidity locked | +2 |
| Creator holds < 5% | +2 |
| Ownership renounced | +2 |
| Non-mintable supply | +1 |
| No balance manipulation | +1 |
| Track-record bonus (3+ prior GREEN) | +1 |
| Track-record penalty (prior RED tokens) | −1.5 to −3 |

Reputation: NEW → CLEAN → TRUSTED (clean track record) or MIXED → SUSPICIOUS → KNOWN SCAMMER (bad history).

### Smart Money signal score (1–10)

| Factor | Points |
|--------|--------|
| 1 wallet detected | +3 (base) |
| 2+ wallets detected | +6 (base, capped) |
| Holder count < 500 (EARLY_ENTRY) | +2 |
| Holder count 500–1500 | +1 |
| Liquidity $10K–$500K | +1 |
| Verdict GREEN (CLEAN_ENTRY) | +1 |
| Verdict RED | −2 |
| Team stability ≥ 7 | +1 |

Labels: 1–3 = WEAK, 4–5 = MODERATE, 6–7 = STRONG, 8–10 = ULTRA.

### Market Pulse macro verdict

| Condition | Verdict |
|-----------|---------|
| BTC dom > 65% OR Fear & Greed > 80 | DEFENSIVE |
| BTC dom < 60% AND Fear & Greed < 40 | CLEAR |
| Everything else | CAUTION |

### AI Brain Verdict — $300 capital rules

| Layer | Conviction | Deploy | Stop Loss |
|-------|-----------|--------|-----------|
| 1 | Moderate | $30 | −15% |
| 2 | Strong | $60 | −20% |
| 3 | Ultra | $90 | −25% |

- Target is always 2.5× entry price
- Pull-out = deployed × 2.5
- Never GREEN on RED-verdict tokens or KNOWN SCAMMER team

### Moby Dick Behavioral Profile — Pattern Score (0–10)

| Factor | Max pts |
|--------|---------|
| Data richness (entries + exits, capped) | 4 |
| F&G entry consistency (std dev < 15) | 2 |
| Liquidity entry consistency (CV < 0.4) | 2 |
| Exit data bonus | 2 |

Grades: 1–3 = UNRELIABLE · 4–5 = EMERGING · 6–7 = RELIABLE · 8–10 = ORACLE

### Moby Dick Alert — match score (0–4)

Fires (score ≥ 3) when the current token matches this whale's historical entry pattern on:

1. Fear & Greed within typical entry range (generous margin)
2. Liquidity within 5× of typical entry liquidity
3. Holder count within typical entry range
4. Market cap within one order of magnitude of typical entry mcap

Requires ≥ 3 prior entry events in the whale's activity log to guard against false positives.

### Moralis whale discovery score (1–10)

| Factor | Max pts | How |
|--------|---------|-----|
| Entry timing | 4 | Percentile rank of first buy among all buyers: top 5%=4, top 15%=3, top 30%=2, rest=1 |
| Buy size | 2 | Buy value vs median: ≥3× median=2, ≥median=1, else=0 |
| Portfolio diversity | 2 | Unique tokens in current holdings: ≥5=2, ≥2=1, else=0 |
| Still holding | 2 | Still holds this exact token at time of query: yes=2, no=0 |

Score ≥ 7 → auto-added to whale database with label `Discovered·{SYMBOL}·#{rank}`.

---

## Behavioral profile fields stored per activity event (Phase 6)

Each event in `activity_log` now carries:

| Field | Source |
|-------|--------|
| `holder_count` | GoPlus `holder_count` |
| `market_cap` | DEX Screener `marketCap` or `fdv` |
| `fear_greed` | Market Pulse snapshot |
| `btc_dominance` | Market Pulse snapshot |
| `macro_verdict` | Market Pulse snapshot |

EXIT events additionally carry `entry_price_usd` (from the stored entry record) for exit multiplier calculation.

Moralis-enriched events additionally carry `value_decimal` (token amount) and `tx_hash`. Fields that require real-time data (price_usd, liquidity_usd, fear_greed etc.) are `null` on Moralis events and get filled in by subsequent live scans.

---

## Moralis API endpoints used

Base URL: `https://deep-index.moralis.io/api/v2.2`  
Auth: `X-API-Key: <MORALIS_API_KEY>` header

| Endpoint | Used for |
|----------|---------|
| `GET /wallets/{address}/history` | Full wallet transaction history |
| `GET /erc20/{address}/transfers` | ERC20 transfers for a wallet (enrichment) or all transfers of a token contract (discovery) |
| `GET /{address}/erc20` | Current ERC20 holdings (still-holding check in discovery scoring) |

Chains supported: `eth`, `bsc`, `base`, `polygon`

---

## UI panels (index.html)

All panels are collapsible. Panels load data lazily on first open.

| Panel | Icon | Function |
|-------|------|----------|
| Scan Form | — | Contract address + chain dropdown + Scan button |
| Result Area | — | Brain Verdict → Moby Dick Alert → Macro Pulse stamp → Scammer banner → Whale banner → Smart Money card → Verdict banner → Token info → DEX data → Findings → Team Stability card |
| Market Pulse | 🌐 | Live BTC/ETH/SOL prices, F&G and dominance bars, macro verdict badge, 14-day history |
| Scammer Database | 🕵️ | Add/remove known bad actor addresses |
| Live Token Sniffer | 🤖 | Start/stop background bot; stats + GREEN alert log |
| Team Profiles | 👥 | Address-filterable list of all auto-profiled creators |
| Whale Intelligence | 🐋 | Add/remove tracked wallets; ⚡ Enrich button (Moralis cross-chain history); 🐋 Profile button (behavioral profile); activity feed; 🔍 Discover Whales (token → scored early buyers → auto-add ≥7 to DB) |
| Smart Money Wallets | 💰 | Add/remove smart money wallets |
| Smart Money Feed | 💎 | Persistent signal feed across all tokens |

---

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Optional | Enables AI Analysis Engine (Phase 7). If absent, `brain_verdict` is `null` and the Brain Verdict card is silently skipped. |
| `MORALIS_API_KEY` | Optional | Enables whale profile enrichment and discovery. If absent, `/whale/enrich` and `/whale/discover` return error; UI shows a notice. |
| `PORT` | Optional | HTTP port (default 8080) |

---

## External APIs used

| API | Usage | Auth required |
|-----|-------|--------------|
| GoPlus Security (`api.gopluslabs.io`) | Token security data — holders, tax, risk flags | None (public) |
| DEX Screener (`api.dexscreener.com`) | Price, liquidity, volume, pair data | None (public) |
| CoinGecko (`api.coingecko.com`) | BTC/ETH/SOL prices; BTC dominance via `/global` → `market_cap_percentage.btc` | None (public) |
| Alternative.me (`api.alternative.me/fng`) | Crypto Fear & Greed Index | None (public) |
| Anthropic API | AI trade verdict via `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` env var |
| Moralis (`deep-index.moralis.io/api/v2.2`) | Cross-chain wallet history, token transfers, current holdings | `MORALIS_API_KEY` env var |

---

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # optional — enables AI verdict
export MORALIS_API_KEY=...            # optional — enables whale enrichment + discovery
python app.py          # starts on http://localhost:8080
```

For production:
```bash
gunicorn app:app --workers 2
```
