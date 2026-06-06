# CryptoBrain Scanner — Project State

**Date:** 2026-06-06  
**Branch:** master  
**Commits:** 13

| # | Commit | Description |
|---|--------|-------------|
| 1 | `3553ac4` | Initial commit: CryptoBrain Scanner project |
| 2 | `5c341ed` | Phase 2 Whale Profiler — track wallets, log entry/exit patterns, Whale Intelligence UI panel |
| 3 | `4bf4e21` | Phase 2 Team Stability Analyzer — creator profiling, stability scoring, track record |
| 4 | `2fd1313` | Phase 3 Smart Money Signal Feed — wallet registry, signal scoring, live feed + scan card |
| 5 | `3d3fc3c` | docs: add PROJECT_STATE.md |
| 6 | `a4ca00e` | Phase 4: Daily Market Pulse snapshot system |
| 7 | `ecaa194` | Fix BTC dominance parsing and timestamp format in market_pulse.py |
| 8 | `0d2a067` | Phase 5: AI Analysis Engine (Claude trade verdict) |
| 9 | `32652a0` | Update AI analyst model to claude-sonnet-4-5 |
| 10 | `03e6363` | Phase 6: Moby Dick Whale Profiler — full behavioral profiling engine |
| 11 | `7fb0ac7` | Phase 7: Moralis API integration for cross-chain whale profiling |
| 12 | `1b375ae` | Add hex chain ID support and BSC-first enrich order |
| 13 | `b7d2877` | Auto-enrich on whale track: remove Enrich button, add loading indicator |

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
7. **AI Analysis Engine** — sends the complete scan data to Claude (`claude-sonnet-4-5`) and returns a structured trade verdict with layer, deploy amount, target price, pull-out amount, stop loss, and 3-sentence reasoning
8. **Moby Dick Whale Profiler** — builds full behavioral profiles per tracked whale wallet: Entry/Exit/Shakeout profiles with historical averages and a Pattern Score (UNRELIABLE → EMERGING → RELIABLE → ORACLE); fires a Moby Dick Alert when a whale's new entry conditions match their historical pattern
9. **Moralis Integration** — cross-chain wallet enrichment and whale discovery; tracking a wallet automatically enriches its history across BSC → ETH → Base via Moralis; paste any successful token to pull its early buyers, score them 1–10 by entry timing/size/holdings, auto-add wallets scoring 7+ to the whale database

A background sniffer bot can poll DEX Screener every 5 minutes for new token
listings and auto-scan them, logging any GREEN verdicts.

---

## File inventory

### Python — core application

| File | Lines | Purpose |
|------|------:|---------|
| `app.py` | 577 | Flask application. All routes. `scan_token()` is the main pipeline. `/whale/add` auto-fires background Moralis enrichment on successful add. |
| `scammer_db.py` | 50 | CRUD wrapper around `scammer_db.json`. Blacklist checked against creator/owner on every scan. |
| `sniffer_bot.py` | 153 | Background daemon. Polls DEX Screener every 5 minutes, auto-scans, logs GREEN verdicts. |
| `whale_profiler.py` | 651 | Manages `whale_profiles.json`. Tracks user-added wallets; classifies ENTRY/EXIT/INCREASE/DECREASE/DETECTED. Computes Entry/Exit/Shakeout/Pattern behavioral profiles; fires Moby Dick Alerts on pattern-matched entries. |
| `moralis_client.py` | 324 | Moralis v2.2 API wrapper. Raw endpoints: wallet history, token transfers (wallet or contract), current holdings. `enrich_whale_profile()`: multi-chain enrichment defaulting to `ENRICH_CHAINS = ["0x38", "0x1", "0x2105"]` (BSC → ETH → Base). `discover_early_buyers()`: token-to-whale discovery with 1–10 scoring. |
| `team_analyzer.py` | 310 | Manages `team_profiles.json`. Scores creator contract practices 1–10; assigns TRUSTED/CLEAN/NEW/MIXED/SUSPICIOUS/KNOWN SCAMMER reputation. |
| `wallet_tracker.py` | 113 | Manages `smart_wallets.json`. CRUD for smart money wallet registry; detects in GoPlus holders. |
| `signal_feed.py` | 198 | Manages `smart_money_signals.json`. Scores smart money hits 1–10; type tags MULTI_WALLET/EARLY_ENTRY/CLEAN_ENTRY/HIGH_CONVICTION. |
| `market_pulse.py` | 97 | Manages `market_pulse_log.json`. Fetches BTC/ETH/SOL prices (CoinGecko), BTC dominance (CoinGecko `/global` → `market_cap_percentage.btc`), Fear & Greed (Alternative.me). Caches once per day; max 90-day rolling window. |
| `ai_analyst.py` | 136 | Calls `claude-sonnet-4-5` with the full scan result. Parses response for verdict/layer/deploy/target_price/pullout/stop_loss/reasoning. Returns `None` if `ANTHROPIC_API_KEY` not set. |

### HTML / templates

| File | Lines | Purpose |
|------|------:|---------|
| `templates/index.html` | 3333 | Single-page UI. Dark theme, no external frameworks. Vanilla JS. Renders all scan result cards and hosts collapsible management panels. |

### Data files (auto-created, git-tracked)

| File | Purpose |
|------|---------|
| `scammer_db.json` | Known bad-actor addresses |
| `whale_profiles.json` | Tracked wallet profiles. Activity logs: 100-event cap for live-scan events, 500-event cap for Moralis-enriched profiles. Each event carries Phase 6 behavioral context fields. |
| `team_profiles.json` | Auto-built creator profiles (rolling 50 tokens per address) |
| `smart_wallets.json` | Smart money wallet registry with signal counts |
| `smart_money_signals.json` | Scored signal feed, one record per token (max 500) |
| `market_pulse_log.json` | Daily macro snapshots, newest first (max 90 days) |

### Deployment / config

| File | Purpose |
|------|---------|
| `Procfile` | Heroku: `web: gunicorn app:app --workers 2` |
| `requirements.txt` | `flask`, `requests`, `gunicorn`, `anthropic` |
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
         │                                   (runs before whale_profiler so macro context is stored on events)
         ▼
whale_profiler           → whale_alerts      list of tracked-wallet hits (moby_dick flag per alert)
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
                                             stop_loss, reasoning (skipped if no ANTHROPIC_API_KEY)
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
| POST | `/whale/add` | Add wallet. Body: `{ address, label }`. If `MORALIS_API_KEY` is set, immediately fires `enrich_whale_profile()` in a background thread and returns `enriching: true`. |
| POST | `/whale/remove` | Remove wallet. Body: `{ address }` |
| GET | `/whale/profile/<address>` | Full profile + computed `behavioral_profile` for one wallet |
| GET | `/whale/moby/<address>` | Behavioral profile only (entry_profile, exit_profile, shakeout_profile, pattern_score) |
| GET | `/whale/activity?n=50` | Global activity feed across all tracked wallets, newest first |
| POST | `/whale/enrich/<address>` | Trigger Moralis enrichment manually. Body: `{ chains: ["0x38","0x1","0x2105"] }` optional — defaults to `ENRICH_CHAINS`. |
| POST | `/whale/discover` | Discover early buyers of a token. Body: `{ token_address, chain, auto_add }`. Scores candidates 1–10, auto-adds ≥7 to whale DB. |

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
| GET | `/snapshot` | Today's macro snapshot (`?refresh=1` to force re-fetch) |
| GET | `/snapshot/log?n=30` | Historical daily snapshots, newest first (max 90) |

### Moralis

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/moralis/status` | `{ available: true/false }` — whether `MORALIS_API_KEY` is set |

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

| Signal | Points |
|--------|--------|
| Source code verified | +2 |
| Liquidity locked | +2 |
| Creator holds < 5% | +2 |
| Ownership renounced | +2 |
| Non-mintable supply | +1 |
| No balance manipulation | +1 |
| Track-record bonus (3+ prior GREEN) | +1 |
| Track-record penalty (prior RED tokens) | −1.5 to −3 |

Reputation ladder: NEW → CLEAN → TRUSTED (clean) or MIXED → SUSPICIOUS → KNOWN SCAMMER (bad history).

### Smart Money signal score (1–10)

| Factor | Points |
|--------|--------|
| 1 wallet detected | +3 |
| 2+ wallets detected | +6 (capped) |
| Holder count < 500 (EARLY_ENTRY) | +2 |
| Holder count 500–1500 | +1 |
| Liquidity $10K–$500K | +1 |
| Verdict GREEN (CLEAN_ENTRY) | +1 |
| Verdict RED | −2 |
| Team stability ≥ 7 | +1 |

Labels: 1–3 = WEAK · 4–5 = MODERATE · 6–7 = STRONG · 8–10 = ULTRA.

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

- Target always 2.5× entry price; pull-out = deployed × 2.5
- Never GREEN on RED-verdict tokens or KNOWN SCAMMER team

### Moby Dick Behavioral Profile — Pattern Score (0–10)

| Factor | Max pts |
|--------|---------|
| Data richness (entries + exits, capped at 4) | 4 |
| F&G entry consistency (stdev < 15 = 2pts, < 30 = 1pt) | 2 |
| Liquidity entry consistency (CV < 0.4 = 2pts, < 0.8 = 1pt) | 2 |
| Exit data bonus (1pt per exit, capped) | 2 |

Grades: 1–3 = UNRELIABLE · 4–5 = EMERGING · 6–7 = RELIABLE · 8–10 = ORACLE

### Moby Dick Alert — match score (0–4)

Fires when score ≥ 3, requires ≥ 3 prior ENTRY events in activity log.

| Check | +1 if… |
|-------|--------|
| F&G match | current F&G within whale's typical entry range ± generous margin |
| Liquidity match | current liquidity within 0.2×–5× of whale's avg entry liquidity |
| Holder count match | current holder count within whale's typical range ± generous margin |
| Market cap match | current market cap within 10× of whale's typical entry mcap |

### Moralis whale discovery score (1–10)

| Factor | Max pts | Condition |
|--------|---------|-----------|
| Entry timing | 4 | Percentile rank among all buyers: top 5%=4, top 15%=3, top 30%=2, rest=1 |
| Buy size | 2 | vs median buy: ≥3× median=2, ≥median=1, else=0 |
| Portfolio diversity | 2 | Unique tokens in current holdings: ≥5=2, ≥2=1, else=0 |
| Still holding | 2 | Still holds the token at query time: yes=2, no=0 |

Score ≥ 7 → auto-added to whale database with label `Discovered·{SYMBOL}·#{rank}`.

---

## Behavioral profile fields per activity event

Every event written to `activity_log` by the live-scan pipeline carries:

| Field | Source |
|-------|--------|
| `timestamp` | UTC, `YYYY-MM-DDTHH:MM:SSZ` format |
| `event_type` | ENTRY / EXIT / INCREASE / DECREASE / DETECTED |
| `chain` / `chain_id` | from CHAIN_NAMES map in whale_profiler.py |
| `token_address` / `token_name` / `token_symbol` | GoPlus |
| `holdings_pct` | GoPlus holder percent × 100 |
| `price_usd` / `liquidity_usd` / `volume_h24` | DEX Screener |
| `holder_count` | GoPlus `holder_count` |
| `market_cap` | DEX Screener `marketCap` or `fdv` |
| `fear_greed` | Market Pulse snapshot |
| `btc_dominance` | Market Pulse snapshot |
| `macro_verdict` | Market Pulse snapshot |

EXIT events additionally carry `entry_price_usd` (copied from the stored entry record at ENTRY time) for exit-multiplier computation.

Moralis-enriched events additionally carry `value_decimal` (token amount transferred) and `tx_hash`. Fields requiring real-time data (`price_usd`, `liquidity_usd`, `fear_greed`, etc.) are `null` on Moralis events — they get filled in when the wallet is detected in a subsequent live scan.

---

## Moralis API (moralis_client.py)

**Base URL:** `https://deep-index.moralis.io/api/v2.2`  
**Auth:** `X-API-Key: <MORALIS_API_KEY>` request header

### Endpoints used

| Endpoint | Purpose |
|----------|---------|
| `GET /wallets/{address}/history` | Full wallet transaction history |
| `GET /erc20/{address}/transfers` | ERC20 transfers for a wallet address (enrichment) |
| `GET /erc20/{token_address}/transfers` | All transfers of a token contract (discovery) |
| `GET /{address}/erc20` | Current ERC20 token balances (still-holding check) |

### Chain identifiers

Both Moralis hex IDs and string aliases are supported as the `chain` parameter. `ENRICH_CHAINS` defines the default order for wallet enrichment:

| Moralis hex | String alias | Network | Decimal chain ID |
|-------------|-------------|---------|-----------------|
| `0x38` | `bsc` | BNB Chain | 56 |
| `0x1` | `eth` | Ethereum | 1 |
| `0x2105` | `base` | Base | 8453 |
| `0x89` | `polygon` | Polygon | 137 |

`ENRICH_CHAINS = ["0x38", "0x1", "0x2105"]` — BSC first (highest meme-coin activity), then ETH, then Base. Polygon excluded from default to limit latency.

### Auto-enrich flow

When a wallet is added via `POST /whale/add` and `MORALIS_API_KEY` is set:
1. `whale_profiler.add_whale()` saves the wallet to `whale_profiles.json`
2. A daemon `threading.Thread` fires `moralis_client.enrich_whale_profile()` immediately
3. The HTTP response returns `{ ok: true, enriching: true }` without waiting
4. The UI shows a spinner banner: *"Enriching [name] — fetching cross-chain history (BSC → ETH → Base)…"*
5. After ~25 s the banner auto-removes and the whale list refreshes

---

## UI panels (index.html)

All panels are collapsible and lazy-load on first open.

| Panel | Icon | Contents |
|-------|------|----------|
| Scan Form | — | Contract address input + chain dropdown + Scan button |
| Result Area | — | Brain Verdict card → Moby Dick Alert banner → Macro Pulse stamp → Scammer banner → Whale alert banner → Smart Money card → Verdict banner → Token info → DEX data → Findings list → Team Stability card |
| Market Pulse | 🌐 | Live BTC/ETH/SOL prices, Fear & Greed bar, BTC dominance bar, macro verdict badge, 14-day history table |
| Scammer Database | 🕵️ | Add/remove known bad-actor addresses |
| Live Token Sniffer | 🤖 | Start/stop background bot; stats counters + GREEN alert log |
| Team Profiles | 👥 | Address-filterable list of all auto-profiled creator wallets |
| Whale Intelligence | 🐋 | Track wallet (auto-enriches on add with spinner); 🐋 Profile button per card (lazy-loads behavioral profile from `/whale/moby/`); activity feed; 🔍 Discover Whales section (token address + chain → scored candidate list → auto-adds ≥7 to DB) |
| Smart Money Wallets | 💰 | Add/remove smart money wallets |
| Smart Money Feed | 💎 | Persistent scored signal feed across all scanned tokens |

---

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Optional | Enables AI Brain Verdict. If absent, `brain_verdict` is `null` and the card is silently skipped. |
| `MORALIS_API_KEY` | Optional | Enables auto-enrichment on whale add, `/whale/enrich`, and `/whale/discover`. If absent, those routes return an error and the UI shows a notice banner. |
| `PORT` | Optional | HTTP port (default `8080`) |

---

## External APIs

| API | Endpoint base | Auth | Usage |
|-----|--------------|------|-------|
| GoPlus Security | `api.gopluslabs.io/api/v1` | None | Token security — holders, taxes, risk flags |
| DEX Screener | `api.dexscreener.com/latest/dex/tokens` | None | Price, liquidity, volume, pair data |
| CoinGecko | `api.coingecko.com/api/v3` | None | BTC/ETH/SOL prices; BTC dominance |
| Alternative.me | `api.alternative.me/fng` | None | Fear & Greed Index |
| Anthropic | `api.anthropic.com` | `ANTHROPIC_API_KEY` | AI trade verdict via `claude-sonnet-4-5` |
| Moralis | `deep-index.moralis.io/api/v2.2` | `MORALIS_API_KEY` | Cross-chain wallet history, token transfers, holdings |

---

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # optional
export MORALIS_API_KEY=...            # optional
python app.py                          # http://localhost:8080
```

Production:
```bash
gunicorn app:app --workers 2
```
