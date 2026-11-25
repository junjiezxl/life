import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..db import now_iso


_PROMO_KEYWORDS = (
    "红包",
    "立减",
    "优惠",
    "满减",
    "天天减",
    "食神卡",
    "抽奖",
    "碰一下",
    "碰友日",
    "随机减",
    "下单立减",
    "还款立减",
    "信用购立减",
    "代金券",
    "抵扣",
    "积分",
)

_BANK_ALIASES: Dict[str, str] = {
    "招行": "招商银行",
    "招商": "招商银行",
    "工行": "工商银行",
    "工商": "工商银行",
    "建行": "建设银行",
    "建设": "建设银行",
    "农行": "农业银行",
    "农业": "农业银行",
    "中行": "中国银行",
    "交行": "交通银行",
    "交通": "交通银行",
    "广发": "广发银行",
    "光大": "光大银行",
    "中信": "中信银行",
    "民生": "民生银行",
    "兴业": "兴业银行",
    "浦发": "浦发银行",
    "平安": "平安银行",
    "华夏": "华夏银行",
    "邮储": "邮储银行",
    "邮政": "邮储银行",
    "北京": "北京银行",
    "上海": "上海银行",
    "宁波": "宁波银行",
    "南京": "南京银行",
    "杭州": "杭州银行",
    "众安": "众安银行",
    "za": "众安银行",
    "za bank": "众安银行",
    "汇丰": "汇丰银行",
    "hsbc": "汇丰银行",
    "中银香港": "中银香港",
    "bochk": "中银香港",
    "渣打": "渣打银行",
    "花旗": "花旗银行",
    "华美": "华美银行",
}

_KNOWN_BANK_NAMES = (
    "招商银行",
    "工商银行",
    "建设银行",
    "农业银行",
    "中国银行",
    "交通银行",
    "广发银行",
    "光大银行",
    "中信银行",
    "民生银行",
    "兴业银行",
    "浦发银行",
    "平安银行",
    "华夏银行",
    "邮储银行",
    "北京银行",
    "上海银行",
    "宁波银行",
    "南京银行",
    "杭州银行",
    "众安银行",
    "汇丰银行",
    "中银香港",
    "渣打银行",
    "花旗银行",
    "华美银行",
)


def _normalize_bank_name(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in _BANK_ALIASES:
        return _BANK_ALIASES[low]
    for full in _KNOWN_BANK_NAMES:
        if full in s:
            return full
    for alias, full in _BANK_ALIASES.items():
        if alias in low:
            return full
    return s


def _is_promo_part(part: str) -> bool:
    p = part.strip()
    if not p:
        return True
    if any(k in p for k in ("信用卡", "储蓄卡", "借记卡", "贷记卡", "花呗", "白条", "余额", "零钱", "现金", "Card", "card")):
        return False
    return any(k in p for k in _PROMO_KEYWORDS)


def _pick_primary_payment_segment(raw_method: str) -> str:
    raw = (raw_method or "").strip()
    if not raw or raw == "/":
        return ""
    parts = [p.strip() for p in re.split(r"[&+＋,，/]+", raw) if p.strip()]
    if not parts:
        return raw

    non_promo = [p for p in parts if not _is_promo_part(p)]
    candidates = non_promo if non_promo else parts

    # Priority: Bank card with 4-digit suffix > Huabei/Baitiao > Yu'ebao/Lingqiantong > Balance/Lingqian > first
    for p in candidates:
        if re.search(r"[（(]\d{4}[）)]|尾号\s*\d{4}", p):
            return p
    for p in candidates:
        if any(k in p for k in ("信用卡", "储蓄卡", "借记卡", "贷记卡", "Card", "card")):
            return p
    for p in candidates:
        if any(k in p for k in ("花呗", "白条")):
            return p
    for p in candidates:
        if any(k in p for k in ("余额宝", "零钱通", "账户余额", "零钱", "余额", "现金")):
            return p
    return candidates[0]


def parse_payment_method_info(
    payment_method: str = "",
    source_type: str = "",
    description: str = "",
    merchant: str = "",
    payment_channel: str = "",
) -> Dict[str, Any]:
    """Parse raw payment method / description into structured bank, last4, card kind, channel, and display name."""
    raw_pm = (payment_method or "").strip()
    if raw_pm == "/":
        raw_pm = ""
    src = (source_type or "").strip().lower()
    explicit_ch = (payment_channel or "").strip().lower()
    primary = _pick_primary_payment_segment(raw_pm)
    probe = primary or raw_pm or f"{description} {merchant}".strip()

    bank_name = ""
    card_last4 = ""
    card_kind = "unknown"  # credit_card | debit_card | wallet | cash | unknown
    account_display_name = ""

    # 1. Chinese bank card format: e.g. 广发银行信用卡(3955), 光大银行信用卡分期(9870) 3期, 工商银行储蓄卡(1797), 储蓄卡(1797), 招行信用卡尾号9613
    m_cn = re.search(
        r"([\u4e00-\u9fa5]{2,12}?(?:银行)?)?\s*(信用卡|贷记卡|储蓄卡|借记卡)(?:分期)?\s*(?:[（(]\s*(?:尾号)?\s*(\d{4})\s*[）)]|尾号\s*[是为:：]?\s*(\d{4}))",
        probe,
    )
    if m_cn:
        raw_bank = (m_cn.group(1) or "").strip()
        raw_type = m_cn.group(2)
        card_last4 = m_cn.group(3) or m_cn.group(4) or ""
        bank_name = _normalize_bank_name(raw_bank)
        if not bank_name or bank_name.startswith("尾号"):
            bank_name = _normalize_bank_name(probe)
            if bank_name == probe:
                bank_name = ""
        card_kind = "credit_card" if raw_type in ("信用卡", "贷记卡") else "debit_card"
        type_label = "信用卡" if card_kind == "credit_card" else "储蓄卡"
        account_display_name = f"{bank_name}{type_label} ({card_last4})" if bank_name else f"{type_label} ({card_last4})"

    # 2. English / HK card format: e.g. ZA Card (0720), HSBC Pulse Card (1234), Visa Card (4567)
    if not card_last4:
        m_en = re.search(
            r"([A-Za-z0-9\u4e00-\u9fa5]{2,20}(?:\s+[A-Za-z0-9]{1,12})?\s*Card)\s*[（(]\s*(\d{4})\s*[）)]",
            probe,
            re.IGNORECASE,
        )
        if m_en:
            card_title = re.sub(r"\s+", " ", m_en.group(1).strip())
            card_last4 = m_en.group(2)
            bank_name = _normalize_bank_name(card_title.split()[0])
            if not bank_name or bank_name.lower() == card_title.split()[0].lower():
                bank_name = _normalize_bank_name(probe) or card_title
            is_credit = any(k in probe.lower() for k in ("credit", "信用卡", "贷记", "pulse", "red card"))
            card_kind = "credit_card" if is_credit else "debit_card"
            if bank_name and bank_name not in card_title:
                account_display_name = f"{bank_name} {card_title} ({card_last4})"
            else:
                account_display_name = f"{card_title} ({card_last4})"

    # 3. Generic bank email format: e.g. 招商银行尾号9613 / 尾号3955信用卡 / 卡号末四位 9870
    if not card_last4:
        m_tail = re.search(r"(?:尾号|末四位|卡号[*xX\s]*)[是为:：\s]*(\d{4})", probe)
        if m_tail:
            card_last4 = m_tail.group(1)
            bank_name = _normalize_bank_name(probe)
            if any(k in probe for k in ("信用卡", "贷记卡", "账单", "Credit", "credit")):
                card_kind = "credit_card"
                account_display_name = f"{bank_name}信用卡 ({card_last4})" if bank_name else f"信用卡 ({card_last4})"
            elif any(k in probe for k in ("储蓄卡", "借记卡", "Debit", "debit")):
                card_kind = "debit_card"
                account_display_name = f"{bank_name}储蓄卡 ({card_last4})" if bank_name else f"储蓄卡 ({card_last4})"
            else:
                card_kind = "credit_card" if src in ("email", "bank_card") else "debit_card"
                type_label = "信用卡" if card_kind == "credit_card" else "银行卡"
                account_display_name = f"{bank_name}{type_label} ({card_last4})" if bank_name else f"银行卡 ({card_last4})"

    # 4. Huabei / Baitiao (virtual credit lines without 4-digit suffix)
    if card_kind == "unknown" and ("花呗" in primary or "花呗" in raw_pm):
        bank_name = "蚂蚁花呗"
        card_kind = "credit_card"
        account_display_name = "支付宝花呗"
    elif card_kind == "unknown" and ("白条" in primary or "白条" in raw_pm):
        bank_name = "京东金融"
        card_kind = "credit_card"
        account_display_name = "京东白条"

    # 5. Wallets & Cash
    if card_kind == "unknown":
        if "余额宝" in primary or "余额宝" in raw_pm:
            bank_name = "支付宝"
            card_kind = "wallet"
            account_display_name = "支付宝余额宝"
        elif "零钱通" in primary or "零钱通" in raw_pm:
            bank_name = "微信支付"
            card_kind = "wallet"
            account_display_name = "微信零钱通"
        elif "零钱" in primary or "零钱" in raw_pm:
            bank_name = "微信支付"
            card_kind = "wallet"
            account_display_name = "微信零钱"
        elif ("账户余额" in primary or primary == "余额") or (src == "alipay" and "余额" in raw_pm):
            bank_name = "支付宝"
            card_kind = "wallet"
            account_display_name = "支付宝余额"
        elif explicit_ch == "cash" or src == "cash" or "现金" in primary or "现金" in raw_pm:
            bank_name = "现金"
            card_kind = "cash"
            account_display_name = "现金钱包"
        elif src == "alipay" and not primary:
            bank_name = "支付宝"
            card_kind = "wallet"
            account_display_name = "支付宝余额"
        elif src == "wechat" and not primary:
            bank_name = "微信支付"
            card_kind = "wallet"
            account_display_name = "微信零钱"

    # 6. Determine standardized payment_channel
    if explicit_ch in ("alipay", "wechat", "card_direct", "cash", "bank", "email", "manual"):
        channel = explicit_ch
        if channel == "email" and (card_last4 or card_kind in ("credit_card", "debit_card")):
            channel = "card_direct"
    elif src == "alipay":
        channel = "alipay"
    elif src == "wechat":
        channel = "wechat"
    elif src == "cash" or card_kind == "cash":
        channel = "cash"
    elif src in ("card_direct", "bank_card"):
        channel = "card_direct"
    elif src == "email":
        channel = "card_direct" if (card_last4 or card_kind in ("credit_card", "debit_card")) else "email"
    elif card_last4 or card_kind in ("credit_card", "debit_card"):
        channel = "card_direct"
    else:
        channel = "manual"

    return {
        "payment_channel": channel,
        "payment_method": raw_pm or account_display_name,
        "bank_name": bank_name,
        "card_last4": card_last4,
        "card_kind": card_kind,
        "account_display_name": account_display_name,
    }


def _find_matching_credit_card(
    conn: Any,
    last4: str,
    bank_name: str,
    display_name: str,
) -> Optional[Dict[str, Any]]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM credit_cards ORDER BY id ASC").fetchall()]
    if last4:
        exact_last4 = [r for r in rows if (r.get("last4") or "").strip() == last4]
        if exact_last4:
            if bank_name:
                for r in exact_last4:
                    if bank_name in (r.get("bank_name") or "") or bank_name in (r.get("name") or ""):
                        return r
            return exact_last4[0]
        # Fallback: last4 embedded in card name
        for r in rows:
            if last4 in (r.get("name") or ""):
                return r
    if display_name:
        for r in rows:
            if (r.get("name") or "").strip() == display_name:
                return r
        if "花呗" in display_name:
            for r in rows:
                if "花呗" in (r.get("name") or "") or "花呗" in (r.get("bank_name") or ""):
                    return r
        if "白条" in display_name:
            for r in rows:
                if "白条" in (r.get("name") or ""):
                    return r
    return None


def _find_matching_account(
    conn: Any,
    last4: str,
    bank_name: str,
    card_kind: str,
    display_name: str,
) -> Optional[Dict[str, Any]]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()]
    if last4:
        # 1. Exact card_last4 column match
        by_last4 = [r for r in rows if (r.get("card_last4") or "").strip() == last4]
        if by_last4:
            if card_kind == "credit_card":
                for r in by_last4:
                    if r.get("type") == "credit_card":
                        return r
            if bank_name:
                for r in by_last4:
                    if bank_name in (r.get("bank_name") or "") or bank_name in (r.get("name") or ""):
                        return r
            return by_last4[0]
        # 2. last4 inside account name e.g. 广发银行信用卡 (3955)
        for r in rows:
            nm = r.get("name") or ""
            if f"({last4})" in nm or f"（{last4}）" in nm or f"尾号{last4}" in nm or nm.endswith(last4):
                return r

    if display_name:
        for r in rows:
            if (r.get("name") or "").strip() == display_name:
                return r
        # Friendly aliases for wallets & cash
        alias_groups = [
            ("支付宝余额宝", ("余额宝",)),
            ("支付宝余额", ("支付宝余额", "支付宝账户余额")),
            ("支付宝花呗", ("花呗",)),
            ("京东白条", ("白条",)),
            ("微信零钱通", ("零钱通",)),
            ("微信零钱", ("微信零钱",)),
            ("现金钱包", ("现金钱包", "现金")),
        ]
        for canonical, aliases in alias_groups:
            if display_name == canonical:
                for r in rows:
                    nm = (r.get("name") or "").strip()
                    if any(a in nm for a in aliases):
                        if canonical == "微信零钱" and "零钱通" in nm:
                            continue
                        if canonical == "支付宝余额" and "余额宝" in nm:
                            continue
                        return r
    return None


def resolve_or_create_account_and_card(
    conn: Any,
    info: Dict[str, Any],
    auto_create: bool = True,
    currency: str = "CNY",
) -> Dict[str, Any]:
    """Match or auto-create corresponding account and/or credit_card from parsed payment info."""
    ts = now_iso()
    last4 = (info.get("card_last4") or "").strip()
    bank_name = (info.get("bank_name") or "").strip()
    card_kind = (info.get("card_kind") or "unknown").strip()
    display_name = (info.get("account_display_name") or "").strip()

    created_account: Optional[Dict[str, Any]] = None
    created_credit_card: Optional[Dict[str, Any]] = None
    account_id: Optional[int] = None
    credit_card_id: Optional[int] = None
    account_name = ""

    if not last4 and not display_name:
        return {
            "account_id": None,
            "credit_card_id": None,
            "account_name": "",
            "bank_name": bank_name,
            "card_last4": last4,
            "created_account": None,
            "created_credit_card": None,
        }

    # First check if a credit_card already exists for this last4 (even if current row didn't mention bank_name!)
    existing_cc = _find_matching_credit_card(conn, last4, bank_name, display_name)
    if existing_cc and (card_kind == "credit_card" or (last4 and card_kind != "debit_card")):
        credit_card_id = existing_cc["id"]
        if not bank_name and existing_cc.get("bank_name"):
            bank_name = existing_cc["bank_name"]
        if existing_cc.get("account_id"):
            acc_row = conn.execute("SELECT * FROM accounts WHERE id = ?", (existing_cc["account_id"],)).fetchone()
            if acc_row:
                account_id = acc_row["id"]
                account_name = acc_row["name"]
        if account_id is None:
            matched_acc = _find_matching_account(conn, last4, bank_name, "credit_card", existing_cc["name"])
            if matched_acc:
                account_id = matched_acc["id"]
                account_name = matched_acc["name"]
                conn.execute("UPDATE credit_cards SET account_id = ?, updated_at = ? WHERE id = ?", (account_id, ts, credit_card_id))
            elif auto_create:
                acc_nm = existing_cc["name"] or display_name
                cur = conn.execute(
                    """INSERT INTO accounts (name, type, balance, credit_limit, currency, bank_name, card_last4, updated_at)
                    VALUES (?, 'credit_card', 0.0, ?, ?, ?, ?, ?)""",
                    (acc_nm, float(existing_cc.get("credit_limit") or 0.0), currency or "CNY", bank_name, last4, ts),
                )
                account_id = cur.lastrowid
                created_account = dict(conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())
                account_name = created_account["name"]
                conn.execute("UPDATE credit_cards SET account_id = ?, updated_at = ? WHERE id = ?", (account_id, ts, credit_card_id))
        return {
            "account_id": account_id,
            "credit_card_id": credit_card_id,
            "account_name": account_name,
            "bank_name": bank_name,
            "card_last4": last4,
            "created_account": created_account,
            "created_credit_card": created_credit_card,
        }

    # Check existing accounts (handles debit cards, wallets, cash, and partial strings like 储蓄卡(1797))
    existing_acc = _find_matching_account(conn, last4, bank_name, card_kind, display_name)
    if existing_acc:
        account_id = existing_acc["id"]
        account_name = existing_acc["name"]
        if not bank_name and existing_acc.get("bank_name"):
            bank_name = existing_acc["bank_name"]
        # Backfill last4/bank_name onto existing account if missing
        if (last4 and not existing_acc.get("card_last4")) or (bank_name and not existing_acc.get("bank_name")):
            conn.execute(
                "UPDATE accounts SET bank_name = CASE WHEN bank_name = '' THEN ? ELSE bank_name END, card_last4 = CASE WHEN card_last4 = '' THEN ? ELSE card_last4 END, updated_at = ? WHERE id = ?",
                (bank_name, last4, ts, account_id),
            )
        # If this is a credit_card and no credit_cards entry exists yet, auto-create the credit_cards profile
        if (card_kind == "credit_card" or existing_acc.get("type") == "credit_card") and not existing_cc and auto_create:
            cc_nm = existing_acc["name"]
            cur_cc = conn.execute(
                """INSERT INTO credit_cards
                (name, bank_name, last4, account_id, credit_limit, current_debt, statement_bill_amount, paid_amount_this_cycle, min_payment, statement_day, due_day, grace_days, swipe_fee_rate, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 5, 23, 3, 0.006, 'active', ?, ?, ?)""",
                (
                    cc_nm,
                    bank_name,
                    last4,
                    account_id,
                    float(existing_acc.get("credit_limit") or 0.0),
                    "由账单支付方式自动识别创建，可补充额度/账单日/还款日",
                    ts,
                    ts,
                ),
            )
            credit_card_id = cur_cc.lastrowid
            created_credit_card = dict(conn.execute("SELECT * FROM credit_cards WHERE id = ?", (credit_card_id,)).fetchone())
        elif existing_cc:
            credit_card_id = existing_cc["id"]

        return {
            "account_id": account_id,
            "credit_card_id": credit_card_id,
            "account_name": account_name,
            "bank_name": bank_name,
            "card_last4": last4,
            "created_account": created_account,
            "created_credit_card": created_credit_card,
        }

    # Not found -> auto-create if permitted
    if not auto_create:
        return {
            "account_id": None,
            "credit_card_id": None,
            "account_name": "",
            "bank_name": bank_name,
            "card_last4": last4,
            "created_account": None,
            "created_credit_card": None,
        }

    if card_kind == "credit_card":
        acc_type = "credit_card"
    elif card_kind == "debit_card":
        acc_type = "bank"
    else:
        acc_type = "cash"

    final_name = display_name or (f"{bank_name} ({last4})" if bank_name and last4 else f"银行卡 ({last4})")
    cur_acc = conn.execute(
        """INSERT INTO accounts (name, type, balance, credit_limit, currency, bank_name, card_last4, updated_at)
        VALUES (?, ?, 0.0, 0.0, ?, ?, ?, ?)""",
        (final_name, acc_type, currency or "CNY", bank_name, last4, ts),
    )
    account_id = cur_acc.lastrowid
    created_account = dict(conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())
    account_name = created_account["name"]

    if card_kind == "credit_card":
        cur_cc = conn.execute(
            """INSERT INTO credit_cards
            (name, bank_name, last4, account_id, credit_limit, current_debt, statement_bill_amount, paid_amount_this_cycle, min_payment, statement_day, due_day, grace_days, swipe_fee_rate, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0.0, 5, 23, 3, 0.006, 'active', ?, ?, ?)""",
            (
                final_name,
                bank_name,
                last4,
                account_id,
                "由账单支付方式自动识别创建，可补充额度/账单日/还款日",
                ts,
                ts,
            ),
        )
        credit_card_id = cur_cc.lastrowid
        created_credit_card = dict(conn.execute("SELECT * FROM credit_cards WHERE id = ?", (credit_card_id,)).fetchone())

    return {
        "account_id": account_id,
        "credit_card_id": credit_card_id,
        "account_name": account_name,
        "bank_name": bank_name,
        "card_last4": last4,
        "created_account": created_account,
        "created_credit_card": created_credit_card,
    }


def _parse_tx_dt(occ: str) -> Optional[datetime]:
    s = (occ or "").strip().replace("/", "-")
    for fmt, n in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%dT%H:%M", 16),
        ("%Y-%m-%d", 10),
    ):
        if len(s) >= n:
            try:
                return datetime.strptime(s[:n], fmt)
            except ValueError:
                continue
    return None


def _is_channel_source(tx: Dict[str, Any]) -> bool:
    ch = (tx.get("payment_channel") or "").lower()
    src = (tx.get("source_type") or "").lower()
    return ch in ("alipay", "wechat") or src in ("alipay", "wechat")


def _is_bank_card_source(tx: Dict[str, Any]) -> bool:
    ch = (tx.get("payment_channel") or "").lower()
    src = (tx.get("source_type") or "").lower()
    if ch in ("alipay", "wechat", "cash"):
        return False
    return ch in ("card_direct", "email") or src in ("email", "bank_card", "card_direct")


def _email_mentions_channel(tx: Dict[str, Any], channel: str) -> bool:
    hay = f"{tx.get('merchant') or ''} {tx.get('description') or ''}".lower()
    if channel == "wechat":
        return any(k in hay for k in ("wechat", "微信", "财付通", "tenpay"))
    if channel == "alipay":
        return any(k in hay for k in ("alipay", "支付宝", "蚂蚁"))
    return False


def _reverse_tx_balance(conn: Any, account_id: Optional[int], tx_type: str, amount: float) -> None:
    if not account_id or amount <= 0:
        return
    delta = -amount if tx_type == "income" else (amount if tx_type == "expense" else 0.0)
    if delta != 0.0:
        conn.execute(
            "UPDATE accounts SET balance = ROUND(balance + ?, 2), updated_at = ? WHERE id = ?",
            (delta, now_iso(), account_id),
        )


def _apply_tx_balance(conn: Any, account_id: Optional[int], tx_type: str, amount: float) -> None:
    if not account_id or amount <= 0:
        return
    delta = amount if tx_type == "income" else (-amount if tx_type == "expense" else 0.0)
    if delta != 0.0:
        conn.execute(
            "UPDATE accounts SET balance = ROUND(balance + ?, 2), updated_at = ? WHERE id = ?",
            (delta, now_iso(), account_id),
        )


def run_cross_source_reconciliation(
    conn: Any,
    backfill_accounts: bool = True,
    auto_create_accounts: bool = True,
) -> Dict[str, Any]:
    """
    1. Optionally backfill payment_channel / card_last4 / account_id / credit_card_id on existing rows.
    2. Cross-reconcile Alipay/WeChat card payments against Bank/Email credit/debit card bills.
       - Matched pairs: keep Alipay/WeChat as authoritative ('matched'), mark bank/email copy as
         'reconciled_duplicate' (excluded from totals & balance reversed so zero double-counting).
       - Unmatched bank/email card swipes: mark 'direct_card' (genuine direct card swipes!).
       - Cash rows: mark 'cash'.
    """
    auto_created_accounts: List[Dict[str, Any]] = []
    auto_created_cards: List[Dict[str, Any]] = []
    backfilled_count = 0

    all_rows = [dict(r) for r in conn.execute("SELECT * FROM transactions ORDER BY occurred_at ASC, id ASC").fetchall()]

    # Phase 1: Backfill metadata & auto-create accounts/cards when missing
    if backfill_accounts:
        for tx in all_rows:
            cur_pm = (tx.get("payment_method") or "").strip()
            cur_ch = (tx.get("payment_channel") or "").strip()
            cur_last4 = (tx.get("card_last4") or "").strip()
            cur_bank = (tx.get("bank_name") or "").strip()

            needs_parse = (
                not cur_ch
                or cur_ch == "manual"
                or (not cur_last4 and (cur_pm or tx.get("source_type") == "email"))
                or (tx.get("account_id") is None and (cur_pm or tx.get("source_type") in ("alipay", "wechat", "email", "cash")))
            )
            if not needs_parse:
                continue

            info = parse_payment_method_info(
                payment_method=cur_pm,
                source_type=tx.get("source_type") or "",
                description=tx.get("description") or "",
                merchant=tx.get("merchant") or "",
                payment_channel="" if cur_ch in ("", "manual") else cur_ch,
            )

            new_ch = info["payment_channel"]
            new_pm = cur_pm or info["payment_method"]
            new_bank = cur_bank or info["bank_name"]
            new_last4 = cur_last4 or info["card_last4"]
            new_acc_id = tx.get("account_id")
            new_cc_id = tx.get("credit_card_id")

            if new_acc_id is None or (info["card_kind"] == "credit_card" and new_cc_id is None):
                resolved = resolve_or_create_account_and_card(
                    conn,
                    {
                        **info,
                        "bank_name": new_bank,
                        "card_last4": new_last4,
                    },
                    auto_create=auto_create_accounts,
                    currency=tx.get("original_currency") or "CNY",
                )
                if resolved["created_account"]:
                    auto_created_accounts.append(resolved["created_account"])
                if resolved["created_credit_card"]:
                    auto_created_cards.append(resolved["created_credit_card"])
                if new_acc_id is None and resolved["account_id"] is not None:
                    new_acc_id = resolved["account_id"]
                    if tx.get("reconcile_status") != "reconciled_duplicate":
                        _apply_tx_balance(conn, new_acc_id, tx["type"], float(tx["amount"]))
                if new_cc_id is None and resolved["credit_card_id"] is not None:
                    new_cc_id = resolved["credit_card_id"]
                if not new_bank and resolved["bank_name"]:
                    new_bank = resolved["bank_name"]

            changed = (
                new_ch != cur_ch
                or new_pm != cur_pm
                or new_bank != cur_bank
                or new_last4 != cur_last4
                or new_acc_id != tx.get("account_id")
                or new_cc_id != tx.get("credit_card_id")
            )
            if changed:
                conn.execute(
                    """UPDATE transactions
                    SET payment_channel = ?, payment_method = ?, bank_name = ?, card_last4 = ?, account_id = ?, credit_card_id = ?
                    WHERE id = ?""",
                    (new_ch, new_pm, new_bank, new_last4, new_acc_id, new_cc_id, tx["id"]),
                )
                tx["payment_channel"] = new_ch
                tx["payment_method"] = new_pm
                tx["bank_name"] = new_bank
                tx["card_last4"] = new_last4
                tx["account_id"] = new_acc_id
                tx["credit_card_id"] = new_cc_id
                backfilled_count += 1

    # Phase 2: Cross-source reconciliation between Alipay/WeChat and Bank/Email Card transactions
    channel_pool = [
        tx
        for tx in all_rows
        if _is_channel_source(tx)
        and tx.get("reconcile_status") not in ("matched", "reconciled_duplicate")
        and not tx.get("matched_tx_id")
    ]
    card_pool = [
        tx
        for tx in all_rows
        if _is_bank_card_source(tx)
        and tx.get("reconcile_status") not in ("matched", "reconciled_duplicate")
        and not tx.get("matched_tx_id")
    ]

    candidate_pairs: List[Tuple[int, float, float, Dict[str, Any], Dict[str, Any]]] = []
    for c_tx in channel_pool:
        c_dt = _parse_tx_dt(c_tx.get("occurred_at") or "")
        if c_dt is None:
            continue
        c_amt = float(c_tx["amount"])
        c_last4 = (c_tx.get("card_last4") or "").strip()
        c_ch = (c_tx.get("payment_channel") or c_tx.get("source_type") or "").lower()
        has_promo = "&" in (c_tx.get("payment_method") or "")

        for e_tx in card_pool:
            if c_tx["id"] == e_tx["id"] or c_tx["type"] != e_tx["type"]:
                continue
            e_dt = _parse_tx_dt(e_tx.get("occurred_at") or "")
            if e_dt is None:
                continue

            diff_minutes = abs((e_dt - c_dt).total_seconds()) / 60.0
            if diff_minutes > 48 * 60:  # max ±2 days posting lag
                continue

            e_last4 = (e_tx.get("card_last4") or "").strip()
            mentions_ch = _email_mentions_channel(e_tx, c_ch)

            # Card suffix constraint
            if c_last4 and e_last4:
                if c_last4 != e_last4:
                    continue
            elif not mentions_ch:
                # If neither card_last4 matches nor bank email mentions WECHAT/ALIPAY, don't merge blindly
                continue
            elif diff_minutes > 24 * 60:
                continue

            e_amt = float(e_tx["amount"])
            amt_diff = abs(c_amt - e_amt)
            e_orig_ccy = (e_tx.get("original_currency") or "CNY").upper()

            match_tier = -1
            if amt_diff <= 0.05:
                match_tier = 0  # Exact CNY match
            elif e_orig_ccy != "CNY" and c_amt > 0 and (amt_diff / c_amt) <= 0.05 and (diff_minutes <= 360 or mentions_ch):
                match_tier = 1  # Foreign-currency FX match (e.g. ZA Card HKD vs WeChat CNY)
            elif has_promo and c_last4 and c_last4 == e_last4 and 0 < (c_amt - e_amt) <= max(5.0, c_amt * 0.25) and diff_minutes <= 30:
                match_tier = 2  # Alipay voucher/立减金 card net charge match

            if match_tier >= 0:
                candidate_pairs.append((match_tier, diff_minutes, amt_diff, c_tx, e_tx))

    candidate_pairs.sort(key=lambda x: (x[0], x[1], x[2]))
    used_channel_ids = set()
    used_card_ids = set()
    newly_matched: List[Dict[str, Any]] = []

    for tier, diff_min, amt_diff, c_tx, e_tx in candidate_pairs:
        if c_tx["id"] in used_channel_ids or e_tx["id"] in used_card_ids:
            continue
        used_channel_ids.add(c_tx["id"])
        used_card_ids.add(e_tx["id"])

        # Enrich channel tx with card info if channel tx lacked last4
        upd_last4 = c_tx.get("card_last4") or e_tx.get("card_last4") or ""
        upd_bank = c_tx.get("bank_name") or e_tx.get("bank_name") or ""
        upd_acc = c_tx.get("account_id") if c_tx.get("account_id") is not None else e_tx.get("account_id")
        upd_cc = c_tx.get("credit_card_id") if c_tx.get("credit_card_id") is not None else e_tx.get("credit_card_id")

        if c_tx.get("account_id") is None and upd_acc is not None:
            _apply_tx_balance(conn, upd_acc, c_tx["type"], float(c_tx["amount"]))

        conn.execute(
            """UPDATE transactions
            SET reconcile_status = 'matched', matched_tx_id = ?, card_last4 = ?, bank_name = ?, account_id = ?, credit_card_id = ?
            WHERE id = ?""",
            (e_tx["id"], upd_last4, upd_bank, upd_acc, upd_cc, c_tx["id"]),
        )
        conn.execute(
            """UPDATE transactions
            SET reconcile_status = 'reconciled_duplicate', matched_tx_id = ?
            WHERE id = ?""",
            (c_tx["id"], e_tx["id"]),
        )
        # Reverse duplicate bank/email tx's impact on its account balance so we never double-deduct
        _reverse_tx_balance(conn, e_tx.get("account_id"), e_tx["type"], float(e_tx["amount"]))

        c_tx["reconcile_status"] = "matched"
        c_tx["matched_tx_id"] = e_tx["id"]
        e_tx["reconcile_status"] = "reconciled_duplicate"
        e_tx["matched_tx_id"] = c_tx["id"]
        newly_matched.append(
            {
                "channel_tx_id": c_tx["id"],
                "card_tx_id": e_tx["id"],
                "amount": float(c_tx["amount"]),
                "card_last4": upd_last4,
                "diff_minutes": round(diff_min, 1),
            }
        )

    # Phase 3: Tag unmatched direct card swipes and cash rows cleanly
    for tx in all_rows:
        st = tx.get("reconcile_status") or "unmatched"
        if st in ("matched", "reconciled_duplicate"):
            continue
        ch = (tx.get("payment_channel") or "").lower()
        if _is_bank_card_source(tx) and st != "direct_card":
            conn.execute(
                "UPDATE transactions SET reconcile_status = 'direct_card', payment_channel = CASE WHEN payment_channel IN ('', 'email', 'manual') THEN 'card_direct' ELSE payment_channel END WHERE id = ?",
                (tx["id"],),
            )
            tx["reconcile_status"] = "direct_card"
        elif ch == "cash" and st != "cash":
            conn.execute("UPDATE transactions SET reconcile_status = 'cash' WHERE id = ?", (tx["id"],))
            tx["reconcile_status"] = "cash"

    total_matched = conn.execute("SELECT COUNT(*) FROM transactions WHERE reconcile_status = 'matched'").fetchone()[0]
    total_direct = conn.execute("SELECT COUNT(*) FROM transactions WHERE reconcile_status = 'direct_card'").fetchone()[0]
    total_deduped = conn.execute("SELECT COUNT(*) FROM transactions WHERE reconcile_status = 'reconciled_duplicate'").fetchone()[0]

    return {
        "newly_matched_count": len(newly_matched),
        "newly_matched_pairs": newly_matched,
        "total_matched_count": total_matched,
        "total_direct_card_count": total_direct,
        "total_deduped_count": total_deduped,
        "backfilled_count": backfilled_count,
        "auto_created_accounts": auto_created_accounts,
        "auto_created_cards": auto_created_cards,
    }


def unmatch_reconciled_pair(conn: Any, tx_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not row:
        raise ValueError("账目不存在")
    tx = dict(row)
    peer_id = tx.get("matched_tx_id")
    if not peer_id:
        raise ValueError("该账目未处于已核对匹配状态")
    peer_row = conn.execute("SELECT * FROM transactions WHERE id = ?", (peer_id,)).fetchone()

    dup_tx = tx if tx.get("reconcile_status") == "reconciled_duplicate" else (dict(peer_row) if peer_row else None)
    pri_tx = dict(peer_row) if (dup_tx and dup_tx["id"] == tx["id"] and peer_row) else tx

    if pri_tx:
        conn.execute(
            "UPDATE transactions SET reconcile_status = 'unmatched', matched_tx_id = NULL WHERE id = ?",
            (pri_tx["id"],),
        )
    if dup_tx:
        new_st = "direct_card" if _is_bank_card_source(dup_tx) else "unmatched"
        conn.execute(
            "UPDATE transactions SET reconcile_status = ?, matched_tx_id = NULL WHERE id = ?",
            (new_st, dup_tx["id"]),
        )
        # Re-apply balance effect since it is no longer excluded as a duplicate
        _apply_tx_balance(conn, dup_tx.get("account_id"), dup_tx["type"], float(dup_tx["amount"]))

    return {"unmatched": True, "tx_id": tx_id, "peer_id": peer_id}


def build_reconciliation_overview(
    conn: Any,
    month: str = "",
    channel: str = "",
    card_last4: str = "",
    reconcile_status: str = "",
    q: str = "",
    include_duplicates: bool = False,
) -> Dict[str, Any]:
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT t.*,
                      a.name AS account_name,
                      a.type AS account_type,
                      cc.name AS credit_card_name
               FROM transactions t
               LEFT JOIN accounts a ON t.account_id = a.id
               LEFT JOIN credit_cards cc ON t.credit_card_id = cc.id
               ORDER BY t.occurred_at DESC, t.id DESC"""
        ).fetchall()
    ]

    if month and month.strip():
        m_prefix = month.strip()[:7]
        rows = [r for r in rows if (r.get("occurred_at") or "").startswith(m_prefix)]

    by_id = {r["id"]: r for r in rows}

    # Effective transactions exclude reconciled_duplicate (bank/email copies of Alipay/WeChat card spends)
    effective = [r for r in rows if (r.get("reconcile_status") or "unmatched") != "reconciled_duplicate"]
    duplicates = [r for r in rows if (r.get("reconcile_status") or "") == "reconciled_duplicate"]

    # Channel summary
    channel_keys = ("alipay", "wechat", "card_direct", "cash", "other")
    channel_labels = {
        "alipay": "支付宝",
        "wechat": "微信支付",
        "card_direct": "直接刷卡 / 银行卡",
        "cash": "现金",
        "other": "其他/手动",
    }
    channel_stats: Dict[str, Dict[str, Any]] = {
        k: {"channel": k, "label": channel_labels[k], "count": 0, "expense": 0.0, "income": 0.0}
        for k in channel_keys
    }

    for r in effective:
        ch = (r.get("payment_channel") or r.get("source_type") or "other").lower()
        if ch not in ("alipay", "wechat", "card_direct", "cash"):
            ch = "card_direct" if ch == "email" else "other"
        bucket = channel_stats[ch]
        bucket["count"] += 1
        if r["type"] == "expense":
            bucket["expense"] = round(bucket["expense"] + float(r["amount"]), 2)
        elif r["type"] == "income":
            bucket["income"] = round(bucket["income"] + float(r["amount"]), 2)

    # Card & Account breakdown
    card_map: Dict[str, Dict[str, Any]] = {}
    for r in effective:
        last4 = (r.get("card_last4") or "").strip()
        acc_id = r.get("account_id")
        cc_id = r.get("credit_card_id")
        acc_name = r.get("account_name") or r.get("credit_card_name") or r.get("payment_method") or "未绑定账户"
        key = f"cc:{cc_id}" if cc_id else (f"acc:{acc_id}" if acc_id else (f"l4:{last4}" if last4 else f"nm:{acc_name}"))
        if key not in card_map:
            card_map[key] = {
                "key": key,
                "account_id": acc_id,
                "credit_card_id": cc_id,
                "name": acc_name,
                "bank_name": r.get("bank_name") or "",
                "card_last4": last4,
                "is_credit_card": bool(cc_id or r.get("account_type") == "credit_card"),
                "count": 0,
                "expense": 0.0,
                "income": 0.0,
                "matched_count": 0,
                "direct_count": 0,
                "channels": set(),
            }
        entry = card_map[key]
        entry["count"] += 1
        if r["type"] == "expense":
            entry["expense"] = round(entry["expense"] + float(r["amount"]), 2)
        elif r["type"] == "income":
            entry["income"] = round(entry["income"] + float(r["amount"]), 2)
        if r.get("reconcile_status") == "matched":
            entry["matched_count"] += 1
        elif r.get("reconcile_status") == "direct_card":
            entry["direct_count"] += 1
        entry["channels"].add((r.get("payment_channel") or r.get("source_type") or "other").lower())

    card_breakdown = []
    for item in card_map.values():
        item["channels"] = sorted(item["channels"])
        card_breakdown.append(item)
    card_breakdown.sort(key=lambda x: (-x["expense"], -x["count"]))

    # Matched pairs side-by-side
    matched_pairs: List[Dict[str, Any]] = []
    for r in effective:
        if r.get("reconcile_status") == "matched" and r.get("matched_tx_id"):
            peer = by_id.get(r["matched_tx_id"])
            if peer is None:
                peer_row = conn.execute(
                    """SELECT t.*, a.name AS account_name FROM transactions t
                       LEFT JOIN accounts a ON t.account_id = a.id WHERE t.id = ?""",
                    (r["matched_tx_id"],),
                ).fetchone()
                peer = dict(peer_row) if peer_row else None
            matched_pairs.append(
                {
                    "primary_tx": r,
                    "duplicate_tx": peer,
                    "card_last4": r.get("card_last4") or (peer.get("card_last4") if peer else ""),
                    "bank_name": r.get("bank_name") or (peer.get("bank_name") if peer else ""),
                    "amount": float(r["amount"]),
                }
            )

    direct_cards = [r for r in effective if r.get("reconcile_status") == "direct_card"]

    # Available months across all transactions
    month_rows = conn.execute(
        "SELECT DISTINCT substr(occurred_at, 1, 7) AS ym FROM transactions WHERE length(occurred_at) >= 7 ORDER BY ym DESC"
    ).fetchall()
    available_months = [r["ym"] for r in month_rows if r["ym"]]

    # Filtered transaction list
    pool = rows if include_duplicates else effective
    filtered = pool
    if channel and channel != "all":
        ch_low = channel.lower()
        if ch_low == "other":
            filtered = [r for r in filtered if (r.get("payment_channel") or r.get("source_type") or "other").lower() not in ("alipay", "wechat", "card_direct", "cash", "email")]
        elif ch_low == "card_direct":
            filtered = [r for r in filtered if (r.get("payment_channel") or "").lower() in ("card_direct", "email")]
        else:
            filtered = [r for r in filtered if (r.get("payment_channel") or r.get("source_type") or "").lower() == ch_low]
    if card_last4 and card_last4.strip():
        l4 = card_last4.strip()
        filtered = [r for r in filtered if (r.get("card_last4") or "").strip() == l4]
    if reconcile_status and reconcile_status != "all":
        filtered = [r for r in filtered if (r.get("reconcile_status") or "unmatched") == reconcile_status]
    if q and q.strip():
        kw = q.strip().lower()
        filtered = [
            r
            for r in filtered
            if kw in (r.get("description") or "").lower()
            or kw in (r.get("merchant") or "").lower()
            or kw in (r.get("category") or "").lower()
            or kw in (r.get("payment_method") or "").lower()
            or kw in (r.get("account_name") or "").lower()
            or kw in (r.get("card_last4") or "").lower()
        ]

    total_expense = round(sum(float(r["amount"]) for r in effective if r["type"] == "expense"), 2)
    total_income = round(sum(float(r["amount"]) for r in effective if r["type"] == "income"), 2)
    deduped_amount = round(sum(float(r["amount"]) for r in duplicates if r["type"] == "expense"), 2)

    return {
        "summary": {
            "total_effective_count": len(effective),
            "total_expense": total_expense,
            "total_income": total_income,
            "net_cashflow": round(total_income - total_expense, 2),
            "matched_count": len(matched_pairs),
            "deduped_count": len(duplicates),
            "deduped_expense_saved": deduped_amount,
            "direct_card_count": len(direct_cards),
            "cash_count": channel_stats["cash"]["count"],
            "tracked_cards_count": len([c for c in card_breakdown if c["card_last4"] or c["is_credit_card"]]),
        },
        "channel_breakdown": [channel_stats[k] for k in channel_keys],
        "card_breakdown": card_breakdown,
        "matched_pairs": matched_pairs,
        "direct_card_transactions": direct_cards,
        "transactions": filtered[:1000],
        "available_months": available_months,
    }
