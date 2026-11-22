"""cTrader OpenAPI integration service for LifeHub.

Reads cTrader credentials from LifeHub's SQLite settings (configured via the UI),
pulls historical/incremental deals over cTrader OpenAPI (asyncio SSL port 5035)
with 7-day window chunking, 1ms hasMore bisection, and 5 req/s rate limiting,
persists them to `ctrader_deals`, and pushes newly discovered deals via Bark.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import ssl
import struct
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..db import get_all_settings, get_conn, now_iso
from .bark_service import send_bark_push

logger = logging.getLogger("lifehub.ctrader_sync")

_DEAL_CHUNK_MS = 7 * 86400 * 1000
_HISTORY_MIN_INTERVAL_SEC = 0.21
_IGNORED_DEAL_STATUSES = {4, 5, 6, 7}  # REJECTED, INTERNALLY_REJECTED, ERROR, MISSED

_sync_lock = asyncio.Lock()
_sync_task: Optional[asyncio.Task] = None
_sync_state: Dict[str, Any] = {
    "running": False,
    "mode": "incremental",
    "phase": "idle",
    "from_time": "",
    "to_time": "",
    "chunks_done": 0,
    "chunks_total": 0,
    "deals_fetched": 0,
    "labels_done": 0,
    "labels_total": 0,
    "inserted": 0,
    "updated": 0,
    "pushed": 0,
    "started_at": "",
    "finished_at": "",
    "last_error": None,
    "last_result": None,
}


def get_ctrader_sync_status() -> Dict[str, Any]:
    return dict(_sync_state)


def _load_ctrader_sdk():
    try:
        from ctrader_open_api import EndPoints
        from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as common_msgs
        from ctrader_open_api.messages import OpenApiMessages_pb2 as oa_msgs
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model_msgs
        return EndPoints, oa_msgs, common_msgs, model_msgs
    except ImportError as exc:
        raise RuntimeError(
            "未安装 ctrader-open-api 依赖，请先执行: pip install ctrader-open-api==0.9.2"
        ) from exc


def _ms_to_local_iso(ms: int) -> str:
    if ms <= 0:
        return ""
    return (
        datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )


def _iso_to_ms(value: str) -> int:
    s = (value or "").strip()
    if not s:
        return 0
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def parse_date_bounds_ms(
    start_date: Optional[str],
    end_date: Optional[str],
    days: int = 7,
    now_ms: Optional[int] = None,
) -> Tuple[int, int]:
    """Convert YYYY-MM-DD local date bounds (or fallback days) into UTC millisecond range."""
    current_ms = (
        now_ms
        if now_ms is not None
        else int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    )
    local_tz = datetime.datetime.now().astimezone().tzinfo

    to_ms = current_ms
    if end_date and end_date.strip():
        d_end = datetime.date.fromisoformat(end_date.strip()[:10])
        dt_end = datetime.datetime(
            d_end.year, d_end.month, d_end.day, 23, 59, 59, 999000, tzinfo=local_tz
        )
        to_ms = min(current_ms, int(dt_end.timestamp() * 1000))

    if start_date and start_date.strip():
        d_start = datetime.date.fromisoformat(start_date.strip()[:10])
        dt_start = datetime.datetime(
            d_start.year, d_start.month, d_start.day, 0, 0, 0, 0, tzinfo=local_tz
        )
        from_ms = int(dt_start.timestamp() * 1000)
    else:
        from_ms = max(0, to_ms - (max(1, days) * 86400 * 1000))

    if from_ms > to_ms:
        raise ValueError("开始日期不能晚于结束日期")
    return from_ms, to_ms


class _CTraderAsyncClient:
    """Async TCP/SSL client for cTrader OpenAPI protobuf protocol."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        account_id: str,
        environment: str = "demo",
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.access_token = access_token.strip()
        self.configured_account_id = account_id.strip()
        self.environment = (environment or "demo").strip().lower()
        if self.environment not in ("demo", "live"):
            self.environment = "demo"

        self.EndPoints, self.oa_msgs, self.common_msgs, self.model_msgs = _load_ctrader_sdk()
        self.host = (
            self.EndPoints.PROTOBUF_LIVE_HOST
            if self.environment == "live"
            else self.EndPoints.PROTOBUF_DEMO_HOST
        )
        self.port = self.EndPoints.PROTOBUF_PORT
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self.ctid_trader_account_id: Optional[int] = None
        self._last_history_req_at: float = 0.0

    async def __aenter__(self) -> "_CTraderAsyncClient":
        ssl_ctx = ssl.create_default_context()
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ssl_ctx),
            timeout=15.0,
        )
        self._read_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except BaseException:
                pass
            self._heartbeat_task = None
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except BaseException:
                pass
            self._read_task = None
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except BaseException:
                pass
            self.writer = None
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(ConnectionResetError("cTrader 连接已关闭"))
        self._pending.clear()

    async def _heartbeat_loop(self) -> None:
        try:
            hb_type = self.common_msgs.ProtoHeartbeatEvent().payloadType
            while self.writer is not None:
                await asyncio.sleep(10.0)
                if self.writer is None:
                    break
                proto = self.common_msgs.ProtoMessage()
                proto.payloadType = hb_type
                data = proto.SerializeToString()
                self.writer.write(struct.pack(">I", len(data)) + data)
                await self.writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _read_loop(self) -> None:
        assert self.reader is not None
        hb_type = self.common_msgs.ProtoHeartbeatEvent().payloadType
        while True:
            header = await self.reader.readexactly(4)
            (length,) = struct.unpack(">I", header)
            payload = await self.reader.readexactly(length)
            proto = self.common_msgs.ProtoMessage()
            proto.ParseFromString(payload)
            if proto.payloadType == hb_type:
                if self.writer is not None:
                    hb_resp = self.common_msgs.ProtoMessage()
                    hb_resp.payloadType = hb_type
                    raw = hb_resp.SerializeToString()
                    self.writer.write(struct.pack(">I", len(raw)) + raw)
                    await self.writer.drain()
                continue
            msg_id = proto.clientMsgId
            if msg_id and msg_id in self._pending:
                fut = self._pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(proto)

    async def _send_req(self, msg: Any, timeout: float = 20.0) -> Any:
        assert self.writer is not None
        msg_id = uuid.uuid4().hex[:16]
        proto = self.common_msgs.ProtoMessage()
        proto.payloadType = msg.payloadType
        proto.payload = msg.SerializeToString()
        proto.clientMsgId = msg_id
        data = proto.SerializeToString()
        frame = struct.pack(">I", len(data)) + data

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg_id] = fut
        self.writer.write(frame)
        await self.writer.drain()

        resp_proto = await asyncio.wait_for(fut, timeout=timeout)
        if resp_proto.payloadType == self.oa_msgs.ProtoOAErrorRes().payloadType:
            err = self.oa_msgs.ProtoOAErrorRes()
            err.ParseFromString(resp_proto.payload)
            raise RuntimeError(f"cTrader OpenAPI 错误 [{err.errorCode}]: {err.description}")
        return resp_proto

    async def _throttle_history(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_history_req_at
        if elapsed < _HISTORY_MIN_INTERVAL_SEC:
            await asyncio.sleep(_HISTORY_MIN_INTERVAL_SEC - elapsed)
        self._last_history_req_at = time.monotonic()

    async def authenticate(self) -> None:
        app_req = self.oa_msgs.ProtoOAApplicationAuthReq()
        app_req.clientId = self.client_id
        app_req.clientSecret = self.client_secret
        await self._send_req(app_req)

        acct_list_req = self.oa_msgs.ProtoOAGetAccountListByAccessTokenReq()
        acct_list_req.accessToken = self.access_token
        acct_list_proto = await self._send_req(acct_list_req)
        acct_list_res = self.oa_msgs.ProtoOAGetAccountListByAccessTokenRes()
        acct_list_res.ParseFromString(acct_list_proto.payload)

        accounts = list(acct_list_res.ctidTraderAccount)
        if not accounts:
            raise RuntimeError("当前 Access Token 下未找到任何 cTrader 账户")

        target = None
        want_live = self.environment == "live"
        if self.configured_account_id:
            for a in accounts:
                id_match = (
                    str(a.ctidTraderAccountId) == self.configured_account_id
                    or str(getattr(a, "traderLogin", "")) == self.configured_account_id
                )
                if id_match and bool(getattr(a, "isLive", False)) == want_live:
                    target = a
                    break
            if target is None:
                for a in accounts:
                    if (
                        str(a.ctidTraderAccountId) == self.configured_account_id
                        or str(getattr(a, "traderLogin", "")) == self.configured_account_id
                    ):
                        target = a
                        break
        if target is None:
            env_matches = [a for a in accounts if bool(getattr(a, "isLive", False)) == want_live]
            target = env_matches[0] if env_matches else accounts[0]

        self.ctid_trader_account_id = int(target.ctidTraderAccountId)

        acc_req = self.oa_msgs.ProtoOAAccountAuthReq()
        acc_req.ctidTraderAccountId = self.ctid_trader_account_id
        acc_req.accessToken = self.access_token
        await self._send_req(acc_req)

    async def fetch_symbols(self) -> Dict[int, str]:
        assert self.ctid_trader_account_id is not None
        req = self.oa_msgs.ProtoOASymbolsListReq()
        req.ctidTraderAccountId = self.ctid_trader_account_id
        proto = await self._send_req(req)
        res = self.oa_msgs.ProtoOASymbolsListRes()
        res.ParseFromString(proto.payload)
        return {int(s.symbolId): str(s.symbolName) for s in res.symbol}

    async def fetch_symbol_lot_sizes(self, symbol_ids: List[int]) -> Dict[int, int]:
        """Fetch contract lotSize (in 0.01-unit cents) via ProtoOASymbolByIdReq."""
        assert self.ctid_trader_account_id is not None
        clean_ids = sorted({int(sid) for sid in symbol_ids if sid})
        if not clean_ids:
            return {}
        lot_sizes: Dict[int, int] = {}
        try:
            await self._throttle_history()
            req = self.oa_msgs.ProtoOASymbolByIdReq()
            req.ctidTraderAccountId = self.ctid_trader_account_id
            req.symbolId.extend(clean_ids)
            proto = await self._send_req(req, timeout=15.0)
            res = self.oa_msgs.ProtoOASymbolByIdRes()
            res.ParseFromString(proto.payload)
            for s in res.symbol:
                lot_val = int(getattr(s, "lotSize", 0) or 0)
                if lot_val > 0:
                    lot_sizes[int(s.symbolId)] = lot_val
        except Exception as exc:
            logger.warning("Failed to fetch symbol lot sizes, using fallback defaults: %s", exc)
        return lot_sizes

    async def fetch_order_labels(
        self,
        order_ids: List[int],
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Tuple[str, str]]:
        assert self.ctid_trader_account_id is not None
        result: Dict[str, Tuple[str, str]] = {}
        total = len(order_ids)
        for idx, oid in enumerate(order_ids, start=1):
            await self._throttle_history()
            try:
                req = self.oa_msgs.ProtoOAOrderDetailsReq()
                req.ctidTraderAccountId = self.ctid_trader_account_id
                req.orderId = oid
                proto = await self._send_req(req, timeout=8.0)
                res = self.oa_msgs.ProtoOAOrderDetailsRes()
                res.ParseFromString(proto.payload)
                order_obj = getattr(res, "order", None)
                trade_data = getattr(order_obj, "tradeData", None) if order_obj else None
                label = str(getattr(trade_data, "label", "") or "").strip()
                comment = str(getattr(trade_data, "comment", "") or "").strip()
                result[str(oid)] = (label, comment)
            except Exception as exc:
                logger.debug("Order details unavailable for order %s: %s", oid, exc)
            if progress_cb:
                progress_cb(idx, total)
        return result

    async def _fetch_deals_range(self, from_ms: int, to_ms: int) -> List[Any]:
        assert self.ctid_trader_account_id is not None
        await self._throttle_history()
        req = self.oa_msgs.ProtoOADealListReq()
        req.ctidTraderAccountId = self.ctid_trader_account_id
        req.fromTimestamp = from_ms
        req.toTimestamp = to_ms
        req.maxRows = 1000
        proto = await self._send_req(req, timeout=20.0)
        res = self.oa_msgs.ProtoOADealListRes()
        res.ParseFromString(proto.payload)
        raw_deals = list(res.deal)
        if getattr(res, "hasMore", False) and (to_ms - from_ms) > 1:
            mid_ms = (from_ms + to_ms) // 2
            left = await self._fetch_deals_range(from_ms, mid_ms)
            right = await self._fetch_deals_range(mid_ms + 1, to_ms)
            by_id = {str(d.dealId): d for d in left + right}
            return list(by_id.values())
        return raw_deals

    async def fetch_raw_deals_chunked(
        self,
        from_ms: int,
        to_ms: int,
        progress_cb: Optional[Callable[[int, int, int], None]] = None,
    ) -> List[Any]:
        """Fetch all raw deals in <= 7-day chunks to avoid broker window limits."""
        chunks: List[Tuple[int, int]] = []
        cursor = from_ms
        while cursor <= to_ms:
            chunk_end = min(to_ms, cursor + _DEAL_CHUNK_MS - 1)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + 1

        by_id: Dict[str, Any] = {}
        total_chunks = len(chunks)
        for idx, (c_start, c_end) in enumerate(chunks, start=1):
            chunk_deals = await self._fetch_deals_range(c_start, c_end)
            for d in chunk_deals:
                status_val = int(getattr(d, "dealStatus", 0) or 0)
                filled = float(getattr(d, "filledVolume", 0) or getattr(d, "volume", 0) or 0)
                if status_val in _IGNORED_DEAL_STATUSES or filled <= 0:
                    continue
                by_id[str(d.dealId)] = d
            if progress_cb:
                progress_cb(idx, total_chunks, len(by_id))

        return sorted(
            by_id.values(),
            key=lambda d: (int(getattr(d, "executionTimestamp", 0) or 0), int(getattr(d, "dealId", 0) or 0)),
        )

    @staticmethod
    def _fallback_lot_size_cents(symbol_name: str) -> int:
        sym = (symbol_name or "").upper()
        if "XAU" in sym or "GOLD" in sym:
            return 10_000  # 1 lot = 100 oz = 10,000 protocol cents
        if "XAG" in sym or "SILVER" in sym:
            return 500_000  # 1 lot = 5,000 oz = 500,000 protocol cents
        return 10_000_000  # standard Forex 1 lot = 100,000 units = 10,000,000 cents

    def decode_raw_deals(
        self,
        raw_deals: List[Any],
        symbol_map: Dict[int, str],
        order_meta: Dict[str, Tuple[str, str]],
        lot_sizes: Optional[Dict[int, int]] = None,
    ) -> List[Dict[str, Any]]:
        deals: List[Dict[str, Any]] = []
        for d in raw_deals:
            money_digits = int(getattr(d, "moneyDigits", 2) or 2)
            scale = 10 ** money_digits

            has_close = (
                d.HasField("closePositionDetail")
                if hasattr(d, "HasField") and hasattr(d, "DESCRIPTOR") and "closePositionDetail" in d.DESCRIPTOR.fields_by_name
                else getattr(d, "closePositionDetail", None) is not None
            )
            close_detail = getattr(d, "closePositionDetail", None) if has_close else None

            gross = 0.0
            swap = 0.0
            fee = 0.0
            is_closing = 1 if close_detail is not None else 0
            entry_price = 0.0
            commission = float(getattr(d, "commission", 0) or 0) / scale
            if close_detail is not None:
                close_digits = int(getattr(close_detail, "moneyDigits", 0) or money_digits)
                close_scale = 10 ** close_digits
                gross = float(getattr(close_detail, "grossProfit", 0) or 0) / close_scale
                swap = float(getattr(close_detail, "swap", 0) or 0) / close_scale
                commission = float(getattr(close_detail, "commission", 0) or 0) / close_scale
                fee = abs(float(getattr(close_detail, "pnlConversionFee", 0) or 0)) / close_scale
                entry_price = float(getattr(close_detail, "entryPrice", 0) or 0)

            net_profit = gross + swap + commission - fee
            exec_ms = int(getattr(d, "executionTimestamp", 0) or 0)
            exec_iso = _ms_to_local_iso(exec_ms) if exec_ms > 0 else now_iso()

            side_val = int(getattr(d, "tradeSide", 0) or 0)
            side = "BUY" if side_val == 1 else ("SELL" if side_val == 2 else str(side_val))
            sym_id = int(getattr(d, "symbolId", 0) or 0)
            sym_name = symbol_map.get(sym_id, f"SYM_{sym_id}")
            raw_vol = float(getattr(d, "filledVolume", 0) or getattr(d, "volume", 0) or 0)
            lot_size_cents = (lot_sizes or {}).get(sym_id) or _CTraderAsyncClient._fallback_lot_size_cents(sym_name)
            vol = raw_vol / float(lot_size_cents)

            oid_str = str(getattr(d, "orderId", "") or "")
            direct_label = str(getattr(d, "label", "") or "").strip()
            direct_comment = str(getattr(d, "comment", "") or "").strip()
            meta_label, meta_comment = order_meta.get(oid_str, ("", ""))
            label_val = direct_label or meta_label
            comment_val = direct_comment or meta_comment

            deals.append(
                {
                    "deal_id": str(d.dealId),
                    "order_id": oid_str,
                    "position_id": str(getattr(d, "positionId", "") or ""),
                    "account_id": str(self.ctid_trader_account_id),
                    "symbol_id": sym_id,
                    "symbol_name": sym_name,
                    "side": side,
                    "volume": round(vol, 4),
                    "price": float(getattr(d, "executionPrice", 0) or 0),
                    "commission": round(commission, 2),
                    "swap": round(swap, 2),
                    "gross_profit": round(gross, 2),
                    "net_profit": round(net_profit, 2),
                    "status": str(getattr(d, "dealStatus", "") or ""),
                    "label": label_val,
                    "comment": comment_val,
                    "executed_at": exec_iso,
                    "execution_ms": exec_ms,
                    "is_closing": is_closing,
                    "entry_price": round(entry_price, 6) if entry_price else 0.0,
                    "raw_json": json.dumps(
                        {
                            "dealId": str(d.dealId),
                            "symbol": sym_name,
                            "side": side,
                            "volume": round(vol, 4),
                            "price": float(getattr(d, "executionPrice", 0) or 0),
                            "netProfit": round(net_profit, 2),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        return deals

    async def fetch_deals(
        self,
        days: int = 7,
        from_ms: Optional[int] = None,
        to_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        assert self.ctid_trader_account_id is not None
        symbol_map = await self.fetch_symbols()
        now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        end_ms = to_ms if to_ms is not None else now_ms
        start_ms = from_ms if from_ms is not None else max(0, end_ms - (max(1, days) * 86400 * 1000))

        raw_deals = await self.fetch_raw_deals_chunked(start_ms, end_ms)
        symbol_ids = [int(getattr(d, "symbolId", 0) or 0) for d in raw_deals]
        lot_sizes = await self.fetch_symbol_lot_sizes(symbol_ids)
        unique_order_ids = list({int(d.orderId) for d in raw_deals if getattr(d, "orderId", 0)})
        order_meta = await self.fetch_order_labels(unique_order_ids) if unique_order_ids else {}
        return self.decode_raw_deals(raw_deals, symbol_map, order_meta, lot_sizes=lot_sizes)


def _get_ctrader_credentials(settings: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    cfg = settings or get_all_settings()
    client_id = (cfg.get("ctrader_client_id") or "").strip()
    client_secret = (cfg.get("ctrader_client_secret") or "").strip()
    access_token = (cfg.get("ctrader_access_token") or "").strip()
    account_id = (cfg.get("ctrader_account_id") or "").strip()
    environment = (cfg.get("ctrader_environment") or "demo").strip().lower()
    if not client_id or not client_secret or not access_token:
        raise RuntimeError("请先在「系统设置 -> cTrader OpenAPI 配置」中填写 Client ID、Client Secret 和 Access Token")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
        "account_id": account_id,
        "environment": environment,
    }


def _get_latest_deal_ms(account_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(execution_ms) AS max_ms FROM ctrader_deals WHERE account_id = ? AND execution_ms > 0",
            (account_id,),
        ).fetchone()
        if row and row["max_ms"]:
            return int(row["max_ms"])
        row2 = conn.execute(
            "SELECT executed_at FROM ctrader_deals WHERE account_id = ? ORDER BY executed_at DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        if row2 and row2["executed_at"]:
            parsed = _iso_to_ms(row2["executed_at"])
            if parsed > 0:
                return parsed
        row3 = conn.execute(
            "SELECT MAX(execution_ms) AS max_ms FROM ctrader_deals WHERE execution_ms > 0"
        ).fetchone()
        if row3 and row3["max_ms"]:
            return int(row3["max_ms"])
        row4 = conn.execute(
            "SELECT executed_at FROM ctrader_deals ORDER BY executed_at DESC LIMIT 1"
        ).fetchone()
        if row4 and row4["executed_at"]:
            return _iso_to_ms(row4["executed_at"])
    return 0


def _get_cached_order_labels() -> Dict[str, Tuple[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT order_id, label, comment FROM ctrader_deals WHERE order_id != '' AND label != ''"
        ).fetchall()
    return {str(r["order_id"]): (str(r["label"]), str(r["comment"] or "")) for r in rows}


def _apply_position_label_inheritance(deals: List[Dict[str, Any]]) -> None:
    """Propagate unique position label to closing/partial deals missing a direct order tag."""
    pos_tags: Dict[str, set[Tuple[str, str]]] = {}
    for d in deals:
        pid = d.get("position_id") or ""
        lbl = (d.get("label") or "").strip()
        if pid and lbl:
            pos_tags.setdefault(pid, set()).add((lbl, (d.get("comment") or "").strip()))

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT position_id, label, comment FROM ctrader_deals WHERE position_id != '' AND label != ''"
        ).fetchall()
    for r in rows:
        pos_tags.setdefault(str(r["position_id"]), set()).add(
            (str(r["label"]), str(r["comment"] or ""))
        )

    for d in deals:
        if not (d.get("label") or "").strip():
            tags = pos_tags.get(d.get("position_id") or "", set())
            if len(tags) == 1:
                d["label"], d["comment"] = next(iter(tags))


def _upsert_deals(deals: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    inserted_deals: List[Dict[str, Any]] = []
    updated_count = 0
    now_str = now_iso()

    with get_conn() as conn:
        for d in deals:
            existing = conn.execute(
                "SELECT deal_id FROM ctrader_deals WHERE deal_id = ?", (d["deal_id"],)
            ).fetchone()
            exec_ms = int(d.get("execution_ms") or _iso_to_ms(d.get("executed_at", "")))
            is_closing = int(d.get("is_closing") or 0)
            entry_price = float(d.get("entry_price") or 0.0)
            if existing:
                conn.execute(
                    """
                    UPDATE ctrader_deals
                    SET symbol_name = ?, side = ?, volume = ?, price = ?,
                        commission = ?, swap = ?, gross_profit = ?, net_profit = ?,
                        status = ?,
                        label = CASE WHEN ? != '' THEN ? ELSE label END,
                        comment = CASE WHEN ? != '' THEN ? ELSE comment END,
                        executed_at = ?,
                        execution_ms = CASE WHEN ? > 0 THEN ? ELSE execution_ms END,
                        is_closing = ?,
                        entry_price = CASE WHEN ? > 0 THEN ? ELSE entry_price END,
                        raw_json = ?
                    WHERE deal_id = ?
                    """,
                    (
                        d["symbol_name"],
                        d["side"],
                        d["volume"],
                        d["price"],
                        d["commission"],
                        d["swap"],
                        d["gross_profit"],
                        d["net_profit"],
                        d["status"],
                        d["label"],
                        d["label"],
                        d["comment"],
                        d["comment"],
                        d["executed_at"],
                        exec_ms,
                        exec_ms,
                        is_closing,
                        entry_price,
                        entry_price,
                        d["raw_json"],
                        d["deal_id"],
                    ),
                )
                updated_count += 1
            else:
                conn.execute(
                    """
                    INSERT INTO ctrader_deals (
                        deal_id, order_id, position_id, account_id, symbol_id, symbol_name,
                        side, volume, price, commission, swap, gross_profit, net_profit,
                        status, label, comment, executed_at, execution_ms, is_closing, entry_price,
                        pushed_at, raw_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        d["deal_id"],
                        d["order_id"],
                        d["position_id"],
                        d["account_id"],
                        d["symbol_id"],
                        d["symbol_name"],
                        d["side"],
                        d["volume"],
                        d["price"],
                        d["commission"],
                        d["swap"],
                        d["gross_profit"],
                        d["net_profit"],
                        d["status"],
                        d["label"],
                        d["comment"],
                        d["executed_at"],
                        exec_ms,
                        is_closing,
                        entry_price,
                        d["raw_json"],
                        now_str,
                    ),
                )
                inserted_deals.append(d)
    return inserted_deals, updated_count


def _update_order_labels_in_db(order_meta: Dict[str, Tuple[str, str]]) -> None:
    if not order_meta:
        return
    with get_conn() as conn:
        for oid, (label, comment) in order_meta.items():
            if not label and not comment:
                continue
            conn.execute(
                """
                UPDATE ctrader_deals
                SET label = CASE WHEN ? != '' THEN ? ELSE label END,
                    comment = CASE WHEN ? != '' THEN ? ELSE comment END
                WHERE order_id = ?
                """,
                (label, label, comment, comment, oid),
            )
        # Also propagate unique position labels within newly enriched positions
        rows = conn.execute(
            "SELECT DISTINCT position_id, label, comment FROM ctrader_deals WHERE position_id != '' AND label != ''"
        ).fetchall()
        pos_tags: Dict[str, set[Tuple[str, str]]] = {}
        for r in rows:
            pos_tags.setdefault(str(r["position_id"]), set()).add(
                (str(r["label"]), str(r["comment"] or ""))
            )
        for pid, tags in pos_tags.items():
            if len(tags) == 1:
                lbl, cmt = next(iter(tags))
                conn.execute(
                    "UPDATE ctrader_deals SET label = ?, comment = CASE WHEN ? != '' THEN ? ELSE comment END WHERE position_id = ? AND label = ''",
                    (lbl, cmt, cmt, pid),
                )


async def test_ctrader_connection() -> Dict[str, Any]:
    creds = _get_ctrader_credentials()
    async with _CTraderAsyncClient(**creds) as client:
        return {
            "ok": True,
            "account_id": str(client.ctid_trader_account_id),
            "environment": client.environment,
            "message": f"cTrader OpenAPI 连接成功（账户 {client.ctid_trader_account_id}，环境 {client.environment}）",
        }


async def sync_ctrader_deals(
    days: int = 7,
    push_new: Optional[bool] = None,
    *,
    mode: str = "incremental",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    overlap_seconds: int = 3,
) -> Dict[str, Any]:
    if mode not in ("incremental", "range"):
        raise ValueError("同步模式必须为 incremental 或 range")
    should_push = (mode == "incremental") if push_new is None else bool(push_new)

    async with _sync_lock:
        cfg = get_all_settings()
        creds = _get_ctrader_credentials(cfg)

        _sync_state.update(
            {
                "running": True,
                "mode": mode,
                "phase": "connecting",
                "chunks_done": 0,
                "chunks_total": 0,
                "deals_fetched": 0,
                "labels_done": 0,
                "labels_total": 0,
                "inserted": 0,
                "updated": 0,
                "pushed": 0,
                "started_at": now_iso(),
                "finished_at": "",
                "last_error": None,
            }
        )

        try:
            async with _CTraderAsyncClient(**creds) as client:
                account_id_str = str(client.ctid_trader_account_id)
                now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)

                if mode == "range":
                    from_ms, to_ms = parse_date_bounds_ms(start_date, end_date, days=days, now_ms=now_ms)
                    sync_strategy = "range"
                else:
                    last_ms = _get_latest_deal_ms(account_id_str)
                    to_ms = now_ms
                    if last_ms > 0:
                        from_ms = max(0, last_ms - max(0, overlap_seconds) * 1000)
                        sync_strategy = "incremental_from_last"
                    else:
                        from_ms = max(0, now_ms - max(1, days) * 86400 * 1000)
                        sync_strategy = "initial_days_fallback"

                _sync_state["from_time"] = _ms_to_local_iso(from_ms)
                _sync_state["to_time"] = _ms_to_local_iso(to_ms)
                _sync_state["phase"] = "fetching_deals"

                symbol_map = await client.fetch_symbols()

                def on_chunk_progress(done: int, total: int, fetched_count: int) -> None:
                    _sync_state["chunks_done"] = done
                    _sync_state["chunks_total"] = total
                    _sync_state["deals_fetched"] = fetched_count

                raw_deals = await client.fetch_raw_deals_chunked(
                    from_ms, to_ms, progress_cb=on_chunk_progress
                )
                _sync_state["deals_fetched"] = len(raw_deals)

                symbol_ids = [int(getattr(d, "symbolId", 0) or 0) for d in raw_deals]
                lot_sizes = await client.fetch_symbol_lot_sizes(symbol_ids)
                cached_labels = _get_cached_order_labels()
                deals = client.decode_raw_deals(raw_deals, symbol_map, cached_labels, lot_sizes=lot_sizes)
                _apply_position_label_inheritance(deals)

                # Save deals immediately so large backfills are visible & durable right away
                inserted_deals, updated_count = _upsert_deals(deals)
                _sync_state["inserted"] = len(inserted_deals)
                _sync_state["updated"] = updated_count

                # Enrich missing order labels in batches with rate-limiting
                missing_order_ids = sorted(
                    {
                        int(d["order_id"])
                        for d in deals
                        if d.get("order_id")
                        and str(d["order_id"]).isdigit()
                        and not (d.get("label") or "").strip()
                        and str(d["order_id"]) not in cached_labels
                    }
                )
                if missing_order_ids:
                    _sync_state["phase"] = "enriching_labels"
                    _sync_state["labels_total"] = len(missing_order_ids)
                    batch_size = 25
                    done_count = 0
                    for start_idx in range(0, len(missing_order_ids), batch_size):
                        batch = missing_order_ids[start_idx : start_idx + batch_size]

                        def on_label_progress(idx_in_batch: int, _batch_len: int, offset: int = start_idx) -> None:
                            _sync_state["labels_done"] = offset + idx_in_batch

                        batch_meta = await client.fetch_order_labels(batch, progress_cb=on_label_progress)
                        _update_order_labels_in_db(batch_meta)
                        for d in deals:
                            oid = d.get("order_id") or ""
                            if oid in batch_meta and not (d.get("label") or "").strip():
                                d["label"], d["comment"] = batch_meta[oid]
                        done_count += len(batch)
                        _sync_state["labels_done"] = done_count
                    _apply_position_label_inheritance(deals)

            pushed_count = 0
            if (
                should_push
                and inserted_deals
                and cfg.get("ctrader_bark_push_enabled") == "1"
                and cfg.get("bark_enabled") == "1"
            ):
                _sync_state["phase"] = "pushing_bark"
                for d in inserted_deals[:5]:
                    pnl_sign = "+" if d["net_profit"] >= 0 else ""
                    title = f"cTrader 成交: {d['symbol_name']} {d['side']}"
                    body = (
                        f"手数: {d['volume']} | 价格: {d['price']} | "
                        f"净盈亏: {pnl_sign}{d['net_profit']:.2f} | 时间: {d['executed_at']}"
                    )
                    res = await send_bark_push(
                        title=title, body=body, settings=cfg, priority="high", group="cTrader"
                    )
                    if res.get("ok"):
                        pushed_count += 1
                        with get_conn() as conn:
                            conn.execute(
                                "UPDATE ctrader_deals SET pushed_at = ? WHERE deal_id = ?",
                                (now_iso(), d["deal_id"]),
                            )

            result = {
                "ok": True,
                "mode": mode,
                "sync_strategy": sync_strategy,
                "account_id": str(creds["account_id"] or account_id_str),
                "environment": creds["environment"],
                "from_time": _ms_to_local_iso(from_ms),
                "to_time": _ms_to_local_iso(to_ms),
                "fetched": len(deals),
                "inserted": len(inserted_deals),
                "updated": updated_count,
                "pushed": pushed_count,
                "synced_at": now_iso(),
            }
            _sync_state.update(
                {
                    "running": False,
                    "phase": "completed",
                    "pushed": pushed_count,
                    "finished_at": result["synced_at"],
                    "last_error": None,
                    "last_result": result,
                }
            )
            return result
        except Exception as exc:
            _sync_state.update(
                {
                    "running": False,
                    "phase": "error",
                    "finished_at": now_iso(),
                    "last_error": str(exc),
                }
            )
            raise


def start_background_ctrader_sync(
    days: int = 7,
    push_new: Optional[bool] = None,
    *,
    mode: str = "range",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    overlap_seconds: int = 3,
) -> Dict[str, Any]:
    global _sync_task
    if _sync_state.get("running") or (_sync_task is not None and not _sync_task.done()):
        return {"started": False, "already_running": True, "status": get_ctrader_sync_status()}

    # Validate date bounds synchronously before launching background task
    if mode == "range":
        parse_date_bounds_ms(start_date, end_date, days=days)

    async def _runner() -> None:
        try:
            await sync_ctrader_deals(
                days=days,
                push_new=push_new,
                mode=mode,
                start_date=start_date,
                end_date=end_date,
                overlap_seconds=overlap_seconds,
            )
        except Exception as exc:
            logger.warning("Background cTrader sync failed: %s", exc)

    _sync_state.update(
        {
            "running": True,
            "mode": mode,
            "phase": "starting",
            "chunks_done": 0,
            "chunks_total": 0,
            "deals_fetched": 0,
            "labels_done": 0,
            "labels_total": 0,
            "inserted": 0,
            "updated": 0,
            "pushed": 0,
            "started_at": now_iso(),
            "finished_at": "",
            "last_error": None,
        }
    )
    _sync_task = asyncio.create_task(_runner())
    return {"started": True, "already_running": False, "status": get_ctrader_sync_status()}


def _trade_group_key(deal: Dict[str, Any]) -> str:
    for field, prefix in (("position_id", "pos"), ("order_id", "ord"), ("deal_id", "deal")):
        val = str(deal.get(field) or "").strip()
        if val and val != "0":
            return f"{prefix}:{val}"
    return f"deal:{deal.get('deal_id', '')}"


def _weighted_price(rows: List[Dict[str, Any]]) -> Optional[float]:
    valid_rows = [r for r in rows if float(r.get("price") or 0) > 0]
    if not valid_rows:
        return None
    total_vol = sum(float(r.get("volume") or 0) for r in valid_rows)
    if total_vol > 0:
        weighted = sum(float(r.get("price") or 0) * float(r.get("volume") or 0) for r in valid_rows)
        return round(weighted / total_vol, 5) if weighted > 0 else None
    return round(sum(float(r.get("price") or 0) for r in valid_rows) / len(valid_rows), 5)


def group_deals_into_trades(deals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge opening and closing execution deals belonging to the same position into one trade."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for d in deals:
        grouped.setdefault(_trade_group_key(d), []).append(d)

    trades: List[Dict[str, Any]] = []
    for key, raw_rows in grouped.items():
        rows = sorted(
            raw_rows,
            key=lambda r: (int(r.get("execution_ms") or 0), str(r.get("executed_at") or ""), str(r.get("deal_id") or "")),
        )
        explicit_flags = any(
            int(r.get("is_closing") or 0) == 1
            or float(r.get("entry_price") or 0) > 0
            or float(r.get("gross_profit") or 0) != 0
            or float(r.get("swap") or 0) != 0
            for r in rows
        )
        opens: List[Dict[str, Any]] = []
        closes: List[Dict[str, Any]] = []
        if explicit_flags:
            for r in rows:
                is_close = (
                    int(r.get("is_closing") or 0) == 1
                    or float(r.get("entry_price") or 0) > 0
                    or float(r.get("gross_profit") or 0) != 0
                    or float(r.get("swap") or 0) != 0
                )
                (closes if is_close else opens).append(r)
        elif len(rows) >= 2:
            first_side = str(rows[0].get("side") or "").upper()
            has_opposite = any(str(r.get("side") or "").upper() != first_side for r in rows[1:])
            if has_opposite:
                for r in rows:
                    (opens if str(r.get("side") or "").upper() == first_side else closes).append(r)
            else:
                opens = list(rows)
        else:
            opens = list(rows)

        open_vol = round(sum(float(r.get("volume") or 0) for r in opens), 4)
        close_vol = round(sum(float(r.get("volume") or 0) for r in closes), 4)
        volume = open_vol if opens else close_vol

        open_price = _weighted_price(opens)
        if open_price is None and closes:
            ep = next((float(r.get("entry_price") or 0) for r in closes if float(r.get("entry_price") or 0) > 0), 0.0)
            open_price = round(ep, 5) if ep > 0 else None
        close_price = _weighted_price(closes) if closes else None

        if not closes:
            status = "open"
        elif opens and (close_vol + 1e-6) < open_vol:
            status = "partial"
        else:
            status = "closed"

        if closes:
            gross_profit = round(sum(float(r.get("gross_profit") or 0) for r in closes), 2)
            swap = round(sum(float(r.get("swap") or 0) for r in closes), 2)
            commission = round(sum(float(r.get("commission") or 0) for r in closes), 2)
            net_profit = round(sum(float(r.get("net_profit") or 0) for r in closes), 2)
        else:
            gross_profit = 0.0
            swap = 0.0
            commission = round(sum(float(r.get("commission") or 0) for r in opens), 2)
            net_profit = 0.0

        first_open_side = str(opens[0].get("side") or "").upper() if opens else ""
        if first_open_side:
            side = first_open_side
        else:
            close_side = str(closes[0].get("side") or "").upper() if closes else ""
            side = "BUY" if close_side == "SELL" else ("SELL" if close_side == "BUY" else close_side)

        label = next(((r.get("label") or "").strip() for r in opens + rows if (r.get("label") or "").strip()), "")
        comment = next(((r.get("comment") or "").strip() for r in opens + rows if (r.get("comment") or "").strip()), "")
        opened_at = str(opens[0].get("executed_at") or "") if opens else ""
        closed_at = str(closes[-1].get("executed_at") or "") if closes else ""
        executed_at = closed_at or opened_at or str(rows[-1].get("executed_at") or "")
        exec_ms = max(int(r.get("execution_ms") or 0) for r in rows)
        pushed_at = next((str(r.get("pushed_at") or "") for r in reversed(rows) if (r.get("pushed_at") or "").strip()), "")

        normalized_deals = [{**r, "is_closing": 0} for r in opens] + [{**r, "is_closing": 1} for r in closes]
        normalized_deals.sort(
            key=lambda r: (int(r.get("execution_ms") or 0), str(r.get("executed_at") or ""), str(r.get("deal_id") or "")),
        )

        trades.append(
            {
                "trade_key": key,
                "position_id": str(rows[0].get("position_id") or ""),
                "order_ids": list(dict.fromkeys(str(r.get("order_id") or "") for r in rows if r.get("order_id"))),
                "deal_ids": [str(r.get("deal_id") or "") for r in rows],
                "account_id": str(rows[0].get("account_id") or ""),
                "symbol_name": str(rows[0].get("symbol_name") or ""),
                "side": side,
                "status": status,
                "volume": volume,
                "open_volume": open_vol,
                "close_volume": close_vol,
                "open_price": open_price,
                "close_price": close_price,
                "gross_profit": gross_profit,
                "swap": swap,
                "commission": commission,
                "net_profit": net_profit,
                "label": label,
                "comment": comment,
                "opened_at": opened_at,
                "closed_at": closed_at,
                "executed_at": executed_at,
                "execution_ms": exec_ms,
                "pushed_at": pushed_at,
                "deal_count": len(normalized_deals),
                "deals": normalized_deals,
            }
        )

    return sorted(
        trades,
        key=lambda t: (str(t.get("executed_at") or ""), int(t.get("execution_ms") or 0), str(t.get("trade_key") or "")),
        reverse=True,
    )


async def repair_legacy_deals_if_needed() -> Dict[str, Any]:
    """One-time background repair for legacy deals stored with wrong lotSize or missing is_closing."""
    from ..db import update_settings

    cfg = get_all_settings()
    if cfg.get("ctrader_deals_repaired_v2") == "1":
        return {"repaired": False, "reason": "already_done"}

    client_id = (cfg.get("ctrader_client_id") or "").strip()
    client_secret = (cfg.get("ctrader_client_secret") or "").strip()
    access_token = (cfg.get("ctrader_access_token") or "").strip()
    if not client_id or not client_secret or not access_token:
        return {"repaired": False, "reason": "unconfigured"}

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS bad_cnt, MIN(executed_at) AS min_exec
            FROM ctrader_deals
            WHERE volume <= 0 OR (gross_profit != 0 AND is_closing = 0)
            """
        ).fetchone()
        bad_cnt = int(row["bad_cnt"] or 0) if row else 0
        if bad_cnt == 0:
            total_cnt = conn.execute("SELECT COUNT(*) FROM ctrader_deals").fetchone()[0]
            if total_cnt > 0:
                update_settings({"ctrader_deals_repaired_v2": "1"})
            return {"repaired": False, "reason": "no_bad_rows"}
        min_row = conn.execute("SELECT MIN(executed_at) AS min_exec FROM ctrader_deals").fetchone()
        min_exec = str((min_row["min_exec"] if min_row else "") or "")

    start_date = min_exec[:10] if len(min_exec) >= 10 else None
    today_str = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
    res = await sync_ctrader_deals(
        days=90,
        push_new=False,
        mode="range",
        start_date=start_date,
        end_date=today_str,
    )
    update_settings({"ctrader_deals_repaired_v2": "1"})
    return {"repaired": True, "result": res}


def list_ctrader_deals(limit: int = 5000) -> Dict[str, Any]:
    safe_limit = max(1, min(limit, 10000))
    raw_limit = min(safe_limit * 3, 20000)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ctrader_deals ORDER BY executed_at DESC, execution_ms DESC LIMIT ?",
            (raw_limit,),
        ).fetchall()
        deals = [dict(r) for r in rows]
        agg = conn.execute(
            """
            SELECT
                COUNT(*) AS total_deals,
                MAX(executed_at) AS latest_executed_at,
                MIN(executed_at) AS earliest_executed_at
            FROM ctrader_deals
            """
        ).fetchone()

    all_trades = group_deals_into_trades(deals)
    trades = all_trades[:safe_limit]
    closed_trades = [t for t in all_trades if t["status"] != "open"]
    win_count = sum(1 for t in closed_trades if float(t["net_profit"]) > 0)
    total_net_profit = round(sum(float(t["net_profit"]) for t in closed_trades), 2)
    total_volume = round(sum(float(t["volume"] or 0) for t in all_trades), 4)
    closed_total = len(closed_trades)

    return {
        "trades": trades,
        "deals": deals[:safe_limit],
        "stats": {
            "total_deals": int(agg["total_deals"] or 0),
            "total_trades": len(all_trades),
            "closed_trades": closed_total,
            "open_trades": len(all_trades) - closed_total,
            "total_net_profit": total_net_profit,
            "total_volume": total_volume,
            "win_rate": round((win_count / closed_total) * 100, 1) if closed_total > 0 else 0.0,
            "latest_executed_at": str(agg["latest_executed_at"] or ""),
            "earliest_executed_at": str(agg["earliest_executed_at"] or ""),
        },
    }
