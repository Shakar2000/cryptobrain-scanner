# CryptoBrain Fast Scanner

A lightweight Flask web app that scans any EVM or Solana contract address using the
[GoPlus Security API](https://gopluslabs.io/) and returns an instant **Green / Yellow / Red**
verdict in the CryptoBrain format.

---

## Features

- One-click contract scan — no wallet, no sign-in required
- Supports Ethereum, BNB Chain, Polygon, Arbitrum, Optimism, Base, Avalanche, Solana
- Detects honeypots, hidden owners, unverified source code, high taxes, unlocked LP, and more
- Color-coded verdict banner with glow effects (Green / Yellow / Red)
- Token info grid: supply, holders, buy/sell tax, top holder %, LP lock status
- Categorised findings: Red Flags 🚩 and Caution Flags ⚠️
- Fully self-contained — no database, no API key required

---

## Quick Start

### 1. Clone / download

```bash
git clone <repo-url>
cd "CryptoBrain Scanner/Scanner Code"
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the app

```bash
python app.py
```

Open your browser at **http://localhost:5000**

---

## Usage

1. Paste a contract address into the input field.
2. Select the correct chain from the dropdown.
3. Press **Scan Contract** (or hit Enter).
4. Read the verdict and findings.

---

## Verdict Logic

| Verdict | Triggered when |
|---------|---------------|
| 🟢 **GREEN** | No red or yellow flags found |
| 🟡 **YELLOW** | One or more caution flags, no red flags |
| 🔴 **RED** | One or more critical red flags present |

### Red flags (any one = RED)

- Honeypot detected
- Source code not verified
- Hidden owner
- Owner can change balances or take back ownership
- Self-destruct function present
- Proxy / upgradeable contract
- Buy or sell tax > 10%
- Top holder owns > 50% of supply

### Yellow / caution flags

- Token is mintable
- Transfers can be paused
- Slippage modifiable
- Liquidity not locked
- Buy or sell tax between 5–10%
- Top holder owns 20–50% of supply
- Anti-whale limits modifiable
- Trading cooldown active

---

## Project Structure

```
Scanner Code/
├── app.py               # Flask app — API calls, analysis logic, routes
├── requirements.txt     # Python dependencies
├── README.md
└── templates/
    └── index.html       # Single-page UI
```

---

## Supported Chains

| Name | Chain ID |
|------|----------|
| Ethereum | 1 |
| BNB Chain | 56 |
| Polygon | 137 |
| Arbitrum One | 42161 |
| Optimism | 10 |
| Base | 8453 |
| Avalanche | 43114 |
| Solana | solana |

You can also pass a raw chain ID directly in the API if calling `/scan` manually.

---

## API Endpoint

### `POST /scan`

**Request body (JSON):**

```json
{
  "contract_address": "0xabc123...",
  "chain": "1"
}
```

**Response (JSON):**

```json
{
  "verdict": "RED",
  "info": {
    "name": "FakeToken",
    "symbol": "FAKE",
    "total_supply": "1000000000",
    "holder_count": "42",
    "buy_tax": "15.0%",
    "sell_tax": "25.0%",
    "is_verified": false,
    "lp_locked": false,
    "top_holder_pct": "72.0%"
  },
  "red_findings": [
    "HONEYPOT DETECTED",
    "Source code NOT verified",
    "High tax: Buy 15.0% / Sell 25.0%"
  ],
  "yellow_findings": []
}
```

---

## Disclaimer

This tool provides automated analysis based on on-chain data from GoPlus Security.
It is not financial advice. Always do your own research (DYOR) before investing in any token.
