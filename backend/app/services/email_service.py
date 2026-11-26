import asyncio
import email
import hashlib
import html
from html.parser import HTMLParser
import imaplib
import io
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Set, Tuple

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


def _discover_imap_folders(client: imaplib.IMAP4) -> List[str]:
    """Return INBOX plus Gmail All Mail / Archive / custom folders (excluding Trash/Drafts/Sent/Junk)."""
    folders: List[str] = ["INBOX"]
    seen: Set[str] = {"inbox"}
    try:
        typ, lines = client.list()
        if typ != "OK" or not lines:
            return folders
        all_mail_folder: Optional[str] = None
        extra_folders: List[str] = []
        for raw_ln in lines:
            if not raw_ln:
                continue
            line = raw_ln.decode("utf-8", errors="ignore") if isinstance(raw_ln, (bytes, bytearray)) else str(raw_ln)
            # Standard IMAP LIST response: (\Flags) "delim" "Folder Name"
            m = re.match(r'^\(([^)]*)\)\s+"?[^"\s]*"?\s+(.+)$', line.strip())
            if not m:
                continue
            flags_low = m.group(1).lower()
            name = m.group(2).strip()
            if name.startswith('"') and name.endswith('"') and len(name) >= 2:
                name = name[1:-1].replace('\\"', '"')
            name_low = name.lower()
            if "\\noselect" in flags_low or name_low in seen:
                continue
            if any(skip in flags_low for skip in ("\\trash", "\\drafts", "\\sent", "\\junk")):
                continue
            if any(skip in name_low for skip in ("trash", "deleted", "draft", "sent", "spam", "junk", "已发送", "草稿", "已删除", "垃圾")):
                continue
            if "\\all" in flags_low or "all mail" in name_low:
                all_mail_folder = name
            else:
                extra_folders.append(name)
        if all_mail_folder:
            # All Mail on Gmail includes Inbox + Archive + labeled folders
            return [all_mail_folder, "INBOX"]
        for ef in extra_folders[:4]:
            if ef.lower() not in seen:
                seen.add(ef.lower())
                folders.append(ef)
    except Exception:
        pass
    return folders


def _fetch_raw_emails_sync(
    settings: Dict[str, str],
    days: int = 14,
    limit: int = 25,
    unread_only: bool = False,
    keyword: str = "",
) -> List[Dict[str, Any]]:
    client = _connect_imap(settings)
    fetched: List[Dict[str, Any]] = []
    seen_msg_ids: Set[str] = set()
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
            reversed_uids = list(reversed(uids))
            remaining = max(1, limit - len(fetched))
            if kw_lower and len(reversed_uids) > remaining:
                pool_uids = reversed_uids[: max(remaining * 4, 240)]
                matched_uids: List[bytes] = []
                try:
                    uid_list = b",".join(pool_uids)
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
                            if _keyword_matches(kw_lower, f"{subj_s} {from_s}"):
                                matched_uids.append(m_u.group(1).encode("ascii"))
                except Exception:
                    matched_uids = []
                selected_uids = matched_uids[:remaining] if matched_uids else reversed_uids[:remaining]
            else:
                selected_uids = reversed_uids[:remaining]

            for uid_bytes in selected_uids:
                if len(fetched) >= limit:
                    break
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
                message_id = (msg.get("Message-ID") or "").strip() or f"uid:{folder}:{uid_str}"
                if message_id in seen_msg_ids:
                    continue
                seen_msg_ids.add(message_id)
                body, attachments, alt_body = _extract_body_variants_and_attachments(msg)

                fetched.append(
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
    effective_zip_pwd = zip_password or settings.get("email_bill_zip_password") or ""
    import inspect
    fetch_kwargs: Dict[str, Any] = {
        "days": days,
        "limit": limit,
        "unread_only": unread_only,
    }
    if keyword and "keyword" in inspect.signature(_fetch_raw_emails_sync).parameters:
        fetch_kwargs["keyword"] = keyword
    raw_emails = await asyncio.to_thread(
        _fetch_raw_emails_sync,
        settings,
        **fetch_kwargs,
    )

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

        subject = item["subject"]
        sender = item["sender"]
        body = item["body"]
        alt_body = item.get("alt_body") or ""
        attachments: List[Tuple[str, bytes]] = item["attachments"]
        att_names = [a[0] for a in attachments]

        email_tx_count = 0
        email_rem_count = 0
        email_stock_count = 0
        extra_docs: List[Tuple[str, str]] = []

        # 1. Parse bill/advice attachments (.zip / .csv / .xlsx / .pdf / .html)
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
            if not stock_only:
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

        doc_blocks = [f"[{dlabel}]\n{dtext}" for dlabel, dtext in extra_docs]
        combined_body = (body + ("\n\n" + "\n\n".join(doc_blocks) if doc_blocks else "")).strip()

        if kw_lower:
            haystack = f"{subject} {sender} {' '.join(att_names)} {combined_body[:4000]}".lower()
            if not _keyword_matches(kw_lower, haystack):
                continue

        hint_haystack = f"{subject} {sender} {' '.join(att_names)} {combined_body[:1200]}".lower()
        is_stock_hint = any(
            h in hint_haystack
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

        # Helper to append deduplicated stock trades for this email
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
                all_stock_trades.append(st)
                email_stock_count += 1

        # 2. Extract bills/reminders/stock trades
        if stock_only:
            # Fast deterministic extraction across primary body, alternate body, AND each PDF/HTML attachment independently
            det_trades = _extract_stock_trades_from_email(subject, sender, item["received_at"], body)
            if not det_trades and alt_body:
                det_trades = _extract_stock_trades_from_email(subject, sender, item["received_at"], alt_body)
            _append_stock_candidates(det_trades)
            for dlabel, dtext in extra_docs:
                _append_stock_candidates(_extract_stock_trades_from_email(f"{subject} {dlabel}", sender, item["received_at"], dtext))

            # Only invoke LLM when email looks broker/stock-related or carries PDF/HTML docs AND deterministic found 0 trades
            if include_body_ai and email_stock_count == 0 and (is_stock_hint or bool(extra_docs)):
                ai_target = extra_docs[0][1] if (extra_docs and len(body) > 2500) else combined_body
                if ai_target.strip():
                    body_res = await extract_from_email_content(
                        subject=subject,
                        sender=sender,
                        received_at=item["received_at"],
                        body_text=ai_target,
                        settings=settings,
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

                _append_stock_candidates(body_res.get("stock_trades", []))
                if email_stock_count == 0 and alt_body:
                    _append_stock_candidates(_extract_stock_trades_from_email(subject, sender, item["received_at"], alt_body))
                for dlabel, dtext in extra_docs:
                    _append_stock_candidates(_extract_stock_trades_from_email(f"{subject} {dlabel}", sender, item["received_at"], dtext))

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
                "parsed_stock_count": email_stock_count,
                "stock_hint": is_stock_hint,
                "body_preview": combined_body[:1500],
            }
        )

    return {
        "emails": email_summaries,
        "transactions": all_txs,
        "reminders": all_reminders,
        "stock_trades": all_stock_trades,
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
    }
