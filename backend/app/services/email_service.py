import asyncio
import email
import hashlib
import html
from html.parser import HTMLParser
import imaplib
import inspect
import io
import json
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..db import get_conn, now_iso, update_settings
from .ai_service import _extract_stock_trades_from_email, extract_from_email_content
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
                self._row_stack[-1]["cur_cell"].append("\n")
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
                raw_cell = "".join(row["cur_cell"])
                cell_lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw_cell.splitlines()]
                txt = "\n".join(ln for ln in cell_lines if ln)
                if txt:
                    row["cells"].append(txt)
                row["cur_cell"] = None
            return
        if t == "tr" and self._row_stack:
            row = self._row_stack.pop()
            if row["cur_cell"] is not None:
                raw_cell = "".join(row["cur_cell"])
                cell_lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw_cell.splitlines()]
                txt = "\n".join(ln for ln in cell_lines if ln)
                if txt:
                    row["cells"].append(txt)
            if row["cells"]:
                if not row["has_nested_table"] and len(row["cells"]) >= 2:
                    flat_cells = [re.sub(r"\s+", " ", c).strip() for c in row["cells"]]
                    self._out.append("\n" + "\t".join(flat_cells) + "\n")
                else:
                    self._out.append("\n" + "\n".join(row["cells"]) + "\n")
            return
        if t in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            if self._row_stack and self._row_stack[-1]["cur_cell"] is not None:
                self._row_stack[-1]["cur_cell"].append("\n")
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


def _decode_bytes_with_charset(payload: bytes, charset: Optional[str]) -> str:
    enc = (charset or "utf-8").strip().lower()
    if enc in ("gb2312", "gbk", "x-gbk"):
        enc = "gb18030"
    elif enc in ("big5", "big5-tw"):
        enc = "big5hkscs"
    for cand in (enc, "utf-8", "gb18030", "big5hkscs"):
        try:
            return payload.decode(cand)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode(enc, errors="ignore")


def _extract_body_variants_and_attachments(msg: Message) -> Tuple[str, List[Tuple[str, bytes]], str]:
    plain_parts: List[str] = []
    html_parts: List[str] = []
    attachments: List[Tuple[str, bytes]] = []

    if msg.is_multipart():
        att_idx = 0
        for part in msg.walk():
            if part.is_multipart():
                continue
            cdisp = str(part.get("Content-Disposition") or "").lower()
            ctype = (part.get_content_type() or "").lower()
            raw_fname = part.get_filename()
            filename = _decode_mime_str(raw_fname) if raw_fname else ""

            if filename or "attachment" in cdisp or ctype in ("application/pdf", "application/zip", "application/x-zip-compressed"):
                payload = part.get_payload(decode=True)
                if payload:
                    if not filename:
                        att_idx += 1
                        ext = ".pdf" if ctype == "application/pdf" else (".zip" if "zip" in ctype else (".html" if ctype == "text/html" else ".bin"))
                        filename = f"attachment_{att_idx}{ext}"
                    attachments.append((filename, payload))
                continue

            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    plain_parts.append(_decode_bytes_with_charset(payload, part.get_content_charset()))
            elif ctype == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html_parts.append(_decode_bytes_with_charset(payload, part.get_content_charset()))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            txt = _decode_bytes_with_charset(payload, msg.get_content_charset())
            if (msg.get_content_type() or "").lower() == "text/html":
                html_parts.append(txt)
            else:
                plain_parts.append(txt)

    plain_body = "\n".join(p.strip() for p in plain_parts if p.strip())
    html_body = _html_to_text("\n".join(html_parts)) if html_parts else ""
    # Chinese/HK bank advices often ship a stub text/plain alongside full HTML tables!
    if html_body and (
        not plain_body
        or "\t" in html_body
        or len(html_body) >= max(120, int(len(plain_body) * 0.9))
    ):
        body = html_body
        alt_body = plain_body
    else:
        body = plain_body or html_body
        alt_body = html_body
    return body, attachments, alt_body


def _extract_body_and_attachments(msg: Message) -> Tuple[str, List[Tuple[str, bytes]]]:
    body, attachments, _ = _extract_body_variants_and_attachments(msg)
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


def _keyword_matches(kw_lower: str, haystack_lower: str) -> bool:
    if not kw_lower:
        return True
    if kw_lower in ("hsbc", "汇丰", "滙豐"):
        return any(k in haystack_lower for k in ("hsbc", "汇丰", "滙豐", "investdirect"))
    return kw_lower in haystack_lower


_STOCK_HEADER_CUES = (
    "hsbc",
    "汇丰",
    "滙豐",
    "investdirect",
    "stock",
    "securit",
    "equity",
    "etf",
    "contract",
    "advice",
    "eadvice",
    "e-advice",
    "order",
    "execut",
    "trade",
    "futu",
    "moomoo",
    "tiger",
    "ibkr",
    "interactive",
    "schwab",
    "bochk",
    "hang seng",
    "恒生",
    "恆生",
    "中銀",
    "中银",
    "股票",
    "證券",
    "证券",
    "股份",
    "成交",
    "交易",
    "買入",
    "买入",
    "賣出",
    "卖出",
    "沽出",
    "執行",
    "执行",
    "結單",
    "结单",
    "通知",
)


def _discover_imap_folders(client: imaplib.IMAP4) -> List[str]:
    """Return Gmail All Mail (superset of Inbox+Archive) if present, else INBOX."""
    try:
        typ, lines = client.list()
        if typ == "OK" and lines:
            for raw_ln in lines:
                if not raw_ln:
                    continue
                line = raw_ln.decode("utf-8", errors="ignore") if isinstance(raw_ln, (bytes, bytearray)) else str(raw_ln)
                m = re.match(r'^\(([^)]*)\)\s+"?[^"\s]*"?\s+(.+)$', line.strip())
                if not m:
                    continue
                flags_low = m.group(1).lower()
                name = m.group(2).strip()
                if name.startswith('"') and name.endswith('"') and len(name) >= 2:
                    name = name[1:-1].replace('\\"', '"')
                if "\\noselect" in flags_low:
                    continue
                if "\\all" in flags_low or "all mail" in name.lower():
                    return [name, "INBOX"]
    except Exception:
        pass
    return ["INBOX"]


def _fetch_raw_emails_sync(
    settings: Dict[str, str],
    days: int = 14,
    limit: int = 25,
    unread_only: bool = False,
    keyword: str = "",
    stock_only: bool = False,
    skip_imap_uids: Optional[Set[Tuple[str, str]]] = None,
    on_batch: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> List[Dict[str, Any]]:
    client = _connect_imap(settings)
    fetched: List[Dict[str, Any]] = []
    seen_msg_ids: Set[str] = set()
    skip_pairs = skip_imap_uids or set()
    try:
        folders = _discover_imap_folders(client)
        since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        criteria = f'(UNSEEN SINCE "{since_date}")' if unread_only else f'(SINCE "{since_date}")'
        kw_lower = (keyword or "").strip().lower()
        opened_any = False

        for folder in folders:
            if len(fetched) >= limit:
                break
            mailbox_arg = f'"{folder}"' if (" " in folder or "[" in folder or "/" in folder) and not folder.startswith('"') else folder
            try:
                status, _ = client.select(mailbox_arg, readonly=True)
            except Exception:
                continue
            if status != "OK":
                continue
            opened_any = True

            typ, data = client.uid("search", None, criteria)
            if typ != "OK" or not data or not data[0]:
                continue

            uids = data[0].split()
            reversed_uids = [
                u for u in reversed(uids)
                if (folder, u.decode("utf-8", errors="ignore")) not in skip_pairs
            ]
            if not reversed_uids:
                if "\\all" in folder.lower() or "all mail" in folder.lower():
                    break
                continue
            remaining = max(1, limit - len(fetched))

            # For keyword or stock_only scan: batch-fetch lightweight headers in 80-UID chunks first
            # so we only download full RFC822 bodies for matching broker/stock emails (~2-4s instead of 200s!)
            if (kw_lower and len(reversed_uids) > remaining) or stock_only:
                pool_uids = reversed_uids[: max(remaining * 3, 240)]
                matched_uids: List[bytes] = []
                for start in range(0, len(pool_uids), 80):
                    chunk = pool_uids[start : start + 80]
                    try:
                        uid_list = b",".join(chunk)
                        typ_h, hdr_data = client.uid("fetch", uid_list, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                        if typ_h == "OK" and hdr_data:
                            for part in hdr_data:
                                if not (isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray))):
                                    continue
                                meta_line = part[0].decode("utf-8", errors="ignore") if isinstance(part[0], (bytes, bytearray)) else str(part[0])
                                m_u = re.search(r"UID\s+(\d+)", meta_line, re.IGNORECASE)
                                if not m_u:
                                    continue
                                hmsg = email.message_from_bytes(bytes(part[1]))
                                subj_s = _decode_mime_str(hmsg.get("Subject")).lower()
                                from_s = _decode_mime_str(hmsg.get("From")).lower()
                                hdr_hay = f"{subj_s} {from_s}"
                                if kw_lower:
                                    if _keyword_matches(kw_lower, hdr_hay):
                                        matched_uids.append(m_u.group(1).encode("ascii"))
                                elif any(cue in hdr_hay for cue in _STOCK_HEADER_CUES):
                                    matched_uids.append(m_u.group(1).encode("ascii"))
                    except Exception:
                        continue
                if matched_uids:
                    selected_uids = matched_uids[:remaining]
                else:
                    # Fallback cap so an empty match downloads at most 15 recent emails for preview instead of hanging
                    selected_uids = reversed_uids[: min(remaining, 15 if stock_only else remaining)]
            else:
                selected_uids = reversed_uids[:remaining]

            # Fetch RFC822 in chunks of 15 UIDs (20x fewer round-trips on 2-year backfills, with single-UID fallback)
            def _parse_fetched_parts(msg_data_list: Any, fallback_uid: str) -> List[Dict[str, Any]]:
                parsed_chunk: List[Dict[str, Any]] = []
                if not msg_data_list:
                    return parsed_chunk
                for part in msg_data_list:
                    if not (isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray))):
                        continue
                    meta_line = part[0].decode("utf-8", errors="ignore") if isinstance(part[0], (bytes, bytearray)) else str(part[0])
                    m_uid = re.search(r"UID\s+(\d+)", meta_line, re.IGNORECASE)
                    uid_str = m_uid.group(1) if m_uid else fallback_uid
                    raw_bytes = bytes(part[1])
                    if not raw_bytes:
                        continue
                    msg = email.message_from_bytes(raw_bytes)
                    subject = _decode_mime_str(msg.get("Subject"))
                    sender = _decode_mime_str(msg.get("From"))
                    received_at = _parse_email_date(msg.get("Date"))
                    message_id = (msg.get("Message-ID") or "").strip() or f"uid:{folder}:{uid_str}"
                    if message_id in seen_msg_ids:
                        continue
                    seen_msg_ids.add(message_id)
                    body, attachments, alt_body = _extract_body_variants_and_attachments(msg)
                    parsed_chunk.append(
                        {
                            "uid": message_id,
                            "imap_uid": uid_str,
                            "folder": folder,
                            "subject": subject or "(无主题)",
                            "sender": sender,
                            "received_at": received_at,
                            "body": body,
                            "alt_body": alt_body,
                            "attachments": attachments,
                        }
                    )
                return parsed_chunk

            for start in range(0, len(selected_uids), 15):
                if len(fetched) >= limit:
                    break
                uid_chunk = selected_uids[start : start + 15]
                batch_items: List[Dict[str, Any]] = []
                chunk_ok = False
                try:
                    typ_f, msg_data = client.uid("fetch", b",".join(uid_chunk), "(RFC822)")
                    if typ_f == "OK" and msg_data:
                        batch_items = _parse_fetched_parts(msg_data, uid_chunk[0].decode("utf-8", errors="ignore"))
                        chunk_ok = True
                except Exception:
                    chunk_ok = False
                if not chunk_ok:
                    for uid_bytes in uid_chunk:
                        if len(fetched) >= limit:
                            break
                        uid_str = uid_bytes.decode("utf-8", errors="ignore")
                        try:
                            typ_f, msg_data = client.uid("fetch", uid_bytes, "(RFC822)")
                            if typ_f == "OK" and msg_data:
                                batch_items.extend(_parse_fetched_parts(msg_data, uid_str))
                        except Exception:
                            continue
                if batch_items:
                    room = max(0, limit - len(fetched))
                    batch_slice = batch_items[:room]
                    fetched.extend(batch_slice)
                    if on_batch and batch_slice:
                        try:
                            on_batch(batch_slice)
                        except Exception:
                            pass

            # For Gmail All Mail we already scanned everything; no need to re-scan INBOX unless All Mail failed
            if "\\all" in folder.lower() or "all mail" in folder.lower():
                break

        if not opened_any:
            raise RuntimeError("无法打开收件箱 INBOX")
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return fetched


def _extract_pdf_text(raw_bytes: bytes, passwords: Optional[List[str]] = None) -> Tuple[str, str]:
    """Extract text from a PDF attachment (supporting layout preservation & optional password decryption)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", ""
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        if reader.is_encrypted:
            unlocked = False
            for pwd in [p for p in (passwords or []) if p] + [""]:
                try:
                    if reader.decrypt(pwd) != 0:
                        unlocked = True
                        break
                except Exception:
                    continue
            if not unlocked:
                return "", "PDF 附件已加密，请在密码框填写解压/打开密码"
        pages = []
        for page in reader.pages[:20]:
            txt = ""
            try:
                txt = (page.extract_text(extraction_mode="layout") or "").strip()
            except Exception:
                txt = ""
            if not txt:
                txt = (page.extract_text() or "").strip()
            if txt:
                pages.append(txt)
        return "\n".join(pages), ""
    except Exception as exc:
        return "", f"PDF 附件解析失败: {exc}"


def _extract_zip_nested_docs(
    raw_bytes: bytes,
    passwords: Optional[List[str]] = None,
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Extract .pdf and .htm/.html files inside a .zip attachment."""
    docs: List[Tuple[str, str]] = []
    errs: List[str] = []
    try:
        import pyzipper
        opener = pyzipper.AESZipFile
    except ImportError:
        import zipfile
        opener = zipfile.ZipFile  # type: ignore[assignment]
    try:
        with opener(io.BytesIO(raw_bytes)) as zf:
            pwd_list = [p for p in (passwords or []) if p] + [""]
            for info in zf.infolist():
                if info.is_dir():
                    continue
                low_name = info.filename.lower()
                if not low_name.endswith((".pdf", ".htm", ".html")):
                    continue
                inner_bytes: Optional[bytes] = None
                for p in pwd_list:
                    try:
                        inner_bytes = zf.read(info, pwd=p.encode("utf-8") if p else None)
                        break
                    except Exception:
                        continue
                if inner_bytes is None:
                    errs.append(f"{info.filename}: ZIP 内文件已加密或解压失败")
                    continue
                if low_name.endswith(".pdf"):
                    ptxt, perr = _extract_pdf_text(inner_bytes, passwords)
                    if perr:
                        errs.append(f"{info.filename}: {perr}")
                    elif ptxt.strip():
                        docs.append((info.filename, ptxt.strip()))
                else:
                    htxt = _html_to_text(_decode_bytes_with_charset(inner_bytes, "utf-8"))
                    if htxt.strip():
                        docs.append((info.filename, htxt.strip()))
    except Exception:
        pass
    return docs, errs


_MAX_CACHED_ATTACHMENT_BYTES = 8 * 1024 * 1024


def _is_stock_hint_text(subject: str, sender: str, att_names: List[str], body_snippet: str) -> bool:
    hay = f"{subject} {sender} {' '.join(att_names)} {body_snippet[:1200]}".lower()
    return any(
        h in hay
        for h in (
            "hsbc",
            "汇丰",
            "滙豐",
            "investdirect",
            "股票",
            "證券",
            "证券",
            "股份",
            "contract note",
            "trade confirmation",
            "trade advice",
            "eadvice",
            "e-advice",
            "futu",
            "ibkr",
            "成交",
        )
    )


def _extract_docs_from_attachments(
    attachments: List[Tuple[str, bytes]],
    effective_zip_pwd: str,
    existing_refs: Optional[Set[str]] = None,
    parse_bills: bool = False,
) -> Tuple[List[Tuple[str, str]], List[Dict[str, Any]], List[str]]:
    extra_docs: List[Tuple[str, str]] = []
    bill_txs: List[Dict[str, Any]] = []
    warnings: List[str] = []
    refs = existing_refs if existing_refs is not None else set()

    for fname, raw_bytes in attachments:
        lower_fn = fname.lower()
        if lower_fn.endswith(".pdf"):
            ptxt, perr = _extract_pdf_text(raw_bytes, [effective_zip_pwd])
            if perr:
                warnings.append(f"{fname}: {perr}")
            elif ptxt.strip():
                extra_docs.append((f"PDF附件: {fname}", ptxt.strip()))
            continue
        if lower_fn.endswith((".htm", ".html")):
            htxt = _html_to_text(_decode_bytes_with_charset(raw_bytes, "utf-8"))
            if htxt.strip():
                extra_docs.append((f"HTML附件: {fname}", htxt.strip()))
            continue
        if not lower_fn.endswith((".zip", ".csv", ".xlsx")):
            continue
        if lower_fn.endswith(".zip"):
            zdocs, zerrs = _extract_zip_nested_docs(raw_bytes, [effective_zip_pwd])
            extra_docs.extend((f"ZIP内文件: {dn}", dt) for dn, dt in zdocs)
            warnings.extend(zerrs)
        if parse_bills:
            parsed = parse_bill_file(
                raw_bytes,
                fname,
                zip_password=effective_zip_pwd,
                existing_refs=refs,
            )
            if parsed["errors"]:
                warnings.extend(parsed["errors"])
            bill_txs.extend(parsed["transactions"])
    return extra_docs, bill_txs, warnings


def save_raw_emails_to_cache(
    conn: Any,
    raw_emails: List[Dict[str, Any]],
    account_id: Optional[int],
    account_name: str,
    zip_password: str = "",
) -> Dict[str, Any]:
    """Persist fetched raw emails + extracted docs + binary attachments into SQLite for shared offline reuse."""
    ts = now_iso()
    db_account_id = int(account_id) if (account_id is not None and int(account_id) > 0) else None
    saved_new = 0
    newly_cached_uids: List[str] = []
    existing_uids = {
        r["message_uid"]
        for r in conn.execute("SELECT message_uid FROM email_messages").fetchall()
        if r["message_uid"]
    }

    for item in raw_emails:
        uid = str(item.get("uid") or "").strip()
        if not uid:
            continue
        is_new = uid not in existing_uids
        if is_new:
            saved_new += 1
            newly_cached_uids.append(uid)
            existing_uids.add(uid)

        subject = str(item.get("subject") or "(无主题)")
        sender = str(item.get("sender") or "")
        received_at = str(item.get("received_at") or "")
        body = str(item.get("body") or "")
        alt_body = str(item.get("alt_body") or "")
        imap_uid = str(item.get("imap_uid") or "")
        folder = str(item.get("folder") or "INBOX")
        attachments: List[Tuple[str, bytes]] = item.get("attachments") or []
        att_names = [a[0] for a in attachments]

        extra_docs, _, _ = _extract_docs_from_attachments(
            attachments,
            effective_zip_pwd=zip_password,
            parse_bills=False,
        )
        stock_hint = 1 if _is_stock_hint_text(subject, sender, att_names, body) else 0

        conn.execute(
            """INSERT INTO email_messages
            (message_uid, email_account_id, email_account_name, imap_uid, folder, subject, sender, received_at,
             body_text, alt_body_text, attachments_json, extra_docs_json, stock_hint, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_uid) DO UPDATE SET
              email_account_id = COALESCE(excluded.email_account_id, email_messages.email_account_id),
              email_account_name = CASE WHEN excluded.email_account_name != '' THEN excluded.email_account_name ELSE email_messages.email_account_name END,
              imap_uid = CASE WHEN excluded.imap_uid != '' THEN excluded.imap_uid ELSE email_messages.imap_uid END,
              folder = CASE WHEN excluded.folder != '' THEN excluded.folder ELSE email_messages.folder END,
              subject = excluded.subject,
              sender = excluded.sender,
              received_at = excluded.received_at,
              body_text = excluded.body_text,
              alt_body_text = excluded.alt_body_text,
              attachments_json = excluded.attachments_json,
              extra_docs_json = CASE WHEN excluded.extra_docs_json != '[]' THEN excluded.extra_docs_json ELSE email_messages.extra_docs_json END,
              stock_hint = MAX(email_messages.stock_hint, excluded.stock_hint),
              fetched_at = excluded.fetched_at""",
            (
                uid,
                db_account_id,
                account_name,
                imap_uid,
                folder,
                subject,
                sender,
                received_at,
                body,
                alt_body,
                json.dumps(att_names, ensure_ascii=False),
                json.dumps(extra_docs, ensure_ascii=False),
                stock_hint,
                ts,
            ),
        )
        row = conn.execute("SELECT id FROM email_messages WHERE message_uid = ?", (uid,)).fetchone()
        if row and attachments:
            email_id = int(row["id"])
            conn.execute("DELETE FROM email_attachments WHERE email_id = ?", (email_id,))
            for fname, raw_bytes in attachments:
                if raw_bytes and len(raw_bytes) <= _MAX_CACHED_ATTACHMENT_BYTES:
                    conn.execute(
                        "INSERT INTO email_attachments (email_id, filename, content_bytes, size_bytes) VALUES (?, ?, ?, ?)",
                        (email_id, fname, raw_bytes, len(raw_bytes)),
                    )

    return {
        "saved_new": saved_new,
        "total_processed": len(raw_emails),
        "newly_cached_uids": newly_cached_uids,
    }


def _call_fetch_raw_emails_compat(
    settings: Dict[str, str],
    days: int,
    limit: int,
    unread_only: bool,
    keyword: str,
    stock_only: bool,
    skip_imap_uids: Optional[Set[Tuple[str, str]]] = None,
    on_batch: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> List[Dict[str, Any]]:
    target_fn = getattr(_fetch_raw_emails_sync, "side_effect", None)
    sig_target = target_fn if callable(target_fn) else _fetch_raw_emails_sync
    sync_params = inspect.signature(sig_target).parameters
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sync_params.values())
    fetch_kwargs: Dict[str, Any] = {
        "days": days,
        "limit": limit,
        "unread_only": unread_only,
    }
    if keyword and (accepts_var_kw or "keyword" in sync_params):
        fetch_kwargs["keyword"] = keyword
    if stock_only and (accepts_var_kw or "stock_only" in sync_params):
        fetch_kwargs["stock_only"] = True
    if skip_imap_uids and (accepts_var_kw or "skip_imap_uids" in sync_params):
        fetch_kwargs["skip_imap_uids"] = skip_imap_uids
    if on_batch is not None and (accepts_var_kw or "on_batch" in sync_params):
        fetch_kwargs["on_batch"] = on_batch
    return _fetch_raw_emails_sync(settings, **fetch_kwargs)


async def _parse_raw_email_items(
    raw_emails: List[Dict[str, Any]],
    settings: Dict[str, str],
    existing_refs: Set[str],
    synced_uids: Set[str],
    keyword: str = "",
    zip_password: str = "",
    include_body_ai: bool = True,
    skip_already_synced: bool = False,
    existing_stock_refs: Optional[Set[str]] = None,
    stock_only: bool = False,
    account_settings_map: Optional[Dict[int, Dict[str, str]]] = None,
) -> Dict[str, Any]:
    default_zip_pwd = zip_password or settings.get("email_bill_zip_password") or ""
    kw_lower = keyword.strip().lower()
    email_summaries: List[Dict[str, Any]] = []
    all_txs: List[Dict[str, Any]] = []
    all_reminders: List[Dict[str, Any]] = []
    all_stock_trades: List[Dict[str, Any]] = []
    warnings: List[str] = []
    stock_refs = existing_stock_refs or set()

    for item in raw_emails:
        uid = item["uid"]
        if skip_already_synced and uid in synced_uids:
            continue

        acct_id = item.get("email_account_id")
        acct_label = str(item.get("email_account_name") or "").strip()
        item_settings = (account_settings_map or {}).get(acct_id, settings) if acct_id is not None else settings
        effective_zip_pwd = zip_password or item_settings.get("email_bill_zip_password") or default_zip_pwd

        subject = item["subject"]
        sender = item["sender"]
        body = item["body"]
        alt_body = item.get("alt_body") or ""
        attachments: List[Tuple[str, bytes]] = item.get("attachments") or []
        att_names = list(item.get("att_names") or [a[0] for a in attachments])

        email_tx_count = 0
        email_rem_count = 0
        email_stock_count = 0

        # Use raw attachments if present (so password-protected PDF/ZIP/bill CSVs always parse with user's zip_password),
        # otherwise fall back to pre-extracted docs stored in local SQLite cache.
        if attachments:
            extra_docs, bill_txs, att_warnings = _extract_docs_from_attachments(
                attachments,
                effective_zip_pwd=effective_zip_pwd,
                existing_refs=existing_refs,
                parse_bills=not stock_only,
            )
            if not extra_docs and item.get("preextracted_docs"):
                extra_docs = list(item["preextracted_docs"])
            for w in att_warnings:
                warnings.append(f"[{acct_label}] {w}" if acct_label else w)
            for tx in bill_txs:
                tx["email_subject"] = subject
                tx["email_uid"] = uid
                if acct_id is not None:
                    tx["email_account_id"] = acct_id
                    tx["email_account_name"] = acct_label
                all_txs.append(tx)
                email_tx_count += 1
        else:
            extra_docs = list(item.get("preextracted_docs") or [])

        doc_blocks = [f"[{dlabel}]\n{dtext}" for dlabel, dtext in extra_docs]
        combined_body = (body + ("\n\n" + "\n\n".join(doc_blocks) if doc_blocks else "")).strip()

        if kw_lower:
            haystack = f"{subject} {sender} {' '.join(att_names)} {combined_body[:4000]}".lower()
            if not _keyword_matches(kw_lower, haystack):
                continue

        is_stock_hint = _is_stock_hint_text(subject, sender, att_names, combined_body)
        email_seen_stk: Set[Tuple[str, str, float, float, str]] = set()

        def _append_stock_candidates(cands: List[Dict[str, Any]]) -> None:
            nonlocal email_stock_count
            for st in cands:
                sig = (str(st.get("side") or ""), str(st.get("symbol") or ""), float(st.get("price") or 0), float(st.get("quantity") or 0), str(st.get("traded_at") or "")[:10])
                if sig in email_seen_stk:
                    continue
                email_seen_stk.add(sig)
                fp_stk = f"email_stk|{uid}|{st.get('side')}|{st.get('symbol')}|{st.get('price')}|{st.get('quantity')}|{st.get('traded_at')}"
                stk_ref = "email_stk:" + hashlib.sha256(fp_stk.encode("utf-8")).hexdigest()[:16]
                st["external_ref"] = stk_ref
                st["already_imported"] = stk_ref in stock_refs
                st["email_subject"] = subject
                st["email_uid"] = uid
                if acct_id is not None:
                    st["email_account_id"] = acct_id
                    st["email_account_name"] = acct_label
                all_stock_trades.append(st)
                email_stock_count += 1

        if stock_only:
            det_trades = _extract_stock_trades_from_email(subject, sender, item["received_at"], body)
            if not det_trades and alt_body:
                det_trades = _extract_stock_trades_from_email(subject, sender, item["received_at"], alt_body)
            _append_stock_candidates(det_trades)
            for dlabel, dtext in extra_docs:
                _append_stock_candidates(_extract_stock_trades_from_email(f"{subject} {dlabel}", sender, item["received_at"], dtext))

            if include_body_ai and email_stock_count == 0 and (is_stock_hint or bool(extra_docs)):
                ai_target = extra_docs[0][1] if (extra_docs and len(body) > 2500) else combined_body
                if ai_target.strip():
                    body_res = await extract_from_email_content(
                        subject=subject,
                        sender=sender,
                        received_at=item["received_at"],
                        body_text=ai_target,
                        settings=item_settings,
                    )
                    _append_stock_candidates(body_res.get("stock_trades", []))
        else:
            should_parse_body = include_body_ai and bool(combined_body.strip()) and (
                email_tx_count == 0
                or bool(extra_docs)
                or any(k in subject for k in ("账单", "还款", "行程", "机票", "火车", "缴费", "提醒", "发票", "工资", "股票", "证券", "證券", "成交", "HSBC", "汇丰", "滙豐"))
            )
            if should_parse_body:
                body_res = await extract_from_email_content(
                    subject=subject,
                    sender=sender,
                    received_at=item["received_at"],
                    body_text=combined_body,
                    settings=item_settings,
                )
                for tx in body_res.get("transactions", []):
                    fp_raw = f"email|{uid}|{tx.get('occurred_at') or ''}|{tx.get('card_last4') or ''}|{tx.get('amount')}|{tx.get('description')}"
                    ext_ref = "email:" + hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()[:16]
                    tx["external_ref"] = ext_ref
                    tx["already_imported"] = ext_ref in existing_refs
                    tx["email_subject"] = subject
                    tx["email_uid"] = uid
                    if acct_id is not None:
                        tx["email_account_id"] = acct_id
                        tx["email_account_name"] = acct_label
                    all_txs.append(tx)
                    email_tx_count += 1

                for rem in body_res.get("reminders", []):
                    rem["email_subject"] = subject
                    rem["email_uid"] = uid
                    if acct_id is not None:
                        rem["email_account_id"] = acct_id
                        rem["email_account_name"] = acct_label
                    all_reminders.append(rem)
                    email_rem_count += 1

                _append_stock_candidates(body_res.get("stock_trades", []))
                if email_stock_count == 0 and alt_body:
                    _append_stock_candidates(_extract_stock_trades_from_email(subject, sender, item["received_at"], alt_body))
                for dlabel, dtext in extra_docs:
                    _append_stock_candidates(_extract_stock_trades_from_email(f"{subject} {dlabel}", sender, item["received_at"], dtext))

        summary_row: Dict[str, Any] = {
            "uid": uid,
            "subject": subject,
            "sender": sender,
            "received_at": item["received_at"],
            "attachments": att_names,
            "already_synced": uid in synced_uids,
            "parsed_tx_count": email_tx_count,
            "parsed_rem_count": email_rem_count,
            "parsed_stock_count": email_stock_count,
            "stock_hint": is_stock_hint,
            "body_preview": combined_body[:1500],
        }
        if acct_id is not None:
            summary_row["email_account_id"] = acct_id
            summary_row["email_account_name"] = acct_label
        email_summaries.append(summary_row)

    return {
        "emails": email_summaries,
        "transactions": all_txs,
        "reminders": all_reminders,
        "stock_trades": all_stock_trades,
        "warnings": list(dict.fromkeys(warnings)),
    }


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
    existing_stock_refs: Optional[Set[str]] = None,
    stock_only: bool = False,
) -> Dict[str, Any]:
    raw_emails = await asyncio.to_thread(
        _call_fetch_raw_emails_compat,
        settings,
        days,
        limit,
        unread_only,
        keyword,
        stock_only,
    )
    return await _parse_raw_email_items(
        raw_emails=raw_emails,
        settings=settings,
        existing_refs=existing_refs,
        synced_uids=synced_uids,
        keyword=keyword,
        zip_password=zip_password,
        include_body_ai=include_body_ai,
        skip_already_synced=skip_already_synced,
        existing_stock_refs=existing_stock_refs,
        stock_only=stock_only,
    )


def get_email_cache_stats(conn: Any, settings: Dict[str, str]) -> Dict[str, Any]:
    total = int((conn.execute("SELECT COUNT(*) AS c FROM email_messages").fetchone() or {"c": 0})["c"])
    stock_hints = int((conn.execute("SELECT COUNT(*) AS c FROM email_messages WHERE stock_hint = 1").fetchone() or {"c": 0})["c"])
    att_cnt = int((conn.execute("SELECT COUNT(*) AS c FROM email_attachments").fetchone() or {"c": 0})["c"])
    per_acct_rows = conn.execute(
        """SELECT email_account_id, email_account_name, COUNT(*) AS cnt, MAX(received_at) AS latest_received_at
        FROM email_messages
        GROUP BY email_account_id, email_account_name
        ORDER BY cnt DESC"""
    ).fetchall()
    return {
        "total_cached": total,
        "stock_hint_count": stock_hints,
        "attachment_count": att_cnt,
        "last_sync_at": settings.get("email_last_sync_at") or "",
        "last_sync_summary": settings.get("email_last_sync_summary") or "",
        "auto_sync_enabled": settings.get("email_auto_sync_enabled", "0") == "1",
        "sync_interval_min": int(settings.get("email_sync_interval_min") or 30),
        "sync_days": int(settings.get("email_sync_days") or 30),
        "per_account": [
            {
                "account_id": r["email_account_id"],
                "account_name": r["email_account_name"] or "默认邮箱",
                "count": int(r["cnt"] or 0),
                "latest_received_at": r["latest_received_at"] or "",
            }
            for r in per_acct_rows
        ],
    }


async def sync_emails_to_local_cache(
    base_settings: Dict[str, str],
    accounts: List[Dict[str, Any]],
    days: int = 30,
    limit: int = 100,
    force_refresh: bool = False,
    zip_password: str = "",
    unread_only: bool = False,
    keyword: str = "",
    stock_only: bool = False,
) -> Dict[str, Any]:
    """Incrementally fetch unseen/new emails via IMAP and persist them locally in email_messages."""
    if not accounts:
        raise ValueError("尚未配置任何可用的邮箱账号，请先在「系统设置 → 邮箱 & Bark」添加至少一个邮箱。")

    all_warnings: List[str] = []
    scanned_accounts: List[str] = []
    newly_cached_uids: List[str] = []
    returned_uids: List[str] = []
    total_fetched_new = 0
    succeeded = 0
    last_exc: Optional[Exception] = None

    with get_conn() as conn:
        known_pairs_by_acct: Dict[Optional[int], Set[Tuple[str, str]]] = {}
        if not force_refresh:
            for r in conn.execute("SELECT email_account_id, folder, imap_uid FROM email_messages WHERE imap_uid != ''").fetchall():
                aid = r["email_account_id"]
                known_pairs_by_acct.setdefault(aid, set()).add((str(r["folder"] or "INBOX"), str(r["imap_uid"])))

    for acct in accounts:
        acct_label = (acct.get("name") or acct.get("username") or "邮箱").strip()
        raw_acct_id = acct.get("id")
        acct_id = int(raw_acct_id) if (raw_acct_id is not None and int(raw_acct_id) > 0) else None
        acct_settings = build_account_imap_settings(base_settings, acct)
        effective_pwd = zip_password or acct_settings.get("email_bill_zip_password") or ""
        skip_pairs = set() if force_refresh else known_pairs_by_acct.get(acct_id, set())

        try:
            streamed_uids: Set[str] = set()

            def _save_batch_cb(batch: List[Dict[str, Any]]) -> None:
                nonlocal total_fetched_new
                with get_conn() as bconn:
                    saved = save_raw_emails_to_cache(
                        bconn,
                        batch,
                        account_id=acct_id,
                        account_name=acct_label,
                        zip_password=effective_pwd,
                    )
                total_fetched_new += int(saved["saved_new"])
                newly_cached_uids.extend(saved["newly_cached_uids"])
                for bi in batch:
                    if bi.get("uid"):
                        streamed_uids.add(str(bi["uid"]))

            raw_items = await asyncio.to_thread(
                _call_fetch_raw_emails_compat,
                acct_settings,
                days,
                limit,
                unread_only,
                keyword,
                stock_only,
                skip_pairs,
                _save_batch_cb,
            )
            succeeded += 1
            scanned_accounts.append(acct_label)
            for item in raw_items:
                if item.get("uid"):
                    returned_uids.append(str(item["uid"]))
            remaining_items = [it for it in raw_items if str(it.get("uid") or "") not in streamed_uids]
            if remaining_items:
                with get_conn() as conn:
                    saved = save_raw_emails_to_cache(
                        conn,
                        remaining_items,
                        account_id=acct_id,
                        account_name=acct_label,
                        zip_password=effective_pwd,
                    )
                total_fetched_new += int(saved["saved_new"])
                newly_cached_uids.extend(saved["newly_cached_uids"])
        except Exception as exc:
            last_exc = exc
            err_msg = str(exc).strip() or type(exc).__name__
            all_warnings.append(f"邮箱「{acct_label}」增量同步失败: {err_msg}")

    if succeeded == 0 and last_exc is not None:
        if len(accounts) == 1:
            raise last_exc
        raise RuntimeError("；".join(all_warnings))

    with get_conn() as conn:
        total_cached = int((conn.execute("SELECT COUNT(*) AS c FROM email_messages").fetchone() or {"c": 0})["c"])
    sync_ts = now_iso()
    summary_text = f"同步 {len(scanned_accounts)} 个邮箱：新增缓存 {total_fetched_new} 封（本地共 {total_cached} 封）"
    update_settings({"email_last_sync_at": sync_ts, "email_last_sync_summary": summary_text})

    return {
        "fetched_new": total_fetched_new,
        "total_cached": total_cached,
        "newly_cached_uids": newly_cached_uids,
        "returned_uids": returned_uids,
        "scanned_accounts": scanned_accounts,
        "warnings": list(dict.fromkeys(all_warnings)),
        "synced_at": sync_ts,
        "summary": summary_text,
    }


def _load_cached_raw_items(
    conn: Any,
    days: int = 30,
    limit: int = 200,
    account_id: Optional[int] = None,
    account_ids: Optional[List[int]] = None,
    force_include_uids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where = ["1=1"]
    params: List[Any] = []
    if account_id is not None and account_id > 0:
        where.append("email_account_id = ?")
        params.append(account_id)
    elif account_ids is not None:
        valid_ids = [int(a) for a in account_ids if a and int(a) > 0]
        if valid_ids:
            ph_a = ",".join("?" for _ in valid_ids)
            where.append(f"email_account_id IN ({ph_a})")
            params.extend(valid_ids)
        else:
            where.append("email_account_id IS NULL")

    include_list = [u for u in (force_include_uids or set()) if u]
    if include_list:
        placeholders = ",".join("?" for _ in include_list)
        where.append(f"(received_at >= ? OR received_at = '' OR message_uid IN ({placeholders}))")
        params.append(cutoff)
        params.extend(include_list)
    else:
        where.append("(received_at >= ? OR received_at = '')")
        params.append(cutoff)

    params.append(max(limit, len(include_list), 20))
    rows = conn.execute(
        f"SELECT * FROM email_messages WHERE {' AND '.join(where)} ORDER BY received_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    if not rows:
        return []

    email_ids = [int(r["id"]) for r in rows]
    att_map: Dict[int, List[Tuple[str, bytes]]] = {}
    ph = ",".join("?" for _ in email_ids)
    for ar in conn.execute(
        f"SELECT email_id, filename, content_bytes FROM email_attachments WHERE email_id IN ({ph}) ORDER BY id ASC",
        email_ids,
    ).fetchall():
        att_map.setdefault(int(ar["email_id"]), []).append((str(ar["filename"]), bytes(ar["content_bytes"])))

    items: List[Dict[str, Any]] = []
    for r in rows:
        eid = int(r["id"])
        try:
            att_names = json.loads(r["attachments_json"] or "[]")
        except Exception:
            att_names = []
        try:
            extra_docs = [tuple(x) for x in json.loads(r["extra_docs_json"] or "[]") if isinstance(x, (list, tuple)) and len(x) >= 2]
        except Exception:
            extra_docs = []
        items.append(
            {
                "uid": str(r["message_uid"]),
                "imap_uid": str(r["imap_uid"] or ""),
                "folder": str(r["folder"] or "INBOX"),
                "email_account_id": r["email_account_id"],
                "email_account_name": str(r["email_account_name"] or ""),
                "subject": str(r["subject"] or "(无主题)"),
                "sender": str(r["sender"] or ""),
                "received_at": str(r["received_at"] or ""),
                "body": str(r["body_text"] or ""),
                "alt_body": str(r["alt_body_text"] or ""),
                "attachments": att_map.get(eid, []),
                "att_names": att_names,
                "preextracted_docs": extra_docs,
            }
        )
    return items


async def parse_cached_emails(
    conn: Any,
    base_settings: Dict[str, str],
    accounts: List[Dict[str, Any]],
    existing_refs: Set[str],
    synced_uids: Set[str],
    days: int = 30,
    limit: int = 200,
    keyword: str = "",
    zip_password: str = "",
    include_body_ai: bool = True,
    skip_already_synced: bool = False,
    existing_stock_refs: Optional[Set[str]] = None,
    account_id: Optional[int] = None,
    stock_only: bool = False,
    force_include_uids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    acct_ids = [int(a["id"]) for a in accounts if a.get("id") and int(a["id"]) > 0]
    raw_items = _load_cached_raw_items(
        conn,
        days=days,
        limit=limit,
        account_id=account_id,
        account_ids=acct_ids,
        force_include_uids=force_include_uids,
    )
    acct_settings_map: Dict[int, Dict[str, str]] = {}
    for a in accounts:
        if a.get("id") is not None:
            acct_settings_map[int(a["id"])] = build_account_imap_settings(base_settings, a)

    parsed = await _parse_raw_email_items(
        raw_emails=raw_items,
        settings=base_settings,
        existing_refs=existing_refs,
        synced_uids=synced_uids,
        keyword=keyword,
        zip_password=zip_password,
        include_body_ai=include_body_ai,
        skip_already_synced=skip_already_synced,
        existing_stock_refs=existing_stock_refs,
        stock_only=stock_only,
        account_settings_map=acct_settings_map,
    )
    parsed["detected_cards"] = summarize_detected_cards(parsed.get("transactions", []))
    parsed["scanned_accounts"] = [
        (a.get("name") or a.get("username") or "邮箱").strip()
        for a in accounts
        if account_id is None or a.get("id") == account_id
    ]
    parsed["from_cache"] = True
    return parsed


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
    existing_stock_refs: Optional[Set[str]] = None,
    stock_only: bool = False,
) -> Dict[str, Any]:
    if not accounts:
        raise ValueError("尚未配置任何可用的邮箱账号，请先在「系统设置 → 邮箱 & Bark」添加至少一个邮箱。")

    all_emails: List[Dict[str, Any]] = []
    all_txs: List[Dict[str, Any]] = []
    all_rems: List[Dict[str, Any]] = []
    all_stock_trades: List[Dict[str, Any]] = []
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
                existing_stock_refs=existing_stock_refs,
                stock_only=stock_only,
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
            for st in res.get("stock_trades", []):
                st["email_account_id"] = acct_id
                st["email_account_name"] = acct_label
                all_stock_trades.append(st)
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
        "stock_trades": all_stock_trades,
        "detected_cards": summarize_detected_cards(all_txs),
        "warnings": list(dict.fromkeys(all_warnings)),
        "scanned_accounts": scanned_accounts,
        "from_cache": False,
    }


async def smart_fetch_and_parse_emails(
    base_settings: Dict[str, str],
    accounts: List[Dict[str, Any]],
    existing_refs: Set[str],
    synced_uids: Set[str],
    days: int = 30,
    limit: int = 100,
    keyword: str = "",
    zip_password: str = "",
    include_body_ai: bool = True,
    unread_only: bool = False,
    skip_already_synced: bool = False,
    existing_stock_refs: Optional[Set[str]] = None,
    account_id: Optional[int] = None,
    stock_only: bool = False,
    source: str = "auto",
) -> Dict[str, Any]:
    """Unified email reader:
    - source='cache': parse 100% from local SQLite email_messages archive in milliseconds (0 IMAP calls).
    - source='imap': live IMAP fetch (and persist fetched messages into local cache too).
    - source='auto' (default): incremental IMAP sync of unseen UIDs only -> instant local cache parse.
    """
    cache_limit = max(limit, 1500 if days >= 365 else 200)
    if source == "cache":
        with get_conn() as conn:
            res = await parse_cached_emails(
                conn=conn,
                base_settings=base_settings,
                accounts=accounts,
                existing_refs=existing_refs,
                synced_uids=synced_uids,
                days=days,
                limit=cache_limit,
                keyword=keyword,
                zip_password=zip_password,
                include_body_ai=include_body_ai,
                skip_already_synced=skip_already_synced,
                existing_stock_refs=existing_stock_refs,
                account_id=account_id,
                stock_only=stock_only,
            )
            res["cache_stats"] = get_email_cache_stats(conn, get_all_settings_from_conn(conn, base_settings))
            return res

    if source == "imap":
        res = await fetch_and_parse_multi_emails(
            base_settings=base_settings,
            accounts=accounts,
            existing_refs=existing_refs,
            synced_uids=synced_uids,
            days=days,
            limit=limit,
            keyword=keyword,
            zip_password=zip_password,
            include_body_ai=include_body_ai,
            unread_only=unread_only,
            skip_already_synced=skip_already_synced,
            existing_stock_refs=existing_stock_refs,
            stock_only=stock_only,
        )
        return res

    # source == "auto": incremental sync of unseen UIDs into SQLite cache, then parse from cache
    sync_res = await sync_emails_to_local_cache(
        base_settings=base_settings,
        accounts=accounts,
        days=days,
        limit=max(limit, 100),
        force_refresh=False,
        zip_password=zip_password,
        unread_only=unread_only,
        keyword=keyword,
        stock_only=stock_only,
    )
    fresh_uids = set(sync_res.get("returned_uids") or []) | set(sync_res.get("newly_cached_uids") or [])
    with get_conn() as conn:
        res = await parse_cached_emails(
            conn=conn,
            base_settings=base_settings,
            accounts=accounts,
            existing_refs=existing_refs,
            synced_uids=synced_uids,
            days=days,
            limit=cache_limit,
            keyword=keyword,
            zip_password=zip_password,
            include_body_ai=include_body_ai,
            skip_already_synced=skip_already_synced,
            existing_stock_refs=existing_stock_refs,
            account_id=account_id,
            stock_only=stock_only,
            force_include_uids=fresh_uids,
        )
        res["warnings"] = list(dict.fromkeys((sync_res.get("warnings") or []) + (res.get("warnings") or [])))
        res["fetched_new"] = sync_res.get("fetched_new", 0)
        res["cache_stats"] = get_email_cache_stats(conn, get_all_settings_from_conn(conn, base_settings))
        return res


def get_all_settings_from_conn(conn: Any, fallback: Dict[str, str]) -> Dict[str, str]:
    merged = dict(fallback)
    for r in conn.execute("SELECT key, value FROM settings").fetchall():
        merged[r["key"]] = r["value"]
    return merged
