"""Real-time cTrader OpenAPI market tick streaming service for LifeHub.

Maintains a persistent SSL/TCP connection to cTrader OpenAPI, subscribes to
real-time spot ticks (`ProtoOASpotEvent`), caches latest quotes and recent tick
history in memory, and broadcasts live ticks via WebSocket and REST endpoints.
"""
from __future__ import annotations

import asyncio
from collections import deque
import datetime
import logging
import ssl
import struct
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from fastapi import WebSocket

from ..db import get_all_settings

logger = logging.getLogger("lifehub.ctrader_market")


def _load_ctrader_sdk():
    try:
        from ctrader_open_api import EndPoints, Protobuf
        from ctrader_open_api.messages import OpenApiMessages_pb2 as msg_pb
        from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
            ProtoErrorRes,
            ProtoHeartbeatEvent,
            ProtoMessage,
        )
        return EndPoints, Protobuf, ProtoHeartbeatEvent, ProtoMessage, ProtoErrorRes, msg_pb
    except ImportError as exc:
        raise RuntimeError(
            "未安装 ctrader-open-api 依赖，请先执行: pip install ctrader-open-api==0.9.2"
        ) from exc


def parse_symbol_list(raw: str | None) -> List[str]:
    if not raw:
        return ["XAUUSD"]
    seen: set[str] = set()
    result: List[str] = []
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        sym = part.strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            result.append(sym)
    return result or ["XAUUSD"]


class _CTraderSpotClient:
    """Async SSL/TCP client for cTrader OpenAPI spot tick subscriptions."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        account_id: str = "",
        environment: str = "demo",
        on_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.access_token = access_token.strip()
        self.configured_account_id = account_id.strip()
        self.environment = (environment or "demo").strip().lower()
        if self.environment not in ("demo", "live"):
            self.environment = "demo"
        self.on_tick = on_tick

        (
            self.EndPoints,
            self.Protobuf,
            self.ProtoHeartbeatEvent,
            self.ProtoMessage,
            self.ProtoErrorRes,
            self.msg_pb,
        ) = _load_ctrader_sdk()

        self.host = (
            self.EndPoints.PROTOBUF_LIVE_HOST
            if self.environment == "live"
            else self.EndPoints.PROTOBUF_DEMO_HOST
        )
        self.port = self.EndPoints.PROTOBUF_PORT
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.is_connected = False
        self.is_authenticated = False
        self.active_account_id: Optional[int] = None

        self._pending: Dict[str, asyncio.Future] = {}
        self._msg_counter = 0
        self._read_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._symbol_to_id: Dict[str, int] = {}
        self._id_to_symbol: Dict[int, str] = {}
        self._last_spot_prices: Dict[int, Dict[str, float]] = {}

    def _pack(self, message_proto: Any, client_msg_id: Optional[str] = None) -> bytes:
        if isinstance(message_proto, self.ProtoMessage):
            data = message_proto.SerializeToString()
        else:
            wrapped = self.ProtoMessage(
                payloadType=message_proto.payloadType,
                payload=message_proto.SerializeToString(),
                clientMsgId=client_msg_id or "",
            )
            data = wrapped.SerializeToString()
        return struct.pack("!I", len(data)) + data

    def _unpack(self, frame_bytes: bytes) -> tuple[Any, Any]:
        proto_msg = self.ProtoMessage()
        proto_msg.ParseFromString(frame_bytes)
        extracted = self.Protobuf.extract(proto_msg)
        return proto_msg, extracted

    def _next_msg_id(self) -> str:
        self._msg_counter += 1
        return f"mkt_{self._msg_counter}_{int(time.time() * 1000)}"

    async def connect(self) -> None:
        if self.is_connected:
            return
        ssl_ctx = ssl.create_default_context()
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ssl_ctx, server_hostname=self.host),
            timeout=15.0,
        )
        self.is_connected = True
        self._read_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self) -> None:
        self.is_connected = False
        self.is_authenticated = False

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
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None

        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(ConnectionResetError("cTrader 连接已关闭"))
        self._pending.clear()

    async def _send_request(self, message_proto: Any, timeout: float = 20.0) -> Any:
        if not self.is_connected or not self.writer:
            raise ConnectionError("尚未连接到 cTrader OpenAPI")

        name = type(message_proto).__name__
        expected_name = name[:-3] + "Res" if name.endswith("Req") else ""
        expected_type = getattr(self.msg_pb, expected_name, None)

        msg_id = self._next_msg_id()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg_id] = fut

        try:
            frame = self._pack(message_proto, client_msg_id=msg_id)
            self.writer.write(frame)
            await self.writer.drain()
            response = await asyncio.wait_for(fut, timeout=timeout)

            if isinstance(response, (self.ProtoErrorRes, self.msg_pb.ProtoOAErrorRes)):
                err_code = getattr(response, "errorCode", "")
                err_desc = getattr(response, "description", "")
                raise RuntimeError(f"cTrader OpenAPI 错误 [{err_code}]: {err_desc}")
            if expected_type and not isinstance(response, expected_type):
                raise RuntimeError(f"cTrader OpenAPI 返回类型异常: {type(response).__name__}")
            return response
        except asyncio.TimeoutError as exc:
            await self.disconnect()
            raise TimeoutError(f"cTrader 请求 {name} 超时 ({timeout}s)") from exc
        finally:
            self._pending.pop(msg_id, None)

    async def _heartbeat_loop(self) -> None:
        try:
            while self.is_connected:
                await asyncio.sleep(10.0)
                if self.is_connected and self.writer:
                    hb = self.ProtoHeartbeatEvent()
                    self.writer.write(self._pack(hb))
                    await self.writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Heartbeat loop error: %s", exc)

    async def _read_loop(self) -> None:
        try:
            while self.is_connected and self.reader:
                header = await self.reader.readexactly(4)
                (size,) = struct.unpack("!I", header)
                payload = await self.reader.readexactly(size)
                proto_msg, extracted = self._unpack(payload)

                if proto_msg.payloadType == self.ProtoHeartbeatEvent().payloadType:
                    if self.writer:
                        self.writer.write(self._pack(self.ProtoHeartbeatEvent()))
                        await self.writer.drain()
                    continue

                if proto_msg.clientMsgId and proto_msg.clientMsgId in self._pending:
                    fut = self._pending.pop(proto_msg.clientMsgId)
                    if not fut.done():
                        fut.set_result(extracted)
                    continue

                if proto_msg.payloadType == self.msg_pb.ProtoOASpotEvent().payloadType:
                    self._handle_spot_event(extracted)
                    continue
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Spot read loop ended: %s", exc)
        finally:
            self.is_connected = False
            self.is_authenticated = False

    def _handle_spot_event(self, event: Any) -> None:
        sym_id = event.symbolId
        symbol_name = self._id_to_symbol.get(sym_id, f"SYM_{sym_id}")

        spot = self._last_spot_prices.setdefault(sym_id, {"bid": 0.0, "ask": 0.0})
        if event.HasField("bid"):
            spot["bid"] = round(event.bid / 100000.0, 5)
        if event.HasField("ask"):
            spot["ask"] = round(event.ask / 100000.0, 5)

        bid = spot["bid"]
        ask = spot["ask"]
        if bid <= 0 or ask <= 0:
            return

        spread = round(ask - bid, 5)
        ts_ms = event.timestamp if event.HasField("timestamp") else int(time.time() * 1000)
        dt_str = datetime.datetime.fromtimestamp(
            ts_ms / 1000.0, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        tick = {
            "source": "ctrader",
            "symbol": symbol_name,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "timestamp": ts_ms,
            "datetime_utc": dt_str,
        }
        if self.on_tick:
            try:
                self.on_tick(tick)
            except Exception as exc:
                logger.error("Error in on_tick callback: %s", exc)

    async def authenticate(self) -> None:
        app_req = self.Protobuf.get(
            "ProtoOAApplicationAuthReq",
            clientId=self.client_id,
            clientSecret=self.client_secret,
        )
        await self._send_request(app_req)

        acc_list_req = self.Protobuf.get(
            "ProtoOAGetAccountListByAccessTokenReq",
            accessToken=self.access_token,
        )
        res = await self._send_request(acc_list_req)
        accounts = list(res.ctidTraderAccount)
        if not accounts:
            raise RuntimeError("当前 Access Token 下未找到任何 cTrader 账户")

        target = None
        want_live = self.environment == "live"
        if self.configured_account_id:
            for item in accounts:
                id_match = (
                    str(item.ctidTraderAccountId) == self.configured_account_id
                    or str(getattr(item, "traderLogin", "")) == self.configured_account_id
                )
                if id_match and bool(getattr(item, "isLive", False)) == want_live:
                    target = item
                    break
            if target is None:
                for item in accounts:
                    if (
                        str(item.ctidTraderAccountId) == self.configured_account_id
                        or str(getattr(item, "traderLogin", "")) == self.configured_account_id
                    ):
                        target = item
                        break
        if target is None:
            env_matches = [a for a in accounts if bool(getattr(a, "isLive", False)) == want_live]
            target = env_matches[0] if env_matches else accounts[0]

        self.active_account_id = int(target.ctidTraderAccountId)
        acc_req = self.Protobuf.get(
            "ProtoOAAccountAuthReq",
            ctidTraderAccountId=self.active_account_id,
            accessToken=self.access_token,
        )
        await self._send_request(acc_req)
        self.is_authenticated = True

    async def load_symbols(self) -> Dict[str, int]:
        assert self.active_account_id is not None
        req = self.Protobuf.get(
            "ProtoOASymbolsListReq",
            ctidTraderAccountId=self.active_account_id,
            includeArchivedSymbols=False,
        )
        res = await self._send_request(req)
        self._symbol_to_id.clear()
        self._id_to_symbol.clear()
        for s in res.symbol:
            name = s.symbolName.upper()
            sid = int(s.symbolId)
            self._symbol_to_id[name] = sid
            self._id_to_symbol[sid] = name

        if "XAUUSD" not in self._symbol_to_id and "GOLD" in self._symbol_to_id:
            self._symbol_to_id["XAUUSD"] = self._symbol_to_id["GOLD"]
        return self._symbol_to_id

    async def subscribe_symbols(self, symbols: List[str]) -> List[str]:
        if not self._symbol_to_id:
            await self.load_symbols()

        target_ids: List[int] = []
        resolved_names: List[str] = []
        for sym in symbols:
            clean = sym.upper().strip()
            sid = self._symbol_to_id.get(clean)
            if sid is None:
                candidates = [
                    (s_name, s_id)
                    for s_name, s_id in self._symbol_to_id.items()
                    if s_name.startswith(clean)
                ]
                if candidates:
                    sid = candidates[0][1]
            if sid is not None and sid not in target_ids:
                target_ids.append(sid)
                resolved_names.append(self._id_to_symbol.get(sid, clean))

        if not target_ids:
            raise RuntimeError(f"未能匹配任何订阅品种: {', '.join(symbols)}")

        req = self.Protobuf.get(
            "ProtoOASubscribeSpotsReq",
            ctidTraderAccountId=self.active_account_id,
            symbolId=target_ids,
            subscribeToSpotTimestamp=True,
        )
        await self._send_request(req)
        return resolved_names


class CTraderMarketService:
    """In-memory real-time tick cache, REST/WS distributor, and lifecycle manager."""

    def __init__(self) -> None:
        self.start_time = time.time()
        self.market_cache: Dict[str, Dict[str, Any]] = {}
        self.tick_counts: Dict[str, int] = {}
        self.recent_ticks: Deque[Dict[str, Any]] = deque(maxlen=300)
        self._tick_times: Deque[float] = deque(maxlen=5000)

        self.all_subscribers: Set[WebSocket] = set()
        self.symbol_subscribers: Dict[str, Set[WebSocket]] = {}

        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._connected = False
        self._authenticated = False
        self._status = "stopped"  # stopped | unconfigured | connecting | streaming | reconnecting
        self._mode = "STOPPED"
        self._last_error: Optional[str] = None
        self._environment = "demo"
        self._account_id = ""
        self._configured_symbols: List[str] = ["XAUUSD", "EURUSD"]
        self._subscribed_symbols: List[str] = []
        self._client: Optional[_CTraderSpotClient] = None

    def _read_config(self) -> Dict[str, Any]:
        cfg = get_all_settings()
        client_id = (cfg.get("ctrader_client_id") or "").strip()
        client_secret = (cfg.get("ctrader_client_secret") or "").strip()
        access_token = (cfg.get("ctrader_access_token") or "").strip()
        account_id = (cfg.get("ctrader_account_id") or "").strip()
        environment = (cfg.get("ctrader_environment") or "demo").strip().lower()
        if environment not in ("demo", "live"):
            environment = "demo"
        enabled = cfg.get("ctrader_market_enabled", "1") == "1"
        symbols = parse_symbol_list(cfg.get("ctrader_market_symbols") or "XAUUSD,EURUSD")
        api_token = (cfg.get("ctrader_market_api_token") or "").strip()
        configured = bool(client_id and client_secret and access_token)
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "access_token": access_token,
            "account_id": account_id,
            "environment": environment,
            "enabled": enabled,
            "symbols": symbols,
            "api_token": api_token,
            "configured": configured,
        }

    def get_api_token(self) -> str:
        return (get_all_settings().get("ctrader_market_api_token") or "").strip()

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop]) -> None:
        if loop is not None:
            self._loop = loop

    def on_tick_received(self, tick: Dict[str, Any]) -> None:
        sym = str(tick["symbol"]).upper()
        self.market_cache[sym] = tick
        self.tick_counts[sym] = self.tick_counts.get(sym, 0) + 1
        self.recent_ticks.appendleft(tick)
        self._tick_times.append(time.time())

        try:
            loop = asyncio.get_running_loop()
            self._loop = loop
            loop.create_task(self.broadcast_tick(sym, tick))
        except RuntimeError:
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(
                    lambda s=sym, t=tick, lp=loop: lp.create_task(self.broadcast_tick(s, t))
                )

    async def broadcast_tick(self, sym: str, tick: Dict[str, Any]) -> None:
        dead_all: Set[WebSocket] = set()
        for ws in list(self.all_subscribers):
            try:
                await ws.send_json(tick)
            except Exception:
                dead_all.add(ws)
        if dead_all:
            self.all_subscribers.difference_update(dead_all)

        for sub_sym, sockets in list(self.symbol_subscribers.items()):
            if sym != sub_sym and not sym.startswith(sub_sym):
                continue
            dead_sym: Set[WebSocket] = set()
            for ws in list(sockets):
                try:
                    await ws.send_json(tick)
                except Exception:
                    dead_sym.add(ws)
            if dead_sym:
                sockets.difference_update(dead_sym)

    def _ticks_per_sec(self) -> float:
        now = time.time()
        while self._tick_times and (now - self._tick_times[0]) > 5.0:
            self._tick_times.popleft()
        return round(len(self._tick_times) / 5.0, 1)

    def active_ws_count(self) -> int:
        return len(self.all_subscribers) + sum(len(s) for s in self.symbol_subscribers.values())

    def get_public_status(self) -> Dict[str, Any]:
        return {
            "code": 200,
            "mode": self._mode,
            "status": self._status,
            "uptime_sec": int(time.time() - self.start_time),
            "cached_symbols": list(self.market_cache.keys()),
            "tick_counts": dict(self.tick_counts),
            "active_ws_clients": self.active_ws_count(),
        }

    def get_detailed_status(self) -> Dict[str, Any]:
        cfg = self._read_config()
        return {
            "running": self._running,
            "connected": self._connected,
            "authenticated": self._authenticated,
            "enabled": cfg["enabled"],
            "configured": cfg["configured"],
            "status": self._status,
            "mode": self._mode,
            "environment": cfg["environment"],
            "account_id": self._account_id or cfg["account_id"],
            "symbols": cfg["symbols"],
            "subscribed_symbols": list(self._subscribed_symbols),
            "has_api_token": bool(cfg["api_token"]),
            "uptime_sec": int(time.time() - self.start_time),
            "ticks_per_sec": self._ticks_per_sec(),
            "tick_counts": dict(self.tick_counts),
            "total_ticks": sum(self.tick_counts.values()),
            "active_ws_clients": self.active_ws_count(),
            "last_error": self._last_error,
            "quotes": dict(self.market_cache),
        }

    def get_all_quotes(self) -> Dict[str, Any]:
        return {
            "code": 200,
            "count": len(self.market_cache),
            "data": dict(self.market_cache),
        }

    def get_single_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.upper().strip()
        data = self.market_cache.get(sym)
        if not data:
            matches = [v for k, v in self.market_cache.items() if k.startswith(sym)]
            if matches:
                data = matches[0]
        if not data:
            return None
        return {"code": 200, "data": data}

    def get_recent_ticks(self, symbol: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 300))
        sym = symbol.upper().strip()
        if not sym or sym == "ALL":
            return list(self.recent_ticks)[:limit]
        matched: List[Dict[str, Any]] = []
        for item in self.recent_ticks:
            item_sym = str(item.get("symbol") or "").upper()
            if item_sym == sym or item_sym.startswith(sym):
                matched.append(item)
                if len(matched) >= limit:
                    break
        return matched

    async def start(self) -> None:
        cfg = self._read_config()
        self._configured_symbols = cfg["symbols"]
        self._environment = cfg["environment"]

        if not cfg["configured"]:
            self._running = False
            self._connected = False
            self._authenticated = False
            self._status = "unconfigured"
            self._mode = "UNCONFIGURED"
            self._last_error = "请先在「系统设置 -> cTrader OpenAPI 配置」填写 Client ID、Client Secret 和 Access Token"
            return

        if self._task and not self._task.done():
            return

        self._running = True
        self._last_error = None
        self._status = "connecting"
        self._mode = f"cTrader ({cfg['environment'].upper()})"
        self._task = asyncio.create_task(self._collector_loop())

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
        self._authenticated = False
        self._subscribed_symbols = []
        self._status = "stopped"
        self._mode = "STOPPED"

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def ensure_started_from_settings(self) -> None:
        cfg = self._read_config()
        self._configured_symbols = cfg["symbols"]
        self._environment = cfg["environment"]
        if cfg["enabled"] and cfg["configured"]:
            if not self._task or self._task.done():
                await self.start()
        elif not cfg["enabled"]:
            await self.stop()
        else:
            self._status = "unconfigured"
            self._mode = "UNCONFIGURED"

    async def on_settings_updated(self) -> None:
        cfg = self._read_config()
        if not cfg["enabled"]:
            await self.stop()
            return
        if not cfg["configured"]:
            await self.stop()
            self._status = "unconfigured"
            self._mode = "UNCONFIGURED"
            self._last_error = "未配置 cTrader OpenAPI 凭据"
            return
        await self.restart()

    async def _collector_loop(self) -> None:
        backoff = 2.0
        while self._running:
            cfg = self._read_config()
            if not cfg["enabled"] or not cfg["configured"]:
                break

            self._configured_symbols = cfg["symbols"]
            self._environment = cfg["environment"]
            self._mode = f"cTrader ({cfg['environment'].upper()})"
            self._status = "connecting"

            client = _CTraderSpotClient(
                client_id=cfg["client_id"],
                client_secret=cfg["client_secret"],
                access_token=cfg["access_token"],
                account_id=cfg["account_id"],
                environment=cfg["environment"],
                on_tick=self.on_tick_received,
            )
            self._client = client

            try:
                logger.info("Connecting cTrader live tick stream (%s) for %s...", cfg["environment"], cfg["symbols"])
                await client.connect()
                self._connected = True
                await client.authenticate()
                self._authenticated = True
                self._account_id = str(client.active_account_id or cfg["account_id"])
                resolved = await client.subscribe_symbols(cfg["symbols"])
                self._subscribed_symbols = resolved
                self._status = "streaming"
                self._last_error = None
                backoff = 2.0
                logger.info("cTrader live tick stream active: %s", resolved)

                while self._running and client.is_connected:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                self._status = "reconnecting"
                logger.warning("cTrader live tick stream error: %s", exc)
            finally:
                self._connected = False
                self._authenticated = False
                try:
                    await client.disconnect()
                except Exception:
                    pass
                if self._client is client:
                    self._client = None

            if not self._running:
                break

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * 2.0, 30.0)

        self._running = False
        self._connected = False
        self._authenticated = False
        if self._status != "unconfigured":
            self._status = "stopped"
            self._mode = "STOPPED"


market_service = CTraderMarketService()
