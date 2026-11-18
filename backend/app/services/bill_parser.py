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
    s = re.sub(r"[¥￥,\s元]", "", s)
    try:
        return round(abs(float(s)), 2)
    except ValueError:
        return 0.0


def _map_category(raw_cat: str, counterparty: str, desc: str, tx_type: str) -> str:
    combined = f"{raw_cat} {counterparty} {desc}"
    detected = _detect_tx_category(combined, tx_type)
    if detected != "其他" and detected != "其他收入":
        return detected
    if tx_type == "income":
        if "退款" in combined:
            return "报销回款"
        return "其他收入"
    if any(k in combined for k in ("餐饮", "美食", "食品", "美团", "饿了么", "瑞幸", "星巴克", "麦当劳", "肯德基")):
        return "餐饮"
    if any(k in combined for k in ("滴滴", "出行", "铁路", "12306", "高德", "哈啰", "公交", "地铁", "加油")):
        return "交通"
    if any(k in combined for k in ("淘宝", "天猫", "京东", "拼多多", "超市", "便利店", "百货", "服饰")):
        return "购物"
    if any(k in combined for k in ("房租", "物业", "水电", "燃气", "国家电网", "自来水")):
        return "住房"
    if any(k in combined for k in ("移动", "联通", "电信", "充值", "话费", "宽带")):
        return "通讯"
    if any(k in combined for k in ("苹果", "App Store", "会员", "百度网盘", "腾讯视频", "爱奇艺", "网易云", "iCloud")):
        return "订阅服务"
    if any(k in combined for k in ("医院", "药房", "药店", "诊所", "体检")):
        return "医疗"
    return "其他"


def _make_fingerprint(channel: str, order_id: str, occurred_at: str, amount: float, desc: str) -> str:
    clean_oid = re.sub(r"\s+", "", order_id or "")
    if clean_oid and clean_oid != "/":
        return f"{channel}:{clean_oid}"
    raw = f"{channel}|{occurred_at}|{amount:.2f}|{desc}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{channel}:{digest}"


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
            if "交易分类" in joined or "交易订单号" in joined or "商品说明" in joined:
                platform = "alipay"
            elif "交易单号" in joined or "金额(元)" in joined or "支付方式" in joined:
                platform = "wechat"
            else:
                platform = "alipay" if "alipay" in filename.lower() or "支付宝" in filename else "wechat"
            break

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
            # Skip '不计收支' or '/' (internal transfers / Yu'ebao / Lingqiantong)
            continue

        status = get_col(row, "交易状态", "当前状态")
        if any(bad in status for bad in ("交易关闭", "已关闭", "失败", "未支付")):
            continue

        amount = _clean_amount(get_col(row, "金额", "金额(元)"))
        if amount <= 0:
            continue

        tx_type = "income" if io_flag == "收入" else "expense"
        raw_cat = get_col(row, "交易分类", "交易类型")
        counterparty = get_col(row, "交易对方")
        goods = get_col(row, "商品说明", "商品")
        order_id = get_col(row, "交易订单号", "交易单号")

        desc_parts = [p for p in (counterparty, goods) if p and p != "/"]
        description = " - ".join(desc_parts) if desc_parts else raw_cat
        category = _map_category(raw_cat, counterparty, goods, tx_type)
        occurred_fmt = occurred_at.replace("/", "-")[:16]
        external_ref = _make_fingerprint(platform, order_id, occurred_fmt, amount, description)

        results.append(
            {
                "type": tx_type,
                "amount": amount,
                "category": category,
                "description": description[:200],
                "occurred_at": occurred_fmt,
                "source_type": platform,
                "external_ref": external_ref,
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
            except Exception as exc:
                last_err = str(exc)
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
        "errors": errors,
    }
