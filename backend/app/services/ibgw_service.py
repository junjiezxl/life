"""Interactive Brokers IB Gateway (ib_async) connection manager & Auto-Grid engine for LifeHub.

Replaces IBKR Flex polling & email stock-trade parsing with direct Gateway TCP integration:
- Live Account NAV / Equity snapshots -> ibkr_equity_snapshots
- Live Positions & Mark Prices -> ibkr_positions
- Live Executions / Fills -> ibkr_trades -> grid_service.sync_ibkr_to_grids -> stock_trades
- Auto-Grid resting LimitOrder management (idempotent orderRef, Paper-only safety guard,
  HKEX/US tick-size & lot-size rounding, real-time fill & disconnect Bark/MeoW alerts)
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from ib_async import IB, LimitOrder, Stock  # type: ignore
except Exception:  # pragma: no cover - fallback only if ib_async missing at import
    IB = None  # type: ignore
    LimitOrder = None  # type: ignore
    Stock = None  # type: ignore

from ..db import get_all_settings, get_conn, now_iso
from .bark_service import has_any_push_channel, send_bark_push
from .grid_service import (
    canonical_symbol_key,
    compute_strategy_state,
    sync_ibkr_to_grids,
)
from .ibkr_service import get_ibkr_overview

logger = logging.getLogger("lifehub.ibgw")

_TERMINAL_ORDER_STATUSES = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}

_gw_lock = asyncio.Lock()
_ib_factory: Optional[Callable[[], Any]] = None
_ib_client: Optional[Any] = None

_gw_state: Dict[str, Any] = {
    "connected": False,
    "connecting": False,
    "host": "127.0.0.1",
    "port": 4002,
    "client_id": 18,
    "trading_mode": "paper",
    "managed_accounts": [],
    "account_id": "",
    "server_version": None,
    "last_connect_at": "",
    "last_sync_at": "",
    "last_order_sync_at": "",
    "last_error": None,
    "phase": "idle",
    "last_result": None,
    "disconnect_alert_sent": False,
}


def set_ib_factory_for_test(factory: Optional[Callable[[], Any]]) -> None:
    """Allow unit tests to inject a deterministic mock IB client factory."""
    global _ib_factory, _ib_client
    _ib_factory = factory
    _ib_client = None


def reset_gateway_state_for_test() -> None:
    global _ib_client
    _ib_client = None
    _gw_state.update(
        {
            "connected": False,
            "connecting": False,
            "host": "127.0.0.1",
            "port": 4002,
            "client_id": 18,
            "trading_mode": "paper",
            "managed_accounts": [],
            "account_id": "",
            "server_version": None,
            "last_connect_at": "",
            "last_sync_at": "",
            "last_order_sync_at": "",
            "last_error": None,
            "phase": "idle",
            "last_result": None,
            "disconnect_alert_sent": False,
        }
    )


def _create_ib_instance() -> Any:
    if _ib_factory is not None:
        return _ib_factory()
    if IB is None:
        raise RuntimeError("ib_async 未安装，请执行 pip install ib_async")
    return IB()


def _parse_int(val: Any, default: int) -> int:
    try:
        return int(str(val or "").strip())
    except (TypeError, ValueError):
        return default


def effective_trading_mode(cfg: Dict[str, str], accounts: Optional[List[str]] = None) -> str:
    port = _parse_int(cfg.get("ibgw_port"), 4002)
    cfg_mode = (cfg.get("ibgw_trading_mode") or "paper").strip().lower()
    if port == 4001 or cfg_mode == "live":
        return "live"
    for acct in accounts or []:
        a = str(acct or "").strip().upper()
        # IBKR live accounts begin with U (or F/I); paper accounts begin with DU / DF / DI
        if a and not a.startswith(("DU", "DF", "DI")) and a[0] in ("U", "F", "I"):
            return "live"
    return "paper"


def is_gateway_connected() -> bool:
    if _ib_client is None:
        return False
    try:
        return bool(_ib_client.isConnected())
    except Exception:
        return bool(_gw_state.get("connected"))


def get_gateway_status(cfg: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    conf = cfg or get_all_settings()
    connected = is_gateway_connected()
    _gw_state["connected"] = connected
    accounts = list(_gw_state.get("managed_accounts") or [])
    mode = effective_trading_mode(conf, accounts)
    return {
        **_gw_state,
        "connected": connected,
        "enabled": conf.get("ibgw_enabled") == "1",
        "auto_grid_enabled": conf.get("ibgw_auto_grid_enabled") == "1",
        "paper_only_guard": (conf.get("ibgw_paper_only_guard") or "1") == "1",
        "auto_reconnect": (conf.get("ibgw_auto_reconnect") or "1") == "1",
        "sync_interval_sec": _parse_int(conf.get("ibgw_sync_interval_sec"), 30),
        "cfg_host": (conf.get("ibgw_host") or "127.0.0.1").strip() or "127.0.0.1",
        "cfg_port": _parse_int(conf.get("ibgw_port"), 4002),
        "cfg_client_id": _parse_int(conf.get("ibgw_client_id"), 18),
        "trading_mode": mode,
        "ib_async_installed": IB is not None or _ib_factory is not None,
    }


def assert_order_allowed(
    cfg: Dict[str, str],
    *,
    accounts: Optional[List[str]] = None,
    strategy: Optional[Dict[str, Any]] = None,
    is_manual_test: bool = False,
) -> str:
    mode = effective_trading_mode(cfg, accounts)
    paper_guard = (cfg.get("ibgw_paper_only_guard") or "1") == "1"
    if mode == "live":
        if paper_guard:
            raise RuntimeError(
                "安全保护已拦截实盘下单：系统设置开启了「仅模拟盘自动下单」锁。"
                "请先在设置中关闭保护锁（端口 4002 为模拟盘，4001/U开头账户为实盘）。"
            )
        if strategy is not None and not is_manual_test and not int(strategy.get("auto_allow_live") or 0):
            raise RuntimeError(
                f"策略 {strategy.get('symbol')} 未勾选「允许实盘自动下单」，已阻止向实盘发送委托。"
            )
    return mode


# ---------------- Market Contract, Tick & Lot Rounding ----------------


def get_tick_size(price: float, market: str, custom_tick: float = 0.0) -> float:
    if custom_tick and custom_tick > 0:
        return float(custom_tick)
    mk = (market or "US").strip().upper()
    if mk == "HK":
        if price <= 0.25:
            return 0.001
        if price <= 0.50:
            return 0.005
        if price <= 10.0:
            return 0.01
        if price <= 20.0:
            return 0.02
        if price <= 100.0:
            return 0.05
        if price <= 200.0:
            return 0.10
        if price <= 500.0:
            return 0.20
        if price <= 1000.0:
            return 0.50
        if price <= 2000.0:
            return 1.00
        return 2.00
    return 0.01


def round_price_to_tick(price: float, market: str, side: str, custom_tick: float = 0.0) -> float:
    p = max(0.0001, float(price or 0))
    tick = get_tick_size(p, market, custom_tick)
    steps = p / tick
    if (side or "buy").lower() == "buy":
        snapped = math.floor(steps + 1e-9) * tick
    else:
        snapped = math.ceil(steps - 1e-9) * tick
    snapped = max(tick, snapped)
    return round(snapped, 4)


def normalize_lot_qty(qty: float, market: str, custom_lot: float = 0.0) -> float:
    q = max(0.0, float(qty or 0))
    if custom_lot and custom_lot > 0:
        lot = float(custom_lot)
    elif (market or "US").strip().upper() == "CN":
        lot = 100.0
    else:
        lot = 1.0
    if lot <= 0:
        lot = 1.0
    units = math.floor((q + 1e-9) / lot)
    return round(units * lot, 4)


def normalize_ib_symbol(symbol: str, market: str) -> str:
    mk, key = canonical_symbol_key(symbol, market)
    if mk == "HK":
        digits = re.sub(r"\D", "", key)
        if digits:
            return str(int(digits))  # IBKR uses "3152" / "700" instead of "03152"
    return key


def build_ib_contract(
    symbol: str,
    market: str = "US",
    *,
    exchange: str = "",
    currency: str = "",
    conid: str = "",
) -> Any:
    mk = (market or "US").strip().upper()
    ib_sym = normalize_ib_symbol(symbol, mk)
    exch = (exchange or "").strip().upper()
    ccy = (currency or "").strip().upper()

    if mk == "HK":
        exch = exch or "SEHK"
        ccy = ccy or "HKD"
    elif mk == "CN":
        if not exch:
            exch = "SEHKNTL" if ib_sym.startswith(("6", "5", "9")) else "SEHKSZSE"
        ccy = ccy or "CNH"
    else:
        exch = exch or "SMART"
        ccy = ccy or "USD"

    if Stock is not None:
        c = Stock(ib_sym, exch, ccy)
        if (conid or "").strip().isdigit() and int(str(conid).strip()) > 0:
            c.conId = int(str(conid).strip())
        return c

    return {
        "secType": "STK",
        "symbol": ib_sym,
        "exchange": exch,
        "currency": ccy,
        "conId": int(conid) if (conid or "").strip().isdigit() else 0,
    }


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


async def _maybe_await(res: Any) -> Any:
    if asyncio.iscoroutine(res) or isinstance(res, asyncio.Future):
        return await res
    return res


def _schedule_disconnect_alert(title: str, body: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _send() -> None:
        try:
            cfg = get_all_settings()
            if (cfg.get("ibgw_notify_disconnect") or "1") != "1":
                return
            if not has_any_push_channel(cfg):
                return
            await send_bark_push(title=title, body=body, settings=cfg, priority="timeSensitive", group="IBKR")
        except Exception as exc:
            logger.warning("Failed to dispatch IB Gateway alert: %s", exc)

    loop.create_task(_send())


def _attach_ib_callbacks(ib: Any) -> None:
    if getattr(ib, "_lifehub_callbacks_attached", False):
        return
    setattr(ib, "_lifehub_callbacks_attached", True)

    def _on_disconnect() -> None:
        _gw_state["connected"] = False
        if not _gw_state.get("disconnect_alert_sent"):
            _gw_state["disconnect_alert_sent"] = True
            _schedule_disconnect_alert(
                "⚠️ IB Gateway 连接已断开",
                f"网关 {_gw_state.get('host')}:{_gw_state.get('port')} 已断开连接，请检查容器状态或 IB Key 二次验证。",
            )

    def _on_error(req_id: Any, error_code: Any, error_string: Any, contract: Any = None) -> None:
        code = _parse_int(error_code, 0)
        msg = str(error_string or "")
        # Non-fatal informational farm/connection status codes from IB API (2104, 2106, 2158)
        if code in (2104, 2106, 2107, 2108, 2158):
            return
        if code in (1100, 1101, 1102, 502, 504, 2103, 2105, 1300):
            _gw_state["last_error"] = f"[{code}] {msg}"
            if code in (1100, 502, 504) and not _gw_state.get("disconnect_alert_sent"):
                _gw_state["disconnect_alert_sent"] = True
                _schedule_disconnect_alert(
                    "⚠️ IB Gateway 异常告警",
                    f"错误码 {code}：{msg}",
                )

    for ev_name, handler in (("disconnectedEvent", _on_disconnect), ("errorEvent", _on_error)):
        ev = getattr(ib, ev_name, None)
        if ev is not None and hasattr(ev, "__iadd__"):
            try:
                ev += handler
            except Exception:
                pass


async def _connect_locked() -> Any:
    global _ib_client
    cfg = get_all_settings()
    host = (cfg.get("ibgw_host") or "127.0.0.1").strip() or "127.0.0.1"
    port = _parse_int(cfg.get("ibgw_port"), 4002)
    client_id = _parse_int(cfg.get("ibgw_client_id"), 18)

    if _ib_client is not None:
        try:
            if _ib_client.isConnected() and _gw_state.get("host") == host and _gw_state.get("port") == port and _gw_state.get("client_id") == client_id:
                _gw_state["connected"] = True
                return _ib_client
            if _ib_client.isConnected():
                _ib_client.disconnect()
        except Exception:
            pass

    _gw_state.update(
        {
            "connecting": True,
            "host": host,
            "port": port,
            "client_id": client_id,
            "last_error": None,
            "phase": "connecting",
        }
    )
    try:
        ib = _create_ib_instance()
        _attach_ib_callbacks(ib)
        connect_fn = getattr(ib, "connectAsync", None) or getattr(ib, "connect", None)
        if connect_fn is None:
            raise RuntimeError("IB 客户端对象缺少 connectAsync/connect 方法")
        await _maybe_await(connect_fn(host, port, clientId=client_id, timeout=12))

        accounts = [str(a).strip() for a in (ib.managedAccounts() or []) if str(a).strip()]
        preferred = (cfg.get("ibgw_account") or "").strip()
        active_acct = preferred if preferred in accounts else (accounts[0] if accounts else "")
        mode = effective_trading_mode(cfg, accounts)

        sv = None
        if hasattr(ib, "client") and getattr(ib, "client", None) is not None:
            sv_fn = getattr(ib.client, "serverVersion", None)
            if callable(sv_fn):
                sv = sv_fn()

        _ib_client = ib
        _gw_state.update(
            {
                "connected": bool(ib.isConnected()),
                "connecting": False,
                "managed_accounts": accounts,
                "account_id": active_acct,
                "trading_mode": mode,
                "server_version": sv,
                "last_connect_at": now_iso(),
                "disconnect_alert_sent": False,
                "phase": "connected",
            }
        )
        return ib
    except Exception as exc:
        err_msg = str(exc).strip() or repr(exc)
        _gw_state.update(
            {
                "connected": False,
                "connecting": False,
                "last_error": f"连接 IB Gateway ({host}:{port}) 失败：{err_msg}",
                "phase": "error",
            }
        )
        raise RuntimeError(_gw_state["last_error"]) from exc


async def connect_gateway(force: bool = False) -> Dict[str, Any]:
    global _ib_client
    async with _gw_lock:
        if force and _ib_client is not None:
            try:
                _ib_client.disconnect()
            except Exception:
                pass
            _ib_client = None
        await _connect_locked()
        return get_gateway_status()


async def disconnect_gateway() -> Dict[str, Any]:
    global _ib_client
    async with _gw_lock:
        if _ib_client is not None:
            try:
                _ib_client.disconnect()
            except Exception:
                pass
            _ib_client = None
        _gw_state.update(
            {
                "connected": False,
                "connecting": False,
                "phase": "idle",
                "disconnect_alert_sent": True,
            }
        )
        return get_gateway_status()


async def test_gateway_connection() -> Dict[str, Any]:
    async with _gw_lock:
        ib = await _connect_locked()
        accounts = [str(a) for a in (ib.managedAccounts() or []) if a]
        summary_rows = []
        if hasattr(ib, "accountSummaryAsync"):
            summary_rows = await _maybe_await(ib.accountSummaryAsync()) or []
        nav = 0.0
        ccy = "USD"
        for item in summary_rows:
            if getattr(item, "tag", "") == "NetLiquidation":
                nav = _safe_float(getattr(item, "value", 0))
                ccy = str(getattr(item, "currency", "") or "USD")
                break
        mode = _gw_state.get("trading_mode", "paper")
        mode_cn = "实盘 (Live)" if mode == "live" else "模拟盘 (Paper)"
        acct_str = ", ".join(accounts) or "--"
        nav_str = f"，当前净值 {ccy} {nav:,.2f}" if nav > 0 else ""
        return {
            "ok": True,
            "accounts": accounts,
            "trading_mode": mode,
            "gateway_status": get_gateway_status(),
            "message": f"IB Gateway 连接成功 [{mode_cn}]（账户：{acct_str}{nav_str}）",
        }


# ---------------- Snapshot & Orders DB Persistence ----------------


def _extract_order_status_name(trade: Any) -> str:
    os_obj = getattr(trade, "orderStatus", None)
    return str(getattr(os_obj, "status", "") or "").strip() or "Submitted"


def _sync_open_trades_to_db(conn: Any, open_trades: List[Any], trading_mode: str) -> int:
    ts = now_iso()
    seen_refs_and_ids: set[Tuple[int, str]] = set()
    updated = 0
    for tr in open_trades or []:
        order = getattr(tr, "order", None)
        contract = getattr(tr, "contract", None)
        status_obj = getattr(tr, "orderStatus", None)
        if not order or not contract:
            continue
        order_id = _parse_int(getattr(order, "orderId", 0), 0)
        perm_id = _parse_int(getattr(order, "permId", 0), 0)
        client_id = _parse_int(getattr(order, "clientId", 0), 0)
        order_ref = str(getattr(order, "orderRef", "") or "").strip()
        action = str(getattr(order, "action", "") or "").upper()
        side = "buy" if action == "BUY" else "sell"
        total_qty = _safe_float(getattr(order, "totalQuantity", 0))
        lmt_price = _safe_float(getattr(order, "lmtPrice", 0))
        filled_qty = _safe_float(getattr(status_obj, "filled", 0))
        remaining_qty = _safe_float(getattr(status_obj, "remaining", max(0.0, total_qty - filled_qty)))
        avg_fill = _safe_float(getattr(status_obj, "avgFillPrice", 0))
        status_name = _extract_order_status_name(tr)
        acct = str(getattr(order, "account", "") or _gw_state.get("account_id") or "").strip()
        sym = str(getattr(contract, "symbol", "") or "").strip().upper()
        exch = str(getattr(contract, "exchange", "") or "").strip().upper()
        ccy = str(getattr(contract, "currency", "") or "").strip().upper()
        mk = "HK" if (ccy == "HKD" or exch == "SEHK") else ("CN" if ccy in ("CNH", "CNY") else "US")

        strat_id: Optional[int] = None
        grid_level: Optional[int] = None
        m = re.match(r"^LH_GRID_(\d+)_(BUY|SELL)_(\d+)$", order_ref)
        if m:
            strat_id = int(m.group(1))
            grid_level = int(m.group(3))

        existing = None
        if perm_id > 0:
            existing = conn.execute("SELECT id FROM ibgw_orders WHERE perm_id = ? LIMIT 1", (perm_id,)).fetchone()
        if existing is None and order_ref:
            existing = conn.execute(
                "SELECT id FROM ibgw_orders WHERE order_ref = ? AND status NOT IN ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive') ORDER BY id DESC LIMIT 1",
                (order_ref,),
            ).fetchone()
        if existing is None and order_id > 0:
            existing = conn.execute(
                "SELECT id FROM ibgw_orders WHERE order_id = ? AND symbol = ? ORDER BY id DESC LIMIT 1",
                (order_id, sym),
            ).fetchone()

        if existing:
            conn.execute(
                """UPDATE ibgw_orders
                SET perm_id = CASE WHEN ? > 0 THEN ? ELSE perm_id END,
                    order_id = CASE WHEN ? > 0 THEN ? ELSE order_id END,
                    filled_qty = ?, remaining_qty = ?, avg_fill_price = ?,
                    lmt_price = CASE WHEN ? > 0 THEN ? ELSE lmt_price END,
                    status = ?, updated_at = ?
                WHERE id = ?""",
                (perm_id, perm_id, order_id, order_id, filled_qty, remaining_qty, avg_fill, lmt_price, lmt_price, status_name, ts, int(existing["id"])),
            )
            seen_refs_and_ids.add((int(existing["id"]), order_ref))
        else:
            cur = conn.execute(
                """INSERT INTO ibgw_orders
                (strategy_id, perm_id, order_id, client_id, order_ref, account_id, symbol, market,
                 side, action, total_qty, filled_qty, remaining_qty, lmt_price, avg_fill_price,
                 grid_level, status, trading_mode, error_msg, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
                (strat_id, perm_id, order_id, client_id, order_ref, acct, sym, mk, side, action, total_qty, filled_qty, remaining_qty, lmt_price, avg_fill, grid_level, status_name, trading_mode, ts, ts),
            )
            seen_refs_and_ids.add((int(cur.lastrowid), order_ref))
        updated += 1

    # Reconcile previously open rows no longer in IB openTrades() -> check ibkr_trades or mark Filled/Cancelled
    live_refs = {r[1] for r in seen_refs_and_ids if r[1]}
    live_ids = {r[0] for r in seen_refs_and_ids}
    active_db_rows = conn.execute(
        "SELECT * FROM ibgw_orders WHERE status NOT IN ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')"
    ).fetchall()
    for row in active_db_rows:
        rid = int(row["id"])
        ref = str(row["order_ref"] or "")
        if rid in live_ids or (ref and ref in live_refs):
            continue
        oid = str(row["order_id"] or "")
        filled_match = None
        if oid and oid != "0":
            filled_match = conn.execute(
                "SELECT SUM(ABS(quantity)) AS fq, AVG(trade_price) AS ap FROM ibkr_trades WHERE order_id = ? AND symbol = ?",
                (oid, row["symbol"]),
            ).fetchone()
        if filled_match and float(filled_match["fq"] or 0) > 0:
            conn.execute(
                "UPDATE ibgw_orders SET status = 'Filled', filled_qty = ?, remaining_qty = 0, avg_fill_price = ?, updated_at = ? WHERE id = ?",
                (float(filled_match["fq"]), round(float(filled_match["ap"] or row["lmt_price"]), 4), ts, rid),
            )
        else:
            conn.execute(
                "UPDATE ibgw_orders SET status = 'Cancelled', remaining_qty = 0, updated_at = ? WHERE id = ?",
                (ts, rid),
            )
    return updated


async def _sync_snapshot_locked(
    ib: Any,
    cfg: Dict[str, str],
    *,
    sync_grids: bool = True,
    reconcile_orders: bool = False,
    push_summary: bool = False,
) -> Dict[str, Any]:
    _gw_state.update({"phase": "syncing", "last_error": None})
    today = datetime.date.today().isoformat()
    now_str = now_iso()
    accounts = [str(a).strip() for a in (ib.managedAccounts() or []) if str(a).strip()]
    if not accounts and _gw_state.get("account_id"):
        accounts = [str(_gw_state["account_id"])]
    default_acct = accounts[0] if accounts else "DEFAULT"

    # 1) Account Summary -> ibkr_equity_snapshots
    summary_rows: List[Any] = []
    if hasattr(ib, "accountSummaryAsync"):
        summary_rows = await _maybe_await(ib.accountSummaryAsync()) or []
    elif hasattr(ib, "accountValues"):
        summary_rows = ib.accountValues() or []

    acct_metrics: Dict[str, Dict[str, Any]] = {}
    for item in summary_rows:
        acct = str(getattr(item, "account", "") or default_acct).strip()
        if not acct or acct == "All":
            continue
        tag = str(getattr(item, "tag", "") or "")
        val = _safe_float(getattr(item, "value", 0))
        ccy = str(getattr(item, "currency", "") or "USD").strip() or "USD"
        m = acct_metrics.setdefault(
            acct,
            {"currency": ccy, "total_nav": 0.0, "total_cash": 0.0, "stock_value": 0.0},
        )
        if tag == "NetLiquidation":
            m["total_nav"] = round(val, 2)
            if ccy and ccy != "BASE":
                m["currency"] = ccy
        elif tag in ("TotalCashValue", "TotalCashBalance"):
            if m["total_cash"] == 0.0 or tag == "TotalCashValue":
                m["total_cash"] = round(val, 2)
        elif tag == "GrossPositionValue":
            m["stock_value"] = round(val, 2)

    # 2) Portfolio / Positions -> ibkr_positions
    portfolio_rows: List[Any] = []
    if hasattr(ib, "portfolio"):
        portfolio_rows = ib.portfolio() or []
    if not portfolio_rows and hasattr(ib, "positions"):
        portfolio_rows = ib.positions() or []

    parsed_positions: List[Dict[str, Any]] = []
    accounts_with_pos: set[str] = set(accounts)
    for item in portfolio_rows:
        contract = getattr(item, "contract", None)
        if not contract:
            continue
        acct = str(getattr(item, "account", "") or default_acct).strip()
        accounts_with_pos.add(acct)
        qty = _safe_float(getattr(item, "position", 0))
        if abs(qty) < 1e-9:
            continue
        conid = str(getattr(contract, "conId", 0) or getattr(contract, "symbol", ""))
        sym = str(getattr(contract, "symbol", "") or "").strip().upper()
        sec_type = str(getattr(contract, "secType", "STK") or "STK").upper()
        ccy = str(getattr(contract, "currency", "USD") or "USD").upper()
        mark_price = _safe_float(getattr(item, "marketPrice", 0))
        pos_val = _safe_float(getattr(item, "marketValue", qty * mark_price))
        avg_cost = _safe_float(getattr(item, "averageCost", getattr(item, "avgCost", 0)))
        unrealized = _safe_float(getattr(item, "unrealizedPNL", 0))
        cost_basis = round(avg_cost * abs(qty), 2)
        if mark_price <= 0 and abs(qty) > 0 and pos_val != 0:
            mark_price = abs(pos_val) / abs(qty)
        parsed_positions.append(
            {
                "account_id": acct,
                "report_date": today,
                "conid": conid,
                "symbol": sym,
                "asset_category": sec_type,
                "description": str(getattr(contract, "localSymbol", "") or sym),
                "currency": ccy,
                "position": qty,
                "mark_price": round(mark_price, 4),
                "position_value": round(pos_val, 2),
                "position_value_base": round(pos_val, 2),
                "open_price": round(avg_cost, 4),
                "cost_basis": cost_basis,
                "unrealized_pnl": round(unrealized, 2),
                "unrealized_pnl_base": round(unrealized, 2),
                "side": "Long" if qty >= 0 else "Short",
                "expiry": str(getattr(contract, "lastTradeDateOrContractMonth", "") or ""),
                "strike": _safe_float(getattr(contract, "strike", 0)),
                "put_call": str(getattr(contract, "right", "") or ""),
                "multiplier": _safe_float(getattr(contract, "multiplier", 1), 1.0) or 1.0,
            }
        )

    # 3) Executions / Fills -> ibkr_trades
    fills: List[Any] = []
    if hasattr(ib, "reqExecutionsAsync"):
        fills = await _maybe_await(ib.reqExecutionsAsync()) or []
    elif hasattr(ib, "fills"):
        fills = ib.fills() or []

    parsed_trades: List[Dict[str, Any]] = []
    for fill in fills:
        contract = getattr(fill, "contract", None)
        execution = getattr(fill, "execution", None)
        comm_rep = getattr(fill, "commissionReport", None)
        if not contract or not execution:
            continue
        exec_id = str(getattr(execution, "execId", "") or "").strip()
        if not exec_id:
            continue
        acct = str(getattr(execution, "acctNumber", "") or default_acct).strip()
        sym = str(getattr(contract, "symbol", "") or "").strip().upper()
        sec_type = str(getattr(contract, "secType", "STK") or "STK").upper()
        ccy = str(getattr(contract, "currency", "USD") or "USD").upper()
        raw_side = str(getattr(execution, "side", "") or "").upper()
        side = "BUY" if raw_side in ("BOT", "BUY", "B") else "SELL"
        shares = abs(_safe_float(getattr(execution, "shares", 0)))
        price = _safe_float(getattr(execution, "price", getattr(execution, "avgPrice", 0)))
        if shares <= 0 or price <= 0:
            continue
        proceeds = round(shares * price * (-1 if side == "BUY" else 1), 2)
        commission = -abs(_safe_float(getattr(comm_rep, "commission", 0)))
        realized_pnl = _safe_float(getattr(comm_rep, "realizedPNL", 0))
        if abs(realized_pnl) > 1e12:  # IBKR uses 1.7976931348623157e308 as unset sentinel
            realized_pnl = 0.0
        raw_time = getattr(execution, "time", None)
        if isinstance(raw_time, datetime.datetime):
            t_str = raw_time.strftime("%Y-%m-%d %H:%M:%S")
            d_str = raw_time.strftime("%Y-%m-%d")
            hm_str = raw_time.strftime("%H:%M:%S")
        else:
            s_t = str(raw_time or "").strip()
            d_str = s_t[:10] if len(s_t) >= 10 else today
            hm_str = s_t[11:19] if len(s_t) >= 19 else "00:00:00"
            t_str = f"{d_str} {hm_str}"

        parsed_trades.append(
            {
                "trade_key": f"gw_{exec_id}",
                "account_id": acct,
                "trade_id": exec_id,
                "order_id": str(getattr(execution, "orderId", "") or ""),
                "conid": str(getattr(contract, "conId", 0) or sym),
                "symbol": sym,
                "asset_category": sec_type,
                "description": str(getattr(contract, "localSymbol", "") or sym),
                "currency": ccy,
                "side": side,
                "quantity": shares if side == "BUY" else -shares,
                "trade_price": round(price, 4),
                "proceeds": proceeds,
                "commission": round(commission, 4),
                "realized_pnl": round(realized_pnl, 2),
                "trade_date": d_str,
                "trade_time": hm_str,
                "report_date": d_str,
                "executed_at": t_str,
                "multiplier": _safe_float(getattr(contract, "multiplier", 1), 1.0) or 1.0,
            }
        )

    # 4) Open Orders -> ibgw_orders
    open_trades: List[Any] = []
    if hasattr(ib, "reqAllOpenOrdersAsync"):
        await _maybe_await(ib.reqAllOpenOrdersAsync())
    if hasattr(ib, "openTrades"):
        open_trades = ib.openTrades() or []

    trading_mode = effective_trading_mode(cfg, accounts)

    eq_count = 0
    pos_count = 0
    trade_inserted = 0
    trade_updated = 0
    orders_synced = 0
    grid_summary: Dict[str, Any] = {}

    with get_conn() as conn:
        for acct, m in acct_metrics.items():
            conn.execute(
                """
                INSERT INTO ibkr_equity_snapshots (
                    account_id, report_date, currency, total_nav, total_cash,
                    stock_value, options_value, futures_value, other_value,
                    starting_cash, accrued_interest, dividend_accruals, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, ?)
                ON CONFLICT(account_id, report_date) DO UPDATE SET
                    currency = excluded.currency,
                    total_nav = excluded.total_nav,
                    total_cash = excluded.total_cash,
                    stock_value = excluded.stock_value,
                    updated_at = excluded.updated_at
                """,
                (acct, today, m["currency"], m["total_nav"], m["total_cash"], m["stock_value"], now_str),
            )
            eq_count += 1

        for acct in accounts_with_pos:
            conn.execute("DELETE FROM ibkr_positions WHERE account_id = ?", (acct,))
        for p in parsed_positions:
            conn.execute(
                """
                INSERT INTO ibkr_positions (
                    account_id, report_date, conid, symbol, asset_category, description,
                    currency, fx_rate_to_base, position, mark_price, position_value,
                    position_value_base, open_price, cost_basis, unrealized_pnl,
                    unrealized_pnl_base, side, expiry, strike, put_call, multiplier, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, conid) DO UPDATE SET
                    report_date = excluded.report_date,
                    symbol = excluded.symbol,
                    position = excluded.position,
                    mark_price = excluded.mark_price,
                    position_value = excluded.position_value,
                    position_value_base = excluded.position_value_base,
                    open_price = excluded.open_price,
                    cost_basis = excluded.cost_basis,
                    unrealized_pnl = excluded.unrealized_pnl,
                    unrealized_pnl_base = excluded.unrealized_pnl_base,
                    updated_at = excluded.updated_at
                """,
                (
                    p["account_id"], p["report_date"], p["conid"], p["symbol"], p["asset_category"],
                    p["description"], p["currency"], p["position"], p["mark_price"],
                    p["position_value"], p["position_value_base"], p["open_price"], p["cost_basis"],
                    p["unrealized_pnl"], p["unrealized_pnl_base"], p["side"], p["expiry"],
                    p["strike"], p["put_call"], p["multiplier"], now_str,
                ),
            )
            pos_count += 1

        for tr in parsed_trades:
            exists = conn.execute("SELECT 1 FROM ibkr_trades WHERE trade_key = ?", (tr["trade_key"],)).fetchone()
            conn.execute(
                """
                INSERT INTO ibkr_trades (
                    trade_key, account_id, trade_id, order_id, conid, symbol,
                    asset_category, description, currency, fx_rate_to_base, side, open_close,
                    quantity, trade_price, proceeds, proceeds_base, commission, commission_base,
                    realized_pnl, realized_pnl_base, trade_date, trade_time, report_date,
                    executed_at, expiry, strike, put_call, multiplier, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, '', ?, ?)
                ON CONFLICT(trade_key) DO UPDATE SET
                    trade_price = excluded.trade_price,
                    commission = excluded.commission,
                    commission_base = excluded.commission_base,
                    realized_pnl = excluded.realized_pnl,
                    realized_pnl_base = excluded.realized_pnl_base
                """,
                (
                    tr["trade_key"], tr["account_id"], tr["trade_id"], tr["order_id"], tr["conid"],
                    tr["symbol"], tr["asset_category"], tr["description"], tr["currency"], tr["side"],
                    tr["quantity"], tr["trade_price"], tr["proceeds"], tr["proceeds"],
                    tr["commission"], tr["commission"], tr["realized_pnl"], tr["realized_pnl"],
                    tr["trade_date"], tr["trade_time"], tr["report_date"], tr["executed_at"],
                    tr["multiplier"], now_str,
                ),
            )
            if exists:
                trade_updated += 1
            else:
                trade_inserted += 1

        orders_synced = _sync_open_trades_to_db(conn, open_trades, trading_mode)

        if sync_grids:
            grid_summary = sync_ibkr_to_grids(conn)

    grid_imported = int(grid_summary.get("imported_trades") or 0)
    if (trade_inserted > 0 or grid_imported > 0) and (cfg.get("ibgw_notify_fills") or "1") == "1" and has_any_push_channel(cfg):
        latest_syms = sorted({t["symbol"] for t in parsed_trades[:5]})
        await send_bark_push(
            title=f"⚡ IBKR 成交回执（{trading_mode.upper()}）",
            body=f"新增成交 {trade_inserted} 笔，自动记入网格 {grid_imported} 笔（涉及：{', '.join(latest_syms) or '--'}）",
            settings=cfg,
            priority="active",
            group="IBKR",
        )

    order_reconcile_summary: Optional[Dict[str, Any]] = None
    if reconcile_orders:
        order_reconcile_summary = await _reconcile_auto_grid_orders_locked(ib, cfg, accounts=accounts)

    if push_summary and has_any_push_channel(cfg):
        ov = get_ibkr_overview()
        s = ov["summary"]
        await send_bark_push(
            title=f"📊 IB Gateway 账户快照 ({today})",
            body=f"总净值：${s['total_nav']:,.2f} | 现金：${s['total_cash']:,.2f} | 持仓：{pos_count} 只 | 挂单：{orders_synced} 笔",
            settings=cfg,
            priority="active",
            group="IBKR",
        )

    synced_at = now_iso()
    res = {
        "ok": True,
        "accounts": accounts,
        "trading_mode": trading_mode,
        "equity_snapshots": eq_count,
        "positions": pos_count,
        "trades_inserted": trade_inserted,
        "trades_updated": trade_updated,
        "open_orders_synced": orders_synced,
        "grid_trades_imported": grid_imported,
        "grid_prices_updated": int(grid_summary.get("updated_prices") or 0),
        "order_reconcile": order_reconcile_summary,
        "synced_at": synced_at,
    }
    _gw_state.update(
        {
            "phase": "completed",
            "last_sync_at": synced_at,
            "last_result": res,
        }
    )
    return res


async def sync_gateway_snapshot(
    *,
    sync_grids: bool = True,
    reconcile_orders: bool = False,
    push_summary: bool = False,
) -> Dict[str, Any]:
    async with _gw_lock:
        ib = await _connect_locked()
        cfg = get_all_settings()
        return await _sync_snapshot_locked(
            ib,
            cfg,
            sync_grids=sync_grids,
            reconcile_orders=reconcile_orders,
            push_summary=push_summary,
        )


# ---------------- Auto-Grid Limit Order Engine ----------------


def _find_matching_open_trade(open_trades: List[Any], order_ref: str, side_prefix: str) -> List[Any]:
    matches = []
    for tr in open_trades or []:
        order = getattr(tr, "order", None)
        if not order:
            continue
        status_name = _extract_order_status_name(tr)
        if status_name in _TERMINAL_ORDER_STATUSES:
            continue
        ref = str(getattr(order, "orderRef", "") or "").strip()
        if ref == order_ref or (side_prefix and ref.startswith(side_prefix)):
            matches.append(tr)
    return matches


def _cancel_ib_trade(ib: Any, trade: Any) -> None:
    order = getattr(trade, "order", None)
    if order is not None and hasattr(ib, "cancelOrder"):
        ib.cancelOrder(order)


def _place_limit_order(ib: Any, contract: Any, action: str, qty: float, price: float, order_ref: str) -> Any:
    if LimitOrder is not None:
        order = LimitOrder(action, qty, price, orderRef=order_ref, tif="GTC", outsideRth=False)
    else:  # pragma: no cover
        order = {"action": action, "orderType": "LMT", "totalQuantity": qty, "lmtPrice": price, "orderRef": order_ref, "tif": "GTC"}
    return ib.placeOrder(contract, order)


async def _reconcile_strategy_orders(
    ib: Any,
    cfg: Dict[str, str],
    conn: Any,
    strat: Dict[str, Any],
    open_trades: List[Any],
    *,
    accounts: List[str],
    cancel_only: bool = False,
) -> Dict[str, Any]:
    sid = int(strat["id"])
    symbol = str(strat["symbol"] or "").strip().upper()
    market = str(strat.get("market") or "US").strip().upper()
    ts = now_iso()

    if cancel_only:
        cancelled = 0
        for tr in _find_matching_open_trade(open_trades, "", f"LH_GRID_{sid}_"):
            _cancel_ib_trade(ib, tr)
            cancelled += 1
        conn.execute(
            "UPDATE ibgw_orders SET status = 'Cancelled', remaining_qty = 0, updated_at = ? WHERE strategy_id = ? AND status NOT IN ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')",
            (ts, sid),
        )
        return {"strategy_id": sid, "symbol": symbol, "placed": 0, "cancelled": cancelled, "kept": 0, "skipped_reason": "已一键撤销该策略活动挂单"}

    trading_mode = assert_order_allowed(cfg, accounts=accounts, strategy=strat, is_manual_test=False)

    trade_rows = [
        dict(r)
        for r in conn.execute("SELECT * FROM stock_trades WHERE strategy_id = ? ORDER BY traded_at ASC, id ASC", (sid,)).fetchall()
    ]
    state = compute_strategy_state(strat, trade_rows)
    summary = state.get("summary") or {}
    signals = state.get("signals") or {}
    cur_price = _safe_float(strat.get("current_price"), 0.0)
    lower_price = _safe_float(strat.get("lower_price"), 0.0)
    max_holding = _safe_float(strat.get("max_holding_qty"), 0.0)
    stop_below = bool(int(strat.get("stop_buy_below_lower") if strat.get("stop_buy_below_lower") is not None else 1))
    custom_lot = _safe_float(strat.get("lot_size"), 0.0)
    custom_tick = _safe_float(strat.get("tick_size"), 0.0)
    holding_qty = _safe_float(summary.get("holding_qty"), 0.0)

    desired_by_side: Dict[str, Optional[Dict[str, Any]]] = {"buy": None, "sell": None}

    nb = signals.get("next_buy")
    if nb and _safe_float(nb.get("price")) > 0:
        buy_blocked = False
        if stop_below and lower_price > 0 and cur_price > 0 and cur_price < lower_price - 1e-6:
            buy_blocked = True
        raw_qty = _safe_float(nb.get("quantity") or strat.get("shares_per_grid"))
        norm_qty = normalize_lot_qty(raw_qty, market, custom_lot)
        if max_holding > 0 and holding_qty + norm_qty > max_holding + 1e-6:
            buy_blocked = True
        if not buy_blocked and norm_qty > 0:
            lvl = int(nb["level"])
            desired_by_side["buy"] = {
                "action": "BUY",
                "side": "buy",
                "level": lvl,
                "price": round_price_to_tick(float(nb["price"]), market, "buy", custom_tick),
                "qty": norm_qty,
                "order_ref": f"LH_GRID_{sid}_BUY_{lvl}",
            }

    ns = signals.get("next_sell")
    if ns and _safe_float(ns.get("price")) > 0 and holding_qty > 1e-6:
        sell_qty = normalize_lot_qty(min(_safe_float(ns.get("quantity")), holding_qty), market, custom_lot)
        if sell_qty > 0:
            lvl = int(ns["level"])
            desired_by_side["sell"] = {
                "action": "SELL",
                "side": "sell",
                "level": lvl,
                "price": round_price_to_tick(float(ns["price"]), market, "sell", custom_tick),
                "qty": sell_qty,
                "order_ref": f"LH_GRID_{sid}_SELL_{lvl}",
            }

    contract = build_ib_contract(
        symbol,
        market,
        exchange=str(strat.get("exchange") or ""),
        currency=str(strat.get("currency") or ""),
        conid=str(strat.get("conid") or ""),
    )

    placed = 0
    cancelled = 0
    kept = 0
    for side in ("buy", "sell"):
        side_prefix = f"LH_GRID_{sid}_{side.upper()}_"
        existing_trades = _find_matching_open_trade(open_trades, "", side_prefix)
        desired = desired_by_side[side]

        matched_existing = None
        for tr in existing_trades:
            ord_obj = getattr(tr, "order", None)
            ref = str(getattr(ord_obj, "orderRef", "") or "")
            lmt = _safe_float(getattr(ord_obj, "lmtPrice", 0))
            qty = _safe_float(getattr(ord_obj, "totalQuantity", 0))
            if (
                desired is not None
                and matched_existing is None
                and ref == desired["order_ref"]
                and abs(lmt - desired["price"]) < 1e-4
                and abs(qty - desired["qty"]) < 1e-4
            ):
                matched_existing = tr
                kept += 1
            else:
                _cancel_ib_trade(ib, tr)
                cancelled += 1
                conn.execute(
                    "UPDATE ibgw_orders SET status = 'Cancelled', remaining_qty = 0, updated_at = ? WHERE order_ref = ? AND status NOT IN ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')",
                    (ts, ref),
                )

        if desired is not None and matched_existing is None:
            trade_ret = _place_limit_order(
                ib, contract, desired["action"], desired["qty"], desired["price"], desired["order_ref"]
            )
            placed += 1
            ord_ret = getattr(trade_ret, "order", None)
            st_ret = getattr(trade_ret, "orderStatus", None)
            oid = _parse_int(getattr(ord_ret, "orderId", 0), 0)
            pid = _parse_int(getattr(ord_ret, "permId", 0), 0)
            st_name = str(getattr(st_ret, "status", "") or "Submitted")
            acct = accounts[0] if accounts else ""
            conn.execute(
                """INSERT INTO ibgw_orders
                (strategy_id, perm_id, order_id, client_id, order_ref, account_id, symbol, market,
                 side, action, total_qty, filled_qty, remaining_qty, lmt_price, avg_fill_price,
                 grid_level, status, trading_mode, error_msg, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, ?, ?, ?, '', ?, ?)""",
                (
                    sid, pid, oid, _parse_int(cfg.get("ibgw_client_id"), 18), desired["order_ref"],
                    acct, symbol, market, side, desired["action"], desired["qty"], desired["qty"],
                    desired["price"], desired["level"], st_name, trading_mode, ts, ts,
                ),
            )

    return {"strategy_id": sid, "symbol": symbol, "placed": placed, "cancelled": cancelled, "kept": kept}


async def _reconcile_auto_grid_orders_locked(
    ib: Any,
    cfg: Dict[str, str],
    *,
    accounts: Optional[List[str]] = None,
    strategy_id: Optional[int] = None,
    cancel_only: bool = False,
) -> Dict[str, Any]:
    acct_list = accounts if accounts is not None else [str(a) for a in (ib.managedAccounts() or []) if a]
    if hasattr(ib, "reqAllOpenOrdersAsync"):
        await _maybe_await(ib.reqAllOpenOrdersAsync())
    open_trades = (ib.openTrades() or []) if hasattr(ib, "openTrades") else []

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    total_placed = 0
    total_cancelled = 0
    total_kept = 0

    with get_conn() as conn:
        if strategy_id is not None:
            rows = conn.execute("SELECT * FROM grid_strategies WHERE id = ?", (strategy_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM grid_strategies WHERE status = 'active' AND COALESCE(auto_trade_enabled, 0) = 1 ORDER BY id ASC"
            ).fetchall()

        for r in rows:
            strat = dict(r)
            if not cancel_only and strategy_id is None and not int(strat.get("auto_trade_enabled") or 0):
                continue
            try:
                res = await _reconcile_strategy_orders(
                    ib, cfg, conn, strat, open_trades, accounts=acct_list, cancel_only=cancel_only
                )
                results.append(res)
                total_placed += int(res.get("placed") or 0)
                total_cancelled += int(res.get("cancelled") or 0)
                total_kept += int(res.get("kept") or 0)
            except Exception as exc:
                errors.append({"strategy_id": strat["id"], "symbol": strat["symbol"], "error": str(exc)})

    _gw_state["last_order_sync_at"] = now_iso()
    return {
        "ok": len(errors) == 0,
        "strategy_count": len(results),
        "placed": total_placed,
        "cancelled": total_cancelled,
        "kept": total_kept,
        "results": results,
        "errors": errors,
        "synced_at": _gw_state["last_order_sync_at"],
    }


async def reconcile_auto_grid_orders(
    strategy_id: Optional[int] = None,
    cancel_only: bool = False,
) -> Dict[str, Any]:
    async with _gw_lock:
        ib = await _connect_locked()
        cfg = get_all_settings()
        return await _reconcile_auto_grid_orders_locked(
            ib, cfg, strategy_id=strategy_id, cancel_only=cancel_only
        )


async def place_test_gateway_order(
    *,
    symbol: str,
    market: str = "US",
    side: str = "buy",
    quantity: float = 1.0,
    lmt_price: float = 1.0,
    currency: str = "",
    exchange: str = "",
    strategy_id: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    cfg = get_all_settings()
    norm_price = round_price_to_tick(lmt_price, market, side)
    norm_qty = normalize_lot_qty(quantity, market)
    if norm_qty <= 0:
        raise ValueError(f"下单数量过小或不符合市场最小手数要求（输入 {quantity}）")
    action = "BUY" if side.lower() == "buy" else "SELL"
    order_ref = f"LH_TEST_{datetime.datetime.now().strftime('%H%M%S')}"

    if dry_run:
        mode = assert_order_allowed(cfg, accounts=_gw_state.get("managed_accounts") or [], is_manual_test=True)
        return {
            "ok": True,
            "dry_run": True,
            "trading_mode": mode,
            "symbol": symbol.upper(),
            "market": market,
            "action": action,
            "quantity": norm_qty,
            "lmt_price": norm_price,
            "order_ref": order_ref,
            "message": f"预校验通过 [{mode.upper()}]：{action} {norm_qty:g} {symbol.upper()} @ {norm_price}",
        }

    async with _gw_lock:
        ib = await _connect_locked()
        accounts = [str(a) for a in (ib.managedAccounts() or []) if a]
        mode = assert_order_allowed(cfg, accounts=accounts, is_manual_test=True)
        contract = build_ib_contract(symbol, market, exchange=exchange, currency=currency)
        trade_ret = _place_limit_order(ib, contract, action, norm_qty, norm_price, order_ref)
        ord_ret = getattr(trade_ret, "order", None)
        st_ret = getattr(trade_ret, "orderStatus", None)
        oid = _parse_int(getattr(ord_ret, "orderId", 0), 0)
        pid = _parse_int(getattr(ord_ret, "permId", 0), 0)
        st_name = str(getattr(st_ret, "status", "") or "Submitted")
        acct = accounts[0] if accounts else ""
        ts = now_iso()

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO ibgw_orders
                (strategy_id, perm_id, order_id, client_id, order_ref, account_id, symbol, market,
                 side, action, total_qty, filled_qty, remaining_qty, lmt_price, avg_fill_price,
                 grid_level, status, trading_mode, error_msg, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, NULL, ?, ?, '', ?, ?)""",
                (
                    strategy_id, pid, oid, _parse_int(cfg.get("ibgw_client_id"), 18), order_ref,
                    acct, symbol.upper(), market, side.lower(), action, norm_qty, norm_qty,
                    norm_price, st_name, mode, ts, ts,
                ),
            )
            row = dict(conn.execute("SELECT * FROM ibgw_orders WHERE id = ?", (cur.lastrowid,)).fetchone())
        return {
            "ok": True,
            "dry_run": False,
            "order": row,
            "message": f"已在 {mode.upper()} 模式发送限价单：{action} {norm_qty:g} {symbol.upper()} @ {norm_price}",
        }


async def cancel_gateway_order(row_id: int) -> Dict[str, Any]:
    async with _gw_lock:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM ibgw_orders WHERE id = ?", (row_id,)).fetchone()
        if not row:
            raise ValueError("委托记录不存在")
        order_row = dict(row)
        cancelled_live = False
        if is_gateway_connected() and _ib_client is not None:
            for tr in (_ib_client.openTrades() or []):
                ord_obj = getattr(tr, "order", None)
                if not ord_obj:
                    continue
                if (
                    (_parse_int(getattr(ord_obj, "orderId", 0), 0) == _parse_int(order_row.get("order_id"), -1) and _parse_int(order_row.get("order_id"), 0) > 0)
                    or (order_row.get("order_ref") and str(getattr(ord_obj, "orderRef", "")) == order_row["order_ref"])
                ):
                    _cancel_ib_trade(_ib_client, tr)
                    cancelled_live = True
                    break
        ts = now_iso()
        with get_conn() as conn:
            conn.execute(
                "UPDATE ibgw_orders SET status = 'Cancelled', remaining_qty = 0, updated_at = ? WHERE id = ?",
                (ts, row_id),
            )
            updated = dict(conn.execute("SELECT * FROM ibgw_orders WHERE id = ?", (row_id,)).fetchone())
        return {"ok": True, "cancelled_live": cancelled_live, "order": updated}


def list_ibgw_orders(strategy_id: Optional[int] = None, status: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM ibgw_orders WHERE 1=1"
    params: List[Any] = []
    if strategy_id is not None:
        sql += " AND strategy_id = ?"
        params.append(strategy_id)
    if (status or "").strip():
        sql += " AND status = ?"
        params.append(status.strip())
    sql += " ORDER BY CASE WHEN status IN ('Submitted','PreSubmitted','PendingSubmit') THEN 0 ELSE 1 END, id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_ibgw_overview(account_id: str = "") -> Dict[str, Any]:
    ov = get_ibkr_overview(account_id=account_id)
    cfg = get_all_settings()
    gw_status = get_gateway_status(cfg)
    open_orders = list_ibgw_orders(limit=200)
    ov["summary"]["configured"] = bool(gw_status["enabled"] or gw_status["connected"] or ov["accounts"])
    ov["summary"]["open_order_count"] = sum(
        1 for o in open_orders if str(o.get("status") or "") not in _TERMINAL_ORDER_STATUSES
    )
    ov["summary"]["trading_mode"] = gw_status["trading_mode"]
    ov["gateway_status"] = gw_status
    ov["orders"] = open_orders
    return ov
