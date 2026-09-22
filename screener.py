#!/usr/bin/env python3
"""
Meme Coin Momentum/Risk Screener
=================================

WHAT THIS ACTUALLY DOES (read this before you use it):
This script does NOT predict which coins will "pump." Nothing can do that
reliably -- if it could, it wouldn't be free. What it DOES do is pull
public, real on-chain/market data and turn it into a single sortable score
based on factors that correlate loosely with momentum and correlate more
strongly with RISK. Use it to filter garbage faster, not to find guaranteed
winners.

DATA SOURCE: DexScreener's free public API (no key required).
  - https://api.dexscreener.com/token-boosts/latest/v1   (newly promoted tokens)
  - https://api.dexscreener.com/token-profiles/latest/v1 (newly listed profiles)
  - https://api.dexscreener.com/latest/dex/search?q=...   (search by name/symbol)
  - https://api.dexscreener.com/latest/dex/tokens/{addr}  (full pair stats)

LIMITATIONS (important):
  - No holder-concentration or "top 10 wallets %" data. For that, add a
    RugCheck.xyz API call (Solana only) -- there's a stub for it below.
  - No historical/candle data -- this is a snapshot tool, run it repeatedly
    to see how scores change over time.
  - No X/Twitter integration by default. X's API requires a PAID developer
    tier to search tweets now -- there is no reliable free way to pull
    real-time social data automatically. If you have a paid X API bearer
    token, there's an optional stub below to wire it in.

HOW TO RUN:
  1. pip install requests
  2. python screener.py
  3. Open the generated report.html in your browser.
  4. Re-run any time you want a fresh snapshot (prices move fast).

WHAT THE SCORE MEANS:
  Higher = more liquidity, more organic-looking buy pressure, more age
  (survived longer = fewer instant rugs), lower concentration risk where
  we have data for it.
  This is a RISK/MOMENTUM filter, not a buy signal. Many high-scoring
  tokens will still go to zero. Many low-scoring ones will occasionally
  moon. This just removes some of the obvious garbage.
"""

import requests
import time
import datetime
import webbrowser
import os

# ---------------------------------------------------------------------------
# CONFIG -- edit these
# ---------------------------------------------------------------------------

CHAINS = ["solana", "base", "ethereum"]

# Add specific token contract addresses you want tracked every run,
# grouped by chain. Leave empty lists to rely only on the trending/boosted
# discovery feed.
WATCHLIST = {
    "solana": [
        # "So11111111111111111111111111111111111111112",
    ],
    "base": [],
    "ethereum": [],
}

MIN_LIQUIDITY_USD = 5000      # filter out near-dead pools
MIN_AGE_MINUTES = 10          # filter out pools that literally just launched
OUTPUT_FILE = "report.html"

# Optional: paid X (Twitter) API bearer token. Leave as None to skip.
X_BEARER_TOKEN = None  # os.environ.get("X_BEARER_TOKEN")

HEADERS = {"User-Agent": "meme-screener/1.0"}
BASE_URL = "https://api.dexscreener.com"


# ---------------------------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------------------------

def get_boosted_tokens():
    """Tokens whose team is currently paying DexScreener to promote them.
    This is a marketing-spend signal, NOT a quality signal -- treat high
    boost spend as 'actively trying to get attention', which cuts both
    ways (real project marketing, or pump prep)."""
    try:
        r = requests.get(f"{BASE_URL}/token-boosts/latest/v1", headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        print(f"[warn] boosted tokens fetch failed: {e}")
        return []


def get_new_profiles():
    try:
        r = requests.get(f"{BASE_URL}/token-profiles/latest/v1", headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        print(f"[warn] new profiles fetch failed: {e}")
        return []


def get_token_pairs(chain_id, token_address):
    """Full pair/market data for one token address on one chain."""
    try:
        url = f"{BASE_URL}/latest/dex/tokens/{token_address}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        pairs = data.get("pairs") or []
        return [p for p in pairs if p.get("chainId") == chain_id]
    except Exception as e:
        print(f"[warn] pair fetch failed for {token_address}: {e}")
        return []


# ---------------------------------------------------------------------------
# OPTIONAL: RugCheck (Solana holder concentration) -- stub, disabled by default
# ---------------------------------------------------------------------------

def get_rugcheck_summary(token_address):
    """Uncomment/wire this up if you want holder-concentration data for
    Solana tokens. https://api.rugcheck.xyz/v1/tokens/{address}/report/summary
    Returns None by default since it's an extra dependency/rate limit."""
    return None


# ---------------------------------------------------------------------------
# OPTIONAL: X / Twitter mention velocity -- requires a PAID bearer token
# ---------------------------------------------------------------------------

def get_x_mentions(symbol):
    if not X_BEARER_TOKEN:
        return None
    try:
        headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
        url = "https://api.twitter.com/2/tweets/counts/recent"
        params = {"query": f"${symbol} -is:retweet"}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        counts = r.json().get("data", [])
        return sum(c.get("tweet_count", 0) for c in counts)
    except Exception as e:
        print(f"[warn] X fetch failed for {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# SCORING
# ---------------------------------------------------------------------------

def score_pair(pair):
    """Returns (score 0-100, list_of_reasons, risk_flags)."""
    reasons = []
    flags = []
    score = 0.0

    liquidity = (pair.get("liquidity") or {}).get("usd") or 0
    fdv = pair.get("fdv") or 0
    volume_24h = (pair.get("volume") or {}).get("h24") or 0
    txns_24h = pair.get("txns", {}).get("h24", {})
    buys = txns_24h.get("buys", 0)
    sells = txns_24h.get("sells", 0)
    price_change_24h = (pair.get("priceChange") or {}).get("h24") or 0
    created_at = pair.get("pairCreatedAt")

    # --- Liquidity (0-25 pts) ---
    if liquidity >= 200_000:
        score += 25; reasons.append("Strong liquidity (>$200k)")
    elif liquidity >= 50_000:
        score += 18; reasons.append("Decent liquidity ($50k-$200k)")
    elif liquidity >= MIN_LIQUIDITY_USD:
        score += 8; reasons.append("Thin liquidity -- high slippage risk")
        flags.append("Low liquidity")
    else:
        flags.append("Very low liquidity -- likely can't exit cleanly")

    # --- Volume / liquidity ratio (0-20 pts) : real trading vs dead pool ---
    if liquidity > 0:
        vol_liq_ratio = volume_24h / liquidity
        if vol_liq_ratio >= 3:
            score += 20; reasons.append("Very high turnover -- active trading")
        elif vol_liq_ratio >= 1:
            score += 12; reasons.append("Healthy turnover")
        elif vol_liq_ratio >= 0.2:
            score += 5
        else:
            flags.append("Very low turnover -- may be dead/illiquid")

    # --- Buy/sell pressure (0-20 pts) ---
    total_txns = buys + sells
    if total_txns >= 20:
        buy_ratio = buys / total_txns if total_txns else 0
        if buy_ratio >= 0.65:
            score += 20; reasons.append(f"Buy-heavy ({buys}/{total_txns} txns)")
        elif buy_ratio >= 0.5:
            score += 10; reasons.append("Balanced buy/sell")
        else:
            flags.append(f"Sell-heavy ({sells}/{total_txns} txns) -- momentum may be fading")
    else:
        flags.append("Very few transactions -- limited data")

    # --- Age (0-20 pts): survival is itself a filter against instant rugs ---
    age_minutes = None
    if created_at:
        age_minutes = (time.time() * 1000 - created_at) / 60000
        if age_minutes < MIN_AGE_MINUTES:
            flags.append("Extremely new (<10 min) -- highest rug risk window")
        elif age_minutes < 60:
            score += 5; reasons.append("Under 1 hour old -- still very early/risky")
        elif age_minutes < 60 * 24:
            score += 12; reasons.append("Under 24h old")
        elif age_minutes < 60 * 24 * 7:
            score += 18; reasons.append("Survived its first week")
        else:
            score += 20; reasons.append("Established (>1 week)")

    # --- FDV sanity check (0-15 pts): absurd FDV vs liquidity = red flag ---
    if fdv and liquidity:
        fdv_liq_ratio = fdv / liquidity
        if fdv_liq_ratio <= 20:
            score += 15; reasons.append("FDV reasonable relative to liquidity")
        elif fdv_liq_ratio <= 100:
            score += 7
        else:
            flags.append("FDV wildly high vs liquidity -- thin exit for the market cap size")

    return round(min(score, 100), 1), reasons, flags, {
        "liquidity": liquidity, "fdv": fdv, "volume_24h": volume_24h,
        "buys": buys, "sells": sells, "price_change_24h": price_change_24h,
        "age_minutes": age_minutes,
    }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def collect_candidates():
    seen = set()
    candidates = []  # list of (chainId, tokenAddress, symbol_hint)

    for item in get_boosted_tokens():
        key = (item.get("chainId"), item.get("tokenAddress"))
        if key not in seen and key[0] in CHAINS:
            seen.add(key)
            candidates.append(key)

    for item in get_new_profiles():
        key = (item.get("chainId"), item.get("tokenAddress"))
        if key not in seen and key[0] in CHAINS:
            seen.add(key)
            candidates.append(key)

    for chain, addrs in WATCHLIST.items():
        for addr in addrs:
            key = (chain, addr)
            if key not in seen:
                seen.add(key)
                candidates.append(key)

    return candidates


def build_report():
    candidates = collect_candidates()
    print(f"Found {len(candidates)} candidate tokens across {CHAINS}. Fetching market data...")

    results = []
    for chain_id, token_address in candidates:
        pairs = get_token_pairs(chain_id, token_address)
        for pair in pairs:
            liq = (pair.get("liquidity") or {}).get("usd") or 0
            if liq < MIN_LIQUIDITY_USD:
                continue
            score, reasons, flags, metrics = score_pair(pair)
            results.append({
                "chain": chain_id,
                "symbol": pair.get("baseToken", {}).get("symbol", "?"),
                "name": pair.get("baseToken", {}).get("name", "?"),
                "address": token_address,
                "url": pair.get("url", "#"),
                "score": score,
                "reasons": reasons,
                "flags": flags,
                "metrics": metrics,
            })
        time.sleep(0.05)  # stay well under the 300/min rate limit

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def render_html(results):
    rows = []
    for r in results:
        m = r["metrics"]
        flag_html = "".join(f'<span class="flag">{f}</span>' for f in r["flags"])
        reason_html = "".join(f'<span class="reason">{x}</span>' for x in r["reasons"])
        score_class = "good" if r["score"] >= 60 else ("mid" if r["score"] >= 35 else "bad")
        rows.append(f"""
        <tr>
          <td class="score {score_class}">{r['score']}</td>
          <td><a href="{r['url']}" target="_blank">{r['symbol']}</a><br><small>{r['name']}</small></td>
          <td>{r['chain']}</td>
          <td>${m['liquidity']:,.0f}</td>
          <td>${m['volume_24h']:,.0f}</td>
          <td>{m['buys']}/{m['sells']}</td>
          <td>{m['price_change_24h']:.1f}%</td>
          <td>{'' if m['age_minutes'] is None else f"{m['age_minutes']/60:.1f}h"}</td>
          <td>{reason_html}</td>
          <td>{flag_html}</td>
        </tr>""")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Meme Coin Screener</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  h1 {{ margin-bottom:4px; }}
  .subtitle {{ color:#9aa0a6; margin-bottom:20px; font-size:14px; }}
  .warning {{ background:#2a1f0f; border:1px solid #7a5c1e; padding:12px 16px; border-radius:8px; margin-bottom:20px; font-size:14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; padding:8px; border-bottom:2px solid #333; color:#9aa0a6; position:sticky; top:0; background:#0f1115; }}
  td {{ padding:8px; border-bottom:1px solid #23262b; vertical-align:top; }}
  a {{ color:#7dc4ff; text-decoration:none; }}
  .score {{ font-weight:bold; font-size:16px; border-radius:6px; text-align:center; }}
  .score.good {{ color:#4ade80; }}
  .score.mid {{ color:#facc15; }}
  .score.bad {{ color:#f87171; }}
  .reason {{ display:block; color:#8fd19e; font-size:11px; }}
  .flag {{ display:block; color:#f87171; font-size:11px; }}
  tr:hover {{ background:#181b20; }}
</style></head>
<body>
  <h1>Meme Coin Momentum/Risk Screener</h1>
  <div class="subtitle">Generated {now} &middot; Chains: {', '.join(CHAINS)} &middot; {len(results)} tokens above ${MIN_LIQUIDITY_USD:,} liquidity</div>
  <div class="warning">
    This is a risk/momentum filter built from public liquidity, volume and
    transaction data -- it is <b>not</b> a prediction of which coins will pump.
    High score = fewer obvious red flags, not a buy signal. No holder-concentration
    or contract-safety data is included here (check RugCheck/Birdeye separately
    before buying anything). Most tokens here will still lose value. Re-run this
    periodically for fresh snapshots -- data goes stale within minutes.
  </div>
  <table>
    <tr><th>Score</th><th>Token</th><th>Chain</th><th>Liquidity</th><th>Vol 24h</th><th>Buys/Sells</th><th>24h %</th><th>Age</th><th>Notes</th><th>Flags</th></tr>
    {''.join(rows)}
  </table>
</body></html>"""
    return html


if __name__ == "__main__":
    results = build_report()
    html = render_html(results)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)
    print(f"Wrote {len(results)} scored tokens to {OUTPUT_FILE}")
    try:
        webbrowser.open("file://" + os.path.abspath(OUTPUT_FILE))
    except Exception:
        pass
