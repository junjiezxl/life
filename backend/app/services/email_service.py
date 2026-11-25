import asyncio
import email
import hashlib
import html
from html.parser import HTMLParser
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from .ai_service import extract_from_email_content
from .bill_parser import parse_bill_file, summarize_detected_cards
from .proxy_service import create_proxied_socket, resolve_proxy_url


def _decode_mime_str(raw: Optional[str]) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded: List[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            enc = charset or "utf-8"
            try:
                decoded.append(chunk.decode(enc, errors="ignore"))
            except LookupError:
                decoded.append(chunk.decode("utf-8", errors="ignore"))
        else:
            decoded.append(str(chunk))
    return "".join(decoded).strip()


class _TableAwareHTMLToText(HTMLParser):
    """Convert HTML email bodies to text while preserving leaf <tr> table rows as single tab-separated lines."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._out: List[str] = []
        # Stack of tables -> each table tracks active row stack: {"cells": List[str], "cur_cell": Optional[List[str]], "has_nested_table": bool}
        self._row_stack: List[Dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "head", "title"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "table":
            if self._row_stack:
                self._row_stack[-1]["has_nested_table"] = True
            return
        if t == "tr":
            self._row_stack.append({"cells": [], "cur_cell": None, "has_nested_table": False})
            return
        if t in ("td", "th") and self._row_stack:
            row = self._row_stack[-1]
            if row["cur_cell"] is not None:
                txt = re.sub(r"\s+", " ", "".join(row["cur_cell"])).strip()
                if txt:
                    row["cells"].append(txt)
            row["cur_cell"] = []
            return
        if t == "br":
            if self._row_stack and self._row_stack[-1]["cur_cell"] is not None:
                self._row_stack[-1]["cur_cell"].append(" ")
            else:
                self._out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "head", "title"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return
        if t in ("td", "th") and self._row_stack:
            row = self._row_stack[-1]
            if row["cur_cell"] is not None:
                txt = re.sub(r"\s+", " ", "".join(row["cur_cell"])).strip()
                if txt:
                    row["cells"].append(txt)
                row["cur_cell"] = None
            return
        if t == "tr" and self._row_stack:
            row = self._row_stack.pop()
            if row["cur_cell"] is not None:
                txt = re.sub(r"\s+", " ", "".join(row["cur_cell"])).strip()
                if txt:
                    row["cells"].append(txt)
            if row["cells"]:
                if not row["has_nested_table"] and len(row["cells"]) >= 2:
                    self._out.append("\n" + "\t".join(row["cells"]) + "\n")
                else:
                    self._out.append("\n" + "\n".join(row["cells"]) + "\n")
            return
        if t in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            if self._row_stack and self._row_stack[-1]["cur_cell"] is not None:
                self._row_stack[-1]["cur_cell"].append(" ")
            else:
                self._out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0 or not data:
            return
        cleaned = data.replace("\xa0", " ").replace("\u3000", " ")
        if self._row_stack and self._row_stack[-1]["cur_cell"] is not None:
            self._row_stack[-1]["cur_cell"].append(cleaned)
        else:
            self._out.append(cleaned)

    def get_text(self) -> str:
        raw = "".join(self._out)
        lines: List[str] = []
        for ln in raw.splitlines():
            if "\t" in ln:
                cells = [re.sub(r"[ \r\f\v]+", " ", c).strip() for c in ln.split("\t")]
                cells = [c for c in cells if c]
                if cells:
                    lines.append("\t".join(cells))
            else:
                s = re.sub(r"[ \r\f\v]+", " ", ln).strip()
                if s:
                    lines.append(s)
        return "\n".join(lines).strip()


def _html_to_text(raw_html: str) -> str:
    try:
        parser = _TableAwareHTMLToText()
        parser.feed(raw_html)
        parser.close()
        parsed = parser.get_text()
        if parsed:
            return parsed
    except Exception:
        pass
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_email_date(date_hdr: Optional[str]) -> str:
    if not date_hdr:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    try:
        dt = parsedate_to_datetime(date_hdr)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _extract_body_and_attachments(msg: Message) -> Tuple[str, List[Tuple[str, bytes]]]:
    plain_parts: List[str] = []
    html_parts: List[str] = []
    attachments: List[Tuple[str, bytes]] = []

    if msg.is_multipart():
        for part in msg.walk():
            cdisp = str(part.get("Content-Disposition") or "").lower()
            ctype = (part.get_content_type() or "").lower()
            raw_fname = part.get_filename()
            filename = _decode_mime_str(raw_fname) if raw_fname else ""

            if filename or "attachment" in cdisp:
                payload = part.get_payload(decode=True)
                if payload and filename:
                    attachments.append((filename, payload))
                continue

            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        plain_parts.append(payload.decode(charset, errors="ignore"))
                    except LookupError:
                        plain_parts.append(payload.decode("utf-8", errors="ignore"))
            elif ctype == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html_parts.append(payload.decode(charset, errors="ignore"))
                    except LookupError:
                        html_parts.append(payload.decode("utf-8", errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                txt = payload.decode(charset, errors="ignore")
            except LookupError:
                txt = payload.decode("utf-8", errors="ignore")
            if (msg.get_content_type() or "").lower() == "text/html":
                html_parts.append(txt)
            else:
                plain_parts.append(txt)

    plain_body = "\n".join(p.strip() for p in plain_parts if p.strip())
    html_body = _html_to_text("\n".join(html_parts)) if html_parts else ""
    # Chinese bank e-statements often ship a stub text/plain ("请用HTML客户端查看") alongside the full HTML table!
    if html_body and (
        not plain_body
        or "\t" in html_body
        or len(html_body) > max(200, int(len(plain_body) * 1.3))
    ):
        body = html_body
    else:
        body = plain_body or html_body
    return body, attachments


class _ProxiedIMAP4(imaplib.IMAP4):
    def __init__(self, host: str, port: int, proxy_url: str, timeout: float = 15.0):
        self._proxy_url = proxy_url
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        return create_proxied_socket(self.host, self.port, self._proxy_url, timeout or 15.0)


class _ProxiedIMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, proxy_url: str, timeout: float = 15.0):
        self._proxy_url = proxy_url
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        import ssl

        sock = create_proxied_socket(self.host, self.port, self._proxy_url, timeout or 15.0)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


def _connect_imap(settings: Dict[str, str]) -> imaplib.IMAP4:
    host = (settings.get("email_imap_host") or "").strip()
    port = int(settings.get("email_imap_port") or 993)
    username = (settings.get("email_username") or "").strip()
    password = (settings.get("email_password") or "").strip()
    use_ssl = settings.get("email_use_ssl", "1") != "0"
    proxy_url = resolve_proxy_url("email_use_proxy", settings)

    if not host or not username or not password:
        raise ValueError("请先在「系统设置」填写邮箱 IMAP 服务器、邮箱账号和授权码。")

    if proxy_url:
        if use_ssl:
            client: imaplib.IMAP4 = _ProxiedIMAP4_SSL(host, port, proxy_url, timeout=15)
        else:
            client = _ProxiedIMAP4(host, port, proxy_url, timeout=15)
    else:
        if use_ssl:
            client = imaplib.IMAP4_SSL(host, port, timeout=15)
        else:
            client = imaplib.IMAP4(host, port, timeout=15)
    client.login(username, password)
    return client


def test_imap_connection(settings: Dict[str, str]) -> Dict[str, Any]:
    try:
        client = _connect_imap(settings)
        try:
            status, data = client.select("INBOX", readonly=True)
            msg_count = int(data[0].decode()) if status == "OK" and data and data[0] else 0
            return {
                "ok": True,
                "message": f"邮箱连接成功！收件箱共有 {msg_count} 封邮件。",
                "inbox_count": msg_count,
            }
        finally:
            try:
                client.logout()
            except Exception:
                pass
    except Exception as exc:
        err = str(exc).strip() or type(exc).__name__
        host = (settings.get("email_imap_host") or "").lower()
        hint = ""
        if "login" in err.lower() or "auth" in err.lower() or "password" in err.lower() or "invalid credentials" in err.lower():
            if "gmail" in host:
                hint = "（Gmail 需开启「两步验证」后在安全设置生成 16 位「应用专用密码 (App Password)」填入授权码框，不能用登录密码。）"
            else:
                hint = "（提示：QQ邮箱/163邮箱/Gmail 请使用「IMAP 专用授权码 / App Password」，而不是登录密码。）"
        elif "timeout" in err.lower() or "timed out" in err.lower() or "refused" in err.lower():
            if "gmail" in host:
                hint = "（提示：国内服务器直连 imap.gmail.com:993 通常会被屏蔽，请在全局网络代理配置代理并开启「邮箱走全局代理」开关。）"
        return {"ok": False, "message": f"邮箱连接失败: {err} {hint}".strip(), "inbox_count": 0}


def _fetch_raw_emails_sync(
    settings: Dict[str, str],
    days: int = 14,
    limit: int = 25,
    unread_only: bool = False,
) -> List[Dict[str, Any]]:
    client = _connect_imap(settings)
    fetched: List[Dict[str, Any]] = []
    try:
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("无法打开收件箱 INBOX")

        since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        criteria = f'(UNSEEN SINCE "{since_date}")' if unread_only else f'(SINCE "{since_date}")'
        typ, data = client.uid("search", None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []

        uids = data[0].split()
        # Most recent first
        selected_uids = list(reversed(uids))[:limit]

        for uid_bytes in selected_uids:
            uid_str = uid_bytes.decode("utf-8", errors="ignore")
            typ_f, msg_data = client.uid("fetch", uid_bytes, "(RFC822)")
            if typ_f != "OK" or not msg_data:
                continue
            raw_bytes: Optional[bytes] = None
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw_bytes = bytes(part[1])
                    break
            if not raw_bytes:
                continue

            msg = email.message_from_bytes(raw_bytes)
            subject = _decode_mime_str(msg.get("Subject"))
            sender = _decode_mime_str(msg.get("From"))
            received_at = _parse_email_date(msg.get("Date"))
            message_id = (msg.get("Message-ID") or "").strip() or f"uid:{uid_str}"
            body, attachments = _extract_body_and_attachments(msg)

            fetched.append(
                {
                    "uid": message_id,
                    "imap_uid": uid_str,
                    "subject": subject or "(无主题)",
                    "sender": sender,
                    "received_at": received_at,
                    "body": body,
                    "attachments": attachments,
                }
            )
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return fetched


async def fetch_and_parse_emails(
    settings: Dict[str, str],
    existing_refs: Set[str],
    synced_uids: Set[str],
    days: int = 14,
    limit: int = 25,
    keyword: str = "",
    zip_password: str = "",
    include_body_ai: bool = True,
    unread_only: bool = False,
    skip_already_synced: bool = False,
) -> Dict[str, Any]:
    effective_zip_pwd = zip_password or settings.get("email_bill_zip_password") or ""
    raw_emails = await asyncio.to_thread(
        _fetch_raw_emails_sync,
        settings,
        days=days,
        limit=limit,
        unread_only=unread_only,
    )

    kw_lower = keyword.strip().lower()
    email_summaries: List[Dict[str, Any]] = []
    all_txs: List[Dict[str, Any]] = []
    all_reminders: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for item in raw_emails:
        uid = item["uid"]
        if skip_already_synced and uid in synced_uids:
            continue

        subject = item["subject"]
        sender = item["sender"]
        body = item["body"]
        attachments: List[Tuple[str, bytes]] = item["attachments"]
        att_names = [a[0] for a in attachments]

        if kw_lower:
            haystack = f"{subject} {sender} {' '.join(att_names)} {body[:600]}".lower()
            if kw_lower not in haystack:
                continue

        email_tx_count = 0
        email_rem_count = 0

        # 1. Parse bill attachments (.zip / .csv / .xlsx)
        for fname, raw_bytes in attachments:
            lower_fn = fname.lower()
            if not lower_fn.endswith((".zip", ".csv", ".xlsx")):
                continue
            parsed = parse_bill_file(
                raw_bytes,
                fname,
                zip_password=effective_zip_pwd,
                existing_refs=existing_refs,
            )
            if parsed["errors"]:
                warnings.extend(parsed["errors"])
            for tx in parsed["transactions"]:
                tx["email_subject"] = subject
                tx["email_uid"] = uid
                all_txs.append(tx)
                email_tx_count += 1

        # 2. Parse email body for bills / itineraries / reminders
        should_parse_body = include_body_ai and bool(body.strip()) and (
            email_tx_count == 0
            or any(k in subject for k in ("账单", "还款", "行程", "机票", "火车", "缴费", "提醒", "发票", "工资"))
        )
        if should_parse_body:
            body_res = await extract_from_email_content(
                subject=subject,
                sender=sender,
                received_at=item["received_at"],
                body_text=body,
                settings=settings,
            )
            for tx in body_res.get("transactions", []):
                fp_raw = f"email|{uid}|{tx.get('occurred_at') or ''}|{tx.get('card_last4') or ''}|{tx.get('amount')}|{tx.get('description')}"
                ext_ref = "email:" + hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()[:16]
                tx["external_ref"] = ext_ref
                tx["already_imported"] = ext_ref in existing_refs
                tx["email_subject"] = subject
                tx["email_uid"] = uid
                all_txs.append(tx)
                email_tx_count += 1

            for rem in body_res.get("reminders", []):
                rem["email_subject"] = subject
                rem["email_uid"] = uid
                all_reminders.append(rem)
                email_rem_count += 1

        email_summaries.append(
            {
                "uid": uid,
                "subject": subject,
                "sender": sender,
                "received_at": item["received_at"],
                "attachments": att_names,
                "already_synced": uid in synced_uids,
                "parsed_tx_count": email_tx_count,
                "parsed_rem_count": email_rem_count,
                "body_preview": body[:600],
            }
        )

    return {
        "emails": email_summaries,
        "transactions": all_txs,
        "reminders": all_reminders,
        "warnings": list(dict.fromkeys(warnings)),
    }


def build_account_imap_settings(base_settings: Dict[str, str], account: Dict[str, Any]) -> Dict[str, str]:
    merged = dict(base_settings)
    merged["email_imap_host"] = str(account.get("imap_host") or "").strip()
    merged["email_imap_port"] = str(account.get("imap_port") or 993)
    merged["email_username"] = str(account.get("username") or "").strip()
    merged["email_password"] = str(account.get("password") or "").strip()
    merged["email_use_ssl"] = "0" if not account.get("use_ssl", 1) else "1"
    merged["email_use_proxy"] = "1" if account.get("use_proxy") else "0"
    if account.get("bill_zip_password"):
        merged["email_bill_zip_password"] = str(account.get("bill_zip_password") or "")
    return merged


async def fetch_and_parse_multi_emails(
    base_settings: Dict[str, str],
    accounts: List[Dict[str, Any]],
    existing_refs: Set[str],
    synced_uids: Set[str],
    days: int = 14,
    limit: int = 25,
    keyword: str = "",
    zip_password: str = "",
    include_body_ai: bool = True,
    unread_only: bool = False,
    skip_already_synced: bool = False,
) -> Dict[str, Any]:
    if not accounts:
        raise ValueError("尚未配置任何可用的邮箱账号，请先在「系统设置 → 邮箱 & Bark」添加至少一个邮箱。")

    all_emails: List[Dict[str, Any]] = []
    all_txs: List[Dict[str, Any]] = []
    all_rems: List[Dict[str, Any]] = []
    all_warnings: List[str] = []
    scanned_accounts: List[str] = []
    succeeded = 0
    last_exc: Optional[Exception] = None

    for acct in accounts:
        acct_label = (acct.get("name") or acct.get("username") or "邮箱").strip()
        acct_id = acct.get("id")
        acct_settings = build_account_imap_settings(base_settings, acct)
        try:
            res = await fetch_and_parse_emails(
                settings=acct_settings,
                existing_refs=existing_refs,
                synced_uids=synced_uids,
                days=days,
                limit=limit,
                keyword=keyword,
                zip_password=zip_password,
                include_body_ai=include_body_ai,
                unread_only=unread_only,
                skip_already_synced=skip_already_synced,
            )
            succeeded += 1
            scanned_accounts.append(acct_label)
            for em in res.get("emails", []):
                em["email_account_id"] = acct_id
                em["email_account_name"] = acct_label
                all_emails.append(em)
            for tx in res.get("transactions", []):
                tx["email_account_id"] = acct_id
                tx["email_account_name"] = acct_label
                all_txs.append(tx)
            for rm in res.get("reminders", []):
                rm["email_account_id"] = acct_id
                rm["email_account_name"] = acct_label
                all_rems.append(rm)
            for w in res.get("warnings", []):
                all_warnings.append(f"[{acct_label}] {w}")
        except Exception as exc:
            last_exc = exc
            err_msg = str(exc).strip() or type(exc).__name__
            all_warnings.append(f"邮箱「{acct_label}」拉取失败: {err_msg}")

    if succeeded == 0 and last_exc is not None:
        if len(accounts) == 1:
            raise last_exc
        raise RuntimeError("；".join(all_warnings))

    all_emails.sort(key=lambda e: str(e.get("received_at") or ""), reverse=True)
    return {
        "emails": all_emails,
        "transactions": all_txs,
        "reminders": all_rems,
        "detected_cards": summarize_detected_cards(all_txs),
        "warnings": list(dict.fromkeys(all_warnings)),
        "scanned_accounts": scanned_accounts,
    }
