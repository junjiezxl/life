"""Interactive Brokers (IBKR) Flex Web Service v3 integration for LifeHub.

Fetches Activity Flex XML reports via SendRequest + GetStatement, parses daily NAV
snapshots, open positions, executed trades, and cash/dividend transactions into SQLite,
and provides daily-aggregated portfolio summaries.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..db import get_all_settings, get_conn, now_iso, update_settings
from .bark_service import has_any_push_channel, send_bark_push
from .proxy_service import resolve_proxy_url

logger = logging.getLogger("lifehub.ibkr")

IBKR_FLEX_SEND_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
IBKR_FLEX_GET_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"

_FLEX_ERROR_MAP: Dict[str, str] = {
    "1003": "Flex 报表请求已受理或数据正在准备中，请稍候重试",
    "1012": "Flex Token 无效或格式错误，请核对 IBKR Client Portal 中的 Token",
    "1015": "Flex Query ID 不存在，请检查查询编号是否正确",
    "1016": "Flex Query 未启用或权限不足",
    "1018": "Flex Token 已过期，请在 IBKR 后台重新生成",
    "1019": "IBKR 正在生成报表，请稍候重试",
    "1021": "请求过于频繁，触发 IBKR 接口限流，请稍后再试",
}

_sync_lock = asyncio.Lock()
_sync_state: Dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "last_sync_at": "",
    "last_error": None,
    "last_result": None,
}


def get_ibkr_sync_status() -> Dict[str, Any]:
    return dict(_sync_state)


def _to_float(val: Any, default: float = 0.0) -> float:
    s = str(val if val is not None else "").strip().replace(",", "")
    if not s or s in ("-", "--", "N/A"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _normalize_ibkr_date(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if ";" in s:
        s = s.split(";", 1)[0].strip()
    if " " in s:
        s = s.split(" ", 1)[0].strip()
    if "T" in s:
        s = s.split("T", 1)[0].strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return s[:10]


def _normalize_ibkr_time(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{6}", s):
        return f"{s[:2]}:{s[2:4]}:{s[4:6]}"
    if re.fullmatch(r"\d{2}:\d{2}:\d{2}", s):
        return s
    return s


def _format_executed_at(date_str: str, time_str: str) -> str:
    d = _normalize_ibkr_date(date_str)
    t = _normalize_ibkr_time(time_str)
    if d and t:
        return f"{d} {t}"
    return d or ""


def _resolve_proxy(cfg: Dict[str, str]) -> str:
    return resolve_proxy_url("ibkr_use_proxy", cfg, legacy_key="ibkr_proxy_url")


def _parse_flex_service_response(xml_text: str) -> Dict[str, str]:
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise RuntimeError(f"IBKR Flex 服务返回非 XML 响应：{exc}") from exc

    info: Dict[str, str] = {}
    for child in root:
        tag = child.tag.split("}")[-1].lower()
        info[tag] = (child.text or "").strip()
    return info


async def fetch_flex_statement_xml(
    token: str,
    query_id: str,
    *,
    proxy_url: str = "",
) -> str:
    clean_token = (token or "").strip()
    clean_qid = (query_id or "").strip()
    if not clean_token or not clean_qid:
        raise RuntimeError("请先在「系统设置 → 盈透证券 (IBKR) Flex 配置」填写 Flex Token 和 Query ID")

    client_kwargs: Dict[str, Any] = {
        "timeout": 25.0,
        "headers": {"User-Agent": "LifeHub-IBKR-Flex/1.0"},
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp1 = await client.get(
                IBKR_FLEX_SEND_URL,
                params={"t": clean_token, "q": clean_qid, "v": "3"},
            )
            resp1.raise_for_status()
            send_info = _parse_flex_service_response(resp1.text)
            status = send_info.get("status", "")
            if status.lower() != "success":
                err_code = send_info.get("errorcode", "")
                err_msg = send_info.get("errormessage", "") or str(send_info)
                cn_hint = _FLEX_ERROR_MAP.get(err_code, "")
                hint_part = f"（{cn_hint}）" if cn_hint else ""
                raise RuntimeError(f"IBKR Flex SendRequest 失败 [{err_code}]: {err_msg}{hint_part}")

            ref_code = send_info.get("referencecode", "")
            get_url = send_info.get("url") or IBKR_FLEX_GET_URL
            if not ref_code:
                raise RuntimeError("IBKR Flex SendRequest 未返回 ReferenceCode")

            # Step 2: Poll GetStatement; IBKR returns errorCode 1019 while generating report
            for attempt in range(5):
                if attempt > 0:
                    await asyncio.sleep(2.0)
                resp2 = await client.get(
                    get_url,
                    params={"t": clean_token, "q": ref_code, "v": "3"},
                )
                resp2.raise_for_status()
                text = resp2.text.strip()
                if "<FlexQueryResponse" in text:
                    return text
                if "<FlexStatementResponse" in text:
                    stmt_err = _parse_flex_service_response(text)
                    err_code = stmt_err.get("errorcode", "")
                    if err_code in ("1019", "1003") and attempt < 4:
                        continue
                    err_msg = stmt_err.get("errormessage", "") or text[:200]
                    cn_hint = _FLEX_ERROR_MAP.get(err_code, "")
                    hint_part = f"（{cn_hint}）" if cn_hint else ""
                    raise RuntimeError(f"IBKR Flex GetStatement 失败 [{err_code}]: {err_msg}{hint_part}")
                return text

            raise RuntimeError("IBKR Flex 报表生成超时，请稍后重试")
    except httpx.HTTPError as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise RuntimeError(
            f"连接 IBKR Flex 服务失败 [{type(exc).__name__}: {detail}]。"
            "若服务器网络无法直连 interactivebrokers.com，可在 IBKR 设置中配置 HTTP 代理。"
        ) from exc


def parse_flex_xml(xml_text: str) -> Dict[str, Any]:
    try:
        root = ET.fromstring((xml_text or "").strip())
    except ET.ParseError as exc:
        raise RuntimeError(f"解析 IBKR Flex XML 失败：{exc}") from exc

    statements = root.findall(".//FlexStatement")
    if not statements and root.tag.endswith("FlexStatement"):
        statements = [root]

    equity_rows: List[Dict[str, Any]] = []
    positions: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    cash_txs: List[Dict[str, Any]] = []
    account_ids_with_positions_section: set[str] = set()
    accounts_seen: set[str] = set()

    for stmt in statements:
        stmt_acct = str(stmt.attrib.get("accountId") or "").strip()
        if stmt_acct:
            accounts_seen.add(stmt_acct)

        # 1. EquitySummaryByReportDateInBase
        for eq in stmt.findall(".//EquitySummaryByReportDateInBase"):
            a = eq.attrib
            acct_id = str(a.get("accountId") or stmt_acct or "").strip()
            rdate = _normalize_ibkr_date(a.get("reportDate", ""))
            if not acct_id or not rdate:
                continue
            accounts_seen.add(acct_id)

            total_cash = _to_float(a.get("totalCash"))
            stock_val = _to_float(a.get("stock"))
            options_val = _to_float(a.get("options"))
            futures_val = _to_float(a.get("futures"))
            bonds_val = _to_float(a.get("bonds")) + _to_float(a.get("corporateBonds"))
            funds_val = _to_float(a.get("funds"))
            other_val = (
                bonds_val
                + funds_val
                + _to_float(a.get("commodities"))
                + _to_float(a.get("forexCash"))
                + _to_float(a.get("cfds"))
                + _to_float(a.get("other"))
            )
            accrued_int = _to_float(a.get("accruedInterest"))
            div_acc = _to_float(a.get("dividendAccurals") or a.get("dividendAccruals"))
            explicit_total = a.get("total")
            if explicit_total is not None and str(explicit_total).strip() != "":
                total_nav = _to_float(explicit_total)
            else:
                total_nav = (
                    total_cash
                    + stock_val
                    + options_val
                    + futures_val
                    + other_val
                    + accrued_int
                    + div_acc
                )
            equity_rows.append(
                {
                    "account_id": acct_id,
                    "report_date": rdate,
                    "currency": str(a.get("currency") or "USD").strip() or "USD",
                    "total_nav": round(total_nav, 2),
                    "total_cash": round(total_cash, 2),
                    "stock_value": round(stock_val, 2),
                    "options_value": round(options_val, 2),
                    "futures_value": round(futures_val, 2),
                    "other_value": round(other_val, 2),
                    "starting_cash": round(_to_float(a.get("startingCash")), 2),
                    "accrued_interest": round(accrued_int, 2),
                    "dividend_accruals": round(div_acc, 2),
                }
            )

        # 2. OpenPositions
        open_pos_container = stmt.find("./OpenPositions")
        raw_pos_nodes = stmt.findall(".//OpenPosition")
        if open_pos_container is not None or raw_pos_nodes:
            if stmt_acct:
                account_ids_with_positions_section.add(stmt_acct)

            has_summary_level = any(
                str(p.attrib.get("levelOfDetail") or "").upper() == "SUMMARY"
                for p in raw_pos_nodes
            )
            for pos in raw_pos_nodes:
                a = pos.attrib
                lod = str(a.get("levelOfDetail") or "").upper()
                if has_summary_level and lod and lod != "SUMMARY":
                    continue
                acct_id = str(a.get("accountId") or stmt_acct or "").strip()
                symbol = str(a.get("symbol") or "").strip()
                conid = str(a.get("conid") or symbol).strip()
                qty = _to_float(a.get("position"))
                if not acct_id or not symbol or abs(qty) < 1e-9:
                    continue
                accounts_seen.add(acct_id)
                account_ids_with_positions_section.add(acct_id)

                fx = _to_float(a.get("fxRateToBase"), 1.0) or 1.0
                pos_val = _to_float(a.get("positionValue"))
                unrl_pnl = _to_float(a.get("fifoPnlUnrealized"))
                open_px = _to_float(a.get("openPrice") or a.get("costBasisPrice"))
                cost_basis = _to_float(a.get("costBasis") or a.get("costBasisMoney"))
                side = str(a.get("side") or ("Long" if qty > 0 else "Short")).strip()

                positions.append(
                    {
                        "account_id": acct_id,
                        "report_date": _normalize_ibkr_date(a.get("reportDate", "")),
                        "conid": conid,
                        "symbol": symbol,
                        "asset_category": str(a.get("assetCategory") or "STK").strip(),
                        "description": str(a.get("description") or "").strip(),
                        "currency": str(a.get("currency") or "USD").strip() or "USD",
                        "fx_rate_to_base": fx,
                        "position": qty,
                        "mark_price": _to_float(a.get("markPrice")),
                        "position_value": round(pos_val, 2),
                        "position_value_base": round(pos_val * fx, 2),
                        "open_price": open_px,
                        "cost_basis": round(cost_basis, 2),
                        "unrealized_pnl": round(unrl_pnl, 2),
                        "unrealized_pnl_base": round(unrl_pnl * fx, 2),
                        "side": side,
                        "expiry": _normalize_ibkr_date(a.get("expiry", "")),
                        "strike": str(a.get("strike") or "").strip(),
                        "put_call": str(a.get("putCall") or "").strip(),
                        "multiplier": _to_float(a.get("multiplier"), 1.0) or 1.0,
                    }
                )

        # 3. Trades
        raw_trade_nodes = stmt.findall(".//Trade")
        has_execution_level = any(
            str(tr.attrib.get("levelOfDetail") or "").upper() == "EXECUTION"
            for tr in raw_trade_nodes
        )
        for tr in raw_trade_nodes:
            a = tr.attrib
            lod = str(a.get("levelOfDetail") or "").upper()
            if has_execution_level and lod and lod != "EXECUTION":
                continue
            if not has_execution_level and lod in ("ORDER", "SYMBOL_SUMMARY", "ASSET_SUMMARY", "CLOSED_LOT"):
                continue

            acct_id = str(a.get("accountId") or stmt_acct or "").strip()
            symbol = str(a.get("symbol") or "").strip()
            if not acct_id or not symbol:
                continue
            accounts_seen.add(acct_id)

            trade_id = str(a.get("tradeID") or "").strip()
            order_id = str(a.get("ibOrderID") or a.get("orderID") or a.get("origOrderID") or "").strip()
            conid = str(a.get("conid") or symbol).strip()
            tdate = _normalize_ibkr_date(a.get("tradeDate") or a.get("reportDate") or "")
            ttime = _normalize_ibkr_time(a.get("tradeTime") or "")
            rdate = _normalize_ibkr_date(a.get("reportDate") or tdate)
            qty = _to_float(a.get("quantity"))
            px = _to_float(a.get("tradePrice"))
            fx = _to_float(a.get("fxRateToBase"), 1.0) or 1.0
            proceeds = _to_float(a.get("proceeds"))
            comm = _to_float(a.get("ibCommission"))
            realized = _to_float(a.get("fifoPnlRealized"))

            if trade_id:
                trade_key = f"{acct_id}:{trade_id}"
            else:
                trade_key = f"{acct_id}:{order_id}:{conid}:{tdate}:{ttime}:{qty}:{px}"

            trades.append(
                {
                    "trade_key": trade_key,
                    "account_id": acct_id,
                    "trade_id": trade_id,
                    "order_id": order_id,
                    "conid": conid,
                    "symbol": symbol,
                    "asset_category": str(a.get("assetCategory") or "STK").strip(),
                    "description": str(a.get("description") or "").strip(),
                    "currency": str(a.get("currency") or "USD").strip() or "USD",
                    "fx_rate_to_base": fx,
                    "side": str(a.get("buySell") or ("BUY" if qty > 0 else "SELL")).upper(),
                    "open_close": str(a.get("openCloseIndicator") or "").strip().upper(),
                    "quantity": abs(qty),
                    "trade_price": px,
                    "proceeds": round(proceeds, 2),
                    "proceeds_base": round(proceeds * fx, 2),
                    "commission": round(comm, 4),
                    "commission_base": round(comm * fx, 4),
                    "realized_pnl": round(realized, 2),
                    "realized_pnl_base": round(realized * fx, 2),
                    "trade_date": tdate or rdate,
                    "trade_time": ttime,
                    "report_date": rdate or tdate,
                    "executed_at": _format_executed_at(tdate or rdate, ttime),
                    "expiry": _normalize_ibkr_date(a.get("expiry", "")),
                    "strike": str(a.get("strike") or "").strip(),
                    "put_call": str(a.get("putCall") or "").strip(),
                    "multiplier": _to_float(a.get("multiplier"), 1.0) or 1.0,
                }
            )

        # 4. CashTransactions
        for ct in stmt.findall(".//CashTransaction"):
            a = ct.attrib
            lod = str(a.get("levelOfDetail") or "").upper()
            if lod in ("SUMMARY", "ASSET_SUMMARY"):
                continue
            acct_id = str(a.get("accountId") or stmt_acct or "").strip()
            if not acct_id:
                continue
            accounts_seen.add(acct_id)

            tx_id = str(a.get("transactionID") or "").strip()
            tx_type = str(a.get("type") or "").strip()
            sym = str(a.get("symbol") or "").strip()
            desc = str(a.get("description") or "").strip()
            rdate = _normalize_ibkr_date(a.get("reportDate") or a.get("settleDate") or a.get("dateTime") or "")
            dt_str = str(a.get("dateTime") or rdate).strip().replace(";", " ")
            amt = _to_float(a.get("amount"))
            fx = _to_float(a.get("fxRateToBase"), 1.0) or 1.0
            tx_key = f"{acct_id}:{tx_id}" if tx_id else f"{acct_id}:{rdate}:{tx_type}:{sym}:{amt}:{desc[:40]}"

            cash_txs.append(
                {
                    "tx_key": tx_key,
                    "account_id": acct_id,
                    "transaction_id": tx_id,
                    "tx_type": tx_type,
                    "symbol": sym,
                    "description": desc,
                    "currency": str(a.get("currency") or "USD").strip() or "USD",
                    "fx_rate_to_base": fx,
                    "amount": round(amt, 2),
                    "amount_base": round(amt * fx, 2),
                    "report_date": rdate,
                    "date_time": dt_str,
                }
            )

    return {
        "accounts": sorted(accounts_seen),
        "accounts_with_positions": sorted(account_ids_with_positions_section),
        "equity_rows": equity_rows,
        "positions": positions,
        "trades": trades,
        "cash_transactions": cash_txs,
    }


def _persist_parsed_flex(parsed: Dict[str, Any]) -> Dict[str, int]:
    now_str = now_iso()
    eq_count = 0
    pos_count = 0
    trade_inserted = 0
    trade_updated = 0
    cash_count = 0

    with get_conn() as conn:
        for row in parsed["equity_rows"]:
            conn.execute(
                """
                INSERT INTO ibkr_equity_snapshots (
                    account_id, report_date, currency, total_nav, total_cash,
                    stock_value, options_value, futures_value, other_value,
                    starting_cash, accrued_interest, dividend_accruals, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, report_date) DO UPDATE SET
                    currency = excluded.currency,
                    total_nav = excluded.total_nav,
                    total_cash = excluded.total_cash,
                    stock_value = excluded.stock_value,
                    options_value = excluded.options_value,
                    futures_value = excluded.futures_value,
                    other_value = excluded.other_value,
                    starting_cash = excluded.starting_cash,
                    accrued_interest = excluded.accrued_interest,
                    dividend_accruals = excluded.dividend_accruals,
                    updated_at = excluded.updated_at
                """,
                (
                    row["account_id"],
                    row["report_date"],
                    row["currency"],
                    row["total_nav"],
                    row["total_cash"],
                    row["stock_value"],
                    row["options_value"],
                    row["futures_value"],
                    row["other_value"],
                    row["starting_cash"],
                    row["accrued_interest"],
                    row["dividend_accruals"],
                    now_str,
                ),
            )
            eq_count += 1

        for acct_id in parsed["accounts_with_positions"]:
            conn.execute("DELETE FROM ibkr_positions WHERE account_id = ?", (acct_id,))

        for pos in parsed["positions"]:
            conn.execute(
                """
                INSERT INTO ibkr_positions (
                    account_id, report_date, conid, symbol, asset_category, description,
                    currency, fx_rate_to_base, position, mark_price, position_value,
                    position_value_base, open_price, cost_basis, unrealized_pnl,
                    unrealized_pnl_base, side, expiry, strike, put_call, multiplier, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, conid) DO UPDATE SET
                    report_date = excluded.report_date,
                    symbol = excluded.symbol,
                    asset_category = excluded.asset_category,
                    description = excluded.description,
                    currency = excluded.currency,
                    fx_rate_to_base = excluded.fx_rate_to_base,
                    position = excluded.position,
                    mark_price = excluded.mark_price,
                    position_value = excluded.position_value,
                    position_value_base = excluded.position_value_base,
                    open_price = excluded.open_price,
                    cost_basis = excluded.cost_basis,
                    unrealized_pnl = excluded.unrealized_pnl,
                    unrealized_pnl_base = excluded.unrealized_pnl_base,
                    side = excluded.side,
                    expiry = excluded.expiry,
                    strike = excluded.strike,
                    put_call = excluded.put_call,
                    multiplier = excluded.multiplier,
                    updated_at = excluded.updated_at
                """,
                (
                    pos["account_id"],
                    pos["report_date"],
                    pos["conid"],
                    pos["symbol"],
                    pos["asset_category"],
                    pos["description"],
                    pos["currency"],
                    pos["fx_rate_to_base"],
                    pos["position"],
                    pos["mark_price"],
                    pos["position_value"],
                    pos["position_value_base"],
                    pos["open_price"],
                    pos["cost_basis"],
                    pos["unrealized_pnl"],
                    pos["unrealized_pnl_base"],
                    pos["side"],
                    pos["expiry"],
                    pos["strike"],
                    pos["put_call"],
                    pos["multiplier"],
                    now_str,
                ),
            )
            pos_count += 1

        for tr in parsed["trades"]:
            exists = conn.execute(
                "SELECT 1 FROM ibkr_trades WHERE trade_key = ?", (tr["trade_key"],)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO ibkr_trades (
                    trade_key, account_id, trade_id, order_id, conid, symbol,
                    asset_category, description, currency, fx_rate_to_base, side, open_close,
                    quantity, trade_price, proceeds, proceeds_base, commission, commission_base,
                    realized_pnl, realized_pnl_base, trade_date, trade_time, report_date,
                    executed_at, expiry, strike, put_call, multiplier, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_key) DO UPDATE SET
                    trade_price = excluded.trade_price,
                    proceeds = excluded.proceeds,
                    proceeds_base = excluded.proceeds_base,
                    commission = excluded.commission,
                    commission_base = excluded.commission_base,
                    realized_pnl = excluded.realized_pnl,
                    realized_pnl_base = excluded.realized_pnl_base,
                    executed_at = excluded.executed_at
                """,
                (
                    tr["trade_key"],
                    tr["account_id"],
                    tr["trade_id"],
                    tr["order_id"],
                    tr["conid"],
                    tr["symbol"],
                    tr["asset_category"],
                    tr["description"],
                    tr["currency"],
                    tr["fx_rate_to_base"],
                    tr["side"],
                    tr["open_close"],
                    tr["quantity"],
                    tr["trade_price"],
                    tr["proceeds"],
                    tr["proceeds_base"],
                    tr["commission"],
                    tr["commission_base"],
                    tr["realized_pnl"],
                    tr["realized_pnl_base"],
                    tr["trade_date"],
                    tr["trade_time"],
                    tr["report_date"],
                    tr["executed_at"],
                    tr["expiry"],
                    tr["strike"],
                    tr["put_call"],
                    tr["multiplier"],
                    now_str,
                ),
            )
            if exists:
                trade_updated += 1
            else:
                trade_inserted += 1

        for ct in parsed["cash_transactions"]:
            conn.execute(
                """
                INSERT INTO ibkr_cash_transactions (
                    tx_key, account_id, transaction_id, tx_type, symbol, description,
                    currency, fx_rate_to_base, amount, amount_base, report_date, date_time, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tx_key) DO UPDATE SET
                    amount = excluded.amount,
                    amount_base = excluded.amount_base,
                    description = excluded.description
                """,
                (
                    ct["tx_key"],
                    ct["account_id"],
                    ct["transaction_id"],
                    ct["tx_type"],
                    ct["symbol"],
                    ct["description"],
                    ct["currency"],
                    ct["fx_rate_to_base"],
                    ct["amount"],
                    ct["amount_base"],
                    ct["report_date"],
                    ct["date_time"],
                    now_str,
                ),
            )
            cash_count += 1

    return {
        "equity_snapshots": eq_count,
        "positions": pos_count,
        "trades_inserted": trade_inserted,
        "trades_updated": trade_updated,
        "cash_transactions": cash_count,
    }


async def test_ibkr_connection() -> Dict[str, Any]:
    cfg = get_all_settings()
    token = (cfg.get("ibkr_flex_token") or "").strip()
    query_id = (cfg.get("ibkr_flex_query_id") or "").strip()
    proxy_url = _resolve_proxy(cfg)
    xml_text = await fetch_flex_statement_xml(token, query_id, proxy_url=proxy_url)
    parsed = parse_flex_xml(xml_text)
    return {
        "ok": True,
        "accounts": parsed["accounts"],
        "message": (
            f"IBKR Flex 连接成功（账户：{', '.join(parsed['accounts']) or '--'}，"
            f"净值记录 {len(parsed['equity_rows'])} 条，持仓 {len(parsed['positions'])} 条，成交 {len(parsed['trades'])} 笔）"
        ),
    }


async def sync_ibkr_flex(
    query_id: Optional[str] = None,
    push_summary: Optional[bool] = None,
) -> Dict[str, Any]:
    async with _sync_lock:
        cfg = get_all_settings()
        token = (cfg.get("ibkr_flex_token") or "").strip()
        effective_qid = (query_id or cfg.get("ibkr_flex_query_id") or "").strip()
        proxy_url = _resolve_proxy(cfg)

        _sync_state.update({"running": True, "phase": "fetching", "last_error": None})
        try:
            xml_text = await fetch_flex_statement_xml(token, effective_qid, proxy_url=proxy_url)
            _sync_state["phase"] = "parsing"
            parsed = parse_flex_xml(xml_text)
            counts = _persist_parsed_flex(parsed)

            # Auto-sync newly fetched IBKR stock/ETF trades into linked stock grid strategies
            grid_sync_summary: Dict[str, Any] = {}
            try:
                from .grid_service import sync_ibkr_to_grids

                with get_conn() as conn:
                    grid_sync_summary = sync_ibkr_to_grids(conn)
            except Exception as grid_exc:
                logger.warning("Auto-sync IBKR trades to grids failed: %s", grid_exc)

            synced_at = now_iso()
            result = {
                "ok": True,
                "accounts": parsed["accounts"],
                **counts,
                "grid_trades_imported": int(grid_sync_summary.get("imported_trades") or 0),
                "synced_at": synced_at,
            }

            should_push = (
                (cfg.get("ibkr_bark_daily_summary") == "1")
                if push_summary is None
                else bool(push_summary)
            )
            if should_push and has_any_push_channel(cfg):
                overview = get_ibkr_overview()
                latest_date = overview["summary"].get("latest_report_date") or ""
                if latest_date and latest_date != (cfg.get("ibkr_last_notified_date") or ""):
                    s = overview["summary"]
                    chg = float(s.get("day_change") or 0)
                    chg_sign = "+" if chg >= 0 else ""
                    body_lines = [
                        f"• 报告日期：{latest_date}",
                        f"• 账户总净值：${s['total_nav']:,.2f} ({chg_sign}${abs(chg):,.2f} / {chg_sign}{s['day_change_pct']:.2f}%)"
                        if chg >= 0
                        else f"• 账户总净值：${s['total_nav']:,.2f} (-${abs(chg):,.2f} / {s['day_change_pct']:.2f}%)",
                        f"• 持仓市值：${s['stock_value'] + s['options_value'] + s['futures_value'] + s['other_value']:,.2f} | 现金：${s['total_cash']:,.2f}",
                        f"• 持仓浮盈：{'+' if s['unrealized_pnl'] >= 0 else ''}${s['unrealized_pnl']:,.2f}",
                        f"• 当日成交：{s['latest_day_trade_count']} 笔 | 已实现净盈亏：{'+' if s['latest_day_net_pnl'] >= 0 else ''}${s['latest_day_net_pnl']:,.2f}",
                    ]
                    push_res = await send_bark_push(
                        title=f"📊 IBKR 每日账户汇总 ({latest_date})",
                        body="\n".join(body_lines),
                        settings=cfg,
                        priority="active",
                        group="IBKR",
                    )
                    if push_res.get("ok"):
                        update_settings({"ibkr_last_notified_date": latest_date})

            _sync_state.update(
                {
                    "running": False,
                    "phase": "completed",
                    "last_sync_at": synced_at,
                    "last_error": None,
                    "last_result": result,
                }
            )
            return result
        except Exception as exc:
            err_msg = str(exc).strip() or repr(exc)
            _sync_state.update(
                {
                    "running": False,
                    "phase": "error",
                    "last_error": err_msg,
                }
            )
            raise


def get_ibkr_overview(account_id: str = "") -> Dict[str, Any]:
    acct_filter = (account_id or "").strip()
    cfg = get_all_settings()
    configured = bool((cfg.get("ibkr_flex_token") or "").strip() and (cfg.get("ibkr_flex_query_id") or "").strip())

    with get_conn() as conn:
        acct_rows = conn.execute(
            """
            SELECT DISTINCT account_id FROM (
                SELECT account_id FROM ibkr_equity_snapshots
                UNION SELECT account_id FROM ibkr_positions
                UNION SELECT account_id FROM ibkr_trades
                UNION SELECT account_id FROM ibkr_cash_transactions
            ) ORDER BY account_id
            """
        ).fetchall()
        accounts = [str(r["account_id"]) for r in acct_rows if r["account_id"]]

        where_clause = "WHERE account_id = ?" if acct_filter else ""
        params: Tuple[Any, ...] = (acct_filter,) if acct_filter else ()

        eq_rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM ibkr_equity_snapshots {where_clause} ORDER BY report_date ASC, account_id ASC",
                params,
            ).fetchall()
        ]
        pos_rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM ibkr_positions {where_clause} ORDER BY ABS(position_value_base) DESC, symbol ASC",
                params,
            ).fetchall()
        ]
        trade_rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM ibkr_trades {where_clause} ORDER BY trade_date DESC, trade_time DESC, trade_key DESC LIMIT 1000",
                params,
            ).fetchall()
        ]
        cash_rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM ibkr_cash_transactions {where_clause} ORDER BY report_date DESC, date_time DESC LIMIT 500",
                params,
            ).fetchall()
        ]

    # Aggregate daily equity across selected accounts
    daily_eq_map: Dict[str, Dict[str, Any]] = {}
    for r in eq_rows:
        d = r["report_date"]
        cur = daily_eq_map.setdefault(
            d,
            {
                "report_date": d,
                "currency": r["currency"] or "USD",
                "total_nav": 0.0,
                "total_cash": 0.0,
                "stock_value": 0.0,
                "options_value": 0.0,
                "futures_value": 0.0,
                "other_value": 0.0,
                "accrued_interest": 0.0,
                "dividend_accruals": 0.0,
            },
        )
        for key in (
            "total_nav",
            "total_cash",
            "stock_value",
            "options_value",
            "futures_value",
            "other_value",
            "accrued_interest",
            "dividend_accruals",
        ):
            cur[key] = round(cur[key] + float(r.get(key) or 0), 2)

    daily_equity = [daily_eq_map[k] for k in sorted(daily_eq_map.keys())]
    prev_nav: Optional[float] = None
    for item in daily_equity:
        nav = float(item["total_nav"])
        if prev_nav is not None and abs(prev_nav) > 1e-9:
            chg = round(nav - prev_nav, 2)
            item["day_change"] = chg
            item["day_change_pct"] = round((chg / prev_nav) * 100.0, 2)
        else:
            item["day_change"] = 0.0
            item["day_change_pct"] = 0.0
        prev_nav = nav

    # Aggregate daily trades
    daily_trade_map: Dict[str, Dict[str, Any]] = {}
    for tr in trade_rows:
        d = tr.get("trade_date") or tr.get("report_date") or ""
        if not d:
            continue
        cur = daily_trade_map.setdefault(
            d,
            {
                "trade_date": d,
                "trade_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "realized_pnl": 0.0,
                "commission": 0.0,
                "net_pnl": 0.0,
                "turnover": 0.0,
                "symbols": set(),
            },
        )
        cur["trade_count"] += 1
        side = str(tr.get("side") or "").upper()
        if side == "BUY":
            cur["buy_count"] += 1
        elif side == "SELL":
            cur["sell_count"] += 1
        rpnl = float(tr.get("realized_pnl_base") or tr.get("realized_pnl") or 0)
        comm = float(tr.get("commission_base") or tr.get("commission") or 0)
        proc = abs(float(tr.get("proceeds_base") or tr.get("proceeds") or 0))
        cur["realized_pnl"] = round(cur["realized_pnl"] + rpnl, 2)
        cur["commission"] = round(cur["commission"] + comm, 2)
        cur["net_pnl"] = round(cur["realized_pnl"] + cur["commission"], 2)
        cur["turnover"] = round(cur["turnover"] + proc, 2)
        if tr.get("symbol"):
            cur["symbols"].add(str(tr["symbol"]))

    daily_trades = []
    for d in sorted(daily_trade_map.keys(), reverse=True):
        item = dict(daily_trade_map[d])
        item["symbols"] = sorted(item["symbols"])
        daily_trades.append(item)

    latest_eq = daily_equity[-1] if daily_equity else None
    latest_date = latest_eq["report_date"] if latest_eq else (daily_trades[0]["trade_date"] if daily_trades else "")
    latest_day_trade = next((dt for dt in daily_trades if dt["trade_date"] == latest_date), None)

    unrealized_pnl = round(sum(float(p.get("unrealized_pnl_base") or p.get("unrealized_pnl") or 0) for p in pos_rows), 2)
    total_realized = round(sum(float(t.get("realized_pnl_base") or t.get("realized_pnl") or 0) for t in trade_rows), 2)
    total_commission = round(sum(float(t.get("commission_base") or t.get("commission") or 0) for t in trade_rows), 2)
    total_dividends = round(
        sum(float(c.get("amount_base") or 0) for c in cash_rows if "DIVIDEND" in str(c.get("tx_type") or "").upper()),
        2,
    )

    summary = {
        "configured": configured,
        "base_currency": latest_eq["currency"] if latest_eq else "USD",
        "latest_report_date": latest_date,
        "total_nav": latest_eq["total_nav"] if latest_eq else 0.0,
        "total_cash": latest_eq["total_cash"] if latest_eq else 0.0,
        "stock_value": latest_eq["stock_value"] if latest_eq else 0.0,
        "options_value": latest_eq["options_value"] if latest_eq else 0.0,
        "futures_value": latest_eq["futures_value"] if latest_eq else 0.0,
        "other_value": latest_eq["other_value"] if latest_eq else 0.0,
        "day_change": latest_eq["day_change"] if latest_eq else 0.0,
        "day_change_pct": latest_eq["day_change_pct"] if latest_eq else 0.0,
        "unrealized_pnl": unrealized_pnl,
        "total_realized_pnl": total_realized,
        "total_commission": total_commission,
        "total_net_pnl": round(total_realized + total_commission, 2),
        "total_dividends": total_dividends,
        "latest_day_trade_count": latest_day_trade["trade_count"] if latest_day_trade else 0,
        "latest_day_net_pnl": latest_day_trade["net_pnl"] if latest_day_trade else 0.0,
        "account_count": len(accounts),
        "position_count": len(pos_rows),
        "trade_count": len(trade_rows),
    }

    return {
        "summary": summary,
        "accounts": accounts,
        "daily_equity": list(reversed(daily_equity)),
        "daily_trades": daily_trades,
        "positions": pos_rows,
        "trades": trade_rows,
        "cash_transactions": cash_rows,
        "sync_status": get_ibkr_sync_status(),
    }
