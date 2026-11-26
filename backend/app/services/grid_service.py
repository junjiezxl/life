"""Stock grid-trading ledger engine: grid levels, fees, buy/sell pairing, signals and live quotes."""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

FX_TO_CNY: Dict[str, float] = {"CNY": 1.0, "HKD": 0.92, "USD": 7.25}
MARKET_CURRENCY: Dict[str, str] = {"CN": "CNY", "HK": "HKD", "US": "USD"}
_EPS = 1e-9


def build_grid_levels(lower: float, upper: float, grid_type: str, step: float) -> List[float]:
    """Arithmetic: fixed price step. Geometric: step is a percentage (e.g. 3 = +3% per grid)."""
    lower = float(lower or 0)
    upper = float(upper or 0)
    step = float(step or 0)
    if lower <= 0 or upper <= lower or step <= 0:
        return []
    levels: List[float] = []
    if grid_type == "geometric":
        ratio = 1.0 + step / 100.0
        price = lower
        while price <= upper * (1 + _EPS) and len(levels) < 500:
            levels.append(round(price, 4))
            price *= ratio
    else:
        n = 0
        while len(levels) < 500:
            price = lower + step * n
            if price > upper + _EPS:
                break
            levels.append(round(price, 4))
            n += 1
    return levels


def nearest_level(levels: List[float], price: float) -> Optional[int]:
    if not levels or price <= 0:
        return None
    return min(range(len(levels)), key=lambda i: abs(levels[i] - price))


def calc_trade_fees(strategy: Dict[str, Any], side: str, price: float, qty: float) -> Tuple[float, float]:
    amount = float(price) * float(qty)
    if amount <= 0:
        return 0.0, 0.0
    commission = max(amount * float(strategy.get("fee_rate") or 0), float(strategy.get("min_fee") or 0))
    tax_side = (strategy.get("stamp_tax_side") or "sell").lower()
    tax = amount * float(strategy.get("stamp_tax_rate") or 0) if (side == "sell" or tax_side == "both") else 0.0
    return round(commission, 2), round(tax, 2)


def _parse_dt(raw: str) -> Optional[datetime]:
    s = (raw or "").replace("T", " ").strip()
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        if len(s) >= n:
            try:
                return datetime.strptime(s[:n], fmt)
            except ValueError:
                continue
    return None


def compute_strategy_state(strategy: Dict[str, Any], trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Replay trades chronologically and pair each sell against open buy lots in grid style.

    Pairing rule: a sell closes the open lot with the highest buy price below the sell price
    (i.e. the grid one step below). If no lot is below, it closes the cheapest lot (stop-loss / rebalance).
    """
    levels = build_grid_levels(
        strategy.get("lower_price") or 0,
        strategy.get("upper_price") or 0,
        strategy.get("grid_type") or "arithmetic",
        strategy.get("step_value") or 0,
    )
    cur = float(strategy.get("current_price") or 0)
    spg = float(strategy.get("shares_per_grid") or 0)
    currency = strategy.get("currency") or MARKET_CURRENCY.get(strategy.get("market") or "CN", "CNY")

    ordered = sorted(trades, key=lambda t: (str(t.get("traded_at") or ""), int(t.get("id") or 0)))
    open_lots: List[Dict[str, Any]] = []
    pairs: List[Dict[str, Any]] = []
    info: Dict[int, Dict[str, Any]] = {}

    total_fees = 0.0
    buy_amount = 0.0
    sell_amount = 0.0
    realized = 0.0
    net_invested = 0.0
    peak_capital = 0.0
    completed_sells = 0

    for t in ordered:
        tid = int(t["id"])
        qty = float(t["quantity"])
        price = float(t["price"])
        fee = float(t.get("fee") or 0)
        tax = float(t.get("tax") or 0)
        total_fees += fee + tax
        entry = {"remaining_qty": 0.0, "matched_qty": 0.0, "unmatched_qty": 0.0, "realized_profit": 0.0}

        if t["side"] == "buy":
            lvl = t.get("grid_level")
            if lvl is None:
                lvl = nearest_level(levels, price)
            entry["grid_level"] = lvl
            open_lots.append(
                {
                    "trade_id": tid,
                    "price": price,
                    "cost_ps": (price * qty + fee + tax) / qty,
                    "qty": qty,
                    "orig_qty": qty,
                    "level": lvl,
                    "traded_at": t.get("traded_at") or "",
                }
            )
            buy_amount += price * qty
            net_invested += price * qty + fee + tax
        else:
            net_ps = (price * qty - fee - tax) / qty
            sell_amount += price * qty
            net_invested -= price * qty - fee - tax
            remaining = qty
            sell_dt = _parse_dt(t.get("traded_at") or "")
            while remaining > _EPS and open_lots:
                below = [lot for lot in open_lots if lot["price"] < price - _EPS]
                if below:
                    lot = max(below, key=lambda x: (x["price"], x["traded_at"]))
                else:
                    lot = min(open_lots, key=lambda x: x["price"])
                take = min(remaining, lot["qty"])
                profit = take * (net_ps - lot["cost_ps"])
                buy_dt = _parse_dt(lot["traded_at"])
                pairs.append(
                    {
                        "buy_trade_id": lot["trade_id"],
                        "sell_trade_id": tid,
                        "quantity": round(take, 4),
                        "buy_price": lot["price"],
                        "sell_price": price,
                        "buy_level": lot["level"],
                        "buy_at": lot["traded_at"],
                        "sell_at": t.get("traded_at") or "",
                        "holding_days": (sell_dt - buy_dt).days if (sell_dt and buy_dt) else None,
                        "profit": round(profit, 2),
                        "profit_pct": round((net_ps / lot["cost_ps"] - 1) * 100, 2) if lot["cost_ps"] > 0 else None,
                    }
                )
                realized += profit
                entry["realized_profit"] += profit
                entry["matched_qty"] += take
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] <= _EPS:
                    open_lots.remove(lot)
            entry["unmatched_qty"] = round(max(0.0, remaining), 4)
            if entry["matched_qty"] > _EPS:
                completed_sells += 1

        peak_capital = max(peak_capital, net_invested)
        info[tid] = entry

    for lot in open_lots:
        info[lot["trade_id"]]["remaining_qty"] += lot["qty"]

    enriched_trades: List[Dict[str, Any]] = []
    for t in sorted(trades, key=lambda x: (str(x.get("traded_at") or ""), int(x.get("id") or 0)), reverse=True):
        e = info.get(int(t["id"]), {})
        row = dict(t)
        qty = float(t["quantity"])
        row["amount"] = round(float(t["price"]) * qty, 2)
        if t["side"] == "buy":
            rem = round(e.get("remaining_qty", 0.0), 4)
            row["remaining_qty"] = rem
            row["grid_level"] = e.get("grid_level", t.get("grid_level"))
            row["pair_status"] = "open" if rem >= qty - _EPS else ("closed" if rem <= _EPS else "partial")
            row["sell_target"] = (
                levels[row["grid_level"] + 1]
                if (row["grid_level"] is not None and row["grid_level"] + 1 < len(levels))
                else None
            )
        else:
            row["realized_profit"] = round(e.get("realized_profit", 0.0), 2)
            row["unmatched_qty"] = e.get("unmatched_qty", 0.0)
            row["pair_status"] = "unmatched" if e.get("unmatched_qty", 0.0) > _EPS else "paired"
        enriched_trades.append(row)

    holding_qty = sum(lot["qty"] for lot in open_lots)
    holding_cost = sum(lot["qty"] * lot["cost_ps"] for lot in open_lots)
    avg_cost = holding_cost / holding_qty if holding_qty > _EPS else 0.0
    market_value = holding_qty * cur if cur > 0 else 0.0
    unrealized = (market_value - holding_cost) if (cur > 0 and holding_qty > _EPS) else 0.0
    total_pnl = realized + unrealized

    # Grid ladder
    level_holdings: Dict[int, float] = {}
    for lot in open_lots:
        if lot["level"] is not None:
            level_holdings[lot["level"]] = level_holdings.get(lot["level"], 0.0) + lot["qty"]
    cur_idx = nearest_level(levels, cur) if cur > 0 else None
    level_rows = [
        {
            "index": i,
            "price": p,
            "sell_target": levels[i + 1] if i + 1 < len(levels) else None,
            "holding_qty": round(level_holdings.get(i, 0.0), 4),
            "is_current": i == cur_idx,
            "distance_pct": round((p - cur) / cur * 100, 2) if cur > 0 else None,
        }
        for i, p in enumerate(levels)
    ]

    # Signals: classic LIFO grid -> next buy one grid below the lowest held lot; next sell one grid above it
    next_buy: Optional[Dict[str, Any]] = None
    next_sell: Optional[Dict[str, Any]] = None
    if levels:
        held = sorted(level_holdings.keys())
        buy_idx: Optional[int] = None
        if held:
            lowest = held[0]
            buy_idx = lowest - 1 if lowest - 1 >= 0 else None
            if lowest + 1 < len(levels):
                next_sell = {
                    "level": lowest + 1,
                    "price": levels[lowest + 1],
                    "quantity": round(level_holdings[lowest], 4),
                    "from_level": lowest,
                }
        else:
            ref = cur if cur > 0 else float(strategy.get("base_price") or 0)
            if ref > 0:
                below_ref = [i for i, p in enumerate(levels) if p <= ref + _EPS]
                buy_idx = max(below_ref) if below_ref else None
        if buy_idx is not None:
            next_buy = {"level": buy_idx, "price": levels[buy_idx], "quantity": spg}

    buy_triggered = bool(cur > 0 and next_buy and cur <= next_buy["price"] + _EPS)
    sell_triggered = bool(cur > 0 and next_sell and cur >= next_sell["price"] - _EPS)
    hints: List[str] = []
    if cur > 0 and levels:
        if cur < levels[0] - _EPS:
            hints.append("现价已跌破网格下沿，建议暂停买入或下移网格区间")
        elif cur > levels[-1] + _EPS:
            hints.append("现价已突破网格上沿，网格可能已卖空，可考虑上移区间")
        if buy_triggered and next_buy:
            crossed = [i for i in range(next_buy["level"], -1, -1) if levels[i] >= cur - _EPS and level_holdings.get(i, 0) <= _EPS]
            n = max(1, len(crossed))
            hints.append(f"现价 {cur:g} 已触及买点 {next_buy['price']:g}" + (f"，共跨越 {n} 格可分批买入" if n > 1 else "，可买入一格"))
        if sell_triggered and next_sell:
            hints.append(f"现价 {cur:g} 已触及卖点 {next_sell['price']:g}，可卖出 {next_sell['quantity']:g} 股完成一格")
    elif not levels:
        hints.append("尚未设置网格区间与步长，当前仅作为普通股票持仓记账")

    full_grid_capital = round(sum(p * spg for p in levels[:-1]), 2) if len(levels) > 1 else 0.0
    if levels and len(levels) > 1:
        if (strategy.get("grid_type") or "arithmetic") == "geometric":
            grid_profit_pct = float(strategy.get("step_value") or 0)
        else:
            grid_profit_pct = round(float(strategy.get("step_value") or 0) / levels[len(levels) // 2] * 100, 2)
    else:
        grid_profit_pct = 0.0

    fx = FX_TO_CNY.get(currency, 1.0)
    summary = {
        "currency": currency,
        "grid_count": max(0, len(levels) - 1),
        "level_count": len(levels),
        "held_grid_count": len([k for k, v in level_holdings.items() if v > _EPS]),
        "holding_qty": round(holding_qty, 4),
        "holding_cost": round(holding_cost, 2),
        "avg_cost": round(avg_cost, 4),
        "market_value": round(market_value, 2),
        "unrealized_pnl": round(unrealized, 2),
        "unrealized_pct": round(unrealized / holding_cost * 100, 2) if holding_cost > _EPS else 0.0,
        "realized_pnl": round(realized, 2),
        "total_pnl": round(total_pnl, 2),
        "total_fees": round(total_fees, 2),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "trade_count": len(trades),
        "completed_grids": completed_sells,
        "pair_count": len(pairs),
        "peak_capital": round(peak_capital, 2),
        "return_on_peak_pct": round(total_pnl / peak_capital * 100, 2) if peak_capital > _EPS else 0.0,
        "full_grid_capital": full_grid_capital,
        "grid_profit_pct": grid_profit_pct,
        "fx_to_cny": fx,
        "realized_pnl_cny": round(realized * fx, 2),
        "unrealized_pnl_cny": round(unrealized * fx, 2),
        "market_value_cny": round(market_value * fx, 2),
        "day_change_pct": (
            round((cur / float(strategy.get("prev_close")) - 1) * 100, 2)
            if cur > 0 and float(strategy.get("prev_close") or 0) > 0
            else None
        ),
    }

    return {
        "summary": summary,
        "levels": level_rows,
        "trades": enriched_trades,
        "pairs": list(reversed(pairs)),
        "open_lots": [
            {**lot, "qty": round(lot["qty"], 4), "cost_ps": round(lot["cost_ps"], 4)}
            for lot in sorted(open_lots, key=lambda x: -x["price"])
        ],
        "signals": {
            "next_buy": next_buy,
            "next_sell": next_sell,
            "buy_triggered": buy_triggered,
            "sell_triggered": sell_triggered,
            "hints": hints,
        },
    }


# ---------------- Live quotes (Tencent qt.gtimg.cn: A-shares / ETFs / HK / US) ----------------


def to_tencent_code(symbol: str, market: str) -> str:
    s = (symbol or "").strip().upper().replace(".", "")
    mk = (market or "CN").upper()
    if mk == "HK":
        digits = re.sub(r"\D", "", s)
        return "hk" + digits.zfill(5)
    if mk == "US":
        return "us" + s.replace("US", "", 1) if s.startswith("US") and len(s) > 2 else "us" + s
    if s[:2] in ("SH", "SZ", "BJ"):
        return s[:2].lower() + s[2:]
    if s.endswith(("SH", "SZ", "BJ")):
        return s[-2:].lower() + s[:-2]
    first = s[:1]
    if first in ("6", "5", "9"):
        return "sh" + s
    if first in ("4", "8"):
        return "bj" + s
    return "sz" + s


def parse_tencent_quote_text(text: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for m in re.finditer(r'v_([A-Za-z0-9_.]+)="([^"]*)"', text or ""):
        code = m.group(1)
        fields = m.group(2).split("~")
        if len(fields) < 5:
            continue
        try:
            price = float(fields[3] or 0)
            prev_close = float(fields[4] or 0)
        except ValueError:
            continue
        if price <= 0:
            continue
        result[code] = {
            "name": fields[1].strip(),
            "price": price,
            "prev_close": prev_close,
            "change_pct": round((price / prev_close - 1) * 100, 2) if prev_close > 0 else None,
        }
    return result


async def fetch_tencent_quotes(items: List[Tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
    """items: [(symbol, market)] -> {tencent_code: quote}"""
    codes = sorted({to_tencent_code(sym, mk) for sym, mk in items if sym})
    if not codes:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
        resp = await client.get(url, headers={"Referer": "https://gu.qq.com/"})
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")
    return parse_tencent_quote_text(text)


# ---------------- IBKR Flex -> Stock Grid Sync ----------------


def infer_market_from_ibkr(symbol: str, currency: str) -> str:
    ccy = (currency or "").strip().upper()
    if ccy == "HKD":
        return "HK"
    if ccy in ("CNY", "CNH"):
        return "CN"
    if ccy == "USD":
        return "US"
    s = (symbol or "").strip().upper()
    if s.isdigit():
        return "HK" if len(s) <= 5 else "CN"
    return "US"


def canonical_symbol_key(symbol: str, market: str) -> Tuple[str, str]:
    """Return normalized (market, symbol_key) so 3152 / 03152 / 3152.HK match cleanly."""
    mk = (market or "US").strip().upper()
    s = (symbol or "").strip().upper()
    s = re.sub(r"\.(HK|US|SS|SZ|SH|BJ)$", "", s)
    if mk == "HK":
        digits = re.sub(r"\D", "", s)
        return ("HK", digits.lstrip("0") or "0") if digits else ("HK", s)
    if mk == "CN":
        s = re.sub(r"^(SH|SZ|BJ)", "", s)
        return ("CN", s)
    return ("US", s)


def format_display_symbol(symbol: str, market: str) -> str:
    mk, key = canonical_symbol_key(symbol, market)
    if mk == "HK" and key.isdigit():
        return key.zfill(5)
    return key


def _ibkr_fee_defaults(market: str) -> Tuple[float, float, float, str]:
    if market == "HK":
        return (0.0003, 3.0, 0.001, "both")
    if market == "CN":
        return (0.00025, 5.0, 0.0005, "sell")
    return (0.0, 1.0, 0.0, "sell")


def list_ibkr_grid_candidates(conn: Any) -> List[Dict[str, Any]]:
    """Aggregate STK positions & trades from IBKR Flex tables and compare with existing grid strategies."""
    strats = [dict(r) for r in conn.execute("SELECT * FROM grid_strategies ORDER BY id ASC").fetchall()]
    strat_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for s in strats:
        k = canonical_symbol_key(s["symbol"], s["market"])
        strat_by_key.setdefault(k, s)

    ext_refs_by_strat: Dict[int, set] = {}
    for r in conn.execute("SELECT strategy_id, external_ref FROM stock_trades WHERE external_ref != ''").fetchall():
        ext_refs_by_strat.setdefault(int(r["strategy_id"]), set()).add(str(r["external_ref"]))

    candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}

    # 1. Open positions (STK)
    pos_rows = conn.execute(
        "SELECT * FROM ibkr_positions WHERE UPPER(COALESCE(asset_category, 'STK')) IN ('STK', 'ETF', '') ORDER BY ABS(position_value_base) DESC"
    ).fetchall()
    for pr in pos_rows:
        p = dict(pr)
        sym = (p.get("symbol") or "").strip()
        if not sym:
            continue
        mk = infer_market_from_ibkr(sym, p.get("currency") or "")
        key = canonical_symbol_key(sym, mk)
        entry = candidates.setdefault(
            key,
            {
                "symbol": format_display_symbol(sym, mk),
                "raw_symbol": sym,
                "market": mk,
                "currency": (p.get("currency") or MARKET_CURRENCY.get(mk, "USD")).upper(),
                "description": (p.get("description") or "").strip(),
                "position_qty": 0.0,
                "mark_price": 0.0,
                "avg_cost": 0.0,
                "report_date": p.get("report_date") or "",
                "trade_count": 0,
                "unimported_trade_count": 0,
                "latest_trade_price": 0.0,
                "latest_trade_qty": 0.0,
            },
        )
        entry["position_qty"] = round(entry["position_qty"] + float(p.get("position") or 0), 4)
        if float(p.get("mark_price") or 0) > 0:
            entry["mark_price"] = float(p["mark_price"])
        if float(p.get("open_price") or 0) > 0 and entry["avg_cost"] <= 0:
            entry["avg_cost"] = float(p["open_price"])
        if not entry["description"] and p.get("description"):
            entry["description"] = str(p["description"]).strip()

    # 2. Executed trades (STK)
    tr_rows = conn.execute(
        "SELECT * FROM ibkr_trades WHERE UPPER(COALESCE(asset_category, 'STK')) IN ('STK', 'ETF', '') ORDER BY executed_at DESC, trade_date DESC"
    ).fetchall()
    for tr in tr_rows:
        t = dict(tr)
        sym = (t.get("symbol") or "").strip()
        if not sym:
            continue
        mk = infer_market_from_ibkr(sym, t.get("currency") or "")
        key = canonical_symbol_key(sym, mk)
        entry = candidates.setdefault(
            key,
            {
                "symbol": format_display_symbol(sym, mk),
                "raw_symbol": sym,
                "market": mk,
                "currency": (t.get("currency") or MARKET_CURRENCY.get(mk, "USD")).upper(),
                "description": (t.get("description") or "").strip(),
                "position_qty": 0.0,
                "mark_price": 0.0,
                "avg_cost": 0.0,
                "report_date": t.get("report_date") or "",
                "trade_count": 0,
                "unimported_trade_count": 0,
                "latest_trade_price": 0.0,
                "latest_trade_qty": 0.0,
            },
        )
        entry["trade_count"] += 1
        if entry["latest_trade_price"] <= 0 and float(t.get("trade_price") or 0) > 0:
            entry["latest_trade_price"] = float(t["trade_price"])
            entry["latest_trade_qty"] = abs(float(t.get("quantity") or 0))
        if not entry["description"] and t.get("description"):
            entry["description"] = str(t["description"]).strip()

        linked = strat_by_key.get(key)
        ext_ref = f"ibkr:{t['trade_key']}"
        if linked:
            if ext_ref not in ext_refs_by_strat.get(int(linked["id"]), set()):
                entry["unimported_trade_count"] += 1
        else:
            entry["unimported_trade_count"] += 1

    out: List[Dict[str, Any]] = []
    for key, entry in candidates.items():
        linked = strat_by_key.get(key)
        entry["linked_strategy_id"] = int(linked["id"]) if linked else None
        entry["linked_strategy_name"] = linked["name"] if linked else ""
        if entry["mark_price"] <= 0 and entry["latest_trade_price"] > 0:
            entry["mark_price"] = entry["latest_trade_price"]
        out.append(entry)

    out.sort(key=lambda x: (-x["unimported_trade_count"], -abs(x["position_qty"]), -x["trade_count"], x["symbol"]))
    return out


def sync_ibkr_to_grids(
    conn: Any,
    strategy_id: Optional[int] = None,
    create_symbols: Optional[List[str]] = None,
    default_step_pct: float = 3.0,
) -> Dict[str, Any]:
    """Sync IBKR Flex STK trades & mark prices into matching grid strategies (deduplicated by ibkr:<trade_key>)."""
    from ..db import now_iso

    ts = now_iso()
    created_strategies: List[Dict[str, Any]] = []

    # Step 1: Optionally auto-create strategies for user-selected IBKR symbols
    if create_symbols:
        wanted = {s.strip().upper() for s in create_symbols if s and s.strip()}
        cands = list_ibkr_grid_candidates(conn)
        for c in cands:
            if c["linked_strategy_id"] is not None:
                continue
            cand_matches = (
                c["symbol"].upper() in wanted
                or c["raw_symbol"].upper() in wanted
                or canonical_symbol_key(c["symbol"], c["market"])[1].upper() in wanted
            )
            if not cand_matches:
                continue
            ref_p = float(c["mark_price"] or c["latest_trade_price"] or c["avg_cost"] or 0)
            low_p = round(ref_p * 0.80, 4) if ref_p > 0 else 0.0
            high_p = round(ref_p * 1.25, 4) if ref_p > 0 else 0.0
            shares = float(c["latest_trade_qty"] or (abs(c["position_qty"]) if abs(c["position_qty"]) > 0 else 100.0))
            fee_rate, min_fee, tax_rate, tax_side = _ibkr_fee_defaults(c["market"])
            name = (c["description"] or c["symbol"])[:40]
            cur = conn.execute(
                """INSERT INTO grid_strategies
                (name, symbol, market, currency, grid_type, lower_price, upper_price, step_value, shares_per_grid,
                 base_price, fee_rate, min_fee, stamp_tax_rate, stamp_tax_side, current_price, price_updated_at,
                 ibkr_auto_sync, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'geometric', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?)""",
                (
                    name,
                    c["symbol"],
                    c["market"],
                    c["currency"],
                    low_p,
                    high_p,
                    round(float(default_step_pct or 3.0), 2),
                    round(shares, 4),
                    ref_p,
                    fee_rate,
                    min_fee,
                    tax_rate,
                    tax_side,
                    ref_p,
                    ts if ref_p > 0 else "",
                    f"从 IBKR 自动创建（原代码 {c['raw_symbol']}），可按需调整网格上下沿与步长",
                    ts,
                    ts,
                ),
            )
            created_strategies.append(dict(conn.execute("SELECT * FROM grid_strategies WHERE id = ?", (cur.lastrowid,)).fetchone()))

    # Step 2: Load target grid strategies
    if strategy_id is not None:
        strat_rows = conn.execute("SELECT * FROM grid_strategies WHERE id = ?", (strategy_id,)).fetchall()
    else:
        strat_rows = conn.execute(
            "SELECT * FROM grid_strategies WHERE status != 'closed' AND COALESCE(ibkr_auto_sync, 1) = 1 ORDER BY id ASC"
        ).fetchall()
    strategies = [dict(r) for r in strat_rows]

    if not strategies:
        return {
            "created_strategies": created_strategies,
            "imported_trades": 0,
            "skipped_duplicates": 0,
            "updated_prices": 0,
            "matched_strategies": 0,
        }

    # Index IBKR STK trades & positions by canonical (market, symbol)
    ibkr_trades_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in conn.execute(
        "SELECT * FROM ibkr_trades WHERE UPPER(COALESCE(asset_category, 'STK')) IN ('STK', 'ETF', '') ORDER BY executed_at ASC, trade_date ASC, trade_key ASC"
    ).fetchall():
        t = dict(r)
        sym = (t.get("symbol") or "").strip()
        if not sym:
            continue
        mk = infer_market_from_ibkr(sym, t.get("currency") or "")
        key = canonical_symbol_key(sym, mk)
        ibkr_trades_by_key.setdefault(key, []).append(t)

    ibkr_pos_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT * FROM ibkr_positions WHERE UPPER(COALESCE(asset_category, 'STK')) IN ('STK', 'ETF', '') ORDER BY report_date DESC"
    ).fetchall():
        p = dict(r)
        sym = (p.get("symbol") or "").strip()
        if not sym:
            continue
        mk = infer_market_from_ibkr(sym, p.get("currency") or "")
        key = canonical_symbol_key(sym, mk)
        if key not in ibkr_pos_by_key and float(p.get("mark_price") or 0) > 0:
            ibkr_pos_by_key[key] = p

    imported_trades = 0
    skipped_duplicates = 0
    updated_prices = 0
    matched_strategies = 0

    for strat in strategies:
        sid = int(strat["id"])
        key = canonical_symbol_key(strat["symbol"], strat["market"])
        matching_trades = ibkr_trades_by_key.get(key, [])
        matching_pos = ibkr_pos_by_key.get(key)

        if not matching_trades and not matching_pos:
            continue
        matched_strategies += 1

        existing_refs = {
            str(r["external_ref"])
            for r in conn.execute(
                "SELECT external_ref FROM stock_trades WHERE strategy_id = ? AND external_ref != ''",
                (sid,),
            ).fetchall()
        }
        levels = build_grid_levels(
            strat.get("lower_price") or 0,
            strat.get("upper_price") or 0,
            strat.get("grid_type") or "arithmetic",
            strat.get("step_value") or 0,
        )

        latest_tr_price = 0.0
        for tr in matching_trades:
            ext_ref = f"ibkr:{tr['trade_key']}"
            if ext_ref in existing_refs:
                skipped_duplicates += 1
                continue

            qty = abs(float(tr.get("quantity") or 0))
            price = float(tr.get("trade_price") or 0)
            if qty <= 0 or price <= 0:
                continue

            raw_side = (tr.get("side") or "").strip().upper()
            if raw_side in ("BUY", "B", "BOT"):
                side = "buy"
            elif raw_side in ("SELL", "S", "SLD"):
                side = "sell"
            else:
                side = "buy" if float(tr.get("quantity") or 0) > 0 else "sell"

            fee = round(abs(float(tr.get("commission") or 0)), 2)
            executed = (tr.get("executed_at") or tr.get("trade_date") or "").strip()
            if len(executed) == 10:
                executed = f"{executed} 16:00"
            elif len(executed) >= 16:
                executed = executed[:16]
            else:
                executed = ts[:16].replace("T", " ")

            lvl = nearest_level(levels, price)
            note = f"IBKR 同步 ({tr.get('account_id') or ''})".strip()
            conn.execute(
                """INSERT INTO stock_trades
                (strategy_id, side, price, quantity, fee, tax, grid_level, traded_at, source_type, external_ref, note, created_at)
                VALUES (?, ?, ?, ?, ?, 0.0, ?, ?, 'ibkr', ?, ?, ?)""",
                (sid, side, round(price, 4), round(qty, 4), fee, lvl, executed, ext_ref, note, ts),
            )
            existing_refs.add(ext_ref)
            imported_trades += 1
            latest_tr_price = price

        # Sync IBKR mark_price as reference price when current_price is missing or not fresher
        ref_price = float(matching_pos["mark_price"]) if matching_pos else latest_tr_price
        if ref_price > 0 and (float(strat.get("current_price") or 0) <= 0 or matching_pos is not None):
            # Only overwrite existing current_price if it was 0 or quote_name is empty (don't clobber fresh live quote!)
            if float(strat.get("current_price") or 0) <= 0 or not (strat.get("quote_name") or "").strip():
                conn.execute(
                    "UPDATE grid_strategies SET current_price = ?, price_updated_at = ?, updated_at = ? WHERE id = ?",
                    (round(ref_price, 4), ts, ts, sid),
                )
                updated_prices += 1

    return {
        "created_strategies": created_strategies,
        "imported_trades": imported_trades,
        "skipped_duplicates": skipped_duplicates,
        "updated_prices": updated_prices,
        "matched_strategies": matched_strategies,
    }
