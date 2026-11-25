import csv
import hashlib
import io
import os
import re
import zipfile
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl
import pyzipper

from .ai_service import _detect_tx_category
from .reconciliation_service import parse_payment_method_info


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _clean_amount(raw: Any) -> float:
    if raw is None:
        return 0.0
    s = str(raw).strip()
    s = re.sub(r"[¥￥,\s元RMBUSDHKD]", "", s, flags=re.IGNORECASE)
    try:
        return round(abs(float(s)), 2)
    except ValueError:
        return 0.0


def _clean_signed_amount(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    s = re.sub(r"[¥￥,\s元RMB]", "", s, flags=re.IGNORECASE)
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _map_category(raw_cat: str, counterparty: str, desc: str, tx_type: str) -> str:
    combined = f"{raw_cat} {counterparty} {desc}"
    detected = _detect_tx_category(combined, tx_type)
    if detected != "其他" and detected != "其他收入":
        return detected
    if tx_type == "income":
        if "退款" in combined:
            return "报销回款"
        return "其他收入"
    if any(k in combined for k in ("餐饮", "美食", "食品", "美团", "饿了么", "瑞幸", "星巴克", "麦当劳", "肯德基", "茶百道", "霸王茶姬")):
        return "餐饮"
    if any(k in combined for k in ("滴滴", "出行", "铁路", "12306", "高德", "哈啰", "公交", "地铁", "加油", "爱车", "停车", "高速")):
        return "交通"
    if any(k in combined for k in ("淘宝", "天猫", "京东", "拼多多", "超市", "便利店", "百货", "服饰", "家居", "数码", "日用")):
        return "购物"
    if any(k in combined for k in ("房租", "物业", "水电", "燃气", "国家电网", "自来水")):
        return "住房"
    if any(k in combined for k in ("移动", "联通", "电信", "充值", "话费", "宽带")):
        return "通讯"
    if any(k in combined for k in ("苹果", "App Store", "会员", "百度网盘", "腾讯视频", "爱奇艺", "网易云", "iCloud", "微信读书", "OpenAI")):
        return "订阅服务"
    if any(k in combined for k in ("医院", "药房", "药店", "诊所", "体检", "医疗")):
        return "医疗"
    if any(k in combined for k in ("酒店", "旅游", "电影", "门票", "游戏", "娱乐")):
        return "娱乐"
    return "其他"


def _make_fingerprint(channel: str, order_id: str, occurred_at: str, amount: float, desc: str) -> str:
    clean_oid = re.sub(r"\s+", "", order_id or "")
    if clean_oid and clean_oid != "/":
        return f"{channel}:{clean_oid}"
    raw = f"{channel}|{occurred_at}|{amount:.2f}|{desc}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{channel}:{digest}"


def _parse_bank_statement_rows(
    rows: List[List[str]],
    header_idx: int,
    filename: str = "",
) -> Tuple[List[Dict[str, Any]], str]:
    headers = [str(c or "").strip() for c in rows[header_idx]]
    col_map = {h: i for i, h in enumerate(headers)}

    preamble = "\n".join(" ".join(str(c or "") for c in r) for r in rows[:header_idx])
    meta_probe = f"{filename} {preamble}"
    meta_pm = parse_payment_method_info(payment_method=meta_probe, source_type="bank_card")

    def get_col(row: List[str], *names: str) -> str:
        for name in names:
            if name in col_map and col_map[name] < len(row):
                return str(row[col_map[name]] or "").strip()
        for name in names:
            for h, idx in col_map.items():
                if name in h and idx < len(row):
                    return str(row[idx] or "").strip()
        return ""

    results: List[Dict[str, Any]] = []
    for row in rows[header_idx + 1 :]:
        if not row or all(not str(c or "").strip() for c in row):
            continue
        date_str = get_col(row, "交易日期", "记账日期", "交易时间", "消费日期")
        if not re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", date_str):
            continue
        desc = get_col(row, "交易描述", "商户名称", "交易摘要", "摘要", "消费商户", "交易说明")
        if any(skip in desc for skip in ("自动还款", "网银还款", "购汇还款", "跨行还款", "转账还款", "约定还款", "信用卡还款")):
            continue
        amt_raw = get_col(row, "人民币金额", "记账金额", "交易金额", "入账金额", "金额")
        signed = _clean_signed_amount(amt_raw)
        if signed is None or abs(signed) <= 0:
            continue
        # On most credit card CSVs, positive = spend (expense), negative = refund/payment (income)
        tx_type = "income" if signed < 0 else "expense"
        amount = round(abs(signed), 2)

        card_col = get_col(row, "卡号末四位", "卡号尾号", "卡末四位", "卡号", "卡片尾号")
        m_l4 = re.search(r"(\d{4})\s*$", card_col.replace("*", "").strip()) if card_col else None
        last4 = m_l4.group(1) if m_l4 else meta_pm["card_last4"]
        bank_name = meta_pm["bank_name"]
        pm_raw = f"{bank_name}信用卡({last4})" if (bank_name and last4) else (f"信用卡({last4})" if last4 else (meta_pm["payment_method"] or "银行卡账单"))
        pm_info = parse_payment_method_info(
            payment_method=pm_raw,
            source_type="bank_card",
            description=desc,
            merchant=desc,
            payment_channel="card_direct",
        )

        occ = date_str.replace("/", "-")[:16]
        if len(occ) == 10:
            occ = f"{occ} 12:00"
        external_ref = _make_fingerprint("bank_card", "", occ, amount, f"{last4}|{desc}")
        results.append(
            {
                "type": tx_type,
                "amount": amount,
                "category": _map_category("", desc, desc, tx_type),
                "description": (desc or "银行卡刷卡消费")[:200],
                "merchant": desc[:100],
                "occurred_at": occ,
                "source_type": "bank_card",
                "external_ref": external_ref,
                "payment_channel": "card_direct",
                "payment_method": pm_info["payment_method"],
                "bank_name": pm_info["bank_name"],
                "card_last4": pm_info["card_last4"],
                "card_kind": pm_info["card_kind"],
                "account_display_name": pm_info["account_display_name"],
                "original_currency": "CNY",
                "original_amount": amount,
                "raw_status": "已入账",
                "counterparty": desc,
            }
        )
    return results, "bank_card"


def _parse_rows_common(
    rows: List[List[str]],
    filename: str = "",
) -> Tuple[List[Dict[str, Any]], str]:
    header_idx = -1
    platform = "unknown"

    for idx, row in enumerate(rows[:60]):
        joined = ",".join(str(c or "").strip() for c in row)
        if "交易时间" in joined and ("收/支" in joined or "收支" in joined):
            header_idx = idx
            if "交易分类" in joined or "交易订单号" in joined or "商品说明" in joined or "收/付款方式" in joined:
                platform = "alipay"
            elif "交易单号" in joined or "金额(元)" in joined or "支付方式" in joined:
                platform = "wechat"
            else:
                platform = "alipay" if "alipay" in filename.lower() or "支付宝" in filename else "wechat"
            break
        if any(k in joined for k in ("交易日期", "记账日期")) and any(k in joined for k in ("交易描述", "商户名称", "交易摘要", "摘要")) and any(k in joined for k in ("交易金额", "人民币金额", "记账金额", "入账金额")):
            return _parse_bank_statement_rows(rows, idx, filename=filename)

    if header_idx == -1:
        return [], "unknown"

    headers = [str(c or "").strip() for c in rows[header_idx]]
    col_map = {h: i for i, h in enumerate(headers)}

    def get_col(row: List[str], *names: str) -> str:
        for name in names:
            if name in col_map and col_map[name] < len(row):
                return str(row[col_map[name]] or "").strip()
        return ""

    results: List[Dict[str, Any]] = []
    for row in rows[header_idx + 1 :]:
        if not row or all(not str(c or "").strip() for c in row):
            continue

        occurred_at = get_col(row, "交易时间")
        if not re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", occurred_at):
            continue

        io_flag = get_col(row, "收/支", "收支")
        if io_flag not in ("收入", "支出"):
            # Skip '不计收支' or '/' (internal transfers, credit card repayments, Yu'ebao / Lingqiantong moves)
            continue

        status = get_col(row, "交易状态", "当前状态")
        if any(bad in status for bad in ("交易关闭", "已关闭", "失败", "未支付", "已全额退款", "对方已退还")):
            continue

        raw_cat = get_col(row, "交易分类", "交易类型")
        if "信用卡还款" in raw_cat:
            continue

        amount = _clean_amount(get_col(row, "金额", "金额(元)"))
        if amount <= 0:
            continue

        tx_type = "income" if io_flag == "收入" else "expense"
        counterparty = get_col(row, "交易对方")
        goods = get_col(row, "商品说明", "商品")
        order_id = get_col(row, "交易订单号", "交易单号")
        raw_payment_method = get_col(row, "收/付款方式", "支付方式", "付款方式")

        desc_parts = [p for p in (counterparty, goods) if p and p != "/"]
        description = " - ".join(desc_parts) if desc_parts else raw_cat
        category = _map_category(raw_cat, counterparty, goods, tx_type)
        occurred_fmt = occurred_at.replace("/", "-")[:16]
        external_ref = _make_fingerprint(platform, order_id, occurred_fmt, amount, description)

        pm_info = parse_payment_method_info(
            payment_method=raw_payment_method,
            source_type=platform,
            description=description,
            merchant=counterparty,
            payment_channel=platform,
        )

        results.append(
            {
                "type": tx_type,
                "amount": amount,
                "category": category,
                "description": description[:200],
                "merchant": counterparty[:100],
                "occurred_at": occurred_fmt,
                "source_type": platform,
                "external_ref": external_ref,
                "payment_channel": pm_info["payment_channel"],
                "payment_method": pm_info["payment_method"],
                "bank_name": pm_info["bank_name"],
                "card_last4": pm_info["card_last4"],
                "card_kind": pm_info["card_kind"],
                "account_display_name": pm_info["account_display_name"],
                "original_currency": "CNY",
                "original_amount": amount,
                "raw_status": status,
                "counterparty": counterparty,
            }
        )

    return results, platform


def parse_csv_bytes(raw: bytes, filename: str = "") -> Tuple[List[Dict[str, Any]], str]:
    text = _decode_bytes(raw)
    reader = csv.reader(io.StringIO(text))
    rows = [[cell.strip() for cell in r] for r in reader]
    return _parse_rows_common(rows, filename)


def parse_xlsx_bytes(raw: bytes, filename: str = "") -> Tuple[List[Dict[str, Any]], str]:
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = [[str(c).strip() if c is not None else "" for c in r] for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return _parse_rows_common(rows, filename)


def summarize_detected_cards(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for it in items:
        disp = (it.get("account_display_name") or it.get("payment_method") or "").strip()
        last4 = (it.get("card_last4") or "").strip()
        bank = (it.get("bank_name") or "").strip()
        kind = (it.get("card_kind") or "unknown").strip()
        if not disp and not last4:
            continue
        key = f"{kind}:{last4}" if last4 else f"{kind}:{disp}"
        if key not in summary:
            summary[key] = {
                "account_display_name": disp or f"{bank} ({last4})",
                "bank_name": bank,
                "card_last4": last4,
                "card_kind": kind,
                "count": 0,
                "expense": 0.0,
                "income": 0.0,
            }
        entry = summary[key]
        # Prefer richer name if earlier row was partial like 储蓄卡(1797) and later row is 工商银行储蓄卡 (1797)
        if bank and not entry["bank_name"]:
            entry["bank_name"] = bank
            if disp:
                entry["account_display_name"] = disp
        entry["count"] += 1
        if it.get("type") == "expense":
            entry["expense"] = round(entry["expense"] + float(it.get("amount") or 0), 2)
        elif it.get("type") == "income":
            entry["income"] = round(entry["income"] + float(it.get("amount") or 0), 2)
    return sorted(summary.values(), key=lambda x: (-x["count"], -x["expense"]))


def parse_bill_file(
    raw: bytes,
    filename: str,
    zip_password: str = "",
    existing_refs: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    lower = filename.lower()
    existing = existing_refs or set()
    parsed_items: List[Dict[str, Any]] = []
    platforms: Set[str] = set()
    errors: List[str] = []

    def _consume_single(file_bytes: bytes, inner_name: str) -> None:
        inner_lower = inner_name.lower()
        if inner_lower.endswith(".csv"):
            items, plat = parse_csv_bytes(file_bytes, inner_name)
        elif inner_lower.endswith(".xlsx"):
            items, plat = parse_xlsx_bytes(file_bytes, inner_name)
        else:
            return
        if plat != "unknown":
            platforms.add(plat)
        for it in items:
            it["already_imported"] = it["external_ref"] in existing
            parsed_items.append(it)

    if lower.endswith(".zip"):
        pwd_bytes = zip_password.encode("utf-8") if zip_password else None
        extracted_ok = False
        for zip_cls in (pyzipper.AESZipFile, zipfile.ZipFile):
            try:
                with zip_cls(io.BytesIO(raw)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        bname = os.path.basename(info.filename)
                        if not bname.lower().endswith((".csv", ".xlsx")):
                            continue
                        content = zf.read(info, pwd=pwd_bytes)
                        _consume_single(content, bname)
                    extracted_ok = True
                    break
            except RuntimeError as exc:
                msg = str(exc)
                if "password" in msg.lower() or "encrypted" in msg.lower():
                    errors.append(f"压缩包 {filename} 需要解压密码或密码错误（可在导入/设置中填写解压密码）。")
                    extracted_ok = True
                    break
            except Exception:
                continue
        if not extracted_ok:
            errors.append(f"无法读取压缩包 {filename}: 可能是加密方式不支持或文件损坏。")
    elif lower.endswith(".csv"):
        _consume_single(raw, filename)
    elif lower.endswith(".xlsx"):
        _consume_single(raw, filename)
    else:
        errors.append(f"不支持的账单文件格式：{filename}（支持 .csv / .xlsx / .zip）")

    return {
        "transactions": parsed_items,
        "platforms": sorted(platforms),
        "detected_cards": summarize_detected_cards(parsed_items),
        "errors": errors,
    }
