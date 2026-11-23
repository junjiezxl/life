import asyncio
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import get_all_settings, get_conn, init_db, now_iso, reset_all_data, update_settings
from .models import (
    CTraderMarketActionRequest,
    CTraderOAuthExchangeRequest,
    CTraderOAuthRefreshRequest,
    CTraderSyncRequest,
    IBKRSyncRequest,
    AccountCreate,
    AccountUpdate,
    AuthChangePasswordRequest,
    AuthLoginRequest,
    AuthSetupRequest,
    BatchImportRemindersRequest,
    BatchImportTransactionsRequest,
    ChatRequest,
    CreditCardCreate,
    CreditCardUpdate,
    DebtOperationCreate,
    DebtPlanRequest,
    EmailFetchRequest,
    EmailImportRequest,
    EmailParseTextRequest,
    LoanCreate,
    LoanUpdate,
    NoteExtractRequest,
    PhoneCommRecordRequest,
    PhoneNumberCreate,
    PhoneNumberUpdate,
    RecurringBillCreate,
    RecurringBillUpdate,
    ReminderCreate,
    ReminderUpdate,
    SettingsUpdateRequest,
    SettlementCreateRequest,
    SettlementRegenerateRequest,
    SiyuanFetchRequest,
    TransactionCreate,
    TransactionUpdate,
)
from .services.ai_service import (
    extract_from_email_content,
    extract_reminders_from_notes,
    generate_settlement_summary,
    process_chat_message,
    test_ai_connection,
)
from .services.auth_service import (
    SESSION_COOKIE_NAME,
    LoginRateLimiter,
    authenticate_user,
    count_active_sessions,
    create_session,
    create_user,
    delete_other_sessions,
    delete_session,
    get_session_user,
    has_any_user,
    update_user_credentials,
)
from .services.bark_service import format_reminder_bark_message, send_bark_push
from .services.ctrader_service import (
    get_ctrader_sync_status,
    list_ctrader_deals,
    repair_legacy_deals_if_needed,
    start_background_ctrader_sync,
    sync_ctrader_deals,
    test_ctrader_connection,
)
from .services.ctrader_oauth_service import (
    build_authorize_url,
    ensure_fresh_ctrader_token,
    exchange_authorization_code,
    refresh_ctrader_token,
)
from .services.ctrader_market_service import market_service
from .services.bill_parser import parse_bill_file
from .services.debt_planner_service import (
    enrich_credit_card,
    enrich_loan,
    plan_optimal_repayment_paths,
)
from .services.email_service import fetch_and_parse_emails, test_imap_connection
from .services.ibkr_service import (
    get_ibkr_overview,
    get_ibkr_sync_status,
    sync_ibkr_flex,
    test_ibkr_connection,
)
from .services.siyuan_service import fetch_siyuan_notes, test_siyuan_connection

app = FastAPI(title="LifeHub API", version="1.0.0")

_PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/logout",
}
_login_limiter = LoginRateLimiter()


def _extract_session_token(request: Request) -> str:
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token.strip()
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or ""
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_https_request(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def _set_session_cookie(response: Response, request: Request, token: str, remember_me: bool, max_age: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_is_https_request(request),
        path="/",
        max_age=max_age if remember_me else None,
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next: Any) -> Response:
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/"):
        return await call_next(request)

    token = _extract_session_token(request)
    session_user = None
    if token:
        with get_conn() as conn:
            session_user = get_session_user(conn, token)
    request.state.auth_user = session_user
    request.state.auth_token = token

    if path in _PUBLIC_API_PATHS:
        return await call_next(request)

    if not session_user:
        return JSONResponse(status_code=401, content={"detail": "未登录或登录已过期，请先登录"})

    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _email_auto_sync_loop() -> None:
    while True:
        interval_min = 30
        try:
            settings = get_all_settings()
            interval_min = max(5, int(settings.get("email_sync_interval_min") or 30))
            if settings.get("email_auto_sync_enabled") == "1" and settings.get("email_username") and settings.get("email_password"):
                with get_conn() as conn:
                    existing_refs = _get_existing_external_refs(conn)
                    synced_uids = _get_synced_email_uids(conn)
                res = await fetch_and_parse_emails(
                    settings=settings,
                    existing_refs=existing_refs,
                    synced_uids=synced_uids,
                    days=7,
                    limit=20,
                    include_body_ai=True,
                    skip_already_synced=True,
                )
                if res["emails"] or res["transactions"] or res["reminders"]:
                    with get_conn() as conn:
                        _commit_email_batch(
                            conn=conn,
                            txs=[TransactionCreate(**{k: t[k] for k in ("type", "amount", "category", "description", "occurred_at", "source_type", "external_ref") if k in t}) for t in res["transactions"]],
                            rems=[ReminderCreate(**{k: r[k] for k in ("title", "description", "due_at", "priority", "category", "source_type", "source_ref", "linked_amount", "linked_tx_type") if k in r}) for r in res["reminders"]],
                            account_id=None,
                            skip_duplicates=True,
                            logs=[
                                {
                                    "message_uid": e["uid"],
                                    "subject": e["subject"],
                                    "sender": e["sender"],
                                    "received_at": e["received_at"],
                                    "tx_imported": e["parsed_tx_count"],
                                    "rem_imported": e["parsed_rem_count"],
                                }
                                for e in res["emails"]
                            ],
                        )
        except Exception:
            pass
        await asyncio.sleep(interval_min * 60)


async def _push_new_reminders_bark(reminders: List[Dict[str, Any]], prefix: str = "📌 新提醒：") -> None:
    if not reminders:
        return
    settings = get_all_settings()
    if settings.get("bark_enabled", "0") != "1" or settings.get("bark_push_on_create", "1") != "1":
        return
    for rem in reminders[:10]:
        msg = format_reminder_bark_message(rem, prefix=prefix)
        await send_bark_push(
            title=msg["title"],
            body=msg["body"],
            settings=settings,
            priority=str(rem.get("priority") or "medium"),
        )


def _parse_due_dt(due_str: str) -> Optional[datetime]:
    s = (due_str or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[: len(fmt) + 2 if fmt == "%Y-%m-%dT%H:%M" else len(fmt)], fmt)
            return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt
    except ValueError:
        return None


async def _check_and_push_due_reminders() -> int:
    settings = get_all_settings()
    if settings.get("bark_enabled", "0") != "1":
        return 0

    advance_min = max(0, int(settings.get("bark_due_advance_min") or 15))
    now = datetime.now().astimezone()
    pushed = 0

    with get_conn() as conn:
        new_phone_rems = _sync_phone_reminders_conn(conn)
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at IS NOT NULL AND due_at != '' AND (bark_notified_at IS NULL OR bark_notified_at = '')"
        ).fetchall()
        due_candidates = [_row_to_dict(r) for r in rows]

    if new_phone_rems:
        await _push_new_reminders_bark(new_phone_rems, prefix="📱 保号提前提醒：")

    for rem in due_candidates:
        due_dt = _parse_due_dt(rem.get("due_at") or "")
        if due_dt is None:
            continue
        if (now + timedelta(minutes=advance_min)) >= due_dt >= (now - timedelta(hours=24)):
            msg = format_reminder_bark_message(rem, prefix="⏰ 提醒到期：")
            res = await send_bark_push(
                title=msg["title"],
                body=msg["body"],
                settings=settings,
                priority=str(rem.get("priority") or "high"),
            )
            if res.get("ok"):
                pushed += 1
                with get_conn() as conn:
                    conn.execute("UPDATE reminders SET bark_notified_at = ? WHERE id = ?", (now_iso(), rem["id"]))
    return pushed


async def _bark_due_reminder_loop() -> None:
    while True:
        try:
            await _check_and_push_due_reminders()
        except Exception:
            pass
        await asyncio.sleep(60)


async def _ctrader_auto_sync_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            settings = get_all_settings()
            if settings.get("ctrader_auto_sync_enabled") != "1":
                continue
            interval_sec = max(15, int(settings.get("ctrader_sync_interval_sec") or "60"))
            await sync_ctrader_deals(days=3, push_new=True, mode="incremental", overlap_seconds=3)
            await asyncio.sleep(max(0, interval_sec - 30))
        except Exception:
            pass


def _format_update_message(raw_msg: str) -> str:
    msg = (raw_msg or "").strip()
    if not msg:
        return ""
    prefix_map = {
        "feat:": "新功能：",
        "fix:": "修复：",
        "style:": "界面优化：",
        "refactor:": "优化重构：",
        "chore:": "维护：",
        "docs:": "文档：",
        "perf:": "性能优化：",
        "ci:": "构建部署：",
    }
    lower = msg.lower()
    for eng, chn in prefix_map.items():
        if lower.startswith(eng):
            rest = msg[len(eng) :].strip()
            return f"{chn}{rest}" if rest else chn.rstrip("：")
    return msg


def get_build_info() -> Dict[str, str]:
    env_sha = os.environ.get("LIFEHUB_BUILD_SHA")
    if env_sha is not None and env_sha.strip():
        version = (os.environ.get("LIFEHUB_VERSION") or "v1.0.0-dev").strip() or "v1.0.0-dev"
        sha = env_sha.strip()
        raw_msg = (os.environ.get("LIFEHUB_BUILD_MSG") or "").strip()
        return {
            "version": version,
            "sha": sha,
            "build_time": (os.environ.get("LIFEHUB_BUILD_TIME") or "").strip(),
            "commit_msg": _format_update_message(raw_msg),
        }

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    meta_path = os.path.join(repo_root, ".build_meta.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            sha = str(meta.get("sha") or "").strip()
            if sha:
                return {
                    "version": str(meta.get("version") or "v1.0.0").strip(),
                    "sha": sha,
                    "build_time": str(meta.get("build_time") or "").strip(),
                    "commit_msg": _format_update_message(str(meta.get("commit_msg") or "")),
                }
        except Exception:
            pass

    if os.path.isdir(os.path.join(repo_root, ".git")):
        try:
            import subprocess

            def _git(*args: str) -> str:
                return subprocess.check_output(
                    ["git", "-C", repo_root, *args],
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                ).decode("utf-8", errors="ignore").strip()

            sha = _git("rev-parse", "--short=7", "HEAD")
            if sha:
                count = int(_git("rev-list", "--count", "HEAD") or "0")
                patch = max(0, count - 31)
                msg = _git("log", "-1", "--pretty=%s")
                btime = _git("log", "-1", "--pretty=%cd", "--date=format:%Y-%m-%d %H:%M:%S")
                return {
                    "version": f"v1.0.{patch}",
                    "sha": sha,
                    "build_time": btime,
                    "commit_msg": _format_update_message(msg),
                }
        except Exception:
            pass

    return {
        "version": (os.environ.get("LIFEHUB_VERSION") or "v1.0.0-dev").strip() or "v1.0.0-dev",
        "sha": "dev",
        "build_time": (os.environ.get("LIFEHUB_BUILD_TIME") or "").strip(),
        "commit_msg": _format_update_message((os.environ.get("LIFEHUB_BUILD_MSG") or "").strip()),
    }


async def check_and_notify_version_update() -> Dict[str, Any]:
    build = get_build_info()
    build_version = build["version"]
    build_sha = build["sha"]
    if not build_sha or build_sha == "dev":
        return {"notified": False, "reason": "dev_build"}

    notify_key = f"{build_version}:{build_sha}"
    settings = get_all_settings()
    if settings.get("bark_push_on_update", "1") != "1":
        return {"notified": False, "reason": "disabled"}
    if settings.get("bark_enabled") != "1" or not (settings.get("bark_device_key") or "").strip():
        return {"notified": False, "reason": "bark_unconfigured"}
    if settings.get("last_notified_build_id", "") == notify_key:
        return {"notified": False, "reason": "already_notified"}

    lines = [f"新版本 {build_version} 已部署并启动完成。"]
    if build["commit_msg"]:
        lines.append(f"更新内容：{build['commit_msg']}")
    if build["build_time"]:
        lines.append(f"构建时间：{build['build_time']}")
    lines.append(f"上线时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}")

    res = await send_bark_push(
        title=f"🚀 LifeHub 已更新上线 ({build_version})",
        body="\n".join(lines),
        settings=settings,
        priority="high",
        group="LifeHub",
    )
    if res.get("ok"):
        update_settings({"last_notified_build_id": notify_key})
        return {"notified": True, "version": build_version, "sha": build_sha}
    return {"notified": False, "reason": res.get("message") or "push_failed"}


async def _startup_update_notify_task() -> None:
    await asyncio.sleep(2.0)
    try:
        res = await check_and_notify_version_update()
        if not res.get("notified") and res.get("reason") not in ("dev_build", "disabled", "bark_unconfigured", "already_notified"):
            await asyncio.sleep(15.0)
            await check_and_notify_version_update()
    except Exception:
        pass


async def _startup_legacy_deals_repair_task() -> None:
    await asyncio.sleep(5.0)
    try:
        await repair_legacy_deals_if_needed()
    except Exception:
        pass


async def _ctrader_token_auto_refresh_loop() -> None:
    await asyncio.sleep(15.0)
    while True:
        try:
            res = await ensure_fresh_ctrader_token()
            if res.get("refreshed"):
                await market_service.on_settings_updated()
        except Exception:
            pass
        await asyncio.sleep(3600.0)


async def _ibkr_auto_sync_loop() -> None:
    await asyncio.sleep(30.0)
    while True:
        interval_min = 360
        try:
            settings = get_all_settings()
            interval_min = max(15, int(settings.get("ibkr_sync_interval_min") or "360"))
            if settings.get("ibkr_auto_sync_enabled") == "1" and (settings.get("ibkr_flex_token") or "").strip():
                await sync_ibkr_flex()
        except Exception:
            pass
        await asyncio.sleep(interval_min * 60.0)


@app.on_event("startup")
async def on_startup() -> None:
    init_db(seed_demo=True)
    asyncio.create_task(_email_auto_sync_loop())
    asyncio.create_task(_bark_due_reminder_loop())
    asyncio.create_task(_ctrader_auto_sync_loop())
    asyncio.create_task(_ctrader_token_auto_refresh_loop())
    asyncio.create_task(_ibkr_auto_sync_loop())
    asyncio.create_task(_startup_update_notify_task())
    asyncio.create_task(_startup_legacy_deals_repair_task())
    await market_service.ensure_started_from_settings()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await market_service.stop()


# ---------------- Auth & Health ----------------


# ---------------- cTrader Orders & Live Market Tick ----------------


def _is_market_authorized(
    headers: Any,
    query_params: Any,
    cookies: Any,
    session_user: Any = None,
) -> bool:
    configured = market_service.get_api_token()
    if not configured:
        return True
    if session_user:
        return True
    cookie_token = (cookies.get(SESSION_COOKIE_NAME) or "").strip() if cookies else ""
    if cookie_token:
        with get_conn() as conn:
            if get_session_user(conn, cookie_token):
                return True
    auth_header = (headers.get("authorization") or "") if headers else ""
    scheme, _, bearer = auth_header.partition(" ")
    supplied = bearer.strip() if scheme.lower() == "bearer" else ""
    if not supplied and headers:
        supplied = (headers.get("x-api-key") or "").strip()
    if not supplied and query_params:
        supplied = (query_params.get("token") or "").strip()
    if not supplied:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), configured.encode("utf-8"))


@app.get("/api/ctrader/deals")
def api_ctrader_deals(limit: int = 5000) -> Dict[str, Any]:
    return list_ctrader_deals(limit=limit)


@app.get("/api/ctrader/sync/status")
def api_ctrader_sync_status() -> Dict[str, Any]:
    return get_ctrader_sync_status()


@app.post("/api/ctrader/sync")
async def api_ctrader_sync(payload: CTraderSyncRequest) -> Dict[str, Any]:
    try:
        if payload.background:
            return start_background_ctrader_sync(
                days=payload.days,
                push_new=payload.push_new,
                mode=payload.mode,
                start_date=payload.start_date,
                end_date=payload.end_date,
                overlap_seconds=payload.overlap_seconds,
            )
        return await sync_ctrader_deals(
            days=payload.days,
            push_new=payload.push_new,
            mode=payload.mode,
            start_date=payload.start_date,
            end_date=payload.end_date,
            overlap_seconds=payload.overlap_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ctrader/test")
async def api_ctrader_test() -> Dict[str, Any]:
    try:
        return await test_ctrader_connection()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ctrader/oauth/authorize-url")
def api_ctrader_oauth_authorize_url(
    redirect_uri: str = Query(...),
    scope: str = Query("accounts"),
) -> Dict[str, Any]:
    try:
        cfg = get_all_settings()
        url = build_authorize_url(
            client_id=cfg.get("ctrader_client_id", ""),
            redirect_uri=redirect_uri,
            scope=scope,
        )
        return {"ok": True, "url": url}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ctrader/oauth/exchange")
async def api_ctrader_oauth_exchange(payload: CTraderOAuthExchangeRequest) -> Dict[str, Any]:
    try:
        res = await exchange_authorization_code(
            code_or_url=payload.code_or_url,
            redirect_uri=payload.redirect_uri or "",
        )
        await market_service.on_settings_updated()
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip() or repr(exc)) from exc


@app.post("/api/ctrader/oauth/refresh")
async def api_ctrader_oauth_refresh(
    payload: Optional[CTraderOAuthRefreshRequest] = None,
) -> Dict[str, Any]:
    try:
        res = await refresh_ctrader_token(
            refresh_token_override=payload.refresh_token if payload else None,
            notify_bark=False,
        )
        await market_service.on_settings_updated()
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip() or repr(exc)) from exc


@app.get("/api/ctrader/oauth/callback", response_class=HTMLResponse)
async def api_ctrader_oauth_callback(
    request: Request,
    code: str = Query(""),
    error: str = Query(""),
    error_description: str = Query(""),
) -> HTMLResponse:
    import html as _html

    if error or not code:
        err_msg = _html.escape(error_description or error or "未收到 cTrader 返回的授权 code")
        return HTMLResponse(
            status_code=400,
            content=f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>cTrader 授权失败</title></head>
<body style="font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;padding:24px;line-height:1.6;">
<h2 style="color:#dc2626;">cTrader OAuth 授权失败</h2>
<p>{err_msg}</p>
<p style="color:#64748b;font-size:14px;">请检查 cTrader 应用后台的 Redirect URI 是否与当前地址完全一致，或返回 LifeHub 设置页重试。</p>
<p><a href="/" style="color:#d97706;">返回 LifeHub</a></p>
</body></html>""",
        )

    try:
        callback_url = str(request.url.replace(query=""))
        saved_redirect = (get_all_settings().get("ctrader_oauth_redirect_uri") or "").strip() or callback_url
        res = await exchange_authorization_code(code_or_url=code, redirect_uri=saved_redirect)
        await market_service.on_settings_updated()
        exp_at = _html.escape(str(res.get("expires_at") or ""))
        return HTMLResponse(
            content=f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>cTrader 授权成功</title></head>
<body style="font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;padding:24px;line-height:1.6;">
<h2 style="color:#059669;">cTrader OAuth 授权成功</h2>
<p>已成功保存 Access Token 与 Refresh Token，到期前系统将自动为您续期。</p>
<p>当前有效期至：<strong>{exp_at}</strong></p>
<p style="color:#64748b;font-size:14px;">您可以关闭此页面并回到 LifeHub 设置页刷新查看。</p>
<p><a href="/" style="color:#d97706;">返回 LifeHub</a></p>
</body></html>""",
        )
    except Exception as exc:
        err_msg = _html.escape(str(exc).strip() or repr(exc))
        return HTMLResponse(
            status_code=400,
            content=f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>cTrader Token 换取失败</title></head>
<body style="font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;padding:24px;line-height:1.6;">
<h2 style="color:#dc2626;">Token 换取失败</h2>
<p>{err_msg}</p>
<p><a href="/" style="color:#d97706;">返回 LifeHub</a></p>
</body></html>""",
        )


# ---------------- IBKR Flex Daily Summary ----------------


@app.get("/api/ibkr/overview")
def api_ibkr_overview(account_id: str = "") -> Dict[str, Any]:
    return get_ibkr_overview(account_id=account_id)


@app.get("/api/ibkr/sync/status")
def api_ibkr_sync_status() -> Dict[str, Any]:
    return get_ibkr_sync_status()


@app.post("/api/ibkr/sync")
async def api_ibkr_sync(payload: Optional[IBKRSyncRequest] = None) -> Dict[str, Any]:
    try:
        return await sync_ibkr_flex(
            query_id=payload.query_id if payload else None,
            push_summary=payload.push_summary if payload else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip() or repr(exc)) from exc


@app.post("/api/ibkr/test")
async def api_ibkr_test() -> Dict[str, Any]:
    try:
        return await test_ibkr_connection()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip() or repr(exc)) from exc


@app.get("/api/ctrader/market/status")
def api_ctrader_market_status() -> Dict[str, Any]:
    return market_service.get_detailed_status()


@app.get("/api/ctrader/market/ticks")
def api_ctrader_market_ticks(symbol: str = "", limit: int = Query(50, ge=1, le=300)) -> Dict[str, Any]:
    return {
        "symbol": symbol.upper().strip() or "ALL",
        "ticks": market_service.get_recent_ticks(symbol=symbol, limit=limit),
        "status": market_service.get_detailed_status(),
    }


@app.post("/api/ctrader/market/start")
async def api_ctrader_market_start(payload: Optional[CTraderMarketActionRequest] = None) -> Dict[str, Any]:
    updates: Dict[str, str] = {"ctrader_market_enabled": "1"}
    if payload and payload.symbols is not None and payload.symbols.strip():
        updates["ctrader_market_symbols"] = payload.symbols.strip()
    update_settings(updates)
    await market_service.restart()
    return {"ok": True, "status": market_service.get_detailed_status()}


@app.post("/api/ctrader/market/stop")
async def api_ctrader_market_stop() -> Dict[str, Any]:
    update_settings({"ctrader_market_enabled": "0"})
    await market_service.stop()
    return {"ok": True, "status": market_service.get_detailed_status()}


@app.post("/api/ctrader/market/restart")
async def api_ctrader_market_restart() -> Dict[str, Any]:
    update_settings({"ctrader_market_enabled": "1"})
    await market_service.restart()
    return {"ok": True, "status": market_service.get_detailed_status()}


# ---------------- Public / Token-Protected Market Feed APIs (gold.36xt.com compat) ----------------


@app.get("/status")
def market_public_status(request: Request) -> Dict[str, Any]:
    if not _is_market_authorized(request.headers, request.query_params, request.cookies, getattr(request.state, "auth_user", None)):
        raise HTTPException(status_code=401, detail="访问实时行情接口需要有效的 Token")
    return market_service.get_public_status()


@app.get("/quotes")
def market_public_quotes(request: Request) -> Dict[str, Any]:
    if not _is_market_authorized(request.headers, request.query_params, request.cookies, getattr(request.state, "auth_user", None)):
        raise HTTPException(status_code=401, detail="访问实时行情接口需要有效的 Token")
    return market_service.get_all_quotes()


@app.get("/quote/{symbol}")
def market_public_single_quote(symbol: str, request: Request) -> Dict[str, Any]:
    if not _is_market_authorized(request.headers, request.query_params, request.cookies, getattr(request.state, "auth_user", None)):
        raise HTTPException(status_code=401, detail="访问实时行情接口需要有效的 Token")
    res = market_service.get_single_quote(symbol)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol.upper().strip()}' not found in memory cache.")
    return res


@app.websocket("/ws")
async def market_ws_all(ws: WebSocket) -> None:
    if not _is_market_authorized(ws.headers, ws.query_params, ws.cookies):
        await ws.close(code=1008, reason="Unauthorized")
        return
    await ws.accept()
    market_service.bind_loop(asyncio.get_running_loop())
    market_service.all_subscribers.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        market_service.all_subscribers.discard(ws)


@app.websocket("/ws/{symbol}")
async def market_ws_symbol(ws: WebSocket, symbol: str) -> None:
    if not _is_market_authorized(ws.headers, ws.query_params, ws.cookies):
        await ws.close(code=1008, reason="Unauthorized")
        return
    sym = symbol.upper().strip()
    await ws.accept()
    market_service.bind_loop(asyncio.get_running_loop())
    subs = market_service.symbol_subscribers.setdefault(sym, set())
    subs.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        subs.discard(ws)


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    return {"ok": True, "status": "ok", "time": now_iso(), "build": get_build_info()}


@app.get("/api/auth/status")
def auth_status(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "auth_user", None)
    with get_conn() as conn:
        setup_required = not has_any_user(conn)
        active_sessions = count_active_sessions(conn, user["user_id"]) if user else 0
    return {
        "authenticated": bool(user),
        "setup_required": setup_required,
        "username": user["username"] if user else None,
        "active_sessions": active_sessions,
    }


@app.post("/api/auth/setup")
def auth_setup(payload: AuthSetupRequest, request: Request, response: Response) -> Dict[str, Any]:
    user_agent = request.headers.get("user-agent") or ""
    with get_conn() as conn:
        if has_any_user(conn):
            raise HTTPException(status_code=403, detail="已完成初始化，请直接登录")
        try:
            user = create_user(conn, payload.username, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        token, expires_at, max_age = create_session(conn, user["id"], user["username"], remember_me=True, user_agent=user_agent)

    _set_session_cookie(response, request, token, remember_me=True, max_age=max_age)
    return {"ok": True, "username": user["username"], "expires_at": expires_at}


@app.post("/api/auth/login")
def auth_login(payload: AuthLoginRequest, request: Request, response: Response) -> Dict[str, Any]:
    ip = _client_ip(request)
    wait_s = _login_limiter.check_blocked(ip)
    if wait_s > 0:
        raise HTTPException(status_code=429, detail=f"登录尝试次数过多，请 {wait_s} 秒后再试")

    user_agent = request.headers.get("user-agent") or ""
    with get_conn() as conn:
        if not has_any_user(conn):
            raise HTTPException(status_code=400, detail="尚未初始化管理员账号，请先设置登录密码")
        user = authenticate_user(conn, payload.username, payload.password)
        if not user:
            locked_s = _login_limiter.record_failure(ip)
            if locked_s > 0:
                raise HTTPException(status_code=429, detail=f"密码错误次数过多，已暂时锁定 {locked_s} 秒")
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        _login_limiter.reset(ip)
        token, expires_at, max_age = create_session(
            conn, user["id"], user["username"], remember_me=payload.remember_me, user_agent=user_agent
        )

    _set_session_cookie(response, request, token, remember_me=payload.remember_me, max_age=max_age)
    return {"ok": True, "username": user["username"], "expires_at": expires_at}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> Dict[str, bool]:
    token = getattr(request.state, "auth_token", "")
    if token:
        with get_conn() as conn:
            delete_session(conn, token)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@app.post("/api/auth/logout-all")
def auth_logout_all(request: Request, include_current: bool = Query(False)) -> Dict[str, Any]:
    user = request.state.auth_user
    token = getattr(request.state, "auth_token", "")
    with get_conn() as conn:
        revoked = delete_other_sessions(
            conn,
            user["user_id"],
            except_raw_token=None if include_current else token,
        )
    return {"ok": True, "revoked_count": revoked}


@app.post("/api/auth/change-password")
def auth_change_password(payload: AuthChangePasswordRequest, request: Request) -> Dict[str, Any]:
    user = request.state.auth_user
    token = getattr(request.state, "auth_token", "")
    with get_conn() as conn:
        try:
            updated = update_user_credentials(
                conn,
                user["user_id"],
                current_password=payload.current_password,
                new_password=payload.new_password,
                new_username=payload.new_username,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        revoked = delete_other_sessions(conn, user["user_id"], except_raw_token=token)
    return {"ok": True, "username": updated["username"], "revoked_other_sessions": revoked}


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def _compute_finance_snapshot(conn: Any) -> Dict[str, Any]:
    accounts = [_row_to_dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()]
    total_assets = sum(a["balance"] for a in accounts if a["balance"] > 0 and a["type"] not in ("credit_card", "loan"))
    total_liabilities = sum(abs(a["balance"]) for a in accounts if a["balance"] < 0 or a["type"] in ("credit_card", "loan"))
    # If a credit_card/loan account has positive balance, treat as prepayment/asset, negative as liability
    total_assets = sum(a["balance"] for a in accounts if a["balance"] > 0)
    total_liabilities = sum(abs(a["balance"]) for a in accounts if a["balance"] < 0)
    net_worth = round(total_assets - total_liabilities, 2)

    unsettled_rows = conn.execute(
        "SELECT * FROM transactions WHERE settlement_id IS NULL ORDER BY occurred_at DESC, id DESC"
    ).fetchall()
    unsettled = [_row_to_dict(r) for r in unsettled_rows]

    unsettled_income = round(sum(t["amount"] for t in unsettled if t["type"] == "income"), 2)
    unsettled_expense = round(sum(t["amount"] for t in unsettled if t["type"] == "expense"), 2)
    category_breakdown: Dict[str, float] = {}
    for t in unsettled:
        if t["type"] == "expense":
            cat = t["category"] or "其他"
            category_breakdown[cat] = round(category_breakdown.get(cat, 0.0) + t["amount"], 2)

    return {
        "accounts": accounts,
        "total_assets": round(total_assets, 2),
        "total_liabilities": round(total_liabilities, 2),
        "net_worth": net_worth,
        "unsettled_transactions": unsettled,
        "unsettled_count": len(unsettled),
        "unsettled_income": unsettled_income,
        "unsettled_expense": unsettled_expense,
        "unsettled_net_cashflow": round(unsettled_income - unsettled_expense, 2),
        "category_breakdown": category_breakdown,
    }


def _apply_tx_to_account(conn: Any, account_id: Optional[int], tx_type: str, amount: float, reverse: bool = False) -> None:
    if not account_id:
        return
    factor = -1.0 if reverse else 1.0
    delta = amount * factor if tx_type == "income" else (-amount * factor if tx_type == "expense" else 0.0)
    if delta != 0.0:
        conn.execute(
            "UPDATE accounts SET balance = ROUND(balance + ?, 2), updated_at = ? WHERE id = ?",
            (delta, now_iso(), account_id),
        )


def _advance_due_date(due_date_str: str, cycle: str, billing_day: int) -> str:
    try:
        dt = datetime.strptime(due_date_str[:10], "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    if cycle == "weekly":
        nxt = dt + timedelta(days=7)
    elif cycle == "yearly":
        try:
            nxt = dt.replace(year=dt.year + 1)
        except ValueError:
            nxt = dt + timedelta(days=365)
    else:
        # monthly
        next_month_first = (dt.replace(day=28) + timedelta(days=4)).replace(day=1)
        day = min(max(billing_day, 1), 28)
        nxt = next_month_first.replace(day=day)
    return nxt.strftime("%Y-%m-%d")


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "time": now_iso()}


@app.get("/api/dashboard")
async def get_dashboard() -> Dict[str, Any]:
    init_db(seed_demo=False)
    settings = get_all_settings()
    with get_conn() as conn:
        new_phone_rems = _sync_phone_reminders_conn(conn)
        pending_reminders = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM reminders WHERE status = 'pending' ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, COALESCE(due_at, '9999') ASC, id DESC"
            ).fetchall()
        ]
        done_count = conn.execute("SELECT COUNT(*) FROM reminders WHERE status = 'done'").fetchone()[0]
        finance = _compute_finance_snapshot(conn)
        bills = [
            _row_to_dict(r)
            for r in conn.execute("SELECT * FROM recurring_bills WHERE active = 1 ORDER BY next_due_date ASC").fetchall()
        ]
        latest_settlement = conn.execute("SELECT * FROM settlements ORDER BY id DESC LIMIT 1").fetchone()
        settlement_dict = _row_to_dict(latest_settlement)
        if settlement_dict:
            settlement_dict["category_breakdown"] = json.loads(settlement_dict.get("category_breakdown_json") or "{}")

    now_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    due_today = [r for r in pending_reminders if (r.get("due_at") or "").startswith(now_str)]
    overdue = [r for r in pending_reminders if r.get("due_at") and r["due_at"][:10] < now_str]

    if new_phone_rems:
        await _push_new_reminders_bark(new_phone_rems, prefix="📱 保号提前提醒：")

    return {
        "reminders_summary": {
            "pending_count": len(pending_reminders),
            "done_count": done_count,
            "due_today_count": len(due_today),
            "overdue_count": len(overdue),
            "upcoming": pending_reminders[:8],
        },
        "finance_summary": {
            "total_assets": finance["total_assets"],
            "total_liabilities": finance["total_liabilities"],
            "net_worth": finance["net_worth"],
            "unsettled_count": finance["unsettled_count"],
            "unsettled_income": finance["unsettled_income"],
            "unsettled_expense": finance["unsettled_expense"],
            "unsettled_net_cashflow": finance["unsettled_net_cashflow"],
            "category_breakdown": finance["category_breakdown"],
            "accounts": finance["accounts"],
            "recent_unsettled": finance["unsettled_transactions"][:6],
        },
        "recurring_bills": bills[:5],
        "latest_settlement": settlement_dict or None,
        "config_status": {
            "ai_provider": settings.get("ai_provider", "local_fallback"),
            "siyuan_mode": settings.get("siyuan_mode", "mcp_stdio"),
            "build": get_build_info(),
        },
    }


# ---------------- Reminders ----------------

@app.get("/api/reminders")
def list_reminders(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM reminders WHERE 1=1"
    params: List[Any] = []
    if status and status != "all":
        sql += " AND status = ?"
        params.append(status)
    if category and category != "all":
        sql += " AND category = ?"
        params.append(category)
    if source_type and source_type != "all":
        sql += " AND source_type = ?"
        params.append(source_type)
    if q:
        sql += " AND (title LIKE ? OR description LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'snoozed' THEN 1 ELSE 2 END, CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, COALESCE(due_at, '9999') ASC, id DESC"
    with get_conn() as conn:
        return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


@app.post("/api/reminders")
async def create_reminder(payload: ReminderCreate) -> Dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO reminders
            (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.title.strip(),
                payload.description.strip(),
                payload.due_at,
                payload.priority,
                payload.category,
                payload.status,
                payload.source_type,
                payload.source_ref,
                payload.linked_amount,
                payload.linked_tx_type,
                ts,
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()
        rem = _row_to_dict(row)
    await _push_new_reminders_bark([rem])
    return rem


@app.post("/api/reminders/batch-import")
async def batch_import_reminders(payload: BatchImportRemindersRequest) -> Dict[str, Any]:
    ts = now_iso()
    created: List[Dict[str, Any]] = []
    with get_conn() as conn:
        for item in payload.reminders:
            cur = conn.execute(
                """INSERT INTO reminders
                (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.title.strip(),
                    item.description.strip(),
                    item.due_at,
                    item.priority,
                    item.category,
                    item.status,
                    item.source_type or "siyuan_note",
                    item.source_ref,
                    item.linked_amount,
                    item.linked_tx_type,
                    ts,
                    ts,
                ),
            )
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()
            created.append(_row_to_dict(row))
    await _push_new_reminders_bark(created, prefix="📝 笔记提取提醒：")
    return {"imported_count": len(created), "items": created}


@app.post("/api/reminders/{reminder_id}/push-bark")
async def push_single_reminder_bark(reminder_id: int) -> Dict[str, Any]:
    settings = get_all_settings()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="提醒不存在")
        rem = _row_to_dict(row)
    msg = format_reminder_bark_message(rem, prefix="🔔 提醒通知：")
    res = await send_bark_push(
        title=msg["title"],
        body=msg["body"],
        settings=settings,
        priority=str(rem.get("priority") or "medium"),
        force=True,
    )
    if res.get("ok"):
        with get_conn() as conn:
            conn.execute("UPDATE reminders SET bark_notified_at = ? WHERE id = ?", (now_iso(), reminder_id))
    return res


@app.patch("/api/reminders/{reminder_id}")
def update_reminder(reminder_id: int, payload: ReminderUpdate) -> Dict[str, Any]:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    params = list(updates.values()) + [reminder_id]
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE reminders SET {sets} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="提醒不存在")
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return _row_to_dict(row)


@app.post("/api/reminders/{reminder_id}/complete-and-record")
def complete_and_record(reminder_id: int, account_id: Optional[int] = Query(None)) -> Dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="提醒不存在")
        rem = _row_to_dict(row)
        conn.execute("UPDATE reminders SET status = 'done', updated_at = ? WHERE id = ?", (ts, reminder_id))
        created_tx = None
        if rem.get("linked_amount") and rem["linked_amount"] > 0:
            tx_type = rem.get("linked_tx_type") or "expense"
            target_acc = account_id
            if not target_acc:
                acc = conn.execute("SELECT id FROM accounts ORDER BY id ASC LIMIT 1").fetchone()
                target_acc = acc["id"] if acc else None
            cur = conn.execute(
                """INSERT INTO transactions
                (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 'reminder', ?)""",
                (
                    tx_type,
                    rem["linked_amount"],
                    "账单" if rem["category"] == "finance" else "其他",
                    target_acc,
                    f"完成提醒入账：{rem['title']}",
                    datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
                    ts,
                ),
            )
            _apply_tx_to_account(conn, target_acc, tx_type, float(rem["linked_amount"]))
            tx_row = conn.execute("SELECT * FROM transactions WHERE id = ?", (cur.lastrowid,)).fetchone()
            created_tx = _row_to_dict(tx_row)

        updated_rem = _row_to_dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())
        return {"reminder": updated_rem, "transaction": created_tx}


@app.delete("/api/reminders/{reminder_id}")
def delete_reminder(reminder_id: int) -> Dict[str, bool]:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="提醒不存在")
        return {"deleted": True}


# ---------------- AI Chat ----------------

@app.get("/api/chat")
def list_chat_messages() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM chat_messages ORDER BY id ASC LIMIT 100").fetchall()
        res = []
        for r in rows:
            d = _row_to_dict(r)
            d["actions"] = json.loads(d.get("actions_json") or "[]")
            res.append(d)
        return res


@app.delete("/api/chat")
def clear_chat_messages() -> Dict[str, bool]:
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_messages")
    return {"cleared": True}


@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest) -> Dict[str, Any]:
    settings = get_all_settings()
    ts = now_iso()

    with get_conn() as conn:
        finance = _compute_finance_snapshot(conn)
        top_reminders = [
            _row_to_dict(r)
            for r in conn.execute("SELECT title, due_at, priority FROM reminders WHERE status='pending' ORDER BY COALESCE(due_at, '9999') ASC LIMIT 6").fetchall()
        ]
        pending_count = conn.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0]
        context_summary = {
            "pending_reminders_count": pending_count,
            "top_reminders": top_reminders,
            "net_worth": finance["net_worth"],
            "unsettled_income": finance["unsettled_income"],
            "unsettled_expense": finance["unsettled_expense"],
            "unsettled_count": finance["unsettled_count"],
        }
        conn.execute(
            "INSERT INTO chat_messages (role, content, actions_json, created_at) VALUES (?, ?, ?, ?)",
            ("user", payload.message.strip(), "[]", ts),
        )

    parsed = await process_chat_message(payload.message.strip(), context_summary, settings)

    actions: List[Dict[str, Any]] = []
    created_reminders: List[Dict[str, Any]] = []
    created_transactions: List[Dict[str, Any]] = []

    with get_conn() as conn:
        default_acc = conn.execute("SELECT id FROM accounts ORDER BY id ASC LIMIT 1").fetchone()
        default_acc_id = default_acc["id"] if default_acc else None

        for r in parsed.get("reminders", []):
            title = str(r.get("title") or "").strip()
            if not title:
                continue
            cur = conn.execute(
                """INSERT INTO reminders
                (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', 'ai_chat', 'AI 对话', ?, ?, ?, ?)""",
                (
                    title,
                    str(r.get("description") or ""),
                    r.get("due_at"),
                    r.get("priority") or "medium",
                    r.get("category") or "life",
                    r.get("linked_amount"),
                    r.get("linked_tx_type"),
                    ts,
                    ts,
                ),
            )
            rem_dict = _row_to_dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone())
            created_reminders.append(rem_dict)
            actions.append({"kind": "reminder", "id": rem_dict["id"], "title": rem_dict["title"], "due_at": rem_dict["due_at"]})

        for t in parsed.get("transactions", []):
            try:
                amount = float(t.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount <= 0:
                continue
            tx_type = t.get("type") if t.get("type") in ("income", "expense") else "expense"
            cat = str(t.get("category") or "其他")
            desc = str(t.get("description") or "")
            occ = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
            cur = conn.execute(
                """INSERT INTO transactions
                (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 'ai_chat', ?)""",
                (tx_type, amount, cat, default_acc_id, desc, occ, ts),
            )
            _apply_tx_to_account(conn, default_acc_id, tx_type, amount)
            tx_dict = _row_to_dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (cur.lastrowid,)).fetchone())
            created_transactions.append(tx_dict)
            actions.append({"kind": "transaction", "id": tx_dict["id"], "type": tx_type, "amount": amount, "category": cat})

        reply_text = parsed.get("reply") or "已处理。"
        if parsed.get("warning"):
            reply_text = f"{reply_text}\n\n（提示：{parsed['warning']}）"

        cur_msg = conn.execute(
            "INSERT INTO chat_messages (role, content, actions_json, created_at) VALUES (?, ?, ?, ?)",
            ("assistant", reply_text, json.dumps(actions, ensure_ascii=False), now_iso()),
        )
        assistant_msg = _row_to_dict(conn.execute("SELECT * FROM chat_messages WHERE id = ?", (cur_msg.lastrowid,)).fetchone())
        assistant_msg["actions"] = actions

    await _push_new_reminders_bark(created_reminders, prefix="💬 AI 提醒：")

    return {
        "message": assistant_msg,
        "created_reminders": created_reminders,
        "created_transactions": created_transactions,
        "engine": parsed.get("engine"),
    }


# ---------------- SiYuan MCP & Note Extraction ----------------

@app.post("/api/siyuan/test")
async def test_siyuan() -> Dict[str, Any]:
    settings = get_all_settings()
    return await test_siyuan_connection(settings)


@app.post("/api/siyuan/fetch-notes")
async def fetch_notes_endpoint(payload: SiyuanFetchRequest) -> Dict[str, Any]:
    settings = get_all_settings()
    return await fetch_siyuan_notes(
        settings=settings,
        keyword=payload.keyword,
        custom_sql=payload.sql,
        limit=payload.limit,
    )


@app.post("/api/notes/extract")
async def extract_notes_endpoint(payload: NoteExtractRequest) -> Dict[str, Any]:
    settings = get_all_settings()
    notes_list = [n.model_dump() for n in payload.notes]
    if payload.raw_text.strip():
        notes_list.append(
            {
                "id": "manual-paste",
                "title": "手动粘贴笔记内容",
                "path": "/手动导入",
                "content": payload.raw_text.strip(),
                "updated": now_iso(),
            }
        )
    return await extract_reminders_from_notes(notes_list, settings)


# ---------------- Finance: Accounts, Transactions, Recurring Bills, Settlement ----------------

@app.get("/api/accounts")
def list_accounts() -> Dict[str, Any]:
    with get_conn() as conn:
        snap = _compute_finance_snapshot(conn)
        return {
            "accounts": snap["accounts"],
            "total_assets": snap["total_assets"],
            "total_liabilities": snap["total_liabilities"],
            "net_worth": snap["net_worth"],
        }


@app.post("/api/accounts")
def create_account(payload: AccountCreate) -> Dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO accounts (name, type, balance, credit_limit, currency, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (payload.name.strip(), payload.type, round(payload.balance, 2), round(payload.credit_limit, 2), payload.currency, ts),
        )
        return _row_to_dict(conn.execute("SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.patch("/api/accounts/{account_id}")
def update_account(account_id: int, payload: AccountUpdate) -> Dict[str, Any]:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    params = list(updates.values()) + [account_id]
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE accounts SET {sets} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="账户不存在")
        return _row_to_dict(conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int) -> Dict[str, bool]:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="账户不存在")
        return {"deleted": True}


@app.get("/api/transactions")
def list_transactions(
    settled: Optional[str] = Query(None),
    settlement_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    tx_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
) -> Dict[str, Any]:
    sql = """SELECT t.*, a.name AS account_name
             FROM transactions t
             LEFT JOIN accounts a ON t.account_id = a.id
             WHERE 1=1"""
    params: List[Any] = []
    if settled == "unsettled":
        sql += " AND t.settlement_id IS NULL"
    elif settled == "settled":
        sql += " AND t.settlement_id IS NOT NULL"
    if settlement_id is not None:
        sql += " AND t.settlement_id = ?"
        params.append(settlement_id)
    if category:
        sql += " AND t.category = ?"
        params.append(category)
    if tx_type:
        sql += " AND t.type = ?"
        params.append(tx_type)
    if q and q.strip():
        like_q = f"%{q.strip()}%"
        sql += " AND (t.description LIKE ? OR t.category LIKE ?)"
        params.extend([like_q, like_q])
    sql += " ORDER BY t.occurred_at DESC, t.id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        items = [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        snap = _compute_finance_snapshot(conn)
        return {
            "transactions": items,
            "unsettled_summary": {
                "count": snap["unsettled_count"],
                "income": snap["unsettled_income"],
                "expense": snap["unsettled_expense"],
                "net_cashflow": snap["unsettled_net_cashflow"],
                "category_breakdown": snap["category_breakdown"],
            },
        }


@app.post("/api/transactions")
def create_transaction(payload: TransactionCreate) -> Dict[str, Any]:
    ts = now_iso()
    occ = payload.occurred_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO transactions
            (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (payload.type, round(payload.amount, 2), payload.category, payload.account_id, payload.description.strip(), occ, payload.source_type, ts),
        )
        _apply_tx_to_account(conn, payload.account_id, payload.type, float(payload.amount))
        row = conn.execute(
            "SELECT t.*, a.name AS account_name FROM transactions t LEFT JOIN accounts a ON t.account_id = a.id WHERE t.id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return _row_to_dict(row)


@app.patch("/api/transactions/{tx_id}")
def update_transaction(tx_id: int, payload: TransactionUpdate) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    if payload.category is not None:
        updates["category"] = payload.category.strip() or "其他"
    if payload.description is not None:
        updates["description"] = payload.description.strip()
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    params = list(updates.values()) + [tx_id]
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE transactions SET {sets} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="账目不存在")
        row = conn.execute(
            "SELECT t.*, a.name AS account_name FROM transactions t LEFT JOIN accounts a ON t.account_id = a.id WHERE t.id = ?",
            (tx_id,),
        ).fetchone()
        return _row_to_dict(row)


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int) -> Dict[str, bool]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="账目不存在")
        tx = _row_to_dict(row)
        _apply_tx_to_account(conn, tx.get("account_id"), tx["type"], float(tx["amount"]), reverse=True)
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        return {"deleted": True}


@app.get("/api/recurring-bills")
def list_recurring_bills() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT b.*, a.name AS account_name FROM recurring_bills b LEFT JOIN accounts a ON b.account_id = a.id ORDER BY b.next_due_date ASC"
            ).fetchall()
        ]


@app.post("/api/recurring-bills")
def create_recurring_bill(payload: RecurringBillCreate) -> Dict[str, Any]:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO recurring_bills
            (name, amount, category, account_id, billing_day, cycle, next_due_date, auto_remind_days, active, last_generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                payload.name.strip(),
                round(payload.amount, 2),
                payload.category,
                payload.account_id,
                payload.billing_day,
                payload.cycle,
                payload.next_due_date,
                payload.auto_remind_days,
                1 if payload.active else 0,
            ),
        )
        return _row_to_dict(conn.execute("SELECT * FROM recurring_bills WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.patch("/api/recurring-bills/{bill_id}")
def update_recurring_bill(bill_id: int, payload: RecurringBillUpdate) -> Dict[str, Any]:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "active" in updates:
        updates["active"] = 1 if updates["active"] else 0
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    params = list(updates.values()) + [bill_id]
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE recurring_bills SET {sets} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="固定账单不存在")
        return _row_to_dict(conn.execute("SELECT * FROM recurring_bills WHERE id = ?", (bill_id,)).fetchone())


@app.delete("/api/recurring-bills/{bill_id}")
def delete_recurring_bill(bill_id: int) -> Dict[str, bool]:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM recurring_bills WHERE id = ?", (bill_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="固定账单不存在")
        return {"deleted": True}


@app.post("/api/recurring-bills/sync-reminders")
async def sync_recurring_bill_reminders(force_all: bool = Query(False)) -> Dict[str, Any]:
    now = datetime.now().astimezone()
    ts = now_iso()
    created: List[Dict[str, Any]] = []
    with get_conn() as conn:
        bills = [_row_to_dict(r) for r in conn.execute("SELECT * FROM recurring_bills WHERE active = 1").fetchall()]
        for b in bills:
            try:
                due_dt = datetime.strptime(b["next_due_date"][:10], "%Y-%m-%d").replace(tzinfo=now.tzinfo)
            except ValueError:
                continue
            days_until = (due_dt.date() - now.date()).days
            if force_all or days_until <= int(b.get("auto_remind_days", 3)):
                source_ref = f"周期账单 #{b['id']} ({b['next_due_date']})"
                exists = conn.execute(
                    "SELECT id FROM reminders WHERE source_type = 'recurring_bill' AND source_ref = ? AND status = 'pending'",
                    (source_ref,),
                ).fetchone()
                if not exists:
                    cur = conn.execute(
                        """INSERT INTO reminders
                        (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
                        VALUES (?, ?, ?, 'high', 'finance', 'pending', 'recurring_bill', ?, ?, 'expense', ?, ?)""",
                        (
                            f"待缴固定账单：{b['name']} (¥{b['amount']:.2f})",
                            f"周期性{b['cycle']}账单，分类：{b['category']}，到期日：{b['next_due_date']}",
                            f"{b['next_due_date']}T09:00",
                            source_ref,
                            b["amount"],
                            ts,
                            ts,
                        ),
                    )
                    created.append(_row_to_dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()))
                    conn.execute("UPDATE recurring_bills SET last_generated_at = ? WHERE id = ?", (ts, b["id"]))
    await _push_new_reminders_bark(created, prefix="💳 账单提醒：")
    return {"created_count": len(created), "reminders": created}


@app.post("/api/recurring-bills/{bill_id}/pay")
def pay_recurring_bill(bill_id: int) -> Dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM recurring_bills WHERE id = ?", (bill_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="固定账单不存在")
        b = _row_to_dict(row)
        occ = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        cur_tx = conn.execute(
            """INSERT INTO transactions
            (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, created_at)
            VALUES ('expense', ?, ?, ?, ?, ?, NULL, 'recurring_bill', ?)""",
            (b["amount"], b["category"], b.get("account_id"), f"周期账单扣款：{b['name']}", occ, ts),
        )
        _apply_tx_to_account(conn, b.get("account_id"), "expense", float(b["amount"]))
        next_due = _advance_due_date(b["next_due_date"], b["cycle"], int(b["billing_day"]))
        conn.execute("UPDATE recurring_bills SET next_due_date = ?, last_generated_at = ? WHERE id = ?", (next_due, ts, bill_id))
        # Mark related pending reminder done
        conn.execute(
            "UPDATE reminders SET status = 'done', updated_at = ? WHERE source_type = 'recurring_bill' AND source_ref LIKE ? AND status = 'pending'",
            (ts, f"周期账单 #{bill_id}%"),
        )
        tx_dict = _row_to_dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (cur_tx.lastrowid,)).fetchone())
        bill_dict = _row_to_dict(conn.execute("SELECT * FROM recurring_bills WHERE id = ?", (bill_id,)).fetchone())
        return {"transaction": tx_dict, "bill": bill_dict}


# ---------------- Settlements (结账功能) ----------------

@app.get("/api/settlements")
def list_settlements() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM settlements ORDER BY id DESC").fetchall()
        res = []
        for r in rows:
            d = _row_to_dict(r)
            d["category_breakdown"] = json.loads(d.get("category_breakdown_json") or "{}")
            d["account_snapshot"] = json.loads(d.get("account_snapshot_json") or "[]")
            res.append(d)
        return res


@app.post("/api/settlements/settle")
async def create_settlement(payload: SettlementCreateRequest) -> Dict[str, Any]:
    settings = get_all_settings()
    ts = now_iso()

    with get_conn() as conn:
        if payload.transaction_ids:
            placeholders = ",".join("?" for _ in payload.transaction_ids)
            tx_rows = conn.execute(
                f"SELECT * FROM transactions WHERE settlement_id IS NULL AND id IN ({placeholders}) ORDER BY occurred_at ASC",
                payload.transaction_ids,
            ).fetchall()
        else:
            tx_rows = conn.execute(
                "SELECT * FROM transactions WHERE settlement_id IS NULL ORDER BY occurred_at ASC"
            ).fetchall()

        txs = [_row_to_dict(r) for r in tx_rows]
        if not txs:
            raise HTTPException(status_code=400, detail="当前没有待结账的收支流水，请先记一笔或等待新账目产生。")

        finance = _compute_finance_snapshot(conn)
        total_income = round(sum(t["amount"] for t in txs if t["type"] == "income"), 2)
        total_expense = round(sum(t["amount"] for t in txs if t["type"] == "expense"), 2)
        net_cashflow = round(total_income - total_expense, 2)

        breakdown: Dict[str, float] = {}
        for t in txs:
            if t["type"] == "expense":
                cat = t["category"] or "其他"
                breakdown[cat] = round(breakdown.get(cat, 0.0) + t["amount"], 2)

        dates = sorted([t["occurred_at"][:10] for t in txs if t.get("occurred_at")])
        start_date = payload.start_date or (dates[0] if dates else datetime.now().strftime("%Y-%m-%d"))
        end_date = payload.end_date or (dates[-1] if dates else datetime.now().strftime("%Y-%m-%d"))
        period_label = payload.period_label.strip() or f"{datetime.now().strftime('%Y-%m-%d')} 周期结账"

        account_snapshot = [
            {"name": a["name"], "type": a["type"], "balance": a["balance"]}
            for a in finance["accounts"]
        ]

    summary_input = {
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "total_income": total_income,
        "total_expense": total_expense,
        "net_cashflow": net_cashflow,
        "net_worth_snapshot": finance["net_worth"],
        "total_assets": finance["total_assets"],
        "total_liabilities": finance["total_liabilities"],
        "tx_count": len(txs),
        "category_breakdown": breakdown,
    }
    ai_summary = await generate_settlement_summary(summary_input, settings)

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO settlements
            (period_label, start_date, end_date, total_income, total_expense, net_cashflow, net_worth_snapshot, tx_count, category_breakdown_json, account_snapshot_json, ai_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                period_label,
                start_date,
                end_date,
                total_income,
                total_expense,
                net_cashflow,
                finance["net_worth"],
                len(txs),
                json.dumps(breakdown, ensure_ascii=False),
                json.dumps(account_snapshot, ensure_ascii=False),
                ai_summary,
                ts,
            ),
        )
        settlement_id = cur.lastrowid
        tx_ids = [t["id"] for t in txs]
        placeholders = ",".join("?" for _ in tx_ids)
        conn.execute(f"UPDATE transactions SET settlement_id = ? WHERE id IN ({placeholders})", [settlement_id] + tx_ids)

        row = conn.execute("SELECT * FROM settlements WHERE id = ?", (settlement_id,)).fetchone()
        res = _row_to_dict(row)
        res["category_breakdown"] = breakdown
        res["account_snapshot"] = account_snapshot
        return res


@app.get("/api/settlements/{settlement_id}/transactions")
def get_settlement_transactions(
    settlement_id: int,
    category: Optional[str] = Query(None),
    tx_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> Dict[str, Any]:
    with get_conn() as conn:
        st_row = conn.execute("SELECT * FROM settlements WHERE id = ?", (settlement_id,)).fetchone()
        if not st_row:
            raise HTTPException(status_code=404, detail="结账记录不存在")
        settlement = _row_to_dict(st_row)
        settlement["category_breakdown"] = json.loads(settlement.get("category_breakdown_json") or "{}")
        settlement["account_snapshot"] = json.loads(settlement.get("account_snapshot_json") or "[]")

        all_txs = [
            _row_to_dict(r)
            for r in conn.execute(
                """SELECT t.*, a.name AS account_name
                   FROM transactions t
                   LEFT JOIN accounts a ON t.account_id = a.id
                   WHERE t.settlement_id = ?
                   ORDER BY t.occurred_at DESC, t.id DESC""",
                (settlement_id,),
            ).fetchall()
        ]

    stats_map: Dict[tuple, Dict[str, Any]] = {}
    for t in all_txs:
        cat = t.get("category") or "其他"
        tp = t.get("type") or "expense"
        key = (tp, cat)
        if key not in stats_map:
            stats_map[key] = {"type": tp, "category": cat, "count": 0, "total": 0.0}
        stats_map[key]["count"] += 1
        stats_map[key]["total"] = round(stats_map[key]["total"] + float(t["amount"]), 2)

    category_stats = sorted(stats_map.values(), key=lambda x: x["total"], reverse=True)

    filtered = all_txs
    if tx_type:
        filtered = [t for t in filtered if t["type"] == tx_type]
    if category:
        filtered = [t for t in filtered if (t.get("category") or "其他") == category]
    if q and q.strip():
        kw = q.strip().lower()
        filtered = [
            t
            for t in filtered
            if kw in (t.get("description") or "").lower() or kw in (t.get("category") or "").lower()
        ]

    return {
        "settlement": settlement,
        "category_stats": category_stats,
        "transactions": filtered,
    }


@app.post("/api/settlements/{settlement_id}/regenerate")
async def regenerate_settlement(
    settlement_id: int,
    payload: Optional[SettlementRegenerateRequest] = None,
) -> Dict[str, Any]:
    settings = get_all_settings()
    with get_conn() as conn:
        st_row = conn.execute("SELECT * FROM settlements WHERE id = ?", (settlement_id,)).fetchone()
        if not st_row:
            raise HTTPException(status_code=404, detail="结账记录不存在")
        st = _row_to_dict(st_row)
        txs = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM transactions WHERE settlement_id = ? ORDER BY occurred_at ASC",
                (settlement_id,),
            ).fetchall()
        ]
        if not txs:
            raise HTTPException(status_code=400, detail="该结账记录下没有关联流水，无法重新生成")

        finance = _compute_finance_snapshot(conn)
        total_income = round(sum(t["amount"] for t in txs if t["type"] == "income"), 2)
        total_expense = round(sum(t["amount"] for t in txs if t["type"] == "expense"), 2)
        net_cashflow = round(total_income - total_expense, 2)

        breakdown: Dict[str, float] = {}
        for t in txs:
            if t["type"] == "expense":
                cat = t["category"] or "其他"
                breakdown[cat] = round(breakdown.get(cat, 0.0) + t["amount"], 2)

        dates = sorted([t["occurred_at"][:10] for t in txs if t.get("occurred_at")])
        start_date = dates[0] if dates else st["start_date"]
        end_date = dates[-1] if dates else st["end_date"]
        period_label = (payload.period_label or "").strip() if payload and payload.period_label is not None else st["period_label"]
        if not period_label:
            period_label = st["period_label"]

    summary_input = {
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "total_income": total_income,
        "total_expense": total_expense,
        "net_cashflow": net_cashflow,
        "net_worth_snapshot": st["net_worth_snapshot"],
        "total_assets": finance["total_assets"],
        "total_liabilities": finance["total_liabilities"],
        "tx_count": len(txs),
        "category_breakdown": breakdown,
    }
    ai_summary = await generate_settlement_summary(summary_input, settings)

    with get_conn() as conn:
        conn.execute(
            """UPDATE settlements
            SET period_label = ?, start_date = ?, end_date = ?, total_income = ?, total_expense = ?,
                net_cashflow = ?, tx_count = ?, category_breakdown_json = ?, ai_summary = ?
            WHERE id = ?""",
            (
                period_label,
                start_date,
                end_date,
                total_income,
                total_expense,
                net_cashflow,
                len(txs),
                json.dumps(breakdown, ensure_ascii=False),
                ai_summary,
                settlement_id,
            ),
        )
        updated = _row_to_dict(conn.execute("SELECT * FROM settlements WHERE id = ?", (settlement_id,)).fetchone())
        updated["category_breakdown"] = breakdown
        updated["account_snapshot"] = json.loads(updated.get("account_snapshot_json") or "[]")
        return updated


@app.delete("/api/settlements/{settlement_id}")
def delete_settlement(settlement_id: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM settlements WHERE id = ?", (settlement_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="结账记录不存在")
        cur_rel = conn.execute("UPDATE transactions SET settlement_id = NULL WHERE settlement_id = ?", (settlement_id,))
        conn.execute("DELETE FROM settlements WHERE id = ?", (settlement_id,))
        return {"deleted": True, "released_tx_count": cur_rel.rowcount or 0}


# ---------------- Bills & Email Sync ----------------

def _get_existing_external_refs(conn: Any) -> Set[str]:
    rows = conn.execute("SELECT external_ref FROM transactions WHERE external_ref IS NOT NULL AND external_ref != ''").fetchall()
    return {r["external_ref"] for r in rows}


def _get_synced_email_uids(conn: Any) -> Set[str]:
    rows = conn.execute("SELECT message_uid FROM email_sync_logs WHERE message_uid IS NOT NULL").fetchall()
    return {r["message_uid"] for r in rows}


def _commit_email_batch(
    conn: Any,
    txs: List[TransactionCreate],
    rems: List[ReminderCreate],
    account_id: Optional[int],
    skip_duplicates: bool,
    logs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    ts = now_iso()
    existing_refs = _get_existing_external_refs(conn) if skip_duplicates else set()
    imported_txs: List[Dict[str, Any]] = []
    skipped_txs = 0

    for item in txs:
        ext_ref = (item.external_ref or "").strip()
        if skip_duplicates and ext_ref and ext_ref in existing_refs:
            skipped_txs += 1
            continue
        target_acc = item.account_id if item.account_id is not None else account_id
        occ = item.occurred_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        cur = conn.execute(
            """INSERT INTO transactions
            (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, external_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
            (
                item.type,
                round(item.amount, 2),
                item.category,
                target_acc,
                item.description.strip(),
                occ,
                item.source_type or "bill_import",
                ext_ref,
                ts,
            ),
        )
        _apply_tx_to_account(conn, target_acc, item.type, float(item.amount))
        if ext_ref:
            existing_refs.add(ext_ref)
        row = conn.execute(
            "SELECT t.*, a.name AS account_name FROM transactions t LEFT JOIN accounts a ON t.account_id = a.id WHERE t.id = ?",
            (cur.lastrowid,),
        ).fetchone()
        imported_txs.append(_row_to_dict(row))

    imported_rems: List[Dict[str, Any]] = []
    for rem in rems:
        cur = conn.execute(
            """INSERT INTO reminders
            (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rem.title.strip(),
                rem.description.strip(),
                rem.due_at,
                rem.priority,
                rem.category,
                rem.status or "pending",
                rem.source_type or "email",
                rem.source_ref,
                rem.linked_amount,
                rem.linked_tx_type,
                ts,
                ts,
            ),
        )
        imported_rems.append(_row_to_dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()))

    for log in logs:
        uid = log.get("message_uid")
        if not uid:
            continue
        conn.execute(
            """INSERT INTO email_sync_logs (message_uid, subject, sender, received_at, tx_imported, rem_imported, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_uid) DO UPDATE SET
                tx_imported = excluded.tx_imported,
                rem_imported = excluded.rem_imported,
                synced_at = excluded.synced_at""",
            (
                uid,
                log.get("subject", ""),
                log.get("sender", ""),
                log.get("received_at", ""),
                int(log.get("tx_imported", 0)),
                int(log.get("rem_imported", 0)),
                ts,
            ),
        )

    return {
        "imported_tx_count": len(imported_txs),
        "skipped_duplicate_count": skipped_txs,
        "imported_rem_count": len(imported_rems),
        "transactions": imported_txs,
        "reminders": imported_rems,
    }


@app.post("/api/bills/parse-upload")
async def parse_uploaded_bill(
    file: UploadFile = File(...),
    zip_password: str = Form(""),
) -> Dict[str, Any]:
    raw = await file.read()
    with get_conn() as conn:
        existing_refs = _get_existing_external_refs(conn)
    return parse_bill_file(
        raw=raw,
        filename=file.filename or "bill.csv",
        zip_password=zip_password.strip(),
        existing_refs=existing_refs,
    )


@app.post("/api/transactions/batch-import")
def batch_import_transactions(payload: BatchImportTransactionsRequest) -> Dict[str, Any]:
    with get_conn() as conn:
        return _commit_email_batch(
            conn=conn,
            txs=payload.transactions,
            rems=[],
            account_id=payload.account_id,
            skip_duplicates=payload.skip_duplicates,
            logs=[],
        )


@app.post("/api/emails/test")
def test_email_endpoint() -> Dict[str, Any]:
    return test_imap_connection(get_all_settings())


@app.post("/api/emails/fetch-parse")
async def fetch_parse_emails_endpoint(payload: EmailFetchRequest) -> Dict[str, Any]:
    settings = get_all_settings()
    with get_conn() as conn:
        existing_refs = _get_existing_external_refs(conn)
        synced_uids = _get_synced_email_uids(conn)
    try:
        return await fetch_and_parse_emails(
            settings=settings,
            existing_refs=existing_refs,
            synced_uids=synced_uids,
            days=payload.days,
            limit=payload.limit,
            keyword=payload.keyword,
            zip_password=payload.zip_password,
            include_body_ai=payload.include_body_ai,
            unread_only=payload.unread_only,
            skip_already_synced=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/emails/parse-text")
async def parse_email_text_endpoint(payload: EmailParseTextRequest) -> Dict[str, Any]:
    settings = get_all_settings()
    received_at = payload.received_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    res = await extract_from_email_content(
        subject=payload.subject,
        sender=payload.sender,
        received_at=received_at,
        body_text=payload.body,
        settings=settings,
    )
    with get_conn() as conn:
        existing_refs = _get_existing_external_refs(conn)
    for tx in res.get("transactions", []):
        fp = hashlib.sha256(f"{payload.subject}|{payload.body}|{tx.get('amount')}".encode("utf-8")).hexdigest()[:16]
        ext_ref = tx.get("external_ref") or f"email_text:{fp}"
        tx["external_ref"] = ext_ref
        tx["already_imported"] = ext_ref in existing_refs
    return res


@app.post("/api/emails/import")
async def import_email_items_endpoint(payload: EmailImportRequest) -> Dict[str, Any]:
    with get_conn() as conn:
        res = _commit_email_batch(
            conn=conn,
            txs=payload.transactions,
            rems=payload.reminders,
            account_id=payload.account_id,
            skip_duplicates=payload.skip_duplicates,
            logs=[l.model_dump() for l in payload.email_logs],
        )
    await _push_new_reminders_bark(res.get("reminders", []), prefix="📧 邮件提醒：")
    return res


@app.get("/api/emails/logs")
def list_email_logs() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [
            _row_to_dict(r)
            for r in conn.execute("SELECT * FROM email_sync_logs ORDER BY synced_at DESC, id DESC LIMIT 50").fetchall()
        ]


# ---------------- Phone Numbers (手机号码管理) ----------------

def _calc_next_keep_alive(last_date: str, cycle_days: int) -> str:
    if cycle_days <= 0:
        return ""
    try:
        base = datetime.strptime(last_date[:10], "%Y-%m-%d") if last_date else datetime.now()
    except ValueError:
        base = datetime.now()
    return (base + timedelta(days=cycle_days)).strftime("%Y-%m-%d")


def _phone_days_left(next_date_str: str) -> Optional[int]:
    if not next_date_str:
        return None
    try:
        target = datetime.strptime(next_date_str[:10], "%Y-%m-%d").date()
        return (target - datetime.now().date()).days
    except ValueError:
        return None


def _sync_phone_reminders_conn(conn: Any, override_within_days: Optional[int] = None) -> List[Dict[str, Any]]:
    ts = now_iso()
    created: List[Dict[str, Any]] = []
    rows = conn.execute("SELECT * FROM phone_numbers WHERE status IN ('active', 'standby')").fetchall()
    for r in rows:
        p = _row_to_dict(r)
        next_ka = p.get("next_keep_alive_date") or ""
        days_left = _phone_days_left(next_ka)
        threshold = override_within_days if override_within_days is not None else int(p.get("remind_before_days") if p.get("remind_before_days") is not None else 7)
        if next_ka and days_left is not None and days_left <= threshold:
            source_ref = f"手机号码 #{p['id']} 保号 ({next_ka})"
            exists = conn.execute(
                "SELECT id FROM reminders WHERE source_type = 'phone_sim' AND source_ref = ?",
                (source_ref,),
            ).fetchone()
            if not exists:
                cur = conn.execute(
                    """INSERT INTO reminders
                    (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
                    VALUES (?, ?, ?, 'high', 'life', 'pending', 'phone_sim', ?, NULL, NULL, ?, ?)""",
                    (
                        f"手机保号充值提醒：{p['label']}（{p['country_region']}）",
                        f"号码：{p['phone_number']} | 截止日：{next_ka}（提前{threshold}天提醒）| 规则：{p.get('keep_alive_rule') or '按期充值或活跃保号'}",
                        f"{next_ka}T10:00",
                        source_ref,
                        ts,
                        ts,
                    ),
                )
                created.append(_row_to_dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()))
    return created


def _validate_parent_phone(conn: Any, parent_phone_id: Optional[int], current_phone_id: Optional[int] = None) -> Optional[int]:
    if parent_phone_id is None:
        return None
    if current_phone_id is not None and parent_phone_id == current_phone_id:
        raise HTTPException(status_code=400, detail="不能将号码设为自己的副卡")
    parent_row = conn.execute(
        "SELECT id, parent_phone_id FROM phone_numbers WHERE id = ?",
        (parent_phone_id,),
    ).fetchone()
    if not parent_row:
        raise HTTPException(status_code=404, detail="所选归属主号不存在")
    if parent_row["parent_phone_id"] is not None:
        raise HTTPException(status_code=400, detail="所选号码本身是副卡，请选择主卡作为归属主号")
    if current_phone_id is not None:
        has_children = conn.execute(
            "SELECT 1 FROM phone_numbers WHERE parent_phone_id = ? LIMIT 1",
            (current_phone_id,),
        ).fetchone()
        if has_children:
            raise HTTPException(status_code=400, detail="该主卡名下已挂有副卡，请先移除副卡关联再修改")
    return parent_phone_id


@app.get("/api/phone-numbers")
async def list_phone_numbers() -> Dict[str, Any]:
    with get_conn() as conn:
        new_phone_rems = _sync_phone_reminders_conn(conn)
        rows = conn.execute("SELECT * FROM phone_numbers ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'standby' THEN 1 WHEN 'paused' THEN 2 ELSE 3 END, id DESC").fetchall()
        items = []
        for r in rows:
            d = _row_to_dict(r)
            d["monthly_fee_cny"] = round(float(d.get("monthly_fee") or 0) * float(d.get("cny_rate") or 1.0), 2)
            d["keep_alive_days_left"] = _phone_days_left(d.get("next_keep_alive_date") or "")
            d["remind_before_days"] = int(d.get("remind_before_days") if d.get("remind_before_days") is not None else 7)
            items.append(d)

    by_id = {p["id"]: p for p in items}
    children_map: Dict[int, List[Dict[str, Any]]] = {}
    for p in items:
        pid = p.get("parent_phone_id")
        if pid and pid in by_id:
            parent = by_id[pid]
            p["parent_label"] = parent["label"]
            p["parent_number"] = parent["phone_number"]
            children_map.setdefault(pid, []).append(
                {
                    "id": p["id"],
                    "label": p["label"],
                    "phone_number": p["phone_number"],
                    "status": p["status"],
                    "monthly_fee_cny": p["monthly_fee_cny"],
                    "sim_type": p["sim_type"],
                    "device_slot": p.get("device_slot") or "",
                }
            )
        else:
            p["parent_phone_id"] = None
            p["parent_label"] = ""
            p["parent_number"] = ""

    for p in items:
        subs = children_map.get(p["id"], [])
        p["sub_cards"] = subs
        p["sub_cards_count"] = len(subs)

    if new_phone_rems:
        await _push_new_reminders_bark(new_phone_rems, prefix="📱 保号提前提醒：")

    active_items = [p for p in items if p["status"] in ("active", "standby")]
    monthly_cny_total = round(sum(p["monthly_fee_cny"] for p in active_items), 2)
    extra_cny_total = round(sum(float(p.get("extra_comm_cost") or 0) * float(p.get("cny_rate") or 1.0) for p in active_items), 2)
    urgent_keep_alive = [
        p for p in active_items
        if p["keep_alive_days_left"] is not None and p["keep_alive_days_left"] <= p["remind_before_days"]
    ]
    sub_card_count = sum(1 for p in items if p.get("parent_phone_id"))

    return {
        "items": items,
        "summary": {
            "total_count": len(items),
            "active_count": len(active_items),
            "sub_card_count": sub_card_count,
            "monthly_cny_total": monthly_cny_total,
            "annual_cny_total": round(monthly_cny_total * 12, 2),
            "extra_cny_total": extra_cny_total,
            "urgent_keep_alive_count": len(urgent_keep_alive),
        },
    }


@app.post("/api/phone-numbers")
async def create_phone_number(payload: PhoneNumberCreate) -> Dict[str, Any]:
    ts = now_iso()
    last_ka = payload.last_keep_alive_date.strip()
    if payload.keep_alive_cycle_days > 0 and not last_ka:
        last_ka = datetime.now().strftime("%Y-%m-%d")
    next_ka = payload.next_keep_alive_date.strip() or _calc_next_keep_alive(last_ka, payload.keep_alive_cycle_days)

    with get_conn() as conn:
        parent_id = _validate_parent_phone(conn, payload.parent_phone_id)
        cur = conn.execute(
            """INSERT INTO phone_numbers
            (label, phone_number, country_region, carrier, sim_type, device_slot, monthly_fee, currency, cny_rate, balance, extra_comm_cost, total_comm_spent, billing_day, keep_alive_rule, keep_alive_cycle_days, remind_before_days, last_keep_alive_date, next_keep_alive_date, expiry_date, bound_services, status, parent_phone_id, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.label.strip(),
                payload.phone_number.strip(),
                payload.country_region.strip(),
                payload.carrier.strip(),
                payload.sim_type,
                payload.device_slot.strip(),
                round(payload.monthly_fee, 2),
                payload.currency.strip().upper() or "CNY",
                round(payload.cny_rate, 4),
                round(payload.balance, 2),
                round(payload.extra_comm_cost, 2),
                round(payload.total_comm_spent, 2),
                payload.billing_day,
                payload.keep_alive_rule.strip(),
                payload.keep_alive_cycle_days,
                payload.remind_before_days,
                last_ka,
                next_ka,
                payload.expiry_date.strip(),
                payload.bound_services.strip(),
                payload.status,
                parent_id,
                payload.notes.strip(),
                ts,
                ts,
            ),
        )
        new_rems = _sync_phone_reminders_conn(conn)
        d = _row_to_dict(conn.execute("SELECT * FROM phone_numbers WHERE id = ?", (cur.lastrowid,)).fetchone())
        d["monthly_fee_cny"] = round(float(d["monthly_fee"]) * float(d["cny_rate"]), 2)
        d["keep_alive_days_left"] = _phone_days_left(d.get("next_keep_alive_date") or "")
    if new_rems:
        await _push_new_reminders_bark(new_rems, prefix="📱 保号提前提醒：")
    return d


@app.patch("/api/phone-numbers/{phone_id}")
async def update_phone_number(phone_id: int, payload: PhoneNumberUpdate) -> Dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")

    with get_conn() as conn:
        existing_row = conn.execute("SELECT * FROM phone_numbers WHERE id = ?", (phone_id,)).fetchone()
        if not existing_row:
            raise HTTPException(status_code=404, detail="号码记录不存在")
        existing = _row_to_dict(existing_row)

        if "parent_phone_id" in updates:
            updates["parent_phone_id"] = _validate_parent_phone(conn, updates["parent_phone_id"], current_phone_id=phone_id)

        if (
            ("keep_alive_cycle_days" in updates or "last_keep_alive_date" in updates)
            and "next_keep_alive_date" not in updates
        ):
            cycle = int(updates.get("keep_alive_cycle_days", existing["keep_alive_cycle_days"]))
            last_d = str(updates.get("last_keep_alive_date", existing["last_keep_alive_date"]))
            updates["next_keep_alive_date"] = _calc_next_keep_alive(last_d, cycle)

        updates["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in updates.keys())
        params = list(updates.values()) + [phone_id]
        conn.execute(f"UPDATE phone_numbers SET {sets} WHERE id = ?", params)
        new_rems = _sync_phone_reminders_conn(conn)

        d = _row_to_dict(conn.execute("SELECT * FROM phone_numbers WHERE id = ?", (phone_id,)).fetchone())
        d["monthly_fee_cny"] = round(float(d["monthly_fee"]) * float(d["cny_rate"]), 2)
        d["keep_alive_days_left"] = _phone_days_left(d.get("next_keep_alive_date") or "")
    if new_rems:
        await _push_new_reminders_bark(new_rems, prefix="📱 保号提前提醒：")
    return d


@app.delete("/api/phone-numbers/{phone_id}")
def delete_phone_number(phone_id: int) -> Dict[str, bool]:
    with get_conn() as conn:
        conn.execute("UPDATE phone_numbers SET parent_phone_id = NULL WHERE parent_phone_id = ?", (phone_id,))
        cur = conn.execute("DELETE FROM phone_numbers WHERE id = ?", (phone_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="号码记录不存在")
        return {"deleted": True}


@app.post("/api/phone-numbers/{phone_id}/keep-alive")
def keep_alive_phone(phone_id: int) -> Dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    ts = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM phone_numbers WHERE id = ?", (phone_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="号码记录不存在")
        p = _row_to_dict(row)
        cycle = int(p.get("keep_alive_cycle_days") or 0)
        next_date = _calc_next_keep_alive(today, cycle) if cycle > 0 else p.get("next_keep_alive_date", "")
        conn.execute(
            "UPDATE phone_numbers SET last_keep_alive_date = ?, next_keep_alive_date = ?, updated_at = ? WHERE id = ?",
            (today, next_date, ts, phone_id),
        )
        conn.execute(
            "UPDATE reminders SET status = 'done', updated_at = ? WHERE source_type = 'phone_sim' AND source_ref LIKE ? AND status = 'pending'",
            (ts, f"手机号码 #{phone_id}%"),
        )
        d = _row_to_dict(conn.execute("SELECT * FROM phone_numbers WHERE id = ?", (phone_id,)).fetchone())
        d["monthly_fee_cny"] = round(float(d["monthly_fee"]) * float(d["cny_rate"]), 2)
        d["keep_alive_days_left"] = _phone_days_left(d.get("next_keep_alive_date") or "")
        return d


@app.post("/api/phone-numbers/{phone_id}/record-cost")
def record_phone_cost(phone_id: int, payload: PhoneCommRecordRequest) -> Dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM phone_numbers WHERE id = ?", (phone_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="号码记录不存在")
        p = _row_to_dict(row)
        amt = round(float(payload.amount), 2)
        bal = float(p.get("balance") or 0)
        extra = float(p.get("extra_comm_cost") or 0)
        spent = float(p.get("total_comm_spent") or 0)

        if payload.kind == "topup":
            bal = round(bal + amt, 2)
            spent = round(spent + amt, 2)
            label_action = "话费充值"
        elif payload.kind == "monthly_fee":
            bal = round(bal - amt, 2)
            spent = round(spent + amt, 2)
            label_action = "月租扣费"
        else:
            extra = round(extra + amt, 2)
            spent = round(spent + amt, 2)
            label_action = "额外通信费"

        conn.execute(
            "UPDATE phone_numbers SET balance = ?, extra_comm_cost = ?, total_comm_spent = ?, updated_at = ? WHERE id = ?",
            (bal, extra, spent, ts, phone_id),
        )

        if payload.mark_keep_alive:
            today = datetime.now().strftime("%Y-%m-%d")
            cycle = int(p.get("keep_alive_cycle_days") or 0)
            next_date = _calc_next_keep_alive(today, cycle) if cycle > 0 else p.get("next_keep_alive_date", "")
            conn.execute(
                "UPDATE phone_numbers SET last_keep_alive_date = ?, next_keep_alive_date = ?, updated_at = ? WHERE id = ?",
                (today, next_date, ts, phone_id),
            )
            conn.execute(
                "UPDATE reminders SET status = 'done', updated_at = ? WHERE source_type = 'phone_sim' AND source_ref LIKE ? AND status = 'pending'",
                (ts, f"手机号码 #{phone_id}%"),
            )

        created_tx = None
        if payload.sync_to_finance:
            cny_amt = round(amt * float(p.get("cny_rate") or 1.0), 2)
            desc = payload.description.strip() or f"{p['label']} ({p['country_region']}) {label_action} {amt} {p['currency']}"
            occ = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
            cur_tx = conn.execute(
                """INSERT INTO transactions
                (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, external_ref, created_at)
                VALUES ('expense', ?, '通讯', ?, ?, ?, NULL, 'phone_sim', '', ?)""",
                (cny_amt, payload.account_id, desc, occ, ts),
            )
            _apply_tx_to_account(conn, payload.account_id, "expense", cny_amt)
            created_tx = _row_to_dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (cur_tx.lastrowid,)).fetchone())

        updated_phone = _row_to_dict(conn.execute("SELECT * FROM phone_numbers WHERE id = ?", (phone_id,)).fetchone())
        updated_phone["monthly_fee_cny"] = round(float(updated_phone["monthly_fee"]) * float(updated_phone["cny_rate"]), 2)
        updated_phone["keep_alive_days_left"] = _phone_days_left(updated_phone.get("next_keep_alive_date") or "")
        return {"phone": updated_phone, "transaction": created_tx}


@app.post("/api/phone-numbers/sync-reminders")
async def sync_phone_reminders(within_days: Optional[int] = Query(None, ge=1, le=180)) -> Dict[str, Any]:
    with get_conn() as conn:
        created = _sync_phone_reminders_conn(conn, override_within_days=within_days)
    if created:
        await _push_new_reminders_bark(created, prefix="📱 保号提前提醒：")
    return {"created_count": len(created), "reminders": created}


# ---------------- Credit Cards, Loans & Optimal Debt Repayment Planner ----------------


def _load_debt_state(conn: Any) -> Dict[str, Any]:
    raw_cards = [
        _row_to_dict(r)
        for r in conn.execute("SELECT * FROM credit_cards ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, due_day ASC, id ASC").fetchall()
    ]
    raw_loans = [
        _row_to_dict(r)
        for r in conn.execute("SELECT * FROM loans ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, due_day ASC, id ASC").fetchall()
    ]
    cards = [enrich_credit_card(c) for c in raw_cards]
    loans = [enrich_loan(l) for l in raw_loans]

    accounts = [_row_to_dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()]
    suggested_cash = round(
        sum(float(a["balance"]) for a in accounts if a["type"] in ("cash", "bank") and float(a["balance"]) > 0),
        2,
    )
    return {"cards": cards, "loans": loans, "suggested_cash": suggested_cash}


@app.get("/api/debt/overview")
def get_debt_overview() -> Dict[str, Any]:
    with get_conn() as conn:
        state = _load_debt_state(conn)
        ops = [
            _row_to_dict(r)
            for r in conn.execute(
                """SELECT o.*,
                          sc.name AS source_card_name,
                          tc.name AS target_card_name,
                          tl.name AS target_loan_name,
                          sa.name AS source_account_name
                   FROM debt_operations o
                   LEFT JOIN credit_cards sc ON o.source_card_id = sc.id
                   LEFT JOIN credit_cards tc ON o.target_card_id = tc.id
                   LEFT JOIN loans tl ON o.target_loan_id = tl.id
                   LEFT JOIN accounts sa ON o.source_account_id = sa.id
                   ORDER BY o.op_date DESC, o.id DESC
                   LIMIT 80"""
            ).fetchall()
        ]

    active_cards = [c for c in state["cards"] if c["status"] == "active"]
    active_loans = [l for l in state["loans"] if l["status"] == "active"]

    total_credit_limit = round(sum(c["credit_limit"] for c in active_cards), 2)
    total_card_debt = round(sum(c["current_debt"] for c in active_cards), 2)
    total_available_credit = round(sum(c["available_credit"] for c in active_cards), 2)
    total_remaining_bill = round(sum(c["remaining_bill"] for c in active_cards), 2)

    total_loan_principal = round(sum(l["remaining_principal"] for l in active_loans), 2)
    total_loan_month_rem = round(sum(l["remaining_month_payment"] for l in active_loans), 2)

    overall_utilization = round((total_card_debt / total_credit_limit) * 100, 1) if total_credit_limit > 0 else 0.0
    total_fee_spent = round(sum(float(o.get("fee_amount") or 0) for o in ops), 2)

    return {
        "cards": state["cards"],
        "loans": state["loans"],
        "operations": ops,
        "suggested_cash": state["suggested_cash"],
        "summary": {
            "active_card_count": len(active_cards),
            "active_loan_count": len(active_loans),
            "total_credit_limit": total_credit_limit,
            "total_card_debt": total_card_debt,
            "total_available_credit": total_available_credit,
            "overall_utilization_pct": overall_utilization,
            "total_remaining_bill": total_remaining_bill,
            "total_loan_principal": total_loan_principal,
            "total_loan_month_remaining": total_loan_month_rem,
            "total_debt": round(total_card_debt + total_loan_principal, 2),
            "total_fee_spent": total_fee_spent,
        },
    }


@app.post("/api/debt/cards")
def create_credit_card(payload: CreditCardCreate) -> Dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO credit_cards
            (name, bank_name, last4, account_id, credit_limit, current_debt, statement_bill_amount, paid_amount_this_cycle, min_payment, statement_day, due_day, grace_days, swipe_fee_rate, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.name.strip(),
                payload.bank_name.strip(),
                payload.last4.strip(),
                payload.account_id,
                round(payload.credit_limit, 2),
                round(payload.current_debt, 2),
                round(payload.statement_bill_amount, 2),
                round(payload.paid_amount_this_cycle, 2),
                round(payload.min_payment, 2),
                payload.statement_day,
                payload.due_day,
                payload.grace_days,
                round(payload.swipe_fee_rate, 5),
                payload.status,
                payload.notes.strip(),
                ts,
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM credit_cards WHERE id = ?", (cur.lastrowid,)).fetchone()
        return enrich_credit_card(_row_to_dict(row))


@app.patch("/api/debt/cards/{card_id}")
def update_credit_card(card_id: int, payload: CreditCardUpdate) -> Dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    params = list(updates.values()) + [card_id]
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE credit_cards SET {sets} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="信用卡不存在")
        row = conn.execute("SELECT * FROM credit_cards WHERE id = ?", (card_id,)).fetchone()
        return enrich_credit_card(_row_to_dict(row))


@app.delete("/api/debt/cards/{card_id}")
def delete_credit_card(card_id: int) -> Dict[str, bool]:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM credit_cards WHERE id = ?", (card_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="信用卡不存在")
        return {"deleted": True}


@app.post("/api/debt/loans")
def create_loan(payload: LoanCreate) -> Dict[str, Any]:
    ts = now_iso()
    rem_principal = payload.remaining_principal if payload.remaining_principal > 0 else payload.total_principal
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO loans
            (name, lender, total_principal, remaining_principal, monthly_payment, paid_this_month, due_day, apr, remaining_periods, account_id, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.name.strip(),
                payload.lender.strip(),
                round(payload.total_principal, 2),
                round(rem_principal, 2),
                round(payload.monthly_payment, 2),
                round(payload.paid_this_month, 2),
                payload.due_day,
                round(payload.apr, 2),
                payload.remaining_periods,
                payload.account_id,
                payload.status,
                payload.notes.strip(),
                ts,
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM loans WHERE id = ?", (cur.lastrowid,)).fetchone()
        return enrich_loan(_row_to_dict(row))


@app.patch("/api/debt/loans/{loan_id}")
def update_loan(loan_id: int, payload: LoanUpdate) -> Dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    params = list(updates.values()) + [loan_id]
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE loans SET {sets} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="贷款记录不存在")
        row = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        return enrich_loan(_row_to_dict(row))


@app.delete("/api/debt/loans/{loan_id}")
def delete_loan(loan_id: int) -> Dict[str, bool]:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="贷款记录不存在")
        return {"deleted": True}


@app.post("/api/debt/plan")
def plan_debt_repayment(payload: DebtPlanRequest) -> Dict[str, Any]:
    with get_conn() as conn:
        state = _load_debt_state(conn)
    cash = state["suggested_cash"] if payload.available_cash is None else float(payload.available_cash)
    return plan_optimal_repayment_paths(
        cards=state["cards"],
        loans=state["loans"],
        available_cash=cash,
        horizon_days=payload.horizon_days,
        reserve_ratio=payload.reserve_ratio,
        strategy=payload.strategy,
        allow_relay=payload.allow_relay,
    )


@app.post("/api/debt/operations")
def execute_debt_operation(payload: DebtOperationCreate) -> Dict[str, Any]:
    ts = now_iso()
    op_date = (payload.op_date or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")).strip()
    amt = round(float(payload.amount), 2)
    fee = round(float(payload.fee_amount or 0.0), 2)

    with get_conn() as conn:
        if payload.op_type == "swipe_repay_card":
            if not payload.source_card_id or not payload.target_card_id:
                raise HTTPException(status_code=400, detail="刷卡还卡需要指定来源信用卡和目标信用卡")
            if payload.source_card_id == payload.target_card_id:
                raise HTTPException(status_code=400, detail="来源卡与目标卡不能相同")
            src = conn.execute("SELECT * FROM credit_cards WHERE id = ?", (payload.source_card_id,)).fetchone()
            tgt = conn.execute("SELECT * FROM credit_cards WHERE id = ?", (payload.target_card_id,)).fetchone()
            if not src or not tgt:
                raise HTTPException(status_code=404, detail="信用卡不存在")
            conn.execute(
                "UPDATE credit_cards SET current_debt = ROUND(current_debt + ?, 2), updated_at = ? WHERE id = ?",
                (amt, ts, payload.source_card_id),
            )
            conn.execute(
                """UPDATE credit_cards
                SET current_debt = MAX(0, ROUND(current_debt - ?, 2)),
                    paid_amount_this_cycle = ROUND(paid_amount_this_cycle + ?, 2),
                    updated_at = ?
                WHERE id = ?""",
                (amt, amt, ts, payload.target_card_id),
            )
            desc = payload.note.strip() or f"刷「{src['name']}」还「{tgt['name']}」¥{amt:.2f}（手续费 ¥{fee:.2f}）"
            tx_amt = fee
            tx_cat = "手续费"

        elif payload.op_type == "cash_repay_card":
            if not payload.target_card_id:
                raise HTTPException(status_code=400, detail="请指定还款目标信用卡")
            tgt = conn.execute("SELECT * FROM credit_cards WHERE id = ?", (payload.target_card_id,)).fetchone()
            if not tgt:
                raise HTTPException(status_code=404, detail="目标信用卡不存在")
            conn.execute(
                """UPDATE credit_cards
                SET current_debt = MAX(0, ROUND(current_debt - ?, 2)),
                    paid_amount_this_cycle = ROUND(paid_amount_this_cycle + ?, 2),
                    updated_at = ?
                WHERE id = ?""",
                (amt, amt, ts, payload.target_card_id),
            )
            if payload.source_account_id:
                _apply_tx_to_account(conn, payload.source_account_id, "expense", amt)
            desc = payload.note.strip() or f"现金还信用卡「{tgt['name']}」¥{amt:.2f}"
            tx_amt = amt
            tx_cat = "信用卡还款"

        elif payload.op_type == "repay_loan":
            if not payload.target_loan_id:
                raise HTTPException(status_code=400, detail="请指定还款目标贷款")
            loan = conn.execute("SELECT * FROM loans WHERE id = ?", (payload.target_loan_id,)).fetchone()
            if not loan:
                raise HTTPException(status_code=404, detail="贷款记录不存在")
            conn.execute(
                """UPDATE loans
                SET paid_this_month = ROUND(paid_this_month + ?, 2),
                    remaining_principal = MAX(0, ROUND(remaining_principal - ?, 2)),
                    updated_at = ?
                WHERE id = ?""",
                (amt, amt, ts, payload.target_loan_id),
            )
            if payload.source_account_id:
                _apply_tx_to_account(conn, payload.source_account_id, "expense", amt)
            desc = payload.note.strip() or f"偿还贷款「{loan['name']}」¥{amt:.2f}"
            tx_amt = amt
            tx_cat = "贷款月供"

        elif payload.op_type == "swipe_cash_out":
            if not payload.source_card_id:
                raise HTTPException(status_code=400, detail="请指定刷出资金的信用卡")
            src = conn.execute("SELECT * FROM credit_cards WHERE id = ?", (payload.source_card_id,)).fetchone()
            if not src:
                raise HTTPException(status_code=404, detail="来源信用卡不存在")
            conn.execute(
                "UPDATE credit_cards SET current_debt = ROUND(current_debt + ?, 2), updated_at = ? WHERE id = ?",
                (amt, ts, payload.source_card_id),
            )
            loan_name = ""
            if payload.target_loan_id:
                loan = conn.execute("SELECT * FROM loans WHERE id = ?", (payload.target_loan_id,)).fetchone()
                if loan:
                    loan_name = loan["name"]
                    conn.execute(
                        """UPDATE loans
                        SET paid_this_month = ROUND(paid_this_month + ?, 2),
                            remaining_principal = MAX(0, ROUND(remaining_principal - ?, 2)),
                            updated_at = ?
                        WHERE id = ?""",
                        (amt, amt, ts, payload.target_loan_id),
                    )
            desc = payload.note.strip() or (
                f"刷「{src['name']}」周转偿还贷款「{loan_name}」¥{amt:.2f}（手续费 ¥{fee:.2f}）"
                if loan_name
                else f"刷「{src['name']}」周转 ¥{amt:.2f}（手续费 ¥{fee:.2f}）"
            )
            tx_amt = fee
            tx_cat = "手续费"
        else:
            raise HTTPException(status_code=400, detail="不支持的操作类型")

        tx_id = None
        if payload.sync_to_finance and tx_amt > 0:
            cur_tx = conn.execute(
                """INSERT INTO transactions
                (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, external_ref, created_at)
                VALUES ('expense', ?, ?, ?, ?, ?, NULL, 'debt_plan', '', ?)""",
                (tx_amt, tx_cat, payload.source_account_id, desc, op_date, ts),
            )
            tx_id = cur_tx.lastrowid

        cur_op = conn.execute(
            """INSERT INTO debt_operations
            (op_type, source_card_id, source_account_id, target_card_id, target_loan_id, amount, fee_amount, op_date, note, tx_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.op_type,
                payload.source_card_id,
                payload.source_account_id,
                payload.target_card_id,
                payload.target_loan_id,
                amt,
                fee,
                op_date,
                desc,
                tx_id,
                ts,
            ),
        )
        op_row = _row_to_dict(conn.execute("SELECT * FROM debt_operations WHERE id = ?", (cur_op.lastrowid,)).fetchone())
        return op_row


@app.delete("/api/debt/operations/{op_id}")
def undo_debt_operation(op_id: int) -> Dict[str, bool]:
    ts = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM debt_operations WHERE id = ?", (op_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="操作记录不存在")
        op = _row_to_dict(row)
        amt = float(op["amount"])
        op_type = op["op_type"]

        if op_type == "swipe_repay_card":
            if op.get("source_card_id"):
                conn.execute(
                    "UPDATE credit_cards SET current_debt = MAX(0, ROUND(current_debt - ?, 2)), updated_at = ? WHERE id = ?",
                    (amt, ts, op["source_card_id"]),
                )
            if op.get("target_card_id"):
                conn.execute(
                    """UPDATE credit_cards
                    SET current_debt = ROUND(current_debt + ?, 2),
                        paid_amount_this_cycle = MAX(0, ROUND(paid_amount_this_cycle - ?, 2)),
                        updated_at = ?
                    WHERE id = ?""",
                    (amt, amt, ts, op["target_card_id"]),
                )
        elif op_type == "cash_repay_card":
            if op.get("target_card_id"):
                conn.execute(
                    """UPDATE credit_cards
                    SET current_debt = ROUND(current_debt + ?, 2),
                        paid_amount_this_cycle = MAX(0, ROUND(paid_amount_this_cycle - ?, 2)),
                        updated_at = ?
                    WHERE id = ?""",
                    (amt, amt, ts, op["target_card_id"]),
                )
            if op.get("source_account_id"):
                _apply_tx_to_account(conn, op["source_account_id"], "expense", amt, reverse=True)
        elif op_type == "repay_loan":
            if op.get("target_loan_id"):
                conn.execute(
                    """UPDATE loans
                    SET paid_this_month = MAX(0, ROUND(paid_this_month - ?, 2)),
                        remaining_principal = ROUND(remaining_principal + ?, 2),
                        updated_at = ?
                    WHERE id = ?""",
                    (amt, amt, ts, op["target_loan_id"]),
                )
            if op.get("source_account_id"):
                _apply_tx_to_account(conn, op["source_account_id"], "expense", amt, reverse=True)
        elif op_type == "swipe_cash_out":
            if op.get("source_card_id"):
                conn.execute(
                    "UPDATE credit_cards SET current_debt = MAX(0, ROUND(current_debt - ?, 2)), updated_at = ? WHERE id = ?",
                    (amt, ts, op["source_card_id"]),
                )
            if op.get("target_loan_id"):
                conn.execute(
                    """UPDATE loans
                    SET paid_this_month = MAX(0, ROUND(paid_this_month - ?, 2)),
                        remaining_principal = ROUND(remaining_principal + ?, 2),
                        updated_at = ?
                    WHERE id = ?""",
                    (amt, amt, ts, op["target_loan_id"]),
                )

        if op.get("tx_id"):
            conn.execute("DELETE FROM transactions WHERE id = ?", (op["tx_id"],))
        conn.execute("DELETE FROM debt_operations WHERE id = ?", (op_id,))
        return {"deleted": True}


@app.post("/api/debt/sync-reminders")
async def sync_debt_reminders(within_days: int = Query(5, ge=1, le=30)) -> Dict[str, Any]:
    ts = now_iso()
    created: List[Dict[str, Any]] = []
    with get_conn() as conn:
        state = _load_debt_state(conn)
        for c in state["cards"]:
            if c["status"] != "active" or c["remaining_bill"] <= 0:
                continue
            if c["days_until_due"] <= within_days:
                source_ref = f"信用卡 #{c['id']} ({c['next_due_date']})"
                exists = conn.execute(
                    "SELECT id FROM reminders WHERE source_type = 'debt_bill' AND source_ref = ? AND status = 'pending'",
                    (source_ref,),
                ).fetchone()
                if not exists:
                    cur = conn.execute(
                        """INSERT INTO reminders
                        (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
                        VALUES (?, ?, ?, 'high', 'finance', 'pending', 'debt_bill', ?, ?, 'expense', ?, ?)""",
                        (
                            f"信用卡还款：{c['name']} 剩余 ¥{c['remaining_bill']:.2f}",
                            f"还款日：{c['next_due_date']}（还有 {c['days_until_due']} 天，宽限期 {c['grace_days']} 天），当前总欠款 ¥{c['current_debt']:.2f}",
                            f"{c['next_due_date']}T10:00",
                            source_ref,
                            c["remaining_bill"],
                            ts,
                            ts,
                        ),
                    )
                    created.append(_row_to_dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()))

        for l in state["loans"]:
            if l["status"] != "active" or l["remaining_month_payment"] <= 0:
                continue
            if l["days_until_due"] <= within_days:
                source_ref = f"贷款 #{l['id']} ({l['next_due_date']})"
                exists = conn.execute(
                    "SELECT id FROM reminders WHERE source_type = 'debt_bill' AND source_ref = ? AND status = 'pending'",
                    (source_ref,),
                ).fetchone()
                if not exists:
                    cur = conn.execute(
                        """INSERT INTO reminders
                        (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
                        VALUES (?, ?, ?, 'high', 'finance', 'pending', 'debt_bill', ?, ?, 'expense', ?, ?)""",
                        (
                            f"贷款月供：{l['name']} 应还 ¥{l['remaining_month_payment']:.2f}",
                            f"还款日：{l['next_due_date']}（还有 {l['days_until_due']} 天，无宽限期请优先确保储蓄卡余额充足）",
                            f"{l['next_due_date']}T09:30",
                            source_ref,
                            l["remaining_month_payment"],
                            ts,
                            ts,
                        ),
                    )
                    created.append(_row_to_dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()))

    if created:
        await _push_new_reminders_bark(created, prefix="💳 还款日临近：")
    return {"created_count": len(created), "reminders": created}


# ---------------- Settings ----------------

@app.get("/api/settings")
def get_settings_endpoint() -> Dict[str, str]:
    settings = get_all_settings()
    # Mask sensitive keys slightly? Actually user needs to view/edit local config in personal app, keep intact or return full in local self-hosted app.
    return settings


@app.put("/api/settings")
async def update_settings_endpoint(payload: SettingsUpdateRequest) -> Dict[str, str]:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    saved = update_settings(updates)
    if any(k.startswith("ctrader_") for k in updates):
        await market_service.on_settings_updated()
    return saved


@app.post("/api/settings/test-ai")
async def test_ai_endpoint() -> Dict[str, Any]:
    return await test_ai_connection(get_all_settings())


@app.post("/api/settings/test-bark")
async def test_bark_endpoint() -> Dict[str, Any]:
    settings = get_all_settings()
    return await send_bark_push(
        title="LifeHub Bark 推送测试",
        body="恭喜！你的个人提醒、保号通知和到期账单现在都会通过 Bark 推送到手机。",
        settings=settings,
        priority="high",
        force=True,
    )


@app.post("/api/reminders/check-bark-due")
async def check_bark_due_endpoint() -> Dict[str, int]:
    pushed = await _check_and_push_due_reminders()
    return {"pushed_count": pushed}


@app.post("/api/settings/reset-data")
def reset_data_endpoint(reseed: bool = Query(False)) -> Dict[str, Any]:
    reset_all_data(reseed=reseed)
    return {"ok": True, "reseeded": reseed}


# Mount built frontend if present
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "dist")
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
