from typing import Any, Dict, Optional

import httpx


def _build_bark_push_url(server_url: str, device_key: str) -> str:
    key = (device_key or "").strip().strip("/")
    base = (server_url or "https://api.day.app").strip().rstrip("/")
    if not key:
        return ""
    if key.startswith("http://") or key.startswith("https://"):
        # User pasted full Bark URL like https://api.day.app/AbCdEf
        return key
    return f"{base}/{key}"


def _priority_to_bark_level(priority: str) -> str:
    if priority == "high":
        return "timeSensitive"
    if priority == "low":
        return "passive"
    return "active"


async def send_bark_push(
    title: str,
    body: str,
    settings: Dict[str, str],
    priority: str = "medium",
    group: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    if not force and settings.get("bark_enabled", "0") != "1":
        return {"ok": False, "skipped": True, "message": "Bark 推送未开启"}

    push_url = _build_bark_push_url(
        settings.get("bark_server_url", "https://api.day.app"),
        settings.get("bark_device_key", ""),
    )
    if not push_url:
        return {"ok": False, "skipped": not force, "message": "未配置 Bark Device Key"}

    payload: Dict[str, Any] = {
        "title": title.strip() or "LifeHub 提醒",
        "body": (body or title).strip()[:500],
        "group": (group or settings.get("bark_group") or "LifeHub").strip(),
        "level": _priority_to_bark_level(priority),
        "isArchive": 1,
    }
    sound = (settings.get("bark_sound") or "").strip()
    if sound:
        payload["sound"] = sound

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(push_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code", 200) != 200:
                return {"ok": False, "message": f"Bark 返回异常: {data.get('message') or data}"}
            return {"ok": True, "message": "Bark 推送成功"}
    except Exception as exc:
        return {"ok": False, "message": f"Bark 推送失败: {exc}"}


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
