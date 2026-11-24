"""Shared outbound proxy resolution, httpx helpers, and pure-stdlib socket tunneling (HTTP CONNECT & SOCKS5)."""
from __future__ import annotations

import base64
import os
import socket
import ssl
import urllib.parse
from typing import Any, Dict, Optional

import httpx

from ..db import get_all_settings


def resolve_proxy_url(
    feature_flag_key: str,
    settings: Optional[Dict[str, str]] = None,
    *,
    legacy_key: str = "",
) -> str:
    cfg = settings if settings is not None else get_all_settings()
    if cfg.get("global_proxy_enabled", "1") == "0":
        return ""

    if feature_flag_key:
        default_flag = "1" if feature_flag_key == "ctrader_oauth_use_proxy" else "0"
        if cfg.get(feature_flag_key, default_flag) != "1":
            return ""

    raw = (cfg.get("global_proxy_url") or "").strip()
    if not raw and legacy_key:
        raw = (cfg.get(legacy_key) or "").strip()
    if not raw:
        raw = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "").strip()
    return raw


def build_httpx_kwargs(
    timeout: float,
    feature_flag_key: str = "",
    settings: Optional[Dict[str, str]] = None,
    *,
    legacy_key: str = "",
    explicit_proxy: str = "",
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if headers:
        kwargs["headers"] = headers
    proxy = (explicit_proxy or "").strip() or resolve_proxy_url(
        feature_flag_key,
        settings,
        legacy_key=legacy_key,
    )
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


def create_proxied_socket(
    target_host: str,
    target_port: int,
    proxy_url: str,
    timeout: float = 15.0,
) -> socket.socket:
    parsed = urllib.parse.urlparse((proxy_url or "").strip())
    scheme = (parsed.scheme or "http").lower()
    proxy_host = parsed.hostname
    if not proxy_host:
        raise ValueError(f"无效的代理地址格式: {proxy_url}")
    default_port = 1080 if scheme.startswith("socks") else (443 if scheme == "https" else 8080)
    proxy_port = parsed.port or default_port

    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        if scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=proxy_host)

        if scheme in ("socks5", "socks5h"):
            user = urllib.parse.unquote(parsed.username or "")
            pwd = urllib.parse.unquote(parsed.password or "")
            methods = b"\x00\x02" if (user or pwd) else b"\x00"
            sock.sendall(bytes([0x05, len(methods)]) + methods)
            resp = sock.recv(2)
            if len(resp) < 2 or resp[0] != 0x05:
                raise ConnectionError("SOCKS5 代理握手失败")
            chosen = resp[1]
            if chosen == 0x02:
                u_b = user.encode("utf-8")
                p_b = pwd.encode("utf-8")
                sock.sendall(bytes([0x01, len(u_b)]) + u_b + bytes([len(p_b)]) + p_b)
                auth_resp = sock.recv(2)
                if len(auth_resp) < 2 or auth_resp[1] != 0x00:
                    raise PermissionError("SOCKS5 代理认证失败")
            elif chosen != 0x00:
                raise ConnectionError(f"SOCKS5 代理拒绝认证方式: {chosen}")

            host_bytes = target_host.encode("utf-8")
            req = (
                b"\x05\x01\x00\x03"
                + bytes([len(host_bytes)])
                + host_bytes
                + target_port.to_bytes(2, "big")
            )
            sock.sendall(req)
            rep = sock.recv(4)
            if len(rep) < 4 or rep[1] != 0x00:
                code = rep[1] if len(rep) >= 2 else -1
                raise ConnectionError(f"SOCKS5 隧道连接 {target_host}:{target_port} 失败 (code={code})")
            atyp = rep[3]
            if atyp == 0x01:
                sock.recv(4 + 2)
            elif atyp == 0x03:
                ln = sock.recv(1)
                if ln:
                    sock.recv(ln[0] + 2)
            elif atyp == 0x04:
                sock.recv(16 + 2)
            return sock

        if scheme in ("http", "https"):
            lines = [
                f"CONNECT {target_host}:{target_port} HTTP/1.1",
                f"Host: {target_host}:{target_port}",
                "Proxy-Connection: keep-alive",
            ]
            if parsed.username is not None or parsed.password is not None:
                raw_cred = f"{urllib.parse.unquote(parsed.username or '')}:{urllib.parse.unquote(parsed.password or '')}"
                b64 = base64.b64encode(raw_cred.encode("utf-8")).decode("ascii")
                lines.append(f"Proxy-Authorization: Basic {b64}")
            payload = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
            sock.sendall(payload)

            buf = b""
            while b"\r\n\r\n" not in buf and len(buf) < 16384:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            status_line = buf.split(b"\r\n", 1)[0].decode("latin1", errors="ignore")
            if " 200 " not in status_line and not status_line.endswith(" 200"):
                raise ConnectionError(f"HTTP 代理隧道连接 {target_host}:{target_port} 失败: {status_line or '无响应'}")
            return sock

        raise ValueError(f"暂不支持的代理协议: {scheme}（请使用 http:// 或 socks5://）")
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise


async def test_proxy_connection(settings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    cfg = settings if settings is not None else get_all_settings()
    proxy_url = (cfg.get("global_proxy_url") or "").strip()
    if not proxy_url:
        return {"ok": False, "message": "请先填写全局代理服务器地址（如 http://10.0.0.x:7890）"}

    checks = [
        ("Google", "https://www.google.com/generate_204"),
        ("cTrader OAuth", "https://openapi.ctrader.com/apps/token"),
    ]
    results = []
    any_ok = False
    try:
        async with httpx.AsyncClient(timeout=10.0, proxy=proxy_url) as client:
            for name, url in checks:
                try:
                    resp = await client.get(url)
                    results.append(f"{name}: HTTP {resp.status_code}")
                    if resp.status_code < 500:
                        any_ok = True
                except Exception as exc:
                    detail = str(exc).strip() or type(exc).__name__
                    results.append(f"{name}: 失败 ({detail})")
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        hint = ""
        if "socksio" in detail.lower() or proxy_url.lower().startswith("socks"):
            hint = "（提示：HTTP 请求请改填 http://IP:端口，例如 Clash 混合端口 7890 直接支持 http://）"
        return {"ok": False, "message": f"代理初始化失败: {detail}{hint}"}

    summary = " | ".join(results)
    if any_ok:
        return {"ok": True, "message": f"代理连通测试成功（{summary}）"}
    return {"ok": False, "message": f"代理连通测试失败（{summary}）"}
