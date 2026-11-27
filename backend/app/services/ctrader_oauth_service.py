"""cTrader OpenAPI OAuth 2.0 authorization & automatic token renewal service."""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import urllib.parse
from typing import Any, Dict, Optional

import httpx

from ..db import get_all_settings, update_settings
from .bark_service import has_any_push_channel, send_bark_push
from .proxy_service import build_httpx_kwargs

logger = logging.getLogger("lifehub.ctrader_oauth")

CTRADER_AUTH_URL = "https://openapi.ctrader.com/apps/auth"
CTRADER_TOKEN_URL = "https://openapi.ctrader.com/apps/token"
DEFAULT_RENEW_BEFORE_SECONDS = 5 * 86400  # Renew 5 days before 30-day expiry

_refresh_lock = asyncio.Lock()


def is_token_expired_error(exc: Exception | str) -> bool:
    text = str(exc or "").upper()
    return any(
        marker in text
        for marker in (
            "CH_ACCESS_TOKEN_INVALID",
            "ACCESS_TOKEN_EXPIRED",
            "OA_AUTH_TOKEN_EXPIRED",
            "TOKEN_EXPIRED",
            "ACCESS TOKEN EXPIRED",
            "INVALID ACCESS TOKEN",
        )
    )


def parse_oauth_code(code_or_url: str) -> str:
    raw = (code_or_url or "").strip()
    if not raw:
        raise ValueError("授权码或回调链接不能为空")
    if raw.startswith(("http://", "https://")) or "?" in raw:
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        codes = qs.get("code") or []
        if codes and codes[0].strip():
            return codes[0].strip()
        raise ValueError("未能从回调链接中解析出 code 参数，请检查粘贴内容")
    return raw


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    scope: str = "accounts",
) -> str:
    cid = (client_id or "").strip()
    ruri = (redirect_uri or "").strip()
    if not cid:
        raise RuntimeError("请先填写并保存 cTrader Client ID")
    if not ruri:
        raise ValueError("请提供 Redirect URI（回调地址）")
    clean_scope = scope.strip().lower() if scope else "accounts"
    if clean_scope not in ("accounts", "trading"):
        clean_scope = "accounts"
    query = urllib.parse.urlencode(
        {
            "client_id": cid,
            "redirect_uri": ruri,
            "scope": clean_scope,
            "product": "web",
        }
    )
    return f"{CTRADER_AUTH_URL}?{query}"


def _parse_expires_at(expires_at_str: str) -> Optional[datetime.datetime]:
    s = (expires_at_str or "").strip()
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
        return dt
    except ValueError:
        return None


def _compute_expires_iso(expires_in: Any) -> tuple[int, str]:
    try:
        sec = int(expires_in or 0)
    except (TypeError, ValueError):
        sec = 0
    if sec <= 0:
        sec = 2628000  # cTrader default 30 days
    exp_dt = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=sec)
    return sec, exp_dt.isoformat(timespec="seconds")


async def _request_ctrader_token(params: Dict[str, str], *, proxy_url: str = "") -> Dict[str, Any]:
    client_kwargs = build_httpx_kwargs(
        20.0,
        "ctrader_oauth_use_proxy",
        legacy_key="ctrader_oauth_proxy",
        explicit_proxy=proxy_url,
    )

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(CTRADER_TOKEN_URL, params=params)
    except httpx.HTTPError as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise RuntimeError(
            f"无法连接 cTrader OAuth 服务器 ({CTRADER_TOKEN_URL}) [{type(exc).__name__}: {detail}]。"
            "国内网络通常会屏蔽或重置 openapi.ctrader.com，请在「系统设置 → 全局网络代理配置」填写可用代理并开启 cTrader OAuth 代理开关。"
        ) from exc

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"cTrader OAuth 接口返回非 JSON 响应 (HTTP {resp.status_code})") from exc

    if resp.status_code >= 400 or data.get("errorCode") or not data.get("accessToken"):
        err_code = data.get("errorCode") or f"HTTP_{resp.status_code}"
        desc = data.get("description") or data.get("error") or str(data)
        raise RuntimeError(f"cTrader Token 获取/刷新失败 [{err_code}]: {desc}")
    return data


def _save_token_bundle(
    data: Dict[str, Any],
    *,
    redirect_uri: Optional[str] = None,
) -> Dict[str, Any]:
    access_token = str(data.get("accessToken") or "").strip()
    refresh_token = str(data.get("refreshToken") or "").strip()
    expires_in, expires_at = _compute_expires_iso(data.get("expiresIn"))

    updates: Dict[str, str] = {
        "ctrader_access_token": access_token,
        "ctrader_token_expires_at": expires_at,
    }
    if refresh_token:
        updates["ctrader_refresh_token"] = refresh_token
    if redirect_uri is not None and redirect_uri.strip():
        updates["ctrader_oauth_redirect_uri"] = redirect_uri.strip()

    update_settings(updates)
    return {
        "ok": True,
        "expires_in": expires_in,
        "expires_at": expires_at,
        "has_refresh_token": bool(refresh_token),
    }


async def exchange_authorization_code(
    code_or_url: str,
    redirect_uri: str = "",
) -> Dict[str, Any]:
    cfg = get_all_settings()
    client_id = (cfg.get("ctrader_client_id") or "").strip()
    client_secret = (cfg.get("ctrader_client_secret") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("请先在「cTrader OpenAPI 配置」填写并保存 Client ID 和 Client Secret")

    final_redirect = (redirect_uri or cfg.get("ctrader_oauth_redirect_uri") or "").strip()
    if not final_redirect:
        raise ValueError("请提供与 cTrader 应用后台一致的 Redirect URI")

    code = parse_oauth_code(code_or_url)
    async with _refresh_lock:
        data = await _request_ctrader_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": final_redirect,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )
        saved = _save_token_bundle(data, redirect_uri=final_redirect)
    return {
        **saved,
        "message": f"cTrader OAuth 授权成功，Access Token 有效期至 {saved['expires_at']}（已保存 Refresh Token 支持自动续期）",
    }


async def refresh_ctrader_token(
    refresh_token_override: Optional[str] = None,
    *,
    previous_access_token: Optional[str] = None,
    notify_bark: bool = False,
) -> Dict[str, Any]:
    async with _refresh_lock:
        cfg = get_all_settings()
        current_access = (cfg.get("ctrader_access_token") or "").strip()
        # If another concurrent coroutine already rotated the token, skip re-spending refreshToken
        if (
            previous_access_token
            and current_access
            and current_access != previous_access_token
            and not refresh_token_override
        ):
            return {
                "ok": True,
                "already_rotated": True,
                "expires_at": cfg.get("ctrader_token_expires_at", ""),
                "message": "Token 已由并发任务完成续期",
            }

        client_id = (cfg.get("ctrader_client_id") or "").strip()
        client_secret = (cfg.get("ctrader_client_secret") or "").strip()
        target_refresh = (refresh_token_override or cfg.get("ctrader_refresh_token") or "").strip()
        if not client_id or not client_secret:
            raise RuntimeError("请先填写 Client ID 和 Client Secret")
        if not target_refresh:
            raise RuntimeError("未配置 Refresh Token，请先通过 OAuth 授权或手动填入 Refresh Token")

        try:
            data = await _request_ctrader_token(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": target_refresh,
                    "client_id": client_id,
                    "client_secret": client_secret,
                }
            )
            saved = _save_token_bundle(data)
        except Exception as exc:
            if notify_bark and has_any_push_channel(cfg):
                try:
                    await send_bark_push(
                        title="⚠️ cTrader Token 自动续期失败",
                        body=f"请打开 LifeHub 系统设置重新授权或更新 Token。\n原因：{exc}",
                        settings=cfg,
                        priority="high",
                        group="cTrader",
                    )
                except Exception:
                    pass
            raise

    if notify_bark and has_any_push_channel(cfg):
        try:
            await send_bark_push(
                title="🔄 cTrader Token 自动续期成功",
                body=f"已自动换取新的 Access Token 与 Refresh Token。\n新有效期至：{saved['expires_at']}",
                settings=cfg,
                priority="active",
                group="cTrader",
            )
        except Exception:
            pass

    return {
        **saved,
        "already_rotated": False,
        "message": f"cTrader Token 续期成功，新有效期至 {saved['expires_at']}",
    }


async def ensure_fresh_ctrader_token(
    renew_before_seconds: int = DEFAULT_RENEW_BEFORE_SECONDS,
) -> Dict[str, Any]:
    """Proactively renew cTrader access token if within renew_before_seconds of expiration."""
    cfg = get_all_settings()
    if cfg.get("ctrader_auto_refresh_enabled", "1") != "1":
        return {"refreshed": False, "reason": "disabled"}

    client_id = (cfg.get("ctrader_client_id") or "").strip()
    client_secret = (cfg.get("ctrader_client_secret") or "").strip()
    refresh_tok = (cfg.get("ctrader_refresh_token") or "").strip()
    if not (client_id and client_secret and refresh_tok):
        return {"refreshed": False, "reason": "unconfigured"}

    exp_dt = _parse_expires_at(cfg.get("ctrader_token_expires_at") or "")
    now_dt = datetime.datetime.now().astimezone()
    if exp_dt is not None:
        remaining = (exp_dt - now_dt).total_seconds()
        if remaining > renew_before_seconds:
            return {"refreshed": False, "reason": "still_valid", "remaining_seconds": int(remaining)}

    res = await refresh_ctrader_token(notify_bark=True)
    return {"refreshed": True, **res}
