# CryptoBrain Scanner — Project State

**Date:** 2026-06-06  
**Branch:** master  
**Commits:** 4 (initial + 3 feature phases)

---

## What the app does

A Flask web app that scans any EVM or Solana token contract and returns a
multi-layered intelligence report:

1. **Security verdict** (RED / YELLOW / GREEN) powered by the GoPlus Security API
2. **Market data** pulled from DEX Screener
3. **Whale Intelligence** — tracks specific wallets across scans, logs entry/exit events
4. **Team Stability** — profiles token creators, scores their contract practices, tracks their deployment history
5. **Smart Money Signal** — detects pre-registered "smart money" wallets in top holders and scores the signal 1–10

A background sniffer bot can poll DEX Screener every 5 minutes for new token
listings and auto-scan them, logging any GREEN verdicts.

---

## File inventory

### Python — core application

| File | Lines | Purpose |
|------|------:|---------|
| `app.py` | 483 | Flask application. Registers all routes. `scan_token()` is the main pipeline: fetches GoPlus + DEX Screener in parallel, runs analysis, then calls all four intelligence modules in sequence. |
| `scammer_db.py` | 50 | CRUD wrapper around `scammer_db.json`. Blacklist of known bad actor addresses; checked against creator/owner on every scan. |
| `sniffer_bot.py` | 153 | Background daemon (daemon thread). Polls `dexscreener.com/token-profiles/latest/v1` every 5 minutes, auto-scans new addresses, appends GREEN verdicts to `green_alerts.txt`. |
| `whale_profiler.py` | 229 | Manages `whale_profiles.json`. Tracks any user-added wallet across scans. Classifies observations as ENTRY / EXIT / INCREASE / DECREASE / DETECTED. Provides a global activity feed sorted by timestamp. |
| `team_analyzer.py` | 310 | Manages `team_profiles.json`. Auto-profiles every token creator on each scan. Scores contract practices 1–10 across six signals (source verification, LP lock, creator concentration, ownership status, mintability, balance manipulation). Assigns reputation: NEW / CLEAN / TRUSTED / MIXED / SUSPICIOUS / KNOWN SCAMMER. |
| `wallet_tracker.py` | 113 | Manages `smart_wallets.json`. CRUD for the "smart money" wallet registry. `detect_in_scan()` cross-references GoPlus top holders against the registry. `bump_signal_count()` increments per-wallet signal counters after each hit. |
| `signal_feed.py` | 198 | Manages `smart_money_signals.json`. `process_token_scan()` calls `wallet_tracker.detect_in_scan()`, then `_score()` weights five factors to produce a 1–10 strength score, a label (WEAK / MODERATE / STRONG / ULTRA), and type tags (MULTI_WALLET, EARLY_ENTRY, CLEAN_ENTRY, HIGH_CONVICTION). Upserts one signal record per token (rolling window of 500). |

### HTML / templates

| File | Lines | Purpose |
|------|------:|---------|
| `templates/index.html` | 2415 | Single-page UI. Dark theme, no external frameworks. Vanilla JS with `fetch()`. Renders all scan result cards and hosts six collapsible management panels. |

### Data files (auto-created, git-tracked)

| File | Purpose |
|------|---------|
| `scammer_db.json` | Known bad-actor addresses `{ address: { label, added } }` |
| `whale_profiles.json` | User-tracked wallet profiles with rolling 100-event activity logs |
| `team_profiles.json` | Auto-built creator profiles with per-verdict token history (rolling 50 tokens) |
| `smart_wallets.json` | Smart money wallet registry with signal counts |
| `smart_money_signals.json` | Scored signal feed, one record per token, newest first (max 500) |
| `green_alerts.txt` | Plain-text log appended by the sniffer bot for GREEN verdicts |

### Deployment / config

| File | Purpose |
|------|---------|
| `Procfile` | Heroku: `web: gunicorn app:app --workers 2` |
| `requirements.txt` | `Flask`, `requests`, `gunicorn` |
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
whale_profiler           → whale_alerts      list of tracked-wallet hits
         │
         ▼
team_analyzer            → team_analysis     stability_score, reputation, signals, track_record
         │
         ▼
signal_feed              → smart_money       has_signal, strength, label, types, wallets, context
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
| POST | `/scan` | Full token scan. Body: `{ contract_address, chain }`. Returns the complete result dict. |

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
| GET | `/whale/profile/<address>` | Full profile for one wallet |
| GET | `/whale/activity?n=50` | Global activity feed, newest first |

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
ULTRA signals trigger a pulsing gold glow animation in the UI.

---

## UI panels (index.html)

All panels are collapsible. Panels load their data lazily on first open.

| Panel | Icon | Function |
|-------|------|----------|
| Scan Form | — | Contract address + chain dropdown + Scan button |
| Result Area | — | Dynamically rendered: scammer banner, whale banner, Smart Money card, verdict banner, token info grid, DEX market data, findings, Team Stability card |
| Scammer Database | 🕵️ | Add/remove known bad actor addresses; auto-checked on every scan |
| Live Token Sniffer | 🤖 | Start/stop background bot; stats + recent GREEN alert log |
| Team Profiles | 👥 | Address-filterable list of all auto-profiled creators; reputation badges + verdict breakdown |
| Whale Intelligence | 🐋 | Add/remove tracked wallets; whale cards with entry/exit counts and current holdings; global activity feed |
| Smart Money Wallets | 💰 | Add/remove smart money wallets; per-wallet signal counts |
| Smart Money Feed | 💎 | Persistent signal feed across all tokens; strength badge + wallet names + chain/holder/liq meta |

---

## External APIs used

| API | Usage | Auth required |
|-----|-------|--------------|
| GoPlus Security (`api.gopluslabs.io`) | Token security data — all holder, tax, risk flags | None (public) |
| DEX Screener (`api.dexscreener.com`) | Price, liquidity, volume, pair data | None (public) |

---

## Running locally

```bash
pip install -r requirements.txt
python app.py          # starts on http://localhost:5000
```

For production:
```bash
gunicorn app:app --workers 2
```
