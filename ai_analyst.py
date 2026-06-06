import os
import re

_ANTHROPIC_AVAILABLE = False
try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    pass

MODEL   = "claude-sonnet-4-20250514"
MAX_TOK = 1024

SYSTEM_PROMPT = """\
You are CryptoBrain, an elite crypto micro-cap analyst specialising in fast-entry \
altcoin trades on EVM chains. You receive a full token intelligence report and must \
output a single structured trade verdict. Be direct, specific, and ruthless — no fluff.

CAPITAL RULES ($300 starting capital):
- Layer 1 (moderate conviction): Deploy $30. Use when signals are promising but limited.
- Layer 2 (strong conviction): Deploy $60. Use when multiple strong signals align.
- Layer 3 (ultra conviction): Deploy $90. Use only when all systems are green.
- Never recommend GREEN on RED-verdict tokens or KNOWN SCAMMER team reputation.
- Target: 2.5x on every trade. Pull-out amount = deployed_amount × 2.5.
- Stop loss: Layer 1 = 15% below entry price. Layer 2 = 20%. Layer 3 = 25%.
- If the token has no price data, use 0.00 for price-dependent fields.

OUTPUT FORMAT — respond with ONLY this block, no other text:
VERDICT: [GREEN|YELLOW|RED]
LAYER: [1|2|3]
DEPLOY: $[amount]
TARGET_PRICE: $[price]
PULLOUT: $[amount]
STOP_LOSS: $[price]
REASONING: [Exactly 3 sentences. Sentence 1: the single lead signal that drove this verdict. \
Sentence 2: the biggest risk or red flag to watch. \
Sentence 3: specific actionable trade advice.]"""


def _build_prompt(scan):
    info     = scan.get("info",          {})
    dex      = scan.get("dex",           {})
    ta       = scan.get("team_analysis", {})
    sm       = scan.get("smart_money",   {})
    mp       = scan.get("market_pulse",  {})
    verdict  = scan.get("verdict",       "UNKNOWN")
    conf     = scan.get("confidence_score", "N/A")
    reds     = scan.get("red_findings",  [])
    yellows  = scan.get("yellow_findings", [])

    red_txt    = "; ".join(reds)    or "None"
    yellow_txt = "; ".join(yellows) or "None"

    sm_line = "No signal"
    if sm.get("has_signal"):
        wallets = ", ".join(w.get("label", w.get("address", "?")) for w in (sm.get("wallets") or []))
        sm_line = (f"{sm.get('signal_label')} {sm.get('signal_strength')}/10 "
                   f"({sm.get('wallet_count')} wallet(s): {wallets}) "
                   f"types: {', '.join(sm.get('signal_types') or [])}")

    mp_line = "Unavailable"
    if mp:
        mp_line = (f"{mp.get('macro_verdict','?')} — "
                   f"BTC dom {mp.get('btc_dominance','?')}%, "
                   f"F&G {mp.get('fear_greed','?')} ({mp.get('fear_greed_label','?')})")

    return f"""TOKEN: {info.get('name','Unknown')} ({info.get('symbol','?')})
CHAIN ID: {scan.get('chain_id', 'unknown')}

SECURITY VERDICT: {verdict} (confidence {conf}/10)
RED FLAGS: {red_txt}
YELLOW FLAGS: {yellow_txt}

MARKET DATA:
- Price USD: {dex.get('price_usd') or 'N/A'}
- Liquidity USD: {dex.get('liquidity_usd') or 'N/A'}
- 24h Volume: {dex.get('volume_h24') or 'N/A'}
- Holders: {info.get('holder_count', 'N/A')}
- Top holder: {info.get('top_holder_pct', 'N/A')}
- Buy tax: {info.get('buy_tax','N/A')} / Sell tax: {info.get('sell_tax','N/A')}
- LP locked: {info.get('lp_locked', False)}
- Source verified: {info.get('is_verified', False)}

TEAM ANALYSIS:
- Stability score: {ta.get('stability_score','N/A')}/10
- Reputation: {ta.get('reputation','N/A')}

SMART MONEY: {sm_line}

MACRO CONTEXT: {mp_line}"""


def _parse(text):
    def grab(pattern, flags=re.IGNORECASE):
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else None

    return {
        "verdict":      grab(r"VERDICT:\s*(GREEN|YELLOW|RED)"),
        "layer":        grab(r"LAYER:\s*([1-3])"),
        "deploy":       grab(r"DEPLOY:\s*\$?([\d,\.]+)"),
        "target_price": grab(r"TARGET_PRICE:\s*\$?([\d,\.]+)"),
        "pullout":      grab(r"PULLOUT:\s*\$?([\d,\.]+)"),
        "stop_loss":    grab(r"STOP_LOSS:\s*\$?([\d,\.]+)"),
        "reasoning":    grab(r"REASONING:\s*(.+)", re.IGNORECASE | re.DOTALL),
        "raw":          text,
    }


def analyze(scan_result):
    """
    Call the Claude API with the full scan result.
    Returns brain_verdict dict, or None if the API key is absent or call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    if not _ANTHROPIC_AVAILABLE:
        return {"error": "anthropic package not installed"}

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOK,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(scan_result)}],
        )
        text = msg.content[0].text if msg.content else ""
        parsed = _parse(text)
        parsed["model"] = MODEL
        return parsed
    except Exception as e:
        return {"error": str(e), "raw": None}
