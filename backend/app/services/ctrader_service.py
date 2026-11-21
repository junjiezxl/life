"""cTrader OpenAPI integration service for LifeHub.

Reads cTrader credentials from LifeHub's SQLite settings (configured via the UI),
pulls historical/recent deals over cTrader OpenAPI (asyncio SSL port 5035),
persists them to `ctrader_deals`, and pushes newly discovered deals via Bark.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import ssl
import struct
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ..db import get_all_settings, get_conn, now_iso
from .bark_service import send_bark_push


def _load_ctrader_sdk():
    try:
        from ctrader_open_api import EndPoints
        from ctrader_open_api.messages import OpenApiMessages_pb2 as oa_msgs
        from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as common_msgs
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model_msgs
        return EndPoints, oa_msgs, common_msgs, model_msgs
    except ImportError as exc:
        raise RuntimeError(
            "未安装 ctrader-open-api 依赖，请先执行: pip install ctrader-open-api==0.9.2"
        ) from exc


class _CTraderAsyncClient:
    """Minimal asyncio TCP/SSL client for cTrader OpenAPI protobuf protocol."""

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
        self.ctid_trader_account_id: Optional[int] = None

    async def __aenter__(self) -> "_CTraderAsyncClient":
        ssl_ctx = ssl.create_default_context()
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ssl_ctx),
            timeout=15.0,
        )
        self._read_task = asyncio.create_task(self._read_loop())
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except BaseException:
                pass
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except BaseException:
                pass

    async def _read_loop(self) -> None:
        assert self.reader is not None
        while True:
            header = await self.reader.readexactly(4)
            (length,) = struct.unpack(">I", header)
            payload = await self.reader.readexactly(length)
            proto = self.common_msgs.ProtoMessage()
            proto.ParseFromString(payload)
            msg_id = proto.clientMsgId
            if msg_id and msg_id in self._pending:
                fut = self._pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(proto)

    async def _send_req(self, msg: Any, timeout: float = 15.0) -> Any:
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
        if self.configured_account_id:
            for a in accounts:
                if (
                    str(a.ctidTraderAccountId) == self.configured_account_id
                    or str(getattr(a, "traderLogin", "")) == self.configured_account_id
                ):
                    target = a
                    break
        if target is None:
            target = accounts[0]

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

    async def fetch_order_labels(self, order_ids: List[int]) -> Dict[str, Tuple[str, str]]:
        assert self.ctid_trader_account_id is not None
        result: Dict[str, Tuple[str, str]] = {}
        for oid in order_ids[:150]:
            try:
                req = self.oa_msgs.ProtoOAOrderDetailsReq()
                req.ctidTraderAccountId = self.ctid_trader_account_id
                req.orderId = oid
                proto = await self._send_req(req, timeout=5.0)
                res = self.oa_msgs.ProtoOAOrderDetailsRes()
                res.ParseFromString(proto.payload)
                order_obj = getattr(res, "order", None)
                trade_data = getattr(order_obj, "tradeData", None) if order_obj else None
                label = str(getattr(trade_data, "label", "") or "").strip()
                comment = str(getattr(trade_data, "comment", "") or "").strip()
                result[str(oid)] = (label, comment)
            except Exception:
                continue
        return result

    async def fetch_deals(self, days: int = 7) -> List[Dict[str, Any]]:
        assert self.ctid_trader_account_id is not None
        symbol_map = await self.fetch_symbols()
        now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        from_ms = now_ms - (max(1, days) * 86400 * 1000)

        req = self.oa_msgs.ProtoOADealListReq()
        req.ctidTraderAccountId = self.ctid_trader_account_id
        req.fromTimestamp = from_ms
        req.toTimestamp = now_ms
        req.maxRows = 500
        proto = await self._send_req(req, timeout=20.0)
        res = self.oa_msgs.ProtoOADealListRes()
        res.ParseFromString(proto.payload)

        unique_order_ids = list({int(d.orderId) for d in res.deal if getattr(d, "orderId", 0)})
        order_meta = await self.fetch_order_labels(unique_order_ids) if unique_order_ids else {}

        deals: List[Dict[str, Any]] = []
        for d in res.deal:
            money_digits = int(getattr(d, "moneyDigits", 2) or 2)
            scale = 10 ** money_digits
            close_detail = getattr(d, "closePositionDetail", None)
            gross = 0.0
            swap = 0.0
            commission = float(getattr(d, "commission", 0) or 0) / scale
            if close_detail and getattr(close_detail, "grossProfit", 0):
                gross = float(close_detail.grossProfit) / scale
                swap = float(getattr(close_detail, "swap", 0) or 0) / scale
                if getattr(close_detail, "commission", 0):
                    commission = float(close_detail.commission) / scale

            net_profit = gross + swap + commission
            exec_ms = int(getattr(d, "executionTimestamp", 0) or 0)
            exec_iso = (
                datetime.datetime.fromtimestamp(exec_ms / 1000.0, tz=datetime.timezone.utc)
                .astimezone()
                .isoformat(timespec="seconds")
                if exec_ms > 0
                else now_iso()
            )
            side_val = int(getattr(d, "tradeSide", 0) or 0)
            side = "BUY" if side_val == 1 else ("SELL" if side_val == 2 else str(side_val))
            sym_id = int(getattr(d, "symbolId", 0) or 0)
            sym_name = symbol_map.get(sym_id, f"SYM_{sym_id}")
            vol = float(getattr(d, "filledVolume", 0) or getattr(d, "volume", 0) or 0) / 10000000.0

            oid_str = str(getattr(d, "orderId", "") or "")
            label_val, comment_val = order_meta.get(oid_str, ("", ""))

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


async def test_ctrader_connection() -> Dict[str, Any]:
    creds = _get_ctrader_credentials()
    async with _CTraderAsyncClient(**creds) as client:
        return {
            "ok": True,
            "account_id": str(client.ctid_trader_account_id),
            "environment": client.environment,
            "message": f"cTrader OpenAPI 连接成功（账户 {client.ctid_trader_account_id}，环境 {client.environment}）",
        }


async def sync_ctrader_deals(days: int = 7, push_new: bool = True) -> Dict[str, Any]:
    cfg = get_all_settings()
    creds = _get_ctrader_credentials(cfg)
    async with _CTraderAsyncClient(**creds) as client:
        deals = await client.fetch_deals(days=days)

    inserted_deals: List[Dict[str, Any]] = []
    updated_count = 0
    now_str = now_iso()

    with get_conn() as conn:
        for d in deals:
            existing = conn.execute(
                "SELECT deal_id FROM ctrader_deals WHERE deal_id = ?", (d["deal_id"],)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE ctrader_deals
                    SET symbol_name = ?, side = ?, volume = ?, price = ?,
                        commission = ?, swap = ?, gross_profit = ?, net_profit = ?,
                        status = ?, label = CASE WHEN ? != '' THEN ? ELSE label END,
                        comment = CASE WHEN ? != '' THEN ? ELSE comment END,
                        executed_at = ?, raw_json = ?
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
                        status, label, comment, executed_at, pushed_at, raw_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
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
                        d["raw_json"],
                        now_str,
                    ),
                )
                inserted_deals.append(d)

    pushed_count = 0
    if (
        push_new
        and inserted_deals
        and cfg.get("ctrader_bark_push_enabled") == "1"
        and cfg.get("bark_enabled") == "1"
    ):
        for d in inserted_deals[:5]:
            pnl_sign = "+" if d["net_profit"] >= 0 else ""
            title = f"cTrader 成交: {d['symbol_name']} {d['side']}"
            body = (
                f"手数: {d['volume']} | 价格: {d['price']} | "
                f"净盈亏: {pnl_sign}{d['net_profit']:.2f} | 时间: {d['executed_at']}"
            )
            res = await send_bark_push(title=title, body=body, settings=cfg, priority="high", group="cTrader")
            if res.get("ok"):
                pushed_count += 1
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE ctrader_deals SET pushed_at = ? WHERE deal_id = ?",
                        (now_iso(), d["deal_id"]),
                    )

    return {
        "ok": True,
        "account_id": str(creds["account_id"] or (deals[0]["account_id"] if deals else "")),
        "environment": creds["environment"],
        "fetched": len(deals),
        "inserted": len(inserted_deals),
        "updated": updated_count,
        "pushed": pushed_count,
        "synced_at": now_str,
    }


def list_ctrader_deals(limit: int = 200) -> Dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ctrader_deals ORDER BY executed_at DESC LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        deals = [dict(r) for r in rows]
        agg = conn.execute(
            """
            SELECT
                COUNT(*) AS total_deals,
                COALESCE(SUM(net_profit), 0) AS total_net_profit,
                COALESCE(SUM(volume), 0) AS total_volume,
                COALESCE(SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END), 0) AS win_count
            FROM ctrader_deals
            """
        ).fetchone()
    total = int(agg["total_deals"] or 0)
    win_count = int(agg["win_count"] or 0)
    return {
        "deals": deals,
        "stats": {
            "total_deals": total,
            "total_net_profit": round(float(agg["total_net_profit"] or 0), 2),
            "total_volume": round(float(agg["total_volume"] or 0), 4),
            "win_rate": round((win_count / total) * 100, 1) if total > 0 else 0.0,
        },
    }
