import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .proxy_service import build_httpx_kwargs


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
            async with httpx.AsyncClient(**build_httpx_kwargs(timeout_s, "ai_use_proxy", settings)) as client:
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
            async with httpx.AsyncClient(**build_httpx_kwargs(timeout_s, "ai_use_proxy", settings)) as client:
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


_FX_TO_CNY: Dict[str, float] = {
    "CNY": 1.0,
    "RMB": 1.0,
    "HKD": 0.92,
    "USD": 7.25,
    "SGD": 5.40,
    "EUR": 7.80,
    "GBP": 9.15,
    "JPY": 0.048,
    "AUD": 4.70,
    "CAD": 5.15,
}


_CREDIT_LIMIT_CONTEXT_RE = re.compile(
    r"(?:信用额度|个人消费额度|固定额度|临时额度|可用额度|信用总额度|预借现金额度|取现额度|分期额度|授信额度|最高额度|最低还款额?)[：:\s￥¥RMB人民币CNY]*$",
    re.IGNORECASE,
)


def _extract_email_amount_and_currency(text: str, exclude_credit_limit: bool = False) -> Tuple[Optional[float], str, Optional[float]]:
    """Return (cny_amount, currency_code, original_amount)."""
    currency_patterns = [
        # High-priority explicit bill/payment keywords FIRST (so 应还金额 beats earlier 信用额度!)
        (
            "CNY",
            r"(?:本期应还金额|本期账单金额|人民币应还总额|应还总额|应还款额|剩余应还|本期应还|消费金额|交易金额|支付金额|扣款金额|支出金额|实付金额)[：:\s\r\n]*(?:人民币|RMB|CNY|¥|￥)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:元)?",
        ),
        ("HKD", r"(?:HKD|HK\$|港币|港元)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("HKD", r"([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:HKD|港币|港元)"),
        ("USD", r"(?:USD|US\$|美元)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("USD", r"([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:USD|美元)"),
        ("SGD", r"(?:SGD|S\$|新币|新加坡元)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("EUR", r"(?:EUR|€|欧元)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("GBP", r"(?:GBP|£|英镑)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("JPY", r"(?:JPY|日元)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("CNY", r"(?:人民币|RMB|CNY|¥|￥)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        (
            "CNY",
            r"(?:金额|应还|还款|实付|扣款|支出|消费|入账|合计|总计|费用)[：:\s\r\n]*(?:人民币|RMB|CNY|¥|￥)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:元)?",
        ),
        ("CNY", r"([0-9][0-9,]*(?:\.\d{1,2})?)\s*元"),
        ("USD", r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
    ]
    for code, pat in currency_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            if exclude_credit_limit:
                prefix_ctx = text[max(0, m.start() - 28) : m.start()]
                if _CREDIT_LIMIT_CONTEXT_RE.search(prefix_ctx):
                    continue
            try:
                orig = float(m.group(1).replace(",", ""))
                if orig > 0:
                    rate = _FX_TO_CNY.get(code, 1.0)
                    cny = round(orig * rate, 2)
                    return cny, code, round(orig, 2)
            except ValueError:
                continue
    return None, "CNY", None


def _extract_credit_bill_metadata(text: str, now: datetime) -> Dict[str, Any]:
    """Extract statement bill due amount, due date, credit limit, and card last4 cleanly (ignoring credit limits & billing period start dates)."""
    bill_amount: Optional[float] = None
    credit_limit: Optional[float] = None
    due_at: Optional[str] = None
    last4 = ""

    # 1. Credit limit (kept separate from bill amount!)
    m_lim = re.search(
        r"(?:信用额度|个人消费额度|固定额度|信用总额度)[：:\s\t]*(?:人民币|RMB|CNY|¥|￥)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if m_lim:
        try:
            credit_limit = round(float(m_lim.group(1).replace(",", "")), 2)
        except ValueError:
            pass

    # 2. Tabular summary rows (e.g. CGB/CMB/CEB/ICBC: "卡号末四位\t本期账单金额\t最低还款额\t最后还款日" followed by value row)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines[:-1]):
        if "\t" not in ln:
            continue
        hdrs = [c.strip() for c in ln.split("\t")]
        has_bill_hdr = any(any(k in h for k in ("本期账单金额", "本期应还", "应还款额", "人民币应还", "账单金额")) for h in hdrs)
        has_due_hdr = any(any(k in h for k in ("最后还款日", "到期还款日", "还款日")) for h in hdrs)
        if not (has_bill_hdr or has_due_hdr):
            continue
        # Look at next 1..3 tab rows for the values
        for nxt in lines[i + 1 : i + 4]:
            if "\t" not in nxt:
                continue
            vals = [c.strip() for c in nxt.split("\t")]
            for col_i, h in enumerate(hdrs):
                if col_i >= len(vals):
                    continue
                v = vals[col_i]
                if bill_amount is None and any(k in h for k in ("本期账单金额", "本期应还", "应还款额", "人民币应还", "账单金额")) and "最低" not in h:
                    m_v = re.search(r"([0-9][0-9,]*(?:\.\d{1,2})?)", v)
                    if m_v:
                        try:
                            val = round(float(m_v.group(1).replace(",", "")), 2)
                            if val > 0:
                                bill_amount = val
                        except ValueError:
                            pass
                if due_at is None and any(k in h for k in ("最后还款日", "到期还款日", "还款日")):
                    dt_parsed = _parse_chinese_datetime(v, now)
                    if dt_parsed:
                        due_at = dt_parsed
                if not last4 and any(k in h for k in ("卡号末四位", "卡号尾号", "卡号")):
                    m_l4 = re.search(r"(\d{4})\b", v)
                    if m_l4:
                        last4 = m_l4.group(1)
            if bill_amount is not None or due_at is not None:
                break

    # 3. Inline explicit due-amount patterns (strictly excluding 信用额度 & 最低还款额)
    if bill_amount is None:
        m_bill = re.search(
            r"(?:本期应还金额|本期应还总额|本期账单金额|人民币应还总额|人民币本期应还|应还总额|应还款额|剩余应还|本期应还)[：:\s\t\r\n]*(?:人民币|RMB|CNY|¥|￥)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
            text,
            re.IGNORECASE,
        )
        if m_bill:
            try:
                val = round(float(m_bill.group(1).replace(",", "")), 2)
                if val > 0:
                    bill_amount = val
            except ValueError:
                pass

    # 4. Inline explicit due-date patterns (ignoring 账单周期 / 账单日!)
    if due_at is None:
        m_due = re.search(
            r"(?:最后还款日|到期还款日|还款截止日|还款日)[：:\s\t\r\n]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?|\d{1,2}[月/-]\d{1,2}[日号]?)",
            text,
        )
        if m_due:
            due_at = _parse_chinese_datetime(m_due.group(1), now)

    return {
        "bill_amount": bill_amount,
        "credit_limit": credit_limit,
        "due_at": due_at,
        "last4": last4,
    }


def _extract_email_amount(text: str) -> Optional[float]:
    cny, _, _ = _extract_email_amount_and_currency(text)
    return cny


def _extract_email_merchant(text: str) -> str:
    patterns = [
        r"(?:消费商户|交易商户|收款方|商户名称|商户|商家|Merchant)[：:\s]+([^\r\n]{2,80})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip(" ：:\t-")
            if val:
                return val[:60]
    return ""


def _extract_email_card_hint(text: str) -> str:
    patterns = [
        r"([\u4e00-\u9fa5]{2,10}?(?:银行)?\s*(?:信用卡|贷记卡|储蓄卡|借记卡)\s*(?:[（(]\s*(?:尾号)?\s*\d{4}\s*[）)]|尾号\s*[是为:：]?\s*\d{4}))",
        r"([A-Za-z0-9\u4e00-\u9fa5]{2,20}\s*Card\s*[（(]\d{4}[）)])",
        r"([\u4e00-\u9fa5]{2,10}?(?:银行)?\s*尾号\s*[是为:：]?\s*\d{4})",
        r"(尾号\s*[是为:：]?\s*\d{4})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


_CCY_SHORT_MAP: Dict[str, str] = {
    "CN": "CNY",
    "CNY": "CNY",
    "RMB": "CNY",
    "人民币": "CNY",
    "¥": "CNY",
    "￥": "CNY",
    "HK": "HKD",
    "HKD": "HKD",
    "港币": "HKD",
    "港元": "HKD",
    "US": "USD",
    "USD": "USD",
    "美元": "USD",
    "$": "USD",
    "EU": "EUR",
    "EUR": "EUR",
    "欧元": "EUR",
    "GB": "GBP",
    "GBP": "GBP",
    "英镑": "GBP",
    "JP": "JPY",
    "JPY": "JPY",
    "日元": "JPY",
    "SG": "SGD",
    "SGD": "SGD",
    "AU": "AUD",
    "AUD": "AUD",
    "CA": "CAD",
    "CAD": "CAD",
}


def _parse_statement_date_token(tok: str, base_year: int, ref_month: int, allow_mmdd: bool = False) -> Optional[str]:
    s = (tok or "").strip()
    if not s:
        return None
    # YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    m_full = re.match(r"^(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?:\s+(\d{1,2}:\d{2}))?$", s)
    if m_full:
        yy, mm, dd = int(m_full.group(1)), int(m_full.group(2)), int(m_full.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            tm = m_full.group(4) or "12:00"
            return f"{yy:04d}-{mm:02d}-{dd:02d} {tm}"
    # YYYYMMDD (8 digits)
    m_ymd8 = re.match(r"^(20\d{2})(\d{2})(\d{2})$", s)
    if m_ymd8:
        yy, mm, dd = int(m_ymd8.group(1)), int(m_ymd8.group(2)), int(m_ymd8.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"{yy:04d}-{mm:02d}-{dd:02d} 12:00"
    # MM/DD or MM-DD
    m_md = re.match(r"^(\d{1,2})[-/](\d{1,2})$", s)
    if m_md:
        mm, dd = int(m_md.group(1)), int(m_md.group(2))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            yr = base_year - 1 if (ref_month <= 2 and mm >= 11) else base_year
            return f"{yr:04d}-{mm:02d}-{dd:02d} 12:00"
    # MMDD (4 digits, common in CMB statements like 1112 / 1025)
    if allow_mmdd and re.match(r"^\d{4}$", s):
        mm, dd = int(s[:2]), int(s[2:])
        if 1 <= mm <= 12 and 1 <= dd <= 31 and not (2020 <= int(s) <= 2035):
            yr = base_year - 1 if (ref_month <= 2 and mm >= 11) else base_year
            return f"{yr:04d}-{mm:02d}-{dd:02d} 12:00"
    return None


def _extract_amount_from_cell(cell: str) -> Optional[Tuple[float, str, bool]]:
    """Return (signed_val, currency_code, is_credit_marker) if cell looks like a money amount."""
    s = (cell or "").strip()
    if not s:
        return None
    # Reject dates or bare 4-digit card numbers (must have decimal .XX)
    m = re.match(
        r"^(?:(HKD|USD|EUR|GBP|SGD|JPY|AUD|CAD|CNY|RMB|人民币|港币|港元|美元|¥|￥|\$)\s*)?"
        r"([+-]?\s*\d[\d,]*\.\d{2})"
        r"(?:\s*(CR|DR|存入|退货))?"
        r"(?:\s*[（(]\s*([A-Za-z]{2,3})\s*[）)])?"
        r"(?:\s*(HKD|USD|EUR|GBP|SGD|JPY|AUD|CAD|CNY|RMB|元))?$",
        s,
        re.IGNORECASE,
    )
    if not m:
        return None
    raw_pre = (m.group(1) or "").strip()
    raw_num = m.group(2).replace(",", "").replace(" ", "")
    cr_flag = (m.group(3) or "").upper() in ("CR", "存入", "退货")
    raw_paren = (m.group(4) or "").strip().upper()
    raw_post = (m.group(5) or "").strip()
    try:
        val = float(raw_num)
    except ValueError:
        return None
    if abs(val) <= 0:
        return None
    if cr_flag and val > 0:
        val = -val
    ccy_key = raw_paren or raw_pre.upper() or raw_post.upper()
    if raw_pre in _CCY_SHORT_MAP:
        ccy = _CCY_SHORT_MAP[raw_pre]
    elif ccy_key in _CCY_SHORT_MAP:
        ccy = _CCY_SHORT_MAP[ccy_key]
    else:
        ccy = "CNY"
    return val, ccy, cr_flag


def _extract_credit_bill_statement_lines(
    subject: str,
    sender: str,
    body_text: str,
    base_year: int,
    default_card_hint: str,
    ref_month: int = 12,
    default_last4: str = "",
) -> List[Dict[str, Any]]:
    """Extract individual card swipe rows from CGB / CMB / CEB / ICBC / general credit card e-statements."""
    from .reconciliation_service import parse_payment_method_info

    header_info = parse_payment_method_info(
        payment_method=f"{subject} {default_card_hint} {sender}".strip(),
        source_type="email",
        description=subject,
    )
    current_bank = header_info["bank_name"]
    if not current_bank:
        s_low = f"{subject} {sender}".lower()
        for cue, bname in (
            ("cgbchina", "广发银行"),
            ("广发", "广发银行"),
            ("cmbchina", "招商银行"),
            ("招商", "招商银行"),
            ("cebbank", "光大银行"),
            ("光大", "光大银行"),
            ("icbc", "工商银行"),
            ("工商", "工商银行"),
            ("ccb", "建设银行"),
            ("建设", "建设银行"),
            ("ecitic", "中信银行"),
            ("中信", "中信银行"),
            ("spdb", "浦发银行"),
            ("浦发", "浦发银行"),
            ("bankcomm", "交通银行"),
            ("交通", "交通银行"),
            ("pingan", "平安银行"),
            ("平安", "平安银行"),
            ("cib", "兴业银行"),
            ("兴业", "兴业银行"),
        ):
            if cue in s_low:
                current_bank = bname
                break

    current_last4 = (default_last4 or "").strip() or header_info["card_last4"]
    items: List[Dict[str, Any]] = []
    skip_words = (
        "本期应还",
        "最低还款",
        "信用额度",
        "可用额度",
        "上期账单",
        "上期余额",
        "本期账单",
        "账单日",
        "最后还款日",
        "到期还款日",
        "合计",
        "小计",
        "总计",
        "汇总",
        "自动还款",
        "网银还款",
        "购汇还款",
        "约定还款",
        "支付宝还款",
        "微信还款",
        "跨行还款",
        "快捷还款",
        "还款入账",
        "本期还款",
        "自动扣款还款",
        "他行还款",
        "手机银行还款",
        "交易日期",
        "记账日期",
        "入账日期",
        "交易摘要",
        "人民币金额",
        "卡号末四位",
        "交易地金额",
        "本期积分",
        "积分余额",
        "温馨提示",
        "免息期",
        "账单分期",
    )

    raw_lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]

    # Merge wrapped lines if a plain-text bank email split a single row across consecutive lines
    merged_lines: List[str] = []
    idx = 0
    while idx < len(raw_lines):
        ln = raw_lines[idx]
        # If line is ONLY a date (no tabs and no amount), try merging with following 1..5 non-date lines
        if "\t" not in ln and _parse_statement_date_token(ln, base_year, ref_month, allow_mmdd=True):
            buf = [ln]
            j = idx + 1
            seen_amt = False
            while j < len(raw_lines) and (j - idx) <= 6:
                nxt = raw_lines[j]
                if "\t" in nxt:
                    break
                if len(buf) >= 2 and _parse_statement_date_token(nxt, base_year, ref_month, allow_mmdd=True):
                    # Second date right after first date = posting date; after merchant/amount = next row!
                    if seen_amt or len(buf) > 2:
                        break
                buf.append(nxt)
                if re.search(r"\d+\.\d{2}", nxt):
                    seen_amt = True
                j += 1
            if seen_amt and len(buf) >= 3:
                merged_lines.append("\t".join(buf))
                idx = j
                continue
        merged_lines.append(ln)
        idx += 1

    for raw_line in merged_lines:
        flat_line = re.sub(r"\s+", " ", raw_line).strip()
        if len(flat_line) < 6:
            continue

        # Detect card section headers like "卡号：6250****3955" / "尾号3955" / "6225********9613"
        m_sec_card = re.search(
            r"(?:卡号|尾号|末四位|主卡|附卡)[^\d]{0,12}(?:\d{4,6}[*xX\s]{2,12})?(\d{4})\b",
            flat_line,
        )
        if not m_sec_card:
            m_sec_card = re.search(r"\b\d{4,6}[*xX]{3,10}(\d{4})\b", flat_line)
        if m_sec_card and not re.search(r"\d+\.\d{2}", flat_line):
            cand_l4 = m_sec_card.group(1)
            if not (2020 <= int(cand_l4) <= 2035):
                current_last4 = cand_l4
            continue

        if any(w in flat_line for w in skip_words):
            continue

        # Split into structured cells
        if "\t" in raw_line:
            cells = [c.strip() for c in raw_line.split("\t") if c.strip()]
        else:
            cells = flat_line.split(" ")

        if len(cells) < 3:
            continue

        # Check if line starts with 1 or 2 date tokens
        is_tab = "\t" in raw_line
        d1 = _parse_statement_date_token(cells[0], base_year, ref_month, allow_mmdd=is_tab or (len(cells) >= 4 and bool(_parse_statement_date_token(cells[1], base_year, ref_month, allow_mmdd=True))))
        if not d1:
            # Handle combined "YYYY-MM-DD HH:MM" split by spaces
            if len(cells) >= 4 and re.match(r"^\d{1,2}:\d{2}$", cells[1]):
                d1 = _parse_statement_date_token(f"{cells[0]} {cells[1]}", base_year, ref_month, allow_mmdd=False)
                rest_cells = cells[2:] if d1 else []
            else:
                continue
        else:
            start_i = 1
            # Skip optional posting date cell (e.g. 交易日 + 记账日)
            if start_i < len(cells) and _parse_statement_date_token(cells[start_i], base_year, ref_month, allow_mmdd=True):
                start_i += 1
            elif start_i < len(cells) and re.match(r"^\d{1,2}:\d{2}$", cells[start_i]):
                d1 = f"{d1[:10]} {cells[start_i]}"
                start_i += 1
            rest_cells = cells[start_i:]

        if not d1 or not rest_cells:
            continue

        # Scan remaining cells for amounts, currency tags, card last4, and merchant description
        amounts_found: List[Tuple[float, str, bool]] = []
        row_last4 = current_last4
        merchant_parts: List[str] = []
        pending_ccy = ""

        for cell in rest_cells:
            c_clean = cell.strip()
            if not c_clean:
                continue
            # Pure currency marker cell (e.g. "RMB", "CNY", "人民币", "HKD")
            if c_clean.upper() in _CCY_SHORT_MAP or c_clean in _CCY_SHORT_MAP:
                pending_ccy = _CCY_SHORT_MAP.get(c_clean, _CCY_SHORT_MAP.get(c_clean.upper(), "CNY"))
                continue
            # Pure 4-digit card last4 or masked card number like ****3955 / 尾号9613
            m_l4_cell = re.match(r"^(?:尾号|[*xX]{2,}|\d{4,6}[*xX]{2,})?(\d{4})$", c_clean)
            if m_l4_cell and not (2020 <= int(m_l4_cell.group(1)) <= 2035):
                row_last4 = m_l4_cell.group(1)
                continue
            # Decimal amount cell
            amt_parsed = _extract_amount_from_cell(c_clean)
            if amt_parsed is not None:
                val, ccy, cr_flag = amt_parsed
                if ccy == "CNY" and pending_ccy:
                    ccy = pending_ccy
                pending_ccy = ""
                amounts_found.append((val, ccy, cr_flag))
                continue
            # Check if cell has trailing amount stuck to merchant e.g. "星巴克 38.50"
            m_stuck = re.search(r"(?:(HKD|USD|EUR|GBP|SGD|JPY|CNY|RMB|¥|￥)\s*)?(-?\d[\d,]*\.\d{2})\s*(CR)?\s*$", c_clean, re.IGNORECASE)
            if m_stuck and not amounts_found:
                sub_amt = _extract_amount_from_cell(m_stuck.group(0).strip())
                if sub_amt is not None:
                    prefix = c_clean[: m_stuck.start()].strip()
                    if prefix:
                        merchant_parts.append(prefix)
                    amounts_found.append(sub_amt)
                    continue
            merchant_parts.append(c_clean)

        if not amounts_found:
            continue

        merchant = re.sub(r"\s+", " ", " ".join(merchant_parts)).strip(" -|·")
        if len(merchant) < 2 or any(w in merchant for w in skip_words):
            continue
        # Skip generic repayment rows like "还款" / "网上还款" / "转入还款"
        if re.search(r"(?:^|[\s\-·])(?:自动|网银|手机|跨行|他行|支付宝|微信|快捷|约定|购汇|转账)?还款", merchant):
            continue

        # Select CNY billing amount + optional original foreign currency amount
        cny_candidates = [a for a in amounts_found if a[1] == "CNY"]
        fx_candidates = [a for a in amounts_found if a[1] != "CNY"]
        if fx_candidates and cny_candidates:
            signed_cny = cny_candidates[0][0]
            orig_amt = round(abs(fx_candidates[0][0]), 2)
            raw_ccy = fx_candidates[0][1]
            cny_amt = round(abs(signed_cny), 2)
            tx_type = "income" if signed_cny < 0 else "expense"
        elif fx_candidates:
            signed_val = fx_candidates[0][0]
            raw_ccy = fx_candidates[0][1]
            orig_amt = round(abs(signed_val), 2)
            cny_amt = round(orig_amt * _FX_TO_CNY.get(raw_ccy, 1.0), 2)
            tx_type = "income" if signed_val < 0 else "expense"
        else:
            signed_val = cny_candidates[0][0]
            raw_ccy = "CNY"
            orig_amt = round(abs(signed_val), 2)
            cny_amt = orig_amt
            tx_type = "income" if signed_val < 0 else "expense"

        if cny_amt <= 0:
            continue

        pm_str = (
            f"{current_bank}信用卡({row_last4})"
            if (current_bank and row_last4)
            else (f"信用卡({row_last4})" if row_last4 else (f"{current_bank}信用卡" if current_bank else "信用卡账单"))
        )
        pm_info = parse_payment_method_info(
            payment_method=pm_str,
            source_type="email",
            description=merchant,
            merchant=merchant,
            payment_channel="card_direct",
        )
        desc = merchant
        if pm_info["payment_method"]:
            desc = f"{merchant} · {pm_info['payment_method']}"
        if raw_ccy != "CNY":
            desc = f"{desc} · {raw_ccy} {orig_amt:.2f}"

        items.append(
            {
                "type": tx_type,
                "amount": cny_amt,
                "category": _detect_tx_category(merchant, tx_type),
                "description": desc[:200],
                "merchant": merchant[:100],
                "occurred_at": d1,
                "source_type": "email",
                "payment_channel": "card_direct",
                "payment_method": pm_info["payment_method"],
                "bank_name": pm_info["bank_name"] or current_bank,
                "card_last4": pm_info["card_last4"] or row_last4,
                "card_kind": "credit_card",
                "account_display_name": pm_info["account_display_name"] or pm_str,
                "original_currency": raw_ccy,
                "original_amount": orig_amt,
            }
        )

    return items


def _detect_email_broker(subject: str, sender: str, body_text: str) -> str:
    combined = f"{sender}\n{subject}\n{body_text[:4000]}".lower()
    for cues, label in (
        (("hsbc", "汇丰", "滙豐", "investdirect", "wayfoong"), "汇丰 (HSBC)"),
        (("bochk", "中银香港", "中銀香港"), "中银香港"),
        (("hang seng", "恒生", "恆生"), "恒生银行"),
        (("futu", "富途", "moomoo"), "富途证券"),
        (("tiger", "老虎"), "老虎证券"),
        (("charles schwab", "嘉信"), "嘉信理财"),
        (("interactive brokers", "ibkr", "盈透"), "盈透证券"),
        (("citic", "中信证券", "华泰", "国泰君安", "招商证券", "广发证券"), "A股券商"),
    ):
        if any(c in combined for c in cues):
            return label
    return "券商邮件"


def _infer_stock_market_currency(symbol: str, currency_hint: str = "", body_hint: str = "") -> Tuple[str, str, str]:
    s = (symbol or "").strip().upper()
    ccy = (currency_hint or "").strip().upper()
    if s.endswith(".HK"):
        num = re.sub(r"\D", "", s[:-3]).zfill(5)
        return "HK", "HKD", num
    if s.endswith((".SS", ".SH", ".SZ", ".BJ")):
        return "CN", "CNY", re.sub(r"\..+$", "", s)
    if s.endswith(".US"):
        return "US", "USD", s[:-3]
    if ccy in ("HKD", "港元", "港币") or (s.isdigit() and len(s) <= 5):
        return "HK", "HKD", s.zfill(5) if s.isdigit() else s
    if ccy in ("CNY", "RMB", "CNH", "人民币") or (s.isdigit() and len(s) == 6):
        return "CN", "CNY", s
    if re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", s):
        return "US", "USD", s
    if "hkd" in body_hint.lower() or "港元" in body_hint or "港币" in body_hint or "港股" in body_hint:
        return "HK", "HKD", s.zfill(5) if s.isdigit() else s
    return "HK" if s.isdigit() else "US", "HKD" if s.isdigit() else "USD", s


def _normalize_stock_trade_dict(raw: Dict[str, Any], default_broker: str, default_dt: str) -> Optional[Dict[str, Any]]:
    symbol = str(raw.get("symbol") or "").strip().upper()
    side_raw = str(raw.get("side") or "").strip().lower()
    if side_raw in ("buy", "b", "买入", "買入", "bought", "bot"):
        side = "buy"
    elif side_raw in ("sell", "s", "卖出", "賣出", "沽出", "sold", "sld"):
        side = "sell"
    else:
        return None
    try:
        price = round(float(str(raw.get("price") or 0).replace(",", "")), 4)
        qty = round(abs(float(str(raw.get("quantity") or 0).replace(",", ""))), 4)
    except ValueError:
        return None
    if not symbol or price <= 0 or qty <= 0:
        return None
    # Reject accidental years like 2026 treated as ticker without digits cue
    if symbol.isdigit() and len(symbol) == 4 and 2020 <= int(symbol) <= 2035 and not raw.get("name"):
        return None

    ccy_hint = str(raw.get("currency") or "").strip().upper()
    if ccy_hint in ("RMB", "人民币"):
        ccy_hint = "CNY"
    elif ccy_hint in ("港元", "港币", "HK$"):
        ccy_hint = "HKD"
    elif ccy_hint in ("美元", "US$"):
        ccy_hint = "USD"
    market_raw = str(raw.get("market") or "").strip().upper()
    inferred_mk, inferred_ccy, disp_sym = _infer_stock_market_currency(symbol, ccy_hint)
    market = market_raw if market_raw in ("CN", "HK", "US") else inferred_mk
    currency = ccy_hint if ccy_hint in ("CNY", "HKD", "USD") else ({"CN": "CNY", "HK": "HKD", "US": "USD"}.get(market, inferred_ccy))

    try:
        fee = round(abs(float(str(raw.get("fee") or 0).replace(",", ""))), 2)
    except ValueError:
        fee = 0.0
    try:
        tax = round(abs(float(str(raw.get("tax") or 0).replace(",", ""))), 2)
    except ValueError:
        tax = 0.0

    traded_at = str(raw.get("traded_at") or "").strip().replace("/", "-")
    if not traded_at:
        traded_at = default_dt
    elif len(traded_at) == 10:
        traded_at = f"{traded_at} 15:00"
    else:
        traded_at = traded_at[:16]

    name = str(raw.get("name") or "").strip() or disp_sym
    broker = str(raw.get("broker") or default_broker).strip() or default_broker
    note = str(raw.get("note") or f"{broker} 邮件同步").strip()
    return {
        "symbol": disp_sym,
        "name": name[:60],
        "market": market,
        "currency": currency,
        "side": side,
        "price": price,
        "quantity": qty,
        "fee": fee,
        "tax": tax,
        "broker": broker,
        "traded_at": traded_at,
        "note": note[:120],
    }


_MONTH_NAME_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_flexible_trade_date(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    m_ymd = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:[T\s]+(\d{1,2}):(\d{2}))?", s)
    if m_ymd:
        y, mo, d = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
        hh = int(m_ymd.group(4)) if m_ymd.group(4) else 15
        mm = int(m_ymd.group(5)) if m_ymd.group(5) else 0
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}"
    m_dmy_txt = re.search(r"\b(\d{1,2})[\s-]+([A-Za-z]{3,4})[\s-]+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m_dmy_txt:
        d = int(m_dmy_txt.group(1))
        mo = _MONTH_NAME_MAP.get(m_dmy_txt.group(2).lower())
        y = int(m_dmy_txt.group(3))
        hh = int(m_dmy_txt.group(4)) if m_dmy_txt.group(4) else 15
        mm = int(m_dmy_txt.group(5)) if m_dmy_txt.group(5) else 0
        if mo and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}"
    m_dmy = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m_dmy:
        d, mo, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        if mo > 12 and d <= 12:
            d, mo = mo, d
        hh = int(m_dmy.group(4)) if m_dmy.group(4) else 15
        mm = int(m_dmy.group(5)) if m_dmy.group(5) else 0
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}"
    return None


def _extract_stock_trades_from_email(
    subject: str,
    sender: str,
    received_at: str,
    body_text: str,
) -> List[Dict[str, Any]]:
    """Extract stock/ETF buy/sell executions from HSBC & general broker trade confirmation emails / PDFs."""
    combined = f"{subject}\n{body_text}"
    low = combined.lower()

    # Must look like a stock/securities trade confirmation rather than a credit card / cash receipt
    has_stock_cue = any(
        k in low
        for k in (
            "contract note",
            "trade confirmation",
            "trade advice",
            "e-advice",
            "eadvice",
            "order confirmation",
            "order executed",
            "execution",
            "stock trading",
            "securities",
            "equity",
            "hkex",
            "nasdaq",
            "nyse",
            "etf",
            "bought",
            "sold",
            "股票",
            "證券",
            "证券",
            "股份",
            "股數",
            "股数",
            "成交",
            "买卖",
            "買賣",
            "交易指示",
            "交易确认",
            "交易確認",
            "執行通知",
            "执行通知",
            "沽出",
            "買入",
            "买入",
            "卖出",
            "賣出",
            "investdirect",
        )
    )
    if not has_stock_cue:
        return []
    # Require explicit stock share/price/code cues so generic marketing emails don't trigger false positives
    has_trade_detail = bool(
        re.search(
            r"(?:股|shares?|units?|qty|quantity|@|成交|單價|单价|價格|价格|均價|均价|price|代號|代号|代碼|代码|編號|编号|號碼|号码|stock\s*no|stock\s*code|symbol|ticker|hk\$|hkd|us\$|usd|cny|rmb)",
            combined,
            re.IGNORECASE,
        )
    )
    if not has_trade_detail:
        return []

    broker = _detect_email_broker(subject, sender, body_text)
    default_dt = (
        _extract_email_exact_datetime(combined)
        or _parse_flexible_trade_date(combined)
        or (received_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))[:16]
    )
    results: List[Dict[str, Any]] = []
    seen_sigs: set = set()

    def _add_candidate(c: Optional[Dict[str, Any]]) -> None:
        if not c:
            return
        sig = (c["side"], c["symbol"], c["price"], c["quantity"], c["traded_at"][:10])
        if sig in seen_sigs:
            return
        seen_sigs.add(sig)
        results.append(c)

    # Optional parenthetical unit/currency suffix before colon/separator, e.g. "Price (HKD): 388.50"
    opt_unit = r"(?:\s*[（(][^）)\r\n]{1,24}[）)])?"
    sep = rf"{opt_unit}[：:\s\t]+"

    # Pattern 1: Structured key-value confirmation block (HSBC HK / CN email alerts & Contract Notes)
    kv_side = ""
    m_side = re.search(
        rf"(?:买卖方向|交易方向|交易类型|交易類型|交易類別|交易类别|買賣類別|买卖类别|指示类别|指示類別|買賣|买卖|Action|Order\s*Type|Transaction\s*Type|Buy\s*/\s*Sell|Direction|Side|Instruction|Type){sep}([^\r\n]+)",
        combined,
        re.IGNORECASE,
    )
    if m_side:
        sv = m_side.group(1).strip().lower()
        if any(k in sv for k in ("买入", "買入", "购入", "購入", "buy", "bought", "认购", "認購")):
            kv_side = "buy"
        elif any(k in sv for k in ("卖出", "賣出", "沽出", "出售", "sell", "sold")):
            kv_side = "sell"
        elif sv in ("b", "bot"):
            kv_side = "buy"
        elif sv in ("s", "sld"):
            kv_side = "sell"
    if not kv_side:
        # Strip legal disclaimer lines ("not a recommendation to buy or sell...") before scanning standalone words
        clean_for_side = "\n".join(
            ln for ln in combined.splitlines()
            if not any(
                d in ln.lower()
                for d in ("disclaimer", "not a recommendation", "buy or sell", "買入或賣出", "买入或卖出", "免責聲明", "免责声明", "重要提示", "risk warning")
            )
        )
        has_b = bool(re.search(r"(?:买入|買入|購入|购入|\bBought\b|\bBuy\b)", clean_for_side, re.IGNORECASE))
        has_s = bool(re.search(r"(?:卖出|賣出|沽出|\bSold\b|\bSell\b)", clean_for_side, re.IGNORECASE))
        if has_b and not has_s:
            kv_side = "buy"
        elif has_s and not has_b:
            kv_side = "sell"

    kv_sym = ""
    kv_name = ""
    # Code first: e.g. "股份編號：03152 (A博時港元)" or "Stock Code: 00700.HK" (must NOT match leading 'A' of 'A博時港元')
    sym_label = r"(?:股票代号|股票代码|股份代号|股份代號|證券代號|证券代码|股份編號|股票編號|證券編號|股份编号|股票编号|证券编号|代號|代号|代碼|代码|編號|编号|Stock\s*Code|Stock\s*No\.?|Stock\s*Number|Security\s*Code|Ticker|Symbol|Instrument)(?:\s*/\s*(?:名稱|名称|Name|Code|No\.?))?"
    m_sym = re.search(
        rf"{sym_label}{sep}(?:HKEX\s*:?\s*|HK\s*:?\s*)?([A-Za-z0-9]{{1,6}}(?:\.[A-Za-z]{{2}})?)(?=$|[\s,.;:()（）])(?:\s*HK)?(?:[\s（(]+([^\r\n）)]+)[）)]?)?",
        combined,
        re.IGNORECASE,
    )
    if m_sym:
        cand_sym = m_sym.group(1).strip().upper()
        # Reject years accidentally captured on date lines
        if not (cand_sym.isdigit() and len(cand_sym) == 4 and 2020 <= int(cand_sym) <= 2035):
            kv_sym = cand_sym
            kv_name = (m_sym.group(2) or "").strip()

    name_label = r"(?:股票名称|股份名称|股份名稱|證券名稱|证券名称|股票名稱|證券/股票名稱|股份名稱\s*/\s*編號|股份名称\s*/\s*编号|Stock\s*Name(?:\s*/\s*Code)?|Security\s*Name|Stock|Security)"
    m_name = re.search(
        rf"{name_label}{sep}([^\r\n\t]+)",
        combined,
        re.IGNORECASE,
    )
    if m_name:
        raw_nm = m_name.group(1).strip()
        # Check if field embeds code like "A博時港元 (03152)" or "騰訊控股 (00700)" or "03152 A博時港元"
        m_emb = re.search(r"[（(]\s*([0-9]{4,5}(?:\.HK)?|[0-9]{6}(?:\.(?:SS|SH|SZ))?|[A-Z]{1,5})\s*[）)]", raw_nm, re.IGNORECASE)
        if m_emb and not kv_sym:
            kv_sym = m_emb.group(1).upper()
            kv_name = raw_nm[: m_emb.start()].strip() or raw_nm
        else:
            m_lead = re.match(r"^([0-9]{4,5}(?:\.HK)?|[A-Z]{1,5})\s+(.+)$", raw_nm)
            if m_lead and not kv_sym:
                kv_sym = m_lead.group(1).upper()
                kv_name = m_lead.group(2).strip()
            elif not kv_name:
                kv_name = raw_nm
    if not kv_sym:
        # Also inspect value right after sym_label when formatted as "編號：A博時港元 (03152)"
        m_sym_line = re.search(rf"{sym_label}{sep}([^\r\n\t]+)", combined, re.IGNORECASE)
        if m_sym_line:
            line_val = m_sym_line.group(1).strip()
            m_emb2 = re.search(r"[（(]\s*([0-9]{4,5}(?:\.HK)?|[0-9]{6}(?:\.(?:SS|SH|SZ))?|[A-Z]{1,5})\s*[）)]", line_val, re.IGNORECASE)
            if m_emb2:
                kv_sym = m_emb2.group(1).upper()
                if not kv_name:
                    kv_name = line_val[: m_emb2.start()].strip() or line_val
    if not kv_sym:
        # Fallback: look for explicit code format like "00700.HK" or "(03152)" or "03152 (xxx)"
        m_hk = re.search(r"\b([0-9]{4,5}\.HK|[0-9]{6}\.(?:SS|SH|SZ))\b", combined, re.IGNORECASE)
        if m_hk:
            kv_sym = m_hk.group(1).upper()
        else:
            m_paren = re.search(r"([^\r\n\t()（）]{2,24})\s*[（(]\s*(0\d{3,4}|[1-9]\d{3,4}(?:\.HK)?|[A-Z]{1,5})\s*[）)]", combined)
            if m_paren and not (m_paren.group(2).isdigit() and 2020 <= int(m_paren.group(2)) <= 2035):
                kv_sym = m_paren.group(2).upper()
                if not kv_name:
                    kv_name = m_paren.group(1).strip()

    kv_qty = 0.0
    m_qty = re.search(
        rf"(?:成交股数|成交股數|成交数量|成交數量|已成交股數|已成交數量|買賣股數|买卖股数|買入股數|賣出股數|沽出股數|總股數|總數量|数量|數量|股数|股數|Executed\s*(?:Qty|Quantity)|Filled\s*(?:Qty|Quantity)|Order\s*(?:Qty|Quantity)|Exec\.?\s*Qty|Quantity|Qty|Shares|Units){sep}([0-9][0-9,]*(?:\.\d+)?)\s*(?:股|shares?|units?)?",
        combined,
        re.IGNORECASE,
    )
    if m_qty:
        kv_qty = float(m_qty.group(1).replace(",", ""))

    kv_price = 0.0
    kv_ccy = ""
    m_px = re.search(
        rf"(?:成交价格|成交價格|成交单价|成交單價|成交价|成交價|平均成交价|平均成交價|平均價|平均价|均價|均价|買入價|买入价|賣出價|卖出价|沽出價|沽出价|每股价格|每股價格|單價|单价|價格|价格|Avg\.?\s*Price|Average\s*(?:Execution\s*)?Price|Filled\s*Price|Exec(?:uted)?\s*Price|Trade\s*Price|Unit\s*Price|Price){sep}(HKD|HK\$|USD|US\$|CNY|RMB|¥|￥|港币|港元|美元)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(HKD|HK\$|USD|US\$|CNY|RMB|港元|美元)?",
        combined,
        re.IGNORECASE,
    )
    if m_px:
        kv_ccy = (m_px.group(1) or m_px.group(3) or "").strip().upper()
        kv_price = float(m_px.group(2).replace(",", ""))
    if not kv_ccy:
        m_ccy = re.search(rf"(?:结算货币|結算貨幣|交易货币|交易貨幣|货币|貨幣|幣種|币种|Currency|Ccy){sep}(HKD|USD|CNY|RMB|港币|港元|美元)", combined, re.IGNORECASE)
        if m_ccy:
            kv_ccy = m_ccy.group(1).upper()
        elif re.search(r"(?:HKD|HK\$|港元|港幣)", combined):
            kv_ccy = "HKD"
        elif re.search(r"(?:USD|US\$|美元)", combined):
            kv_ccy = "USD"

    kv_fee = 0.0
    m_fee = re.search(
        rf"(?:经纪佣金|經紀佣金|交易佣金|佣金|手續費|手续费|Commission|Brokerage(?:\s*Fee)?){sep}(?:HKD|HK\$|USD|US\$|CNY|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d+)?)",
        combined,
        re.IGNORECASE,
    )
    if m_fee:
        kv_fee = float(m_fee.group(1).replace(",", ""))

    kv_tax = 0.0
    for tax_pat in (
        rf"(?:政府印花税|政府印花稅|印花税|印花稅|Stamp\s*Duty){sep}(?:HKD|HK\$|USD|US\$|CNY|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d+)?)",
        rf"(?:证监会交易征费|證監會交易徵費|交易征费|交易徵費|SFC\s*Levy|AFRC\s*Levy){sep}(?:HKD|HK\$|USD|US\$|CNY|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d+)?)",
        rf"(?:交易所交易费|聯交所交易費|联交所交易费|Trading\s*Fee|Settlement\s*Fee|交收费|交收費){sep}(?:HKD|HK\$|USD|US\$|CNY|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d+)?)",
    ):
        for mt in re.finditer(tax_pat, combined, re.IGNORECASE):
            kv_tax += float(mt.group(1).replace(",", ""))

    kv_dt = default_dt
    m_tdt = re.search(
        rf"(?:成交时间|成交時間|交易时间|交易時間|成交日期|交易日期|Trade\s*Date|Execution\s*Date|Execution\s*Time|Order\s*Date){sep}([^\r\n]+)",
        combined,
        re.IGNORECASE,
    )
    if m_tdt:
        parsed_dt = _parse_flexible_trade_date(m_tdt.group(1))
        if parsed_dt:
            kv_dt = parsed_dt

    if kv_side and kv_sym and kv_qty > 0 and kv_price > 0:
        _add_candidate(
            _normalize_stock_trade_dict(
                {
                    "symbol": kv_sym,
                    "name": kv_name,
                    "currency": kv_ccy,
                    "side": kv_side,
                    "price": kv_price,
                    "quantity": kv_qty,
                    "fee": kv_fee,
                    "tax": kv_tax,
                    "broker": broker,
                    "traded_at": kv_dt,
                },
                broker,
                default_dt,
            )
        )

    # Pattern 2: Inline sentence & Contract Note / multi-row table lines (tab or multi-space delimited)
    line_patterns = (
        # Chinese inline code-first: 卖出 03152(A博时港元) 11 股 ... @ HKD 1085.50
        re.compile(
            r"(买入|買入|卖出|賣出|沽出|購入|购入)\s*(?:股票|股份)?\s*([A-Za-z0-9]{1,6}(?:\.[A-Za-z]{2})?)\s*(?:[（(]([^）)\r\n]{1,30})[）)])?\s+([0-9][0-9,]*(?:\.\d+)?)\s*股[^0-9\r\n]{0,40}?(?:成交价|成交價|价格|價格|單價|单价|@|每股)\s*(HKD|HK\$|USD|US\$|CNY|RMB|¥|￥|港元|美元)?\s*([0-9][0-9,]*(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        # Chinese inline name-first: 买入 腾讯控股 (00700) 100 股 ... @ HKD 388.50
        re.compile(
            r"(买入|買入|卖出|賣出|沽出|購入|购入)\s*(?:[0-9,]+\s*股\s*)?([^\s（()）]{2,20})\s*[（(]\s*([A-Za-z0-9]{1,6}(?:\.[A-Za-z]{2})?)\s*[）)]\s*([0-9][0-9,]*(?:\.\d+)?)\s*股[^0-9\r\n]{0,40}?(?:成交价|成交價|价格|價格|單價|单价|@|每股)\s*(HKD|HK\$|USD|US\$|CNY|RMB|¥|￥|港元|美元)?\s*([0-9][0-9,]*(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        # English inline code-first: Bought/Sold 100 [shares of] 00700 [(Tencent)] @ HKD 388.50
        re.compile(
            r"\b(Bought|Buy|Sold|Sell)\s+([0-9][0-9,]*(?:\.\d+)?)\s*(?:shares?\s*(?:of)?\s*)?([A-Za-z0-9]{1,6}(?:\.[A-Za-z]{2})?)\s*(?:[（(]([^）)\r\n]{1,30})[）)])?\s*(?:@|at|price:?)\s*(HKD|HK\$|USD|US\$|CNY|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        # English inline name-first: Bought/Sold 100 shares of TENCENT (00700) @ HKD 388.50
        re.compile(
            r"\b(Bought|Buy|Sold|Sell)\s+([0-9][0-9,]*(?:\.\d+)?)\s*(?:shares?\s*(?:of)?\s*)?([A-Za-z][A-Za-z0-9\s.&-]{1,35}?)\s*[（(]\s*([A-Za-z0-9]{1,6}(?:\.[A-Za-z]{2})?)\s*[）)]\s*(?:@|at|price:?)\s*(HKD|HK\$|USD|US\$|CNY|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    )

    for ln in combined.splitlines():
        clean_ln = re.sub(r"\s+", " ", ln).strip()
        if not clean_ln:
            continue
        ln_dt = _parse_flexible_trade_date(clean_ln) or kv_dt

        # Try Chinese inline code-first
        for m in line_patterns[0].finditer(clean_ln):
            _add_candidate(
                _normalize_stock_trade_dict(
                    {
                        "side": m.group(1),
                        "symbol": m.group(2),
                        "name": (m.group(3) or "").strip(),
                        "quantity": m.group(4),
                        "currency": m.group(5) or kv_ccy,
                        "price": m.group(6),
                        "fee": kv_fee if len(results) == 0 else 0.0,
                        "tax": kv_tax if len(results) == 0 else 0.0,
                        "broker": broker,
                        "traded_at": ln_dt,
                    },
                    broker,
                    default_dt,
                )
            )

        # Try Chinese inline name-first
        for m in line_patterns[1].finditer(clean_ln):
            _add_candidate(
                _normalize_stock_trade_dict(
                    {
                        "side": m.group(1),
                        "name": m.group(2).strip(),
                        "symbol": m.group(3),
                        "quantity": m.group(4),
                        "currency": m.group(5) or kv_ccy,
                        "price": m.group(6),
                        "fee": kv_fee if len(results) == 0 else 0.0,
                        "tax": kv_tax if len(results) == 0 else 0.0,
                        "broker": broker,
                        "traded_at": ln_dt,
                    },
                    broker,
                    default_dt,
                )
            )

        # Try English inline code-first
        for m in line_patterns[2].finditer(clean_ln):
            _add_candidate(
                _normalize_stock_trade_dict(
                    {
                        "side": m.group(1),
                        "quantity": m.group(2),
                        "symbol": m.group(3),
                        "name": (m.group(4) or "").strip(),
                        "currency": m.group(5) or kv_ccy,
                        "price": m.group(6),
                        "fee": kv_fee if len(results) == 0 else 0.0,
                        "tax": kv_tax if len(results) == 0 else 0.0,
                        "broker": broker,
                        "traded_at": ln_dt,
                    },
                    broker,
                    default_dt,
                )
            )

        # Try English inline name-first
        for m in line_patterns[3].finditer(clean_ln):
            _add_candidate(
                _normalize_stock_trade_dict(
                    {
                        "side": m.group(1),
                        "quantity": m.group(2),
                        "name": m.group(3).strip(),
                        "symbol": m.group(4),
                        "currency": m.group(5) or kv_ccy,
                        "price": m.group(6),
                        "fee": kv_fee if len(results) == 0 else 0.0,
                        "tax": kv_tax if len(results) == 0 else 0.0,
                        "broker": broker,
                        "traded_at": ln_dt,
                    },
                    broker,
                    default_dt,
                )
            )

        # Try tab-separated (HTML) OR multi-space-separated (PDF Contract Note) table rows
        cells: List[str] = []
        if "\t" in ln:
            cells = [c.strip() for c in ln.split("\t") if c.strip()]
        elif "  " in ln.strip():
            cells = [c.strip() for c in re.split(r"\s{2,}", ln.strip()) if c.strip()]
        if len(cells) >= 4:
            row_side = ""
            row_sym = ""
            row_name = ""
            nums: List[Tuple[float, str]] = []
            for c in cells:
                cl = c.lower()
                if not row_side and cl in ("buy", "bought", "bot", "b", "买入", "買入", "購入", "购入"):
                    row_side = "buy"
                    continue
                if not row_side and cl in ("sell", "sold", "sld", "s", "卖出", "賣出", "沽出", "出售"):
                    row_side = "sell"
                    continue
                dt_cell = _parse_flexible_trade_date(c)
                if dt_cell:
                    ln_dt = dt_cell
                    continue
                if not row_sym and re.fullmatch(r"(?:[0-9]{4,5}(?:\.HK)?|[0-9]{6}(?:\.(?:SS|SH|SZ))?|[A-Z]{1,5})", c.upper()):
                    # Do not mistake a 4-digit year for ticker unless it looks like HK code with leading 0
                    if not (c.isdigit() and len(c) == 4 and 2020 <= int(c) <= 2035):
                        row_sym = c.upper()
                        continue
                m_num = re.match(r"^(HKD|HK\$|USD|US\$|CNY|RMB|¥|￥)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:股|shares?|units?)?$", c, re.IGNORECASE)
                if m_num:
                    nums.append((float(m_num.group(2).replace(",", "")), (m_num.group(1) or "").upper()))
                    continue
                if not row_name and len(c) >= 2 and not re.match(r"^[\d.,]+$", c):
                    row_name = c
            if row_side and row_sym and len(nums) >= 2:
                q_val, _ = nums[0]
                p_val, p_ccy = nums[1]
                _add_candidate(
                    _normalize_stock_trade_dict(
                        {
                            "side": row_side,
                            "symbol": row_sym,
                            "name": row_name,
                            "quantity": q_val,
                            "price": p_val,
                            "currency": p_ccy or kv_ccy,
                            "broker": broker,
                            "traded_at": ln_dt,
                        },
                        broker,
                        default_dt,
                    )
                )

    return results


def _extract_email_exact_datetime(text: str) -> Optional[str]:
    m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2})", text)
    if m:
        raw = m.group(1).replace("/", "-")
        parts = raw.split()
        dparts = parts[0].split("-")
        tparts = parts[1].split(":")
        return f"{int(dparts[0]):04d}-{int(dparts[1]):02d}-{int(dparts[2]):02d} {int(tparts[0]):02d}:{int(tparts[1]):02d}"
    return None


def _local_parse_email(subject: str, sender: str, received_at: str, body_text: str) -> Dict[str, Any]:
    from .reconciliation_service import parse_payment_method_info

    now = datetime.now().astimezone()
    combined = f"{subject}\n{body_text}"
    transactions: List[Dict[str, Any]] = []
    reminders: List[Dict[str, Any]] = []
    stock_trades = _extract_stock_trades_from_email(subject, sender, received_at, body_text)

    is_credit_bill = any(
        k in combined
        for k in ("信用卡账单", "信用卡电子账单", "本期账单", "应还金额", "最后还款日", "到期还款日", "最低还款额", "电子对账单", "电子账单", "客户对账单", "Bank Statement")
    )
    is_utility = any(k in combined for k in ("水费", "电费", "燃气费", "物业费", "宽带", "话费账单"))
    is_itinerary = any(k in combined for k in ("行程单", "出票成功", "登机", "列车", "高铁", "航班", "入住"))
    is_receipt = any(
        k in combined.lower()
        for k in (
            "付款成功",
            "支付成功",
            "消费通知",
            "扣款成功",
            "电子发票",
            "订单确认",
            "入账通知",
            "工资条",
            "消费金额",
            "完成一笔以下消费",
            "消费商户",
            "交易金额",
            "支出金额",
            "已扣款",
            "扣账",
            "刷卡",
            "za card",
            "card (",
            "charged",
            "purchase",
            "payment received",
        )
    )
    is_todo = any(k in combined for k in ("截止", "请于", "提醒", "待办", "确认", "到期", "续费"))

    amount, currency, orig_amount = _extract_email_amount_and_currency(combined, exclude_credit_limit=is_credit_bill)
    due_at = _parse_chinese_datetime(combined, now)
    merchant = _extract_email_merchant(combined)
    card_hint = _extract_email_card_hint(combined)
    exact_dt = _extract_email_exact_datetime(combined)

    if is_credit_bill or is_utility:
        bill_title = subject.strip()[:60] or "邮件账单缴费提醒"
        rem_amount = amount
        rem_due_at = due_at
        rem_desc = f"来自邮件（{sender}）：{body_text.strip()[:160]}"
        if is_credit_bill:
            meta = _extract_credit_bill_metadata(combined, now)
            # For credit card bills, prefer explicit 本期应还/账单金额 & 最后还款日; NEVER use 信用额度!
            if meta["bill_amount"] is not None:
                rem_amount = meta["bill_amount"]
            elif meta["credit_limit"] is not None and rem_amount is not None and abs(rem_amount - meta["credit_limit"]) < 0.01:
                rem_amount = None
            if meta["due_at"] is not None:
                rem_due_at = meta["due_at"]
            summary_bits: List[str] = []
            if rem_amount is not None:
                summary_bits.append(f"本期应还 ¥{rem_amount:,.2f}")
            if meta["credit_limit"] is not None:
                summary_bits.append(f"卡片额度 ¥{meta['credit_limit']:,.2f}")
            if meta["last4"]:
                summary_bits.append(f"尾号 {meta['last4']}")
            if summary_bits:
                rem_desc = f"{' · '.join(summary_bits)} | 来自邮件（{sender}）"
        reminders.append(
            {
                "title": bill_title,
                "description": rem_desc,
                "due_at": rem_due_at,
                "priority": "high",
                "category": "finance",
                "source_type": "email",
                "source_ref": f"邮件: {subject[:60]}",
                "linked_amount": rem_amount,
                "linked_tx_type": "expense" if rem_amount else None,
            }
        )
        # Also extract individual line-item card spends from credit card statements for cross-reconciliation!
        if is_credit_bill:
            stmt_year = now.year
            stmt_month = now.month
            m_ym = re.search(r"(20\d{2})[-/年](\d{1,2})", received_at or subject or "")
            if m_ym:
                stmt_year = int(m_ym.group(1))
                stmt_month = int(m_ym.group(2))
            else:
                m_yr = re.search(r"(20\d{2})", received_at or subject or "")
                if m_yr:
                    stmt_year = int(m_yr.group(1))
            effective_hint = card_hint or (f"尾号{meta['last4']}" if meta.get("last4") else "")
            stmt_txs = _extract_credit_bill_statement_lines(
                subject=subject,
                sender=sender,
                body_text=body_text,
                base_year=stmt_year,
                default_card_hint=effective_hint,
                ref_month=stmt_month,
                default_last4=meta.get("last4") or "",
            )
            transactions.extend(stmt_txs)
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
    elif is_todo and not is_receipt:
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

    if is_receipt and amount and not is_credit_bill and not stock_trades:
        is_income = any(k in combined for k in ("入账", "工资", "薪资", "退款到账", "收款", "股息派发"))
        tx_type = "income" if is_income else "expense"
        occ = exact_dt or (received_at or now.strftime("%Y-%m-%d %H:%M"))[:16]
        pm_info = parse_payment_method_info(
            payment_method=card_hint or f"{subject} {sender}",
            source_type="email",
            description=f"{subject} {body_text[:240]}",
            merchant=merchant,
        )
        desc_parts: List[str] = []
        if merchant:
            desc_parts.append(merchant)
        else:
            desc_parts.append(subject.strip()[:80])
        if card_hint:
            desc_parts.append(card_hint)
        elif pm_info["payment_method"]:
            desc_parts.append(pm_info["payment_method"])
        if currency != "CNY" and orig_amount is not None:
            desc_parts.append(f"{currency} {orig_amount:.2f}")
        elif sender and not card_hint:
            desc_parts.append(sender[:36])
        transactions.append(
            {
                "type": tx_type,
                "amount": amount,
                "category": _detect_tx_category(combined, tx_type),
                "description": " · ".join(p for p in desc_parts if p),
                "merchant": merchant[:100],
                "occurred_at": occ,
                "source_type": "email",
                "payment_channel": pm_info["payment_channel"],
                "payment_method": pm_info["payment_method"],
                "bank_name": pm_info["bank_name"],
                "card_last4": pm_info["card_last4"],
                "card_kind": pm_info["card_kind"],
                "account_display_name": pm_info["account_display_name"],
                "original_currency": currency,
                "original_amount": orig_amount if orig_amount is not None else amount,
            }
        )

    return {"transactions": transactions, "reminders": reminders, "stock_trades": stock_trades}


async def extract_from_email_content(
    subject: str,
    sender: str,
    received_at: str,
    body_text: str,
    settings: Dict[str, str],
) -> Dict[str, Any]:
    from .reconciliation_service import parse_payment_method_info

    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    system_prompt = f"""你是用户的个人邮件助理。请分析以下邮件内容，识别是否包含：
1. 需要用户处理的提醒事项（如信用卡最后还款日、水电缴费通知、机票高铁行程、会议/工作截止日期、续费提醒等）
2. 已经真实发生、需要记入个人日常消费的收支流水：
   - 银行卡/众安银行 ZA Card/信用卡单笔刷卡消费通知、消费电子小票、扣款通知、App Store 订阅扣款、工资/报销/股息入账通知；
   - 若邮件是「信用卡月度电子账单」且正文里列出了逐笔消费明细，请把每一笔具体刷卡消费明细提取到 transactions 中，但绝对不要把「本期应还总额」当作单笔消费入账，而是生成一条还款提醒。
   - **特别注意**：信用卡账单还款提醒的 `linked_amount` 必须填写「本期应还金额 / 本期账单金额」，绝对不要填「信用额度 / 个人消费额度 / 固定额度 / 可用额度」；`due_at` 必须填写「最后还款日 / 到期还款日」。
   - 若是股票/ETF 买卖成交确认（如汇丰银行 HSBC 股票交易、证券成交单），请提取到 `stock_trades`，不要混入日常消费 `transactions`。
3. 证券/股票买卖成交通知（汇丰 HSBC / 中银 / 富途等券商邮件或成交单据）：提取到 `stock_trades`。

外币处理规则：
- 日常消费 `transactions` 若为外币请折算为人民币（HKD≈0.92，USD≈7.25）；`stock_trades` 保留原成交币种与价格。

如果只是普通广告、验证码、系统推广垃圾邮件，请返回空数组。当前时间：{now_str}。
严格返回 JSON：
{{
  "transactions": [
    {{
      "type": "expense | income",
      "amount": 100.0,
      "category": "餐饮 | 交通 | 购物 | 住房 | 通讯 | 订阅服务 | 医疗 | 娱乐 | 工资收入 | 理财收益 | 其他",
      "description": "简短说明（含商户、卡尾号与外币原金额）",
      "merchant": "商户名称",
      "bank_name": "银行名称或空字符串",
      "card_last4": "4位卡尾号或空字符串",
      "payment_method": "支付方式如 广发银行信用卡(3955) 或 ZA Card (0720)",
      "original_currency": "CNY | HKD | USD | ...",
      "original_amount": 100.0,
      "occurred_at": "YYYY-MM-DD HH:MM"
    }}
  ],
  "stock_trades": [
    {{
      "symbol": "股票代码如 03152 或 00700 或 NVDA",
      "name": "股票/ETF名称",
      "market": "HK | US | CN",
      "currency": "HKD | USD | CNY",
      "side": "buy | sell",
      "price": 1085.5,
      "quantity": 11,
      "fee": 18.0,
      "tax": 0.0,
      "broker": "汇丰 (HSBC) 等",
      "traded_at": "YYYY-MM-DD HH:MM"
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
        f"主题: {subject}\n发件人: {sender}\n时间: {received_at}\n正文:\n{body_text[:8000]}"
    )

    default_broker = _detect_email_broker(subject, sender, body_text)
    default_dt = _extract_email_exact_datetime(f"{subject}\n{body_text}") or (received_at or now_str)[:16]
    llm_res, warning = await _call_llm_json(system_prompt, user_prompt, settings, timeout_s=30.0)
    if llm_res and isinstance(llm_res, dict):
        txs = llm_res.get("transactions") or []
        rems = llm_res.get("reminders") or []
        raw_stk = llm_res.get("stock_trades") or []
        # Deterministic parsers handle full un-truncated HTML tables & structured broker notes!
        local_check = _local_parse_email(subject, sender, received_at, body_text)
        if len(local_check.get("transactions") or []) > len(txs):
            txs = local_check["transactions"]
        stock_trades: List[Dict[str, Any]] = []
        for s in raw_stk:
            norm = _normalize_stock_trade_dict(s, default_broker, default_dt)
            if norm:
                stock_trades.append(norm)
        if len(local_check.get("stock_trades") or []) > len(stock_trades):
            stock_trades = local_check["stock_trades"]

        for t in txs:
            t["source_type"] = "email"
            pm_info = parse_payment_method_info(
                payment_method=str(t.get("payment_method") or f"{t.get('bank_name') or ''} {t.get('card_last4') or ''}".strip()),
                source_type="email",
                description=f"{t.get('description') or ''} {subject}",
                merchant=str(t.get("merchant") or ""),
            )
            t["payment_channel"] = pm_info["payment_channel"]
            t["payment_method"] = t.get("payment_method") or pm_info["payment_method"]
            t["bank_name"] = t.get("bank_name") or pm_info["bank_name"]
            t["card_last4"] = t.get("card_last4") or pm_info["card_last4"]
            t["card_kind"] = pm_info["card_kind"]
            t["account_display_name"] = pm_info["account_display_name"]
            t.setdefault("merchant", "")
            t.setdefault("original_currency", "CNY")
            t.setdefault("original_amount", t.get("amount"))
        for r in rems:
            r["source_type"] = "email"
            r["source_ref"] = f"邮件: {subject[:60]}"
        return {
            "transactions": txs,
            "reminders": rems,
            "stock_trades": stock_trades,
            "engine": settings.get("ai_provider"),
            "warning": None,
        }

    local = _local_parse_email(subject, sender, received_at, body_text)
    local["engine"] = "local_fallback"
    local["warning"] = warning
    return local
