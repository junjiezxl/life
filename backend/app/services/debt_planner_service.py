import calendar
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


def _clamp_date(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, max(1, min(day, last_day)))


def _add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
    total = (year * 12 + (month - 1)) + delta
    return total // 12, (total % 12) + 1


def calc_swipe_interest_free_days(
    today: date,
    statement_day: int,
    due_day: int,
) -> Dict[str, Any]:
    """Calculate interest-free period if swiping today on a card."""
    if today.day <= statement_day:
        bill_dt = _clamp_date(today.year, today.month, statement_day)
    else:
        ny, nm = _add_months(today.year, today.month, 1)
        bill_dt = _clamp_date(ny, nm, statement_day)

    if due_day > statement_day:
        repay_dt = _clamp_date(bill_dt.year, bill_dt.month, due_day)
    else:
        ry, rm = _add_months(bill_dt.year, bill_dt.month, 1)
        repay_dt = _clamp_date(ry, rm, due_day)

    free_days = max(0, (repay_dt - today).days)

    # Also check what free days look like right after the upcoming statement day
    next_cycle_start = bill_dt + timedelta(days=1)
    ny2, nm2 = _add_months(bill_dt.year, bill_dt.month, 1)
    next_bill_dt = _clamp_date(ny2, nm2, statement_day)
    if due_day > statement_day:
        next_repay_dt = _clamp_date(next_bill_dt.year, next_bill_dt.month, due_day)
    else:
        ry2, rm2 = _add_months(next_bill_dt.year, next_bill_dt.month, 1)
        next_repay_dt = _clamp_date(ry2, rm2, due_day)
    next_cycle_free_days = max(0, (next_repay_dt - next_cycle_start).days)

    return {
        "swipe_bill_date": bill_dt.isoformat(),
        "swipe_repay_date": repay_dt.isoformat(),
        "swipe_free_days": free_days,
        "days_to_next_statement": (bill_dt - today).days,
        "is_statement_day": today.day == statement_day,
        "next_cycle_start_date": next_cycle_start.isoformat(),
        "next_cycle_free_days": next_cycle_free_days,
    }


def enrich_credit_card(card: Dict[str, Any], ref_date: Optional[date] = None) -> Dict[str, Any]:
    today = ref_date or datetime.now().astimezone().date()
    d = dict(card)
    limit = float(d.get("credit_limit") or 0.0)
    debt = max(0.0, float(d.get("current_debt") or 0.0))
    bill_amt = max(0.0, float(d.get("statement_bill_amount") or 0.0))
    paid_amt = max(0.0, float(d.get("paid_amount_this_cycle") or 0.0))
    stmt_day = int(d.get("statement_day") or 1)
    due_day = int(d.get("due_day") or 20)
    grace_days = max(0, int(d.get("grace_days") or 0))
    fee_rate = max(0.0, float(d.get("swipe_fee_rate") or 0.006))

    available = max(0.0, round(limit - debt, 2))
    utilization_pct = round((debt / limit) * 100, 1) if limit > 0 else 0.0
    remaining_bill = max(0.0, round(bill_amt - paid_amt, 2))

    due_this_month = _clamp_date(today.year, today.month, due_day)
    if due_this_month >= today:
        next_due = due_this_month
    else:
        ny, nm = _add_months(today.year, today.month, 1)
        next_due = _clamp_date(ny, nm, due_day)

    days_until_due = (next_due - today).days
    swipe_info = calc_swipe_interest_free_days(today, stmt_day, due_day)

    d.update(
        {
            "credit_limit": round(limit, 2),
            "current_debt": round(debt, 2),
            "available_credit": available,
            "utilization_pct": utilization_pct,
            "statement_bill_amount": round(bill_amt, 2),
            "paid_amount_this_cycle": round(paid_amt, 2),
            "remaining_bill": remaining_bill,
            "swipe_fee_rate": fee_rate,
            "next_due_date": next_due.isoformat(),
            "days_until_due": days_until_due,
            "grace_deadline_date": (next_due + timedelta(days=grace_days)).isoformat(),
            **swipe_info,
        }
    )
    return d


def enrich_loan(loan: Dict[str, Any], ref_date: Optional[date] = None) -> Dict[str, Any]:
    today = ref_date or datetime.now().astimezone().date()
    d = dict(loan)
    due_day = int(d.get("due_day") or 15)
    monthly = max(0.0, float(d.get("monthly_payment") or 0.0))
    paid = max(0.0, float(d.get("paid_this_month") or 0.0))
    remaining_month = max(0.0, round(monthly - paid, 2))

    due_this_month = _clamp_date(today.year, today.month, due_day)
    if due_this_month >= today:
        next_due = due_this_month
    else:
        ny, nm = _add_months(today.year, today.month, 1)
        next_due = _clamp_date(ny, nm, due_day)

    d.update(
        {
            "total_principal": round(float(d.get("total_principal") or 0.0), 2),
            "remaining_principal": round(float(d.get("remaining_principal") or 0.0), 2),
            "monthly_payment": round(monthly, 2),
            "paid_this_month": round(paid, 2),
            "remaining_month_payment": remaining_month,
            "apr": round(float(d.get("apr") or 0.0), 2),
            "next_due_date": next_due.isoformat(),
            "days_until_due": (next_due - today).days,
        }
    )
    return d


def _score_source_card(
    card: Dict[str, Any],
    strategy: str,
    sim_debt: Dict[int, float],
    sim_paid_today: Dict[int, float],
) -> Tuple[float, float, float]:
    """Higher tuple = better candidate card for swiping out funds."""
    limit = max(1.0, float(card.get("credit_limit") or 1.0))
    util = sim_debt[card["id"]] / limit
    free_days = int(card.get("swipe_free_days") or 0)
    fee_rate = float(card.get("swipe_fee_rate") or 0.006)
    is_relay = sim_paid_today.get(card["id"], 0.0) > 0
    relay_penalty = -1000.0 if is_relay else 0.0

    if strategy == "max_free_days":
        return (relay_penalty + free_days, -fee_rate, -util)
    if strategy == "min_fee":
        return (relay_penalty - fee_rate * 10000.0, float(free_days), -util)

    # balanced: strong preference for long interest-free period, low fee, moderate utilization
    score = relay_penalty + (free_days * 1.5) - (fee_rate * 10000.0 * 0.8) - (util * 25.0)
    return (score, float(free_days), -fee_rate)


def plan_optimal_repayment_paths(
    cards: List[Dict[str, Any]],
    loans: List[Dict[str, Any]],
    available_cash: float = 0.0,
    horizon_days: int = 30,
    reserve_ratio: float = 0.05,
    strategy: str = "balanced",
    allow_relay: bool = True,
    ref_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Plan optimal cash + swipe-to-repay sequence over the next `horizon_days`."""
    today = ref_date or datetime.now().astimezone().date()
    enriched_cards = [
        enrich_credit_card(c, ref_date=today)
        for c in cards
        if c.get("status", "active") == "active"
    ]
    enriched_loans = [
        enrich_loan(l, ref_date=today)
        for l in loans
        if l.get("status", "active") == "active"
    ]

    obligations: List[Dict[str, Any]] = []
    for l in enriched_loans:
        if l["remaining_month_payment"] > 0 and l["days_until_due"] <= horizon_days:
            obligations.append(
                {
                    "kind": "loan",
                    "id": l["id"],
                    "name": l["name"],
                    "amount": l["remaining_month_payment"],
                    "due_date": l["next_due_date"],
                    "days_until_due": l["days_until_due"],
                    "apr": l["apr"],
                    "rigid_cash": True,
                }
            )

    for c in enriched_cards:
        if c["remaining_bill"] > 0 and c["days_until_due"] <= horizon_days:
            obligations.append(
                {
                    "kind": "credit_card",
                    "id": c["id"],
                    "name": c["name"],
                    "amount": c["remaining_bill"],
                    "due_date": c["next_due_date"],
                    "days_until_due": c["days_until_due"],
                    "grace_days": c.get("grace_days", 0),
                    "rigid_cash": False,
                }
            )

    # Sort obligations: earliest due first; on same day, rigid loans before credit cards; larger amount first
    obligations.sort(key=lambda o: (o["days_until_due"], 0 if o["rigid_cash"] else 1, -o["amount"]))

    sim_debt: Dict[int, float] = {c["id"]: float(c["current_debt"]) for c in enriched_cards}
    sim_rem_bill: Dict[int, float] = {c["id"]: float(c["remaining_bill"]) for c in enriched_cards}
    sim_paid_today: Dict[int, float] = {c["id"]: 0.0 for c in enriched_cards}
    sim_loan_rem: Dict[int, float] = {l["id"]: float(l["remaining_month_payment"]) for l in enriched_loans}

    def usable_avail(card: Dict[str, Any]) -> float:
        limit = float(card["credit_limit"])
        reserve_floor = round(limit * max(0.0, min(reserve_ratio, 0.5)), 2)
        raw_avail = max(0.0, limit - sim_debt[card["id"]])
        return max(0.0, round(raw_avail - reserve_floor, 2))

    cash_pool = max(0.0, round(float(available_cash), 2))
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    uncovered_obligations: List[Dict[str, Any]] = []

    total_due = round(sum(o["amount"] for o in obligations), 2)
    total_loan_due = round(sum(o["amount"] for o in obligations if o["kind"] == "loan"), 2)
    total_card_due = round(sum(o["amount"] for o in obligations if o["kind"] == "credit_card"), 2)

    cash_used_total = 0.0
    swipe_used_total = 0.0
    total_fee = 0.0
    weighted_free_days_acc = 0.0

    # Phase 1: Cash first covers rigid loan monthly payments, then earliest credit cards
    for ob in [o for o in obligations if o["kind"] == "loan"]:
        if cash_pool <= 0.01:
            break
        amt = min(cash_pool, sim_loan_rem[ob["id"]])
        amt = round(amt, 2)
        if amt <= 0:
            continue
        cash_pool = round(cash_pool - amt, 2)
        sim_loan_rem[ob["id"]] = round(sim_loan_rem[ob["id"]] - amt, 2)
        cash_used_total = round(cash_used_total + amt, 2)
        steps.append(
            {
                "step_no": len(steps) + 1,
                "op_type": "repay_loan",
                "title": f"储蓄卡/现金归还贷款「{ob['name']}」",
                "source_type": "cash",
                "source_id": None,
                "source_name": "可用现金 / 储蓄卡",
                "target_type": "loan",
                "target_id": ob["id"],
                "target_name": ob["name"],
                "amount": amt,
                "fee_amount": 0.0,
                "due_date": ob["due_date"],
                "days_until_due": ob["days_until_due"],
                "free_days_gained": 0,
                "repay_date": ob["due_date"],
                "reason": "贷款月供不支持直接刷卡抵扣且无宽限期，优先用现金直还零手续费。",
                "risk_tip": "",
            }
        )

    for ob in [o for o in obligations if o["kind"] == "credit_card"]:
        if cash_pool <= 0.01:
            break
        cid = ob["id"]
        amt = min(cash_pool, sim_rem_bill[cid])
        amt = round(amt, 2)
        if amt <= 0:
            continue
        cash_pool = round(cash_pool - amt, 2)
        sim_rem_bill[cid] = round(sim_rem_bill[cid] - amt, 2)
        sim_debt[cid] = max(0.0, round(sim_debt[cid] - amt, 2))
        sim_paid_today[cid] = round(sim_paid_today[cid] + amt, 2)
        cash_used_total = round(cash_used_total + amt, 2)
        steps.append(
            {
                "step_no": len(steps) + 1,
                "op_type": "cash_repay_card",
                "title": f"现金直还信用卡「{ob['name']}」",
                "source_type": "cash",
                "source_id": None,
                "source_name": "可用现金 / 储蓄卡",
                "target_type": "credit_card",
                "target_id": cid,
                "target_name": ob["name"],
                "amount": amt,
                "fee_amount": 0.0,
                "due_date": ob["due_date"],
                "days_until_due": ob["days_until_due"],
                "free_days_gained": 0,
                "repay_date": ob["due_date"],
                "reason": "优先用剩余现金直还临近账单，降低整体负债并释放该卡可用额度。",
                "risk_tip": "",
            }
        )

    # Phase 2: Remaining loan shortfalls need swipe cash-out -> pay loan
    for ob in [o for o in obligations if o["kind"] == "loan"]:
        lid = ob["id"]
        while sim_loan_rem[lid] > 0.01:
            candidates = [
                c for c in enriched_cards
                if usable_avail(c) >= 1.0 and (allow_relay or sim_paid_today.get(c["id"], 0.0) <= 0)
            ]
            if not candidates:
                break
            candidates.sort(key=lambda c: _score_source_card(c, strategy, sim_debt, sim_paid_today), reverse=True)
            src = candidates[0]
            avail = usable_avail(src)
            amt = round(min(sim_loan_rem[lid], avail), 2)
            if amt < 1.0:
                break
            fee = round(amt * src["swipe_fee_rate"], 2)

            tips: List[str] = []
            if sim_paid_today.get(src["id"], 0.0) > 0:
                tips.append("此卡本周期刚入账又转出，建议间隔1~2天并分笔操作防风控")
            if (sim_debt[src["id"]] + amt) / max(1.0, src["credit_limit"]) >= 0.85:
                tips.append("刷出后该卡额度使用率将超过85%，注意风控安全")
            if src["is_statement_day"]:
                tips.append("今日为该卡账单日，若可延后1天刷将额外多出约30天免息期")

            sim_loan_rem[lid] = round(sim_loan_rem[lid] - amt, 2)
            sim_debt[src["id"]] = round(sim_debt[src["id"]] + amt, 2)
            swipe_used_total = round(swipe_used_total + amt, 2)
            total_fee = round(total_fee + fee, 2)
            weighted_free_days_acc += amt * int(src["swipe_free_days"])

            steps.append(
                {
                    "step_no": len(steps) + 1,
                    "op_type": "swipe_cash_out",
                    "title": f"刷「{src['name']}」周转补充储蓄卡还「{ob['name']}」",
                    "source_type": "credit_card",
                    "source_id": src["id"],
                    "source_name": src["name"],
                    "target_type": "loan",
                    "target_id": lid,
                    "target_name": ob["name"],
                    "amount": amt,
                    "fee_amount": fee,
                    "due_date": ob["due_date"],
                    "days_until_due": ob["days_until_due"],
                    "free_days_gained": src["swipe_free_days"],
                    "repay_date": src["swipe_repay_date"],
                    "reason": f"现金不足以覆盖贷款月供，从「{src['name']}」刷出可得 {src['swipe_free_days']} 天免息期（延至 {src['swipe_repay_date']} 归还）。",
                    "risk_tip": "；".join(tips),
                }
            )

        if sim_loan_rem[lid] > 0.01:
            uncovered_obligations.append(
                {
                    "kind": "loan",
                    "id": lid,
                    "name": ob["name"],
                    "shortfall": sim_loan_rem[lid],
                    "due_date": ob["due_date"],
                }
            )

    # Phase 3: Cover remaining credit card bills by swiping optimal source cards
    for ob in [o for o in obligations if o["kind"] == "credit_card"]:
        tid = ob["id"]
        while sim_rem_bill[tid] > 0.01:
            candidates = [
                c for c in enriched_cards
                if c["id"] != tid
                and usable_avail(c) >= 1.0
                and (allow_relay or sim_paid_today.get(c["id"], 0.0) <= 0)
            ]
            if not candidates:
                break
            candidates.sort(key=lambda c: _score_source_card(c, strategy, sim_debt, sim_paid_today), reverse=True)
            src = candidates[0]
            avail = usable_avail(src)
            amt = round(min(sim_rem_bill[tid], avail), 2)
            if amt < 1.0:
                break
            fee = round(amt * src["swipe_fee_rate"], 2)

            tips = []
            if sim_paid_today.get(src["id"], 0.0) > 0:
                tips.append("此卡刚还款释出额度又刷出（接力周转），建议间隔1~2天并拆成多笔小额防风控")
            if (sim_debt[src["id"]] + amt) / max(1.0, src["credit_limit"]) >= 0.85:
                tips.append("刷出后该卡额度使用率将超85%，谨防银行降额风控")
            if src["is_statement_day"]:
                tips.append("今日恰逢该卡账单日，若目标卡不急，明日刷可多享约30天免息期")
            elif 0 < src["days_to_next_statement"] <= 3 and ob["days_until_due"] > src["days_to_next_statement"] + 1:
                tips.append(
                    f"「{src['name']}」将在 {src['days_to_next_statement']} 天后出账单，若等到 {src['next_cycle_start_date']} 再刷，免息期可由 {src['swipe_free_days']} 天提升至 {src['next_cycle_free_days']} 天"
                )

            sim_rem_bill[tid] = round(sim_rem_bill[tid] - amt, 2)
            sim_debt[tid] = max(0.0, round(sim_debt[tid] - amt, 2))
            sim_paid_today[tid] = round(sim_paid_today[tid] + amt, 2)
            sim_debt[src["id"]] = round(sim_debt[src["id"]] + amt, 2)

            swipe_used_total = round(swipe_used_total + amt, 2)
            total_fee = round(total_fee + fee, 2)
            weighted_free_days_acc += amt * int(src["swipe_free_days"])

            steps.append(
                {
                    "step_no": len(steps) + 1,
                    "op_type": "swipe_repay_card",
                    "title": f"刷「{src['name']}」还「{ob['name']}」",
                    "source_type": "credit_card",
                    "source_id": src["id"],
                    "source_name": src["name"],
                    "target_type": "credit_card",
                    "target_id": tid,
                    "target_name": ob["name"],
                    "amount": amt,
                    "fee_amount": fee,
                    "due_date": ob["due_date"],
                    "days_until_due": ob["days_until_due"],
                    "free_days_gained": src["swipe_free_days"],
                    "repay_date": src["swipe_repay_date"],
                    "reason": f"「{src['name']}」当前免息期长达 {src['swipe_free_days']} 天（延至 {src['swipe_repay_date']} 还款），费率 {(src['swipe_fee_rate']*100):.2f}%，可替 {ob['due_date']} 到期的「{ob['name']}」续命周转。",
                    "risk_tip": "；".join(tips),
                }
            )

        if sim_rem_bill[tid] > 0.01:
            uncovered_obligations.append(
                {
                    "kind": "credit_card",
                    "id": tid,
                    "name": ob["name"],
                    "shortfall": sim_rem_bill[tid],
                    "due_date": ob["due_date"],
                }
            )

    total_shortfall = round(sum(u["shortfall"] for u in uncovered_obligations), 2)
    if total_shortfall > 0:
        warnings.append(
            f"当前可用现金与各卡安全可用额度不足以覆盖全部应还账单，仍有 ¥{total_shortfall:,.2f} 资金缺口，建议补充现金或先还部分最紧急账单/考虑最低还款。"
        )
    if any(s["risk_tip"] for s in steps):
        warnings.append("部分步骤存在高使用率或账单日临近提示，实操时建议拆分为多笔非整数金额并注意商户多样性。")

    avg_free_days = round(weighted_free_days_acc / swipe_used_total, 1) if swipe_used_total > 0 else 0.0

    # Rank all cards as potential swipe sources right now for reference
    swipe_ranking = sorted(
        [
            {
                "id": c["id"],
                "name": c["name"],
                "usable_available": max(
                    0.0,
                    round(max(0.0, c["credit_limit"] - c["current_debt"]) - c["credit_limit"] * reserve_ratio, 2),
                ),
                "swipe_free_days": c["swipe_free_days"],
                "swipe_repay_date": c["swipe_repay_date"],
                "swipe_fee_rate": c["swipe_fee_rate"],
                "utilization_pct": c["utilization_pct"],
                "days_to_next_statement": c["days_to_next_statement"],
                "next_cycle_free_days": c["next_cycle_free_days"],
            }
            for c in enriched_cards
        ],
        key=lambda x: (x["swipe_free_days"], -x["swipe_fee_rate"], x["usable_available"]),
        reverse=True,
    )

    return {
        "strategy": strategy,
        "horizon_days": horizon_days,
        "available_cash_input": round(float(available_cash), 2),
        "remaining_cash_after": cash_pool,
        "summary": {
            "obligation_count": len(obligations),
            "total_due": total_due,
            "total_loan_due": total_loan_due,
            "total_card_due": total_card_due,
            "cash_used": cash_used_total,
            "swipe_used": swipe_used_total,
            "total_fee": total_fee,
            "avg_free_days": avg_free_days,
            "total_shortfall": total_shortfall,
            "fully_covered": total_shortfall <= 0.01,
        },
        "steps": steps,
        "uncovered_obligations": uncovered_obligations,
        "warnings": warnings,
        "swipe_ranking": swipe_ranking,
    }
