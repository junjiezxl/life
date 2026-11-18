import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx


WEEKDAY_MAP = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


async def _call_llm_json(
    system_prompt: str,
    user_prompt: str,
    settings: Dict[str, str],
    timeout_s: float = 30.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    provider = settings.get("ai_provider", "local_fallback")
    api_key = (settings.get("ai_api_key") or "").strip()
    model = (settings.get("ai_model") or "").strip()
    base_url = (settings.get("ai_base_url") or "https://api.openai.com/v1").strip().rstrip("/")

    if provider == "local_fallback":
        return None, None

    if provider == "gemini":
        if not api_key:
            return None, "未配置 Gemini API Key，已自动使用本地智能解析。"
        target_model = model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(_strip_json_fences(text)), None
        except Exception as exc:
            return None, f"Gemini API 调用失败（已回退到本地解析）: {exc}"

    if provider == "openai_compatible":
        is_local = any(host in base_url for host in ("127.0.0.1", "localhost", "0.0.0.0"))
        if not api_key and not is_local:
            return None, "未配置 OpenAI 兼容 API Key，已自动使用本地智能解析。"
        target_model = model or "gpt-4o-mini"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": target_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return json.loads(_strip_json_fences(text)), None
        except Exception as exc:
            return None, f"大模型接口调用失败（已回退到本地解析）: {exc}"

    return None, None


def _parse_chinese_datetime(text: str, now: Optional[datetime] = None) -> Optional[str]:
    base = now or datetime.now().astimezone()
    target_date: Optional[datetime] = None

    # Explicit date like 11月25日 / 11-25 / 2026-11-25
    m_ymd = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日号]?", text)
    m_md = re.search(r"(?<!\d)(\d{1,2})[月/-](\d{1,2})[日号]?", text)
    m_day_of_month = re.search(r"每月\s*(\d{1,2})\s*[号日]", text)

    if m_ymd:
        try:
            target_date = base.replace(year=int(m_ymd.group(1)), month=int(m_ymd.group(2)), day=int(m_ymd.group(3)))
        except ValueError:
            target_date = None
    elif m_md and not re.search(r"\d+\.\d+", m_md.group(0)):
        month, day = int(m_md.group(1)), int(m_md.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                candidate = base.replace(month=month, day=day)
                if candidate.date() < base.date():
                    candidate = candidate.replace(year=base.year + 1)
                target_date = candidate
            except ValueError:
                target_date = None
    elif m_day_of_month:
        day = min(max(int(m_day_of_month.group(1)), 1), 28)
        candidate = base.replace(day=day)
        if candidate.date() < base.date():
            next_month = (base.replace(day=28) + timedelta(days=4)).replace(day=day)
            candidate = next_month
        target_date = candidate
    elif "大后天" in text:
        target_date = base + timedelta(days=3)
    elif "后天" in text:
        target_date = base + timedelta(days=2)
    elif "明天" in text or "明早" in text or "明晚" in text:
        target_date = base + timedelta(days=1)
    elif "今天" in text or "今晚" in text or "今早" in text:
        target_date = base
    else:
        m_week = re.search(r"(下)?(?:周|星期)([一二三四五六日天])", text)
        if m_week:
            is_next = bool(m_week.group(1))
            wd = WEEKDAY_MAP[m_week.group(2)]
            delta = (wd - base.weekday()) % 7
            if is_next:
                delta = delta + 7 if delta != 0 else 7
            elif delta == 0:
                delta = 0
            target_date = base + timedelta(days=delta)

    # Time of day
    hour = 9
    minute = 0
    has_time = False
    m_hm = re.search(r"(?:(凌晨|早上|早|上午|中午|下午|傍晚|晚上|今晚|明晚)\s*)?(\d{1,2})\s*[:：点时]\s*(?:(半)|(\d{1,2})\s*分?)?", text)
    if m_hm:
        period = m_hm.group(1) or ""
        h = int(m_hm.group(2))
        if 0 <= h <= 23:
            if period in ("下午", "傍晚", "晚上", "今晚", "明晚") and h < 12:
                h += 12
            elif period == "中午" and h < 11:
                h += 12
            hour = h
            if m_hm.group(3) == "半":
                minute = 30
            elif m_hm.group(4):
                minute = min(int(m_hm.group(4)), 59)
            has_time = True
    elif "明早" in text or "早上" in text or "上午" in text:
        hour, minute, has_time = 9, 0, True
    elif "中午" in text:
        hour, minute, has_time = 12, 0, True
    elif "下午" in text:
        hour, minute, has_time = 15, 0, True
    elif "晚上" in text or "今晚" in text or "明晚" in text:
        hour, minute, has_time = 20, 0, True

    if target_date is None and not has_time:
        return None
    if target_date is None:
        target_date = base

    dt = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.isoformat(timespec="minutes")


def _detect_reminder_category(text: str) -> str:
    if any(k in text for k in ("房租", "水电", "信用卡", "还款", "报销", "缴费", "账单", "转账", "工资", "续费", "元", "块")):
        return "finance"
    if any(k in text for k in ("项目", "周报", "会议", "开会", "客户", "提交", "上线", "代码", "复盘", "面试", "文档")):
        return "work"
    if any(k in text for k in ("医院", "体检", "牙科", "吃药", "跑步", "健身", "预约门诊")):
        return "health"
    if any(k in text for k in ("学习", "读书", "课程", "考试", "复习", "笔记", "论文")):
        return "study"
    return "life"


def _detect_tx_category(text: str, tx_type: str) -> str:
    if tx_type == "income":
        if any(k in text for k in ("工资", "薪资", "发薪")):
            return "工资收入"
        if "奖金" in text or "年终" in text:
            return "奖金"
        if "报销" in text:
            return "报销回款"
        if any(k in text for k in ("理财", "利息", "分红", "基金")):
            return "理财收益"
        return "其他收入"

    mapping = [
        (("房租", "物业", "水电", "燃气", "房贷"), "住房"),
        (("午饭", "晚饭", "早饭", "咖啡", "奶茶", "超市", "买菜", "吃饭", "外卖", "水果", "聚餐"), "餐饮"),
        (("打车", "地铁", "公交", "高铁", "机票", "加油", "停车"), "交通"),
        (("会员", "订阅", "云盘", "软件", "话费", "宽带", "充值"), "订阅服务"),
        (("医院", "买药", "体检", "看病"), "医疗"),
        (("电影", "游戏", "旅游", "门票"), "娱乐"),
        (("买", "淘宝", "京东", "衣服", "日用", "键盘", "鼠标"), "购物"),
    ]
    for keywords, cat in mapping:
        if any(k in text for k in keywords):
            return cat
    return "其他"


def _extract_amount(text: str) -> Optional[float]:
    m = re.search(r"(?:¥|￥|RMB)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱|rmb)?", text)
    if not m:
        return None
    # Avoid matching time like 3点 or dates like 11月25日
    matched_str = m.group(0)
    end_idx = m.end()
    after = text[end_idx : end_idx + 2]
    if after.startswith(("点", "时", "分", "月", "日", "号", "年")):
        # Try searching after this match
        rest = _extract_amount(text[end_idx + 1 :])
        return rest
    # Only accept bare numbers if currency cue or expense verb exists
    if not any(u in matched_str for u in ("元", "块", "¥", "￥", "RMB", "rmb")) and not any(
        v in text for v in ("花", "费", "付", "交", "还", "收", "工资", "买", "报销", "转账")
    ):
        return None
    try:
        val = float(m.group(1))
        return val if val > 0 else None
    except ValueError:
        return None


def _clean_reminder_title(clause: str) -> str:
    title = clause.strip()
    title = re.sub(
        r"^(?:请|帮我|麻烦)?(?:在)?(?:今天|明天|后天|大后天|下?周[一二三四五六日天]|下?星期[一二三四五六日天]|每月\d+号|\d{1,2}月\d{1,2}[日号])?\s*(?:凌晨|早上|早|上午|中午|下午|傍晚|晚上|今晚|明晚)?\s*(?:\d{1,2}[:：点时](?:半|\d{1,2}分?)?)?\s*(?:之前|前|左右)?",
        "",
        title,
    )
    title = re.sub(r"^(?:记得|别忘了|不要忘了|提醒我|提醒|待办|安排|需要)\s*", "", title)
    title = re.sub(r"^[：:，,\s]+", "", title)
    return title.strip() or clause.strip()


def _local_parse_chat(message: str, context_summary: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now().astimezone()
    text = message.strip()

    # Check if user is asking for a status/financial summary
    is_query = any(q in text for q in ("总结", "汇总", "多少", "情况", "概览", "怎么", "有哪些", "查看")) and not any(
        act in text for act in ("提醒我", "花了", "买了", "记一笔", "新增")
    )
    if is_query:
        pending_count = context_summary.get("pending_reminders_count", 0)
        unsettled_exp = context_summary.get("unsettled_expense", 0.0)
        unsettled_inc = context_summary.get("unsettled_income", 0.0)
        net_worth = context_summary.get("net_worth", 0.0)
        top_reminders = context_summary.get("top_reminders", [])
        reminder_lines = "\n".join([f"- {r['title']}（{r.get('due_at') or '无截止时间'}）" for r in top_reminders[:4]])
        reply = (
            f"为你实时汇总当前状态：\n"
            f"• 待处理提醒：{pending_count} 项\n"
            f"• 本期未结账：收入 ¥{unsettled_inc:,.2f}，支出 ¥{unsettled_exp:,.2f}，净现金流 ¥{unsettled_inc - unsettled_exp:,.2f}\n"
            f"• 当前个人净资产：¥{net_worth:,.2f}\n"
        )
        if reminder_lines:
            reply += f"\n近期优先提醒：\n{reminder_lines}"
        return {"reply": reply, "reminders": [], "transactions": []}

    clauses = [c.strip() for c in re.split(r"[，,；;\n]+|(?:\s*(?:另外|顺便|还有|并且|以及)\s*)", text) if c.strip()]
    reminders: List[Dict[str, Any]] = []
    transactions: List[Dict[str, Any]] = []

    for clause in clauses:
        due_at = _parse_chinese_datetime(clause, now)
        amount = _extract_amount(clause)
        has_income_cue = any(k in clause for k in ("工资", "发薪", "收到", "收入", "入账", "报销到账", "赚了", "奖金"))
        has_expense_cue = any(k in clause for k in ("花了", "花费", "消费", "支出", "买了", "买", "打车", "午饭", "晚饭", "早饭", "咖啡", "奶茶", "充值", "付款"))
        has_explicit_reminder = any(k in clause for k in ("提醒", "记得", "别忘", "待办", "截止", "预约", "提交", "复盘", "准备"))
        has_future_day = any(k in clause for k in ("明天", "后天", "大后天", "下周", "周", "星期", "每月"))
        has_reminder_cue = has_explicit_reminder or (
            due_at is not None and (has_future_day or not (has_expense_cue or has_income_cue))
        )

        if has_reminder_cue:
            title = _clean_reminder_title(clause)
            priority = "high" if any(p in clause for p in ("紧急", "重要", "务必", "房租", "还款", "截止")) else "medium"
            reminders.append(
                {
                    "title": title,
                    "description": f"由 AI 对话解析：{clause}",
                    "due_at": due_at,
                    "priority": priority,
                    "category": _detect_reminder_category(clause),
                    "linked_amount": amount,
                    "linked_tx_type": "expense" if amount else None,
                }
            )
            # If it's explicitly a past spending + reminder in the same clause, usually separate clauses handle it;
            # only record transaction immediately if there's an explicit spent verb like "花了/买了" without future time.
            if amount and (has_expense_cue or has_income_cue) and due_at is None:
                tx_type = "income" if has_income_cue else "expense"
                transactions.append(
                    {
                        "type": tx_type,
                        "amount": amount,
                        "category": _detect_tx_category(clause, tx_type),
                        "description": clause,
                    }
                )
        elif amount is not None and (has_expense_cue or has_income_cue or "记账" in clause or "记一笔" in clause):
            tx_type = "income" if has_income_cue else "expense"
            transactions.append(
                {
                    "type": tx_type,
                    "amount": amount,
                    "category": _detect_tx_category(clause, tx_type),
                    "description": clause,
                }
            )
        elif len(clauses) == 1:
            # Default fallback: treat single short statement as a reminder
            reminders.append(
                {
                    "title": _clean_reminder_title(clause),
                    "description": f"由 AI 对话新增：{clause}",
                    "due_at": due_at,
                    "priority": "medium",
                    "category": _detect_reminder_category(clause),
                    "linked_amount": amount,
                    "linked_tx_type": "expense" if amount else None,
                }
            )

    parts = []
    if reminders:
        parts.append(f"已为你新增 {len(reminders)} 条提醒：" + "、".join(f"「{r['title']}」" for r in reminders))
    if transactions:
        parts.append(
            f"已记录 {len(transactions)} 笔账目："
            + "、".join(f"{'收入' if t['type']=='income' else '支出'} ¥{t['amount']:.2f}（{t['category']}）" for t in transactions)
        )
    if not parts:
        parts.append("我没太确定你的具体操作。你可以告诉我像「明天上午10点提醒我交报告」或「今天买菜花了68元」。")

    return {"reply": "\n".join(parts), "reminders": reminders, "transactions": transactions}


async def process_chat_message(
    message: str,
    context_summary: Dict[str, Any],
    settings: Dict[str, str],
) -> Dict[str, Any]:
    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (%A)")
    system_prompt = f"""你是用户的个人提醒与财务智能助手。当前时间：{now_str}。
你可以同时从用户的自然语言里：
1. 提取需要新增的提醒事项（reminders）
2. 提取已经发生需要入账的个人收支（transactions）
3. 如果用户是在询问现状或总结，结合上下文直接回答，不要强行创建提醒/账单。

请严格返回合法 JSON 对象，格式如下：
{{
  "reply": "给用户的自然语言回复（中文，简洁友好）",
  "reminders": [
    {{
      "title": "简短明确的提醒标题",
      "description": "补充说明",
      "due_at": "YYYY-MM-DDTHH:MM 或 null",
      "priority": "high | medium | low",
      "category": "work | life | finance | health | study",
      "linked_amount": 数值或null,
      "linked_tx_type": "expense | income | null"
    }}
  ],
  "transactions": [
    {{
      "type": "expense | income",
      "amount": 12.5,
      "category": "餐饮 | 交通 | 购物 | 住房 | 通讯 | 订阅服务 | 医疗 | 娱乐 | 工资收入 | 理财收益 | 其他",
      "description": "简短备注"
    }}
  ]
}}"""
    user_prompt = f"当前个人面板上下文：{json.dumps(context_summary, ensure_ascii=False)}\n\n用户输入：{message}"

    llm_res, warning = await _call_llm_json(system_prompt, user_prompt, settings)
    if llm_res and isinstance(llm_res, dict):
        return {
            "reply": str(llm_res.get("reply") or "已处理你的请求。"),
            "reminders": llm_res.get("reminders") or [],
            "transactions": llm_res.get("transactions") or [],
            "engine": settings.get("ai_provider"),
            "warning": None,
        }

    local_res = _local_parse_chat(message, context_summary)
    local_res["engine"] = "local_fallback"
    local_res["warning"] = warning
    return local_res


def _local_extract_from_notes(notes: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    now = datetime.now().astimezone()
    candidates: List[Dict[str, Any]] = []
    seen_titles = set()

    for note in notes:
        note_title = note.get("title") or "未命名笔记"
        note_path = note.get("path") or note_title
        content = note.get("content") or ""
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Skip already checked tasks
            if re.match(r"^[-*]\s*\[[xX]\]", line):
                continue

            is_todo_box = bool(re.match(r"^[-*]\s*\[\s\]", line))
            has_todo_tag = bool(re.search(r"(?:TODO|待办|提醒|记得|别忘|截止|DDL|到期|缴费|还款|预约)", line, re.IGNORECASE))
            if not (is_todo_box or has_todo_tag):
                continue

            clean = re.sub(r"^[-*]\s*\[\s\]\s*", "", line)
            clean = re.sub(r"^(?:TODO|待办)[：:\s]*", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"#[\w\u4e00-\u9fa5/-]+", "", clean).strip()
            if len(clean) < 2:
                continue

            title = _clean_reminder_title(clean)
            if title in seen_titles:
                continue
            seen_titles.add(title)

            due_at = _parse_chinese_datetime(clean, now)
            amount = _extract_amount(clean)
            priority = "high" if any(k in clean for k in ("紧急", "重要", "截止", "DDL", "房租", "还款", "到期")) else "medium"
            reason = "未勾选待办项" if is_todo_box else "包含提醒/待办关键词"

            candidates.append(
                {
                    "title": title[:120],
                    "description": f"摘自笔记《{note_title}》: {clean[:180]}",
                    "due_at": due_at,
                    "priority": priority,
                    "category": _detect_reminder_category(clean),
                    "source_type": "siyuan_note",
                    "source_ref": f"思源笔记: {note_path}",
                    "linked_amount": amount,
                    "linked_tx_type": "expense" if amount else None,
                    "reason": reason,
                }
            )
    return candidates


async def extract_reminders_from_notes(
    notes: List[Dict[str, str]],
    settings: Dict[str, str],
) -> Dict[str, Any]:
    if not notes:
        return {"candidates": [], "engine": "none", "warning": None}

    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    system_prompt = f"""请从用户的思源笔记片段中识别出需要设定提醒的待办事项、缴费账单、截止日期或预约。当前时间：{now_str}。
忽略已完成（如 [x]）或纯背景记录的内容。
严格返回 JSON：
{{
  "candidates": [
    {{
      "title": "提醒标题",
      "description": "原文上下文摘要",
      "due_at": "YYYY-MM-DDTHH:MM 或 null",
      "priority": "high | medium | low",
      "category": "work | life | finance | health | study",
      "source_ref": "思源笔记: 笔记路径或标题",
      "linked_amount": 数值或null,
      "linked_tx_type": "expense | income | null",
      "reason": "提取理由（如：包含未完成待办/明确截止日期）"
    }}
  ]
}}"""
    trimmed = [
        {"title": n.get("title", ""), "path": n.get("path", ""), "content": (n.get("content") or "")[:1500]}
        for n in notes[:15]
    ]
    user_prompt = json.dumps(trimmed, ensure_ascii=False)

    llm_res, warning = await _call_llm_json(system_prompt, user_prompt, settings, timeout_s=40.0)
    if llm_res and isinstance(llm_res.get("candidates"), list):
        candidates = llm_res["candidates"]
        for c in candidates:
            c.setdefault("source_type", "siyuan_note")
        return {"candidates": candidates, "engine": settings.get("ai_provider"), "warning": None}

    return {
        "candidates": _local_extract_from_notes(notes),
        "engine": "local_fallback",
        "warning": warning,
    }


def _local_settlement_summary(data: Dict[str, Any]) -> str:
    income = data.get("total_income", 0.0)
    expense = data.get("total_expense", 0.0)
    net_cf = data.get("net_cashflow", 0.0)
    net_worth = data.get("net_worth_snapshot", 0.0)
    breakdown = data.get("category_breakdown", {})
    tx_count = data.get("tx_count", 0)

    savings_rate = ((income - expense) / income * 100) if income > 0 else 0.0
    top_cats = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)[:3]
    top_str = "、".join(f"{k} ¥{v:,.2f}" for k, v in top_cats) if top_cats else "暂无大额分类支出"

    status_comment = "本期保持正向现金流结余" if net_cf >= 0 else "本期支出高于收入，出现现金流净流出"
    rate_comment = f"储蓄率为 {savings_rate:.1f}%" if income > 0 else "本期暂无收入入账"

    return (
        f"本次共结账 {tx_count} 笔账目：总收入 ¥{income:,.2f}，总支出 ¥{expense:,.2f}，净结余 ¥{net_cf:,.2f}（{rate_comment}）。"
        f"{status_comment}。主要支出集中在：{top_str}。结账时点个人净资产为 ¥{net_worth:,.2f}。"
    )


async def generate_settlement_summary(
    settlement_data: Dict[str, Any],
    settings: Dict[str, str],
) -> str:
    system_prompt = """你是个人财务顾问。根据用户本次结账的收支汇总、分类占比和资产负债快照，写一段简明、专业、有洞察力的中文个人财务总结（120字以内），包含收支结余评价、主要支出提示和一条温和的财务建议。
请严格返回 JSON：{"summary": "你的总结内容"}"""
    user_prompt = json.dumps(settlement_data, ensure_ascii=False)

    llm_res, _ = await _call_llm_json(system_prompt, user_prompt, settings)
    if llm_res and llm_res.get("summary"):
        return str(llm_res["summary"])
    return _local_settlement_summary(settlement_data)


async def test_ai_connection(settings: Dict[str, str]) -> Dict[str, Any]:
    provider = settings.get("ai_provider", "local_fallback")
    if provider == "local_fallback":
        return {"ok": True, "provider": provider, "message": "当前使用本地智能中文规则解析引擎（无需 API Key，随时可用）。"}

    system_prompt = 'Return JSON only: {"status": "ok", "message": "连接测试成功"}'
    res, err = await _call_llm_json(system_prompt, "ping", settings, timeout_s=15.0)
    if res:
        return {"ok": True, "provider": provider, "message": f"模型连接成功：{res.get('message', 'OK')}"}
    return {"ok": False, "provider": provider, "message": err or "连接失败"}


def _extract_email_amount(text: str) -> Optional[float]:
    patterns = [
        r"(?:人民币|RMB|CNY|¥|￥)\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        r"(?:金额|应还|还款|实付|扣款|支出|消费|入账|合计|总计|费用)[：:\s]*(?:人民币|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:元)?",
        r"([0-9][0-9,]*(?:\.\d{1,2})?)\s*元",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if val > 0:
                    return round(val, 2)
            except ValueError:
                continue
    return None


def _local_parse_email(subject: str, sender: str, received_at: str, body_text: str) -> Dict[str, Any]:
    now = datetime.now().astimezone()
    combined = f"{subject}\n{body_text}"
    transactions: List[Dict[str, Any]] = []
    reminders: List[Dict[str, Any]] = []

    is_credit_bill = any(k in combined for k in ("信用卡", "账单", "应还金额", "最后还款日", "还款日"))
    is_utility = any(k in combined for k in ("水费", "电费", "燃气费", "物业费", "宽带", "话费账单"))
    is_itinerary = any(k in combined for k in ("行程单", "出票成功", "登机", "列车", "高铁", "航班", "入住"))
    is_receipt = any(k in combined for k in ("付款成功", "支付成功", "消费通知", "扣款成功", "电子发票", "订单确认", "入账通知", "工资条"))
    is_todo = any(k in combined for k in ("截止", "请于", "提醒", "待办", "确认", "到期", "续费"))

    amount = _extract_email_amount(combined)
    due_at = _parse_chinese_datetime(combined, now)

    if is_credit_bill or is_utility:
        bill_title = subject.strip()[:60] or "邮件账单缴费提醒"
        reminders.append(
            {
                "title": bill_title,
                "description": f"来自邮件（{sender}）：{body_text.strip()[:160]}",
                "due_at": due_at,
                "priority": "high",
                "category": "finance",
                "source_type": "email",
                "source_ref": f"邮件: {subject[:60]}",
                "linked_amount": amount,
                "linked_tx_type": "expense" if amount else None,
            }
        )
    elif is_itinerary:
        reminders.append(
            {
                "title": subject.strip()[:80] or "出行行程提醒",
                "description": f"来自邮件（{sender}）：{body_text.strip()[:160]}",
                "due_at": due_at,
                "priority": "high",
                "category": "life",
                "source_type": "email",
                "source_ref": f"邮件: {subject[:60]}",
                "linked_amount": amount,
                "linked_tx_type": "expense" if amount else None,
            }
        )
    elif is_todo:
        reminders.append(
            {
                "title": subject.strip()[:80] or "邮件待办提醒",
                "description": f"来自邮件（{sender}）：{body_text.strip()[:160]}",
                "due_at": due_at,
                "priority": "medium",
                "category": _detect_reminder_category(combined),
                "source_type": "email",
                "source_ref": f"邮件: {subject[:60]}",
                "linked_amount": amount,
                "linked_tx_type": "expense" if amount else None,
            }
        )

    if is_receipt and amount and not is_credit_bill:
        is_income = any(k in combined for k in ("入账", "工资", "薪资", "退款到账", "收款"))
        tx_type = "income" if is_income else "expense"
        occ = (received_at or now.strftime("%Y-%m-%d %H:%M"))[:16]
        transactions.append(
            {
                "type": tx_type,
                "amount": amount,
                "category": _detect_tx_category(combined, tx_type),
                "description": f"{subject.strip()[:100]} ({sender[:40]})".strip(),
                "occurred_at": occ,
                "source_type": "email",
            }
        )

    return {"transactions": transactions, "reminders": reminders}


async def extract_from_email_content(
    subject: str,
    sender: str,
    received_at: str,
    body_text: str,
    settings: Dict[str, str],
) -> Dict[str, Any]:
    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    system_prompt = f"""你是用户的个人邮件助理。请分析以下邮件内容，识别是否包含：
1. 需要用户处理的提醒事项（如信用卡最后还款日、水电缴费通知、机票高铁行程、会议/工作截止日期、续费提醒等）
2. 已经真实发生、需要记账的收支流水（如消费电子小票、扣款通知、工资/报销入账通知；注意：信用卡账单汇总不要重复当作单笔消费入账，而是生成还款提醒）

如果只是普通广告、验证码、系统推广垃圾邮件，请返回空数组。当前时间：{now_str}。
严格返回 JSON：
{{
  "transactions": [
    {{
      "type": "expense | income",
      "amount": 100.0,
      "category": "餐饮 | 交通 | 购物 | 住房 | 通讯 | 订阅服务 | 医疗 | 娱乐 | 工资收入 | 理财收益 | 其他",
      "description": "简短说明",
      "occurred_at": "YYYY-MM-DD HH:MM"
    }}
  ],
  "reminders": [
    {{
      "title": "提醒标题",
      "description": "补充说明",
      "due_at": "YYYY-MM-DDTHH:MM 或 null",
      "priority": "high | medium | low",
      "category": "work | life | finance | health | study",
      "linked_amount": 数值或null,
      "linked_tx_type": "expense | income | null"
    }}
  ]
}}"""
    user_prompt = (
        f"主题: {subject}\n发件人: {sender}\n时间: {received_at}\n正文:\n{body_text[:2500]}"
    )

    llm_res, warning = await _call_llm_json(system_prompt, user_prompt, settings, timeout_s=30.0)
    if llm_res and isinstance(llm_res, dict):
        txs = llm_res.get("transactions") or []
        rems = llm_res.get("reminders") or []
        for t in txs:
            t["source_type"] = "email"
        for r in rems:
            r["source_type"] = "email"
            r["source_ref"] = f"邮件: {subject[:60]}"
        return {"transactions": txs, "reminders": rems, "engine": settings.get("ai_provider"), "warning": None}

    local = _local_parse_email(subject, sender, received_at, body_text)
    local["engine"] = "local_fallback"
    local["warning"] = warning
    return local
