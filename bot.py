#!/usr/bin/env python3
"""
Solana wallet alert bot for strategy v2.
Monitors wallet 5G6n... and sends Telegram PASS/WATCH alerts when a token matches conditions.

No private keys. Read-only monitoring only.
"""
from __future__ import annotations

import html
import json
import os
import time
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
ENV_FILE = ROOT / ".env"

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4vFYCKGZkHvUXXzTDWc"
QUOTE_MINTS = {WSOL, USDC, USDT}

DEFAULT_WALLET = "5G6nFdugA2D5qGP4zr6b2DzPKwCTuP22gSEAhykWpLF"


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def getenv_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def getenv_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def getenv_int(key: str, default: int) -> int:
    try:
        return int(float(os.environ.get(key, str(default))))
    except Exception:
        return default


def getenv_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except Exception:
        return default


load_dotenv()

CONFIG = {
    "telegram_bot_token": getenv_str("TELEGRAM_BOT_TOKEN"),
    "telegram_chat_id": getenv_str("TELEGRAM_CHAT_ID"),
    "watch_wallet": getenv_str("WATCH_WALLET", DEFAULT_WALLET),
    "rpc_urls": [x.strip() for x in getenv_str("RPC_URLS", "https://api.mainnet.solana.com,https://api.mainnet-beta.solana.com,https://solana-rpc.publicnode.com").split(",") if x.strip()],
    "poll_seconds": getenv_int("POLL_SECONDS", 20),
    "signature_limit": getenv_int("SIGNATURE_LIMIT", 50),
    "bootstrap_skip_history": getenv_bool("BOOTSTRAP_SKIP_HISTORY", True),
    "agg_window_seconds": getenv_int("AGG_WINDOW_SECONDS", 900),
    "min_buy_count": getenv_int("MIN_BUY_COUNT", 2),
    "min_total_buy_usd": getenv_float("MIN_TOTAL_BUY_USD", 50),
    "max_price_above_entry_pct": getenv_float("MAX_PRICE_ABOVE_ENTRY_PCT", 25),
    "max_price_below_entry_pct": getenv_float("MAX_PRICE_BELOW_ENTRY_PCT", -25),
    "max_sell_pct_for_pass": getenv_float("MAX_SELL_PCT_FOR_PASS", 25),
    "min_liquidity_usd": getenv_float("MIN_LIQUIDITY_USD", 2000),
    "preferred_liquidity_usd": getenv_float("PREFERRED_LIQUIDITY_USD", 10000),
    "min_buys_5m": getenv_int("MIN_BUYS_5M", 3),
    "min_volume_5m_usd": getenv_float("MIN_VOLUME_5M_USD", 0),
    "require_rugcheck_for_pass": getenv_bool("REQUIRE_RUGCHECK_FOR_PASS", False),
    "send_watch": getenv_bool("SEND_WATCH", False),
    "send_no_entry": getenv_bool("SEND_NO_ENTRY", False),
    "position_size_normal_pct": getenv_float("POSITION_SIZE_NORMAL_PCT", 0.5),
    "position_size_strong_pct": getenv_float("POSITION_SIZE_STRONG_PCT", 1.0),
    "stop_loss_pct": getenv_float("STOP_LOSS_PCT", 30),
    "hard_stop_loss_pct": getenv_float("HARD_STOP_LOSS_PCT", 35),
    "tp1_pct": getenv_float("TP1_PCT", 50),
    "tp2_pct": getenv_float("TP2_PCT", 100),
    "tp3_min_pct": getenv_float("TP3_MIN_PCT", 150),
    "tp3_max_pct": getenv_float("TP3_MAX_PCT", 200),
    "trailing_stop_min_pct": getenv_float("TRAILING_STOP_MIN_PCT", 25),
    "trailing_stop_max_pct": getenv_float("TRAILING_STOP_MAX_PCT", 35),
    # For GitHub Actions / cron mode: run one scan and exit instead of staying alive.
    "run_once": getenv_bool("RUN_ONCE", False),
    "max_cycles": getenv_int("MAX_CYCLES", 0),
}

SESSION = requests.Session()


def now_ts() -> int:
    return int(time.time())


def short_addr(a: str) -> str:
    return a[:6] + "..." + a[-4:] if len(a) > 14 else a


def fmt_usd(x: Optional[float]) -> str:
    if x is None or not math.isfinite(float(x)):
        return "?"
    if abs(x) >= 1000:
        return f"${x:,.0f}"
    return f"${x:,.2f}"


def fmt_pct(x: Optional[float]) -> str:
    if x is None or not math.isfinite(float(x)):
        return "?"
    return f"{x:+.1f}%"


def fmt_num(x: Optional[float], digits: int = 6) -> str:
    if x is None or not math.isfinite(float(x)):
        return "?"
    if x == 0:
        return "0"
    if abs(x) >= 1:
        return f"{x:,.{min(4, digits)}f}".rstrip("0").rstrip(".")
    return f"{x:.{digits}g}"


def duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h"
    return f"{seconds/86400:.1f}d"


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = {
            "seen_signatures": [],
            "candidates": {},
            "alerts_sent": {},
            "created_at": now_ts(),
        }
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text()))
            except Exception as e:
                print(f"[WARN] Could not read state: {e}")

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)

    @property
    def seen(self) -> set:
        return set(self.data.get("seen_signatures", []))

    def mark_seen(self, sig: str) -> None:
        arr = self.data.setdefault("seen_signatures", [])
        if sig not in arr:
            arr.append(sig)
        # cap state size
        if len(arr) > 5000:
            del arr[:-3000]

    def get_candidate(self, mint: str) -> Dict[str, Any]:
        return self.data.setdefault("candidates", {}).setdefault(mint, {})

    def set_candidate(self, mint: str, cand: Dict[str, Any]) -> None:
        self.data.setdefault("candidates", {})[mint] = cand

    def alert_key_sent(self, mint: str, rating: str) -> bool:
        return f"{mint}:{rating}" in self.data.setdefault("alerts_sent", {})

    def mark_alert_sent(self, mint: str, rating: str) -> None:
        self.data.setdefault("alerts_sent", {})[f"{mint}:{rating}"] = now_ts()


class SolanaRPC:
    def __init__(self, urls: List[str]):
        if not urls:
            raise ValueError("No RPC_URLS configured")
        self.urls = urls
        self.idx = 0

    def call(self, method: str, params: list, timeout: int = 30) -> Any:
        last_err = None
        for attempt in range(max(3, len(self.urls) * 2)):
            url = self.urls[self.idx % len(self.urls)]
            self.idx += 1
            try:
                r = SESSION.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}: {r.text[:120]}"
                    time.sleep(min(2 + attempt, 8))
                    continue
                j = r.json()
                if "error" in j:
                    last_err = str(j["error"])
                    if "Too many" in last_err or "429" in last_err:
                        time.sleep(min(2 + attempt, 10))
                        continue
                    raise RuntimeError(last_err)
                return j.get("result")
            except Exception as e:
                last_err = repr(e)
                time.sleep(min(1 + attempt, 8))
        raise RuntimeError(f"RPC {method} failed: {last_err}")

    def get_signatures(self, address: str, limit: int) -> List[Dict[str, Any]]:
        return self.call("getSignaturesForAddress", [address, {"limit": limit}], timeout=30) or []

    def get_transaction(self, sig: str) -> Optional[Dict[str, Any]]:
        params = [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        return self.call("getTransaction", params, timeout=45)


class PriceCache:
    def __init__(self):
        self.sol_usd: Optional[float] = None
        self.last = 0

    def get_sol_usd(self) -> float:
        if self.sol_usd and now_ts() - self.last < 60:
            return self.sol_usd
        # Jupiter first, CoinGecko fallback
        try:
            r = SESSION.get(f"https://lite-api.jup.ag/price/v3?ids={WSOL}", timeout=10)
            self.sol_usd = float(r.json()[WSOL]["usdPrice"])
            self.last = now_ts()
            return self.sol_usd
        except Exception:
            pass
        try:
            r = SESSION.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=10)
            self.sol_usd = float(r.json()["solana"]["usd"])
            self.last = now_ts()
            return self.sol_usd
        except Exception:
            return self.sol_usd or 0.0


PRICE = PriceCache()


def token_balance_map(items: List[Dict[str, Any]], owner: str) -> Tuple[Dict[str, float], Dict[str, int]]:
    out: Dict[str, float] = {}
    raw: Dict[str, int] = {}
    for b in items or []:
        if b.get("owner") != owner:
            continue
        mint = b.get("mint")
        ui = b.get("uiTokenAmount") or {}
        decimals = int(ui.get("decimals") or 0)
        amount_raw = int(ui.get("amount") or 0)
        val = amount_raw / (10 ** decimals) if decimals >= 0 else float(ui.get("uiAmount") or 0)
        out[mint] = out.get(mint, 0.0) + val
        raw[mint] = raw.get(mint, 0) + amount_raw
    return out, raw


def native_delta_sol(tx: Dict[str, Any], owner: str) -> float:
    msg = tx.get("transaction", {}).get("message", {})
    keys = msg.get("accountKeys") or []
    pubs = []
    for k in keys:
        if isinstance(k, str):
            pubs.append(k)
        elif isinstance(k, dict):
            pubs.append(k.get("pubkey"))
    if owner not in pubs:
        return 0.0
    idx = pubs.index(owner)
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if idx >= len(pre) or idx >= len(post):
        return 0.0
    return (post[idx] - pre[idx]) / 1e9


def parse_wallet_events(tx: Dict[str, Any], owner: str) -> List[Dict[str, Any]]:
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return []
    pre, _ = token_balance_map(meta.get("preTokenBalances") or [], owner)
    post, _ = token_balance_map(meta.get("postTokenBalances") or [], owner)
    mints = set(pre) | set(post)
    deltas = {m: post.get(m, 0.0) - pre.get(m, 0.0) for m in mints}
    native_sol = native_delta_sol(tx, owner)
    sol_delta = native_sol + deltas.get(WSOL, 0.0)
    stable_delta = deltas.get(USDC, 0.0) + deltas.get(USDT, 0.0)
    nonquote = {m: v for m, v in deltas.items() if m not in QUOTE_MINTS and abs(v) > 1e-18}
    sig = (tx.get("transaction", {}).get("signatures") or [None])[0]
    t = tx.get("blockTime") or now_ts()
    events = []
    sol_usd = PRICE.get_sol_usd()
    for mint, td in nonquote.items():
        typ = None
        quote_amt = 0.0
        quote = "SOL"
        usd = 0.0
        if td > 0 and sol_delta < -1e-7:
            typ = "BUY"
            quote_amt = -sol_delta
            quote = "SOL"
            usd = quote_amt * sol_usd
        elif td < 0 and sol_delta > 1e-7:
            typ = "SELL"
            quote_amt = sol_delta
            quote = "SOL"
            usd = quote_amt * sol_usd
        elif td > 0 and stable_delta < -0.01:
            typ = "BUY"
            quote_amt = -stable_delta
            quote = "USD"
            usd = quote_amt
        elif td < 0 and stable_delta > 0.01:
            typ = "SELL"
            quote_amt = stable_delta
            quote = "USD"
            usd = quote_amt
        if typ:
            events.append({
                "type": typ,
                "mint": mint,
                "token_delta": td,
                "quote": quote,
                "quote_amt": quote_amt,
                "usd": usd,
                "time": t,
                "sig": sig,
                "sol_delta": sol_delta,
                "stable_delta": stable_delta,
            })
    return events


class TokenIntel:
    def __init__(self):
        self.dex_cache: Dict[str, Tuple[int, Dict[str, Any]]] = {}
        self.rug_cache: Dict[str, Tuple[int, Dict[str, Any]]] = {}

    def dex(self, mint: str) -> Dict[str, Any]:
        cached = self.dex_cache.get(mint)
        if cached and now_ts() - cached[0] < 30:
            return cached[1]
        result: Dict[str, Any] = {"mint": mint, "ok": False}
        try:
            url = f"https://api.dexscreener.com/tokens/v1/solana/{mint}"
            r = SESSION.get(url, timeout=15)
            if r.status_code == 200:
                pairs = r.json() or []
                if pairs:
                    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
                    base = p.get("baseToken") or {}
                    result.update({
                        "ok": True,
                        "symbol": base.get("symbol") or mint[:4],
                        "name": base.get("name") or "",
                        "price_usd": float(p.get("priceUsd") or 0),
                        "liquidity_usd": float((p.get("liquidity") or {}).get("usd") or 0),
                        "pair_created_at_ms": p.get("pairCreatedAt"),
                        "url": p.get("url"),
                        "volume": p.get("volume") or {},
                        "txns": p.get("txns") or {},
                        "price_change": p.get("priceChange") or {},
                        "market_cap": p.get("marketCap"),
                        "fdv": p.get("fdv"),
                    })
        except Exception as e:
            result["error"] = str(e)
        self.dex_cache[mint] = (now_ts(), result)
        return result

    def rugcheck(self, mint: str) -> Dict[str, Any]:
        cached = self.rug_cache.get(mint)
        if cached and now_ts() - cached[0] < 120:
            return cached[1]
        result: Dict[str, Any] = {"ok": False, "fatal": False, "warnings": [], "dangers": []}
        # Try summary first, full report second for holders if needed.
        try:
            url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
            r = SESSION.get(url, timeout=15)
            if r.status_code == 200:
                j = r.json()
                risks = j.get("risks") or []
                dangers = [x for x in risks if (x.get("level") or "").lower() == "danger"]
                warns = [x for x in risks if (x.get("level") or "").lower() == "warn"]
                result.update({
                    "ok": True,
                    "score_normalised": j.get("score_normalised"),
                    "lp_locked_pct": j.get("lpLockedPct"),
                    "risks": risks,
                    "warnings": [x.get("name") for x in warns],
                    "dangers": [x.get("name") for x in dangers],
                    "fatal": bool(dangers),
                })
        except Exception as e:
            result["error"] = str(e)
        self.rug_cache[mint] = (now_ts(), result)
        return result


INTEL = TokenIntel()


def get_candidate_state(state: State, mint: str) -> Dict[str, Any]:
    cand = state.get_candidate(mint)
    if not cand:
        cand = {
            "mint": mint,
            "first_buy_time": None,
            "last_event_time": None,
            "buy_count": 0,
            "sell_count": 0,
            "total_buy_usd": 0.0,
            "total_tokens_bought": 0.0,
            "largest_buy_usd": 0.0,
            "sold_tokens": 0.0,
            "sold_usd": 0.0,
            "buy_sigs": [],
            "sell_sigs": [],
            "created_at": now_ts(),
            "last_rating": None,
        }
        state.set_candidate(mint, cand)
    return cand


def process_event(state: State, ev: Dict[str, Any]) -> None:
    mint = ev["mint"]
    cand = get_candidate_state(state, mint)
    if ev["type"] == "BUY":
        if cand["first_buy_time"] is None:
            cand["first_buy_time"] = ev["time"]
        cand["last_event_time"] = ev["time"]
        cand["buy_count"] += 1
        cand["total_buy_usd"] += max(0.0, ev.get("usd") or 0.0)
        cand["total_tokens_bought"] += max(0.0, ev.get("token_delta") or 0.0)
        cand["largest_buy_usd"] = max(cand.get("largest_buy_usd", 0.0), ev.get("usd") or 0.0)
        if ev.get("sig") not in cand["buy_sigs"]:
            cand["buy_sigs"].append(ev.get("sig"))
    elif ev["type"] == "SELL":
        cand["last_event_time"] = ev["time"]
        cand["sell_count"] += 1
        cand["sold_tokens"] += abs(ev.get("token_delta") or 0.0)
        cand["sold_usd"] += max(0.0, ev.get("usd") or 0.0)
        if ev.get("sig") not in cand["sell_sigs"]:
            cand["sell_sigs"].append(ev.get("sig"))
    state.set_candidate(mint, cand)


def evaluate_candidate(cand: Dict[str, Any]) -> Dict[str, Any]:
    mint = cand["mint"]
    dex = INTEL.dex(mint)
    rug = INTEL.rugcheck(mint)

    total_buy_usd = float(cand.get("total_buy_usd") or 0)
    token_qty = float(cand.get("total_tokens_bought") or 0)
    avg_entry = total_buy_usd / token_qty if token_qty > 0 else None
    price = dex.get("price_usd") if dex.get("ok") else None
    price_vs_entry = ((price / avg_entry - 1) * 100) if price and avg_entry else None

    first_buy = cand.get("first_buy_time")
    pair_created = dex.get("pair_created_at_ms")
    age_sec = None
    if first_buy and pair_created:
        age_sec = first_buy - (pair_created / 1000)

    sold_pct = 0.0
    if token_qty > 0:
        sold_pct = (float(cand.get("sold_tokens") or 0) / token_qty) * 100

    vol = dex.get("volume") or {}
    txns = dex.get("txns") or {}
    m5 = txns.get("m5") or {}
    buys_5m = int(m5.get("buys") or 0)
    sells_5m = int(m5.get("sells") or 0)
    vol_5m = float(vol.get("m5") or 0)
    liq = float(dex.get("liquidity_usd") or 0)

    score = 0
    reasons: List[str] = []
    fatal: List[str] = []

    # Age
    if age_sec is None:
        reasons.append("Token age unavailable")
        score -= 15
    elif age_sec < 0:
        reasons.append("Pair created after buy? data mismatch")
        score -= 15
    elif age_sec <= 180:
        score += 20
        reasons.append(f"Very new token age {duration(age_sec)}")
    elif age_sec <= 600:
        score += 15
        reasons.append(f"New token age {duration(age_sec)}")
    elif age_sec <= 1800:
        score += 5
        reasons.append(f"Age acceptable but late {duration(age_sec)}")
    else:
        reasons.append(f"Old token age {duration(age_sec)}")
        score -= 20

    # Buy count and size
    bc = int(cand.get("buy_count") or 0)
    if bc >= 3:
        score += 20
        reasons.append(f"Repeated wallet buys: {bc}")
    elif bc >= CONFIG["min_buy_count"]:
        score += 15
        reasons.append(f"Wallet bought {bc} times")
    else:
        reasons.append(f"Only {bc} buy(s)")

    if total_buy_usd >= 100:
        score += 20
        reasons.append(f"Wallet total buy {fmt_usd(total_buy_usd)}")
    elif total_buy_usd >= CONFIG["min_total_buy_usd"]:
        score += 15
        reasons.append(f"Wallet total buy {fmt_usd(total_buy_usd)}")
    else:
        reasons.append(f"Buy size too small {fmt_usd(total_buy_usd)}")

    # Price vs entry
    if price_vs_entry is None:
        reasons.append("Current price unavailable")
        score -= 15
    elif price_vs_entry > 50:
        fatal.append(f"Price is too late: {fmt_pct(price_vs_entry)} above wallet entry")
        score -= 40
    elif price_vs_entry > CONFIG["max_price_above_entry_pct"]:
        reasons.append(f"Late entry: {fmt_pct(price_vs_entry)} above wallet entry")
        score -= 20
    elif price_vs_entry < CONFIG["max_price_below_entry_pct"]:
        reasons.append(f"Price is weak: {fmt_pct(price_vs_entry)} vs wallet entry")
        score -= 15
    else:
        score += 15
        reasons.append(f"Price close to wallet entry: {fmt_pct(price_vs_entry)}")

    # Sold by wallet
    if sold_pct >= 99:
        fatal.append("Wallet sold full/near-full position")
    elif sold_pct > CONFIG["max_sell_pct_for_pass"]:
        fatal.append(f"Wallet sold too much: {sold_pct:.1f}%")
        score -= 50
    elif sold_pct >= 10:
        reasons.append(f"Wallet sold partial: {sold_pct:.1f}%")
        score -= 15
    else:
        score += 10
        reasons.append("Wallet has not sold meaningful amount")

    # Liquidity
    if liq >= CONFIG["preferred_liquidity_usd"]:
        score += 10
        reasons.append(f"Good liquidity {fmt_usd(liq)}")
    elif liq >= CONFIG["min_liquidity_usd"]:
        score += 5
        reasons.append(f"Minimum liquidity {fmt_usd(liq)}")
    else:
        fatal.append(f"Liquidity too low {fmt_usd(liq)}")
        score -= 30

    # Volume / buyers
    if buys_5m >= CONFIG["min_buys_5m"] or vol_5m >= CONFIG["min_volume_5m_usd"] > 0:
        score += 10
        reasons.append(f"Active 5m flow: buys={buys_5m}, sells={sells_5m}, vol={fmt_usd(vol_5m)}")
    else:
        reasons.append(f"Weak 5m flow: buys={buys_5m}, sells={sells_5m}, vol={fmt_usd(vol_5m)}")
        score -= 10

    # RugCheck
    if rug.get("ok"):
        if rug.get("fatal"):
            fatal.append("RugCheck danger: " + ", ".join(rug.get("dangers") or []))
            score -= 60
        else:
            score += 10
            if rug.get("warnings"):
                reasons.append("RugCheck warnings: " + ", ".join(rug.get("warnings")[:3]))
            else:
                reasons.append("RugCheck: no danger")
    else:
        reasons.append("RugCheck unavailable")
        score -= 15
        if CONFIG["require_rugcheck_for_pass"]:
            fatal.append("RugCheck required but unavailable")

    # Basic thresholds as fatal for PASS
    if bc < CONFIG["min_buy_count"]:
        fatal.append("Not enough wallet buys")
    if total_buy_usd < CONFIG["min_total_buy_usd"]:
        fatal.append("Wallet buy amount below threshold")
    if age_sec is None:
        fatal.append("Token age missing")
    elif age_sec > 600:
        fatal.append("Token older than 10m for PASS")

    rating = "NO ENTRY"
    if not fatal and score >= 75:
        rating = "PASS"
    elif score >= 55 and not any("sold full" in x.lower() for x in fatal):
        rating = "WATCH"
    else:
        rating = "NO ENTRY"

    return {
        "mint": mint,
        "score": score,
        "rating": rating,
        "fatal": fatal,
        "reasons": reasons,
        "dex": dex,
        "rug": rug,
        "avg_entry": avg_entry,
        "price": price,
        "price_vs_entry": price_vs_entry,
        "age_sec": age_sec,
        "sold_pct": sold_pct,
        "liq": liq,
        "vol_5m": vol_5m,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
    }


def build_message(cand: Dict[str, Any], ev: Dict[str, Any]) -> str:
    dex = ev["dex"]
    rug = ev["rug"]
    rating = ev["rating"]
    symbol = dex.get("symbol") or cand["mint"][:6]
    url = dex.get("url") or f"https://dexscreener.com/solana/{cand['mint']}"
    rug_url = f"https://rugcheck.xyz/tokens/{cand['mint']}"
    avg = ev.get("avg_entry")
    price = ev.get("price")

    entry_low = avg
    entry_high = avg * 1.25 if avg else None
    sl = avg * (1 - CONFIG["stop_loss_pct"] / 100) if avg else None
    hard_sl = avg * (1 - CONFIG["hard_stop_loss_pct"] / 100) if avg else None
    tp1 = avg * (1 + CONFIG["tp1_pct"] / 100) if avg else None
    tp2 = avg * (1 + CONFIG["tp2_pct"] / 100) if avg else None
    tp3a = avg * (1 + CONFIG["tp3_min_pct"] / 100) if avg else None
    tp3b = avg * (1 + CONFIG["tp3_max_pct"] / 100) if avg else None

    pos_size = CONFIG["position_size_strong_pct"] if ev["score"] >= 85 else CONFIG["position_size_normal_pct"]

    emoji = "🚨" if rating == "PASS" else ("👀" if rating == "WATCH" else "❌")
    title = f"{emoji} {rating} — Solana Wallet Strategy v2"

    sigs = cand.get("buy_sigs") or []
    last_sig = sigs[-1] if sigs else ""
    solscan_tx = f"https://solscan.io/tx/{last_sig}" if last_sig else ""

    lines = [
        f"<b>{html.escape(title)}</b>",
        "",
        f"<b>Token:</b> {html.escape(symbol)}",
        f"<b>CA:</b> <code>{html.escape(cand['mint'])}</code>",
        f"<b>Dex:</b> {html.escape(url)}",
        f"<b>RugCheck:</b> {html.escape(rug_url)}",
        f"<b>Last tx:</b> {html.escape(solscan_tx)}" if solscan_tx else "",
        "",
        f"<b>Age:</b> {duration(ev.get('age_sec'))}",
        f"<b>Wallet buys:</b> {cand.get('buy_count')} buys",
        f"<b>Wallet total buy:</b> {fmt_usd(cand.get('total_buy_usd'))}",
        f"<b>Largest buy:</b> {fmt_usd(cand.get('largest_buy_usd'))}",
        f"<b>Wallet avg entry:</b> {fmt_num(avg, 10)}",
        f"<b>Current price:</b> {fmt_num(price, 10)}",
        f"<b>Price vs wallet:</b> {fmt_pct(ev.get('price_vs_entry'))}",
        f"<b>Wallet sold:</b> {ev.get('sold_pct', 0):.1f}%",
        "",
        f"<b>Liquidity:</b> {fmt_usd(ev.get('liq'))}",
        f"<b>Volume 5m:</b> {fmt_usd(ev.get('vol_5m'))}",
        f"<b>Buys/Sells 5m:</b> {ev.get('buys_5m')}/{ev.get('sells_5m')}",
        f"<b>RugCheck:</b> {'OK' if rug.get('ok') and not rug.get('fatal') else 'CHECK'}",
        f"<b>Rug warnings:</b> {html.escape(', '.join(rug.get('warnings') or [])[:250]) if rug.get('warnings') else '-'}",
        "",
        f"<b>Score:</b> {ev.get('score')}",
        f"<b>Entry zone:</b> {fmt_num(entry_low, 10)} → {fmt_num(entry_high, 10)}",
        f"<b>Stop loss:</b> {fmt_num(sl, 10)} (-{CONFIG['stop_loss_pct']}%) | hard {fmt_num(hard_sl, 10)}",
        f"<b>TP1:</b> {fmt_num(tp1, 10)} (+{CONFIG['tp1_pct']}%) sell 30%",
        f"<b>TP2:</b> {fmt_num(tp2, 10)} (+{CONFIG['tp2_pct']}%) sell 30%",
        f"<b>TP3:</b> {fmt_num(tp3a, 10)}–{fmt_num(tp3b, 10)} sell 25%",
        f"<b>Moonbag:</b> 15% if momentum continues",
        f"<b>Position size:</b> {pos_size}% capital max",
        "",
        "<b>Reasons:</b>",
    ]
    for r in ev.get("reasons", [])[:8]:
        lines.append("• " + html.escape(str(r)))
    if ev.get("fatal"):
        lines.append("<b>Fatal / blockers:</b>")
        for f in ev.get("fatal", [])[:6]:
            lines.append("• " + html.escape(str(f)))
    return "\n".join([x for x in lines if x != ""])


def send_telegram(text: str) -> bool:
    token = CONFIG["telegram_bot_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or "PUT_YOUR" in token or not chat_id or "PUT_YOUR" in chat_id:
        print("\n[DRY RUN TELEGRAM MESSAGE]\n" + text.replace("<b>", "").replace("</b>", ""))
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = SESSION.post(url, json=payload, timeout=15)
    if r.status_code != 200:
        print(f"[Telegram error] {r.status_code}: {r.text}")
        return False
    return True


def maybe_alert(state: State, cand: Dict[str, Any], eval_result: Dict[str, Any]) -> None:
    rating = eval_result["rating"]
    mint = cand["mint"]
    if rating == "PASS":
        if state.alert_key_sent(mint, "PASS"):
            return
        msg = build_message(cand, eval_result)
        send_telegram(msg)
        state.mark_alert_sent(mint, "PASS")
    elif rating == "WATCH" and CONFIG["send_watch"]:
        if state.alert_key_sent(mint, "WATCH"):
            return
        msg = build_message(cand, eval_result)
        send_telegram(msg)
        state.mark_alert_sent(mint, "WATCH")
    elif rating == "NO ENTRY" and CONFIG["send_no_entry"]:
        if state.alert_key_sent(mint, "NO ENTRY"):
            return
        msg = build_message(cand, eval_result)
        send_telegram(msg)
        state.mark_alert_sent(mint, "NO ENTRY")


def cleanup_candidates(state: State) -> None:
    # Keep candidates for a day. Remove dead old ones with no alert.
    keep: Dict[str, Any] = {}
    for mint, cand in state.data.get("candidates", {}).items():
        first = cand.get("first_buy_time") or cand.get("created_at") or now_ts()
        if now_ts() - first < 86400:
            keep[mint] = cand
        elif state.alert_key_sent(mint, "PASS"):
            keep[mint] = cand
    state.data["candidates"] = keep


def main() -> None:
    print("Starting Solana Wallet Alert Bot")
    print("Wallet:", CONFIG["watch_wallet"])
    print("RPCs:", CONFIG["rpc_urls"])
    print("Telegram:", "configured" if CONFIG["telegram_bot_token"] and CONFIG["telegram_chat_id"] else "DRY RUN / not configured")

    state = State(STATE_FILE)
    rpc = SolanaRPC(CONFIG["rpc_urls"])

    # Bootstrap: prevent old alerts on first run.
    if CONFIG["bootstrap_skip_history"] and not state.data.get("seen_signatures"):
        sigs = rpc.get_signatures(CONFIG["watch_wallet"], CONFIG["signature_limit"])
        for s in sigs:
            state.mark_seen(s["signature"])
        state.save()
        print(f"Bootstrapped {len(sigs)} existing signatures. Waiting for new buys...")

    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            sigs = rpc.get_signatures(CONFIG["watch_wallet"], CONFIG["signature_limit"])
            seen = state.seen
            new_sigs = [s for s in reversed(sigs) if s.get("signature") not in seen]
            if new_sigs:
                print(f"Found {len(new_sigs)} new signature(s)")
            for s in new_sigs:
                sig = s["signature"]
                try:
                    tx = rpc.get_transaction(sig)
                    if not tx:
                        state.mark_seen(sig)
                        continue
                    events = parse_wallet_events(tx, CONFIG["watch_wallet"])
                    for ev in events:
                        print(f"Event {ev['type']} {short_addr(ev['mint'])} {fmt_usd(ev.get('usd'))} sig={short_addr(sig)}")
                        process_event(state, ev)
                        cand = get_candidate_state(state, ev["mint"])
                        eval_result = evaluate_candidate(cand)
                        cand["last_rating"] = eval_result["rating"]
                        cand["last_score"] = eval_result["score"]
                        state.set_candidate(ev["mint"], cand)
                        maybe_alert(state, cand, eval_result)
                    state.mark_seen(sig)
                    state.save()
                except Exception as e:
                    print(f"[ERROR] processing {sig}: {e}")
                    # Mark seen only after repeated? Here we leave it unseen to retry next loop.
            # Re-evaluate active candidates because price/rug/dex data can appear after buy.
            for mint, cand in list(state.data.get("candidates", {}).items()):
                if not cand.get("first_buy_time"):
                    continue
                if now_ts() - int(cand["first_buy_time"]) > CONFIG["agg_window_seconds"] + 300:
                    continue
                eval_result = evaluate_candidate(cand)
                cand["last_rating"] = eval_result["rating"]
                cand["last_score"] = eval_result["score"]
                state.set_candidate(mint, cand)
                maybe_alert(state, cand, eval_result)
            cleanup_candidates(state)
            state.data["last_run_ts"] = now_ts()
            state.data["last_cycle_new_signatures"] = len(new_sigs)
            state.data["last_cycle_ts"] = now_ts()
            state.save()
            if CONFIG["run_once"] or (CONFIG["max_cycles"] and cycle_count >= CONFIG["max_cycles"]):
                print(f"Run-once/max-cycles mode complete after {cycle_count} cycle(s). Exiting.")
                break
        except Exception as e:
            print("[LOOP ERROR]", e)
            if CONFIG["run_once"] or (CONFIG["max_cycles"] and cycle_count >= CONFIG["max_cycles"]):
                raise
        time.sleep(CONFIG["poll_seconds"])


if __name__ == "__main__":
    main()
