import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .proxy_service import build_httpx_kwargs


def _build_bark_push_url(server_url: str, device_key: str) -> str:
    key = (device_key or "").strip().strip("/")
    base = (server_url or "https://api.day.app").strip().rstrip("/")
    if not key:
        return ""
    if key.startswith("http://") or key.startswith("https://"):
        # User pasted full Bark URL like https://api.day.app/AbCdEf
        return key
    return f"{base}/{key}"


def _parse_extra_push_urls(raw: str) -> List[str]:
    urls: List[str] = []
    seen = set()
    for part in re.split(r"[\r\n,;]+", raw or ""):
        u = part.strip()
        if not u:
            continue
        if not (u.startswith("http://") or u.startswith("https://")):
            continue
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def has_any_push_channel(settings: Dict[str, str]) -> bool:
    """Return True if global notification push is enabled and at least Bark OR an extra push URL is configured."""
    if settings.get("bark_enabled", "0") != "1":
        return False
    if (settings.get("bark_device_key") or "").strip():
        return True
    if settings.get("extra_push_enabled", "1") != "0" and _parse_extra_push_urls(settings.get("extra_push_urls") or ""):
        return True
    return False


def _priority_to_bark_level(priority: str) -> str:
    if priority == "high":
        return "timeSensitive"
    if priority == "low":
        return "passive"
    return "active"


def _build_extra_push_payload(url: str, title: str, body: str, priority: str, group: str, sound: str) -> Dict[str, Any]:
    host = (urlparse(url).netloc or "").lower()
    if "chuckfang.com" in host or "/meow" in url.lower():
        # MeoW push format (api.chuckfang.com): title + msg + optional category
        return {
            "title": title,
            "msg": body[:1000],
            "category": group[:10],
        }
    # Default Bark-compatible / generic webhook JSON payload
    payload: Dict[str, Any] = {
        "title": title,
        "body": body[:500],
        "msg": body[:500],
        "group": group,
        "level": _priority_to_bark_level(priority),
        "isArchive": 1,
    }
    if sound:
        payload["sound"] = sound
    return payload


async def send_bark_push(
    title: str,
    body: str,
    settings: Dict[str, str],
    priority: str = "medium",
    group: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    if not force and settings.get("bark_enabled", "0") != "1":
        return {"ok": False, "skipped": True, "message": "消息推送未开启"}

    push_url = _build_bark_push_url(
        settings.get("bark_server_url", "https://api.day.app"),
        settings.get("bark_device_key", ""),
    )
    extra_enabled = settings.get("extra_push_enabled", "1") != "0"
    extra_urls = _parse_extra_push_urls(settings.get("extra_push_urls") or "") if extra_enabled else []
    # Never duplicate if extra_url happens to equal primary Bark URL
    extra_urls = [u for u in extra_urls if u.rstrip("/") != push_url.rstrip("/")]

    if not push_url and not extra_urls:
        return {"ok": False, "skipped": not force, "message": "未配置 Bark Device Key 或附加推送地址"}

    clean_title = title.strip() or "LifeHub 提醒"
    clean_body = (body or title).strip()
    clean_group = (group or settings.get("bark_group") or "LifeHub").strip()
    sound = (settings.get("bark_sound") or "").strip()

    channel_results: List[str] = []
    any_ok = False

    try:
        async with httpx.AsyncClient(**build_httpx_kwargs(10.0, "bark_use_proxy", settings)) as client:
            if push_url:
                bark_payload: Dict[str, Any] = {
                    "title": clean_title,
                    "body": clean_body[:500],
                    "group": clean_group,
                    "level": _priority_to_bark_level(priority),
                    "isArchive": 1,
                }
                if sound:
                    bark_payload["sound"] = sound
                try:
                    resp = await client.post(push_url, json=bark_payload)
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("code", 200) != 200:
                        channel_results.append(f"Bark 返回异常: {data.get('message') or data}")
                    else:
                        any_ok = True
                        channel_results.append("Bark 推送成功")
                except Exception as exc:
                    channel_results.append(f"Bark 推送失败: {exc}")

            for ext_url in extra_urls:
                tag = urlparse(ext_url).netloc or ext_url[:36]
                ext_payload = _build_extra_push_payload(ext_url, clean_title, clean_body, priority, clean_group, sound)
                try:
                    resp = await client.post(ext_url, json=ext_payload)
                    resp.raise_for_status()
                    ok_status = True
                    msg_err = ""
                    try:
                        edata = resp.json()
                        if isinstance(edata, dict):
                            st_val = edata.get("status", edata.get("code", 200))
                            if int(st_val) != 200:
                                ok_status = False
                                msg_err = str(edata.get("msg") or edata.get("message") or edata)
                    except Exception:
                        pass
                    if ok_status:
                        any_ok = True
                        channel_results.append(f"附加推送({tag}) 成功")
                    else:
                        channel_results.append(f"附加推送({tag}) 异常: {msg_err}")
                except Exception as exc:
                    channel_results.append(f"附加推送({tag}) 失败: {exc}")
    except Exception as exc:
        return {"ok": False, "message": f"消息推送失败: {exc}"}

    return {
        "ok": any_ok,
        "message": "；".join(channel_results) if channel_results else "无可用推送通道",
        "channels": channel_results,
    }


def format_reminder_bark_message(rem: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    title_raw = str(rem.get("title") or "待办提醒")
    title = f"{prefix}{title_raw}" if prefix else title_raw
    parts = []
    if rem.get("due_at"):
        parts.append(f"截止：{str(rem['due_at']).replace('T', ' ')[:16]}")
    if rem.get("linked_amount"):
        parts.append(f"金额：¥{float(rem['linked_amount']):.2f}")
    if rem.get("description"):
        parts.append(str(rem["description"]))
    body = "\n".join(parts) or title_raw
    return {"title": title, "body": body}
