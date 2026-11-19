import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  BellRing,
  CalendarClock,
  CheckCircle2,
  CreditCard as CreditCardIcon,
  Edit3,
  Landmark,
  Plus,
  RefreshCw,
  RotateCcw,
  Route,
  ShieldAlert,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { api } from '../api';
import type {
  Account,
  CreditCard,
  DebtOverviewResponse,
  DebtPlanResponse,
  DebtPlanStep,
  Loan,
} from '../types';
import { formatMoney } from '../utils/format';

interface Props {
  accounts: Account[];
  onRefreshAll: () => void;
  onNotify: (msg: string) => void;
}

const EMPTY_CARD_FORM = {
  name: '',
  bank_name: '',
  last4: '',
  credit_limit: '',
  current_debt: '',
  statement_bill_amount: '',
  paid_amount_this_cycle: '0',
  min_payment: '0',
  statement_day: '5',
  due_day: '23',
  grace_days: '3',
  swipe_fee_rate_pct: '0.60',
  status: 'active' as CreditCard['status'],
  notes: '',
};

const EMPTY_LOAN_FORM = {
  name: '',
  lender: '',
  total_principal: '',
  remaining_principal: '',
  monthly_payment: '',
  paid_this_month: '0',
  due_day: '15',
  apr: '4.5',
  remaining_periods: '12',
  status: 'active' as Loan['status'],
  notes: '',
};

export const DebtPlannerView: React.FC<Props> = ({ accounts, onRefreshAll, onNotify }) => {
  const [overview, setOverview] = useState<DebtOverviewResponse>({
    cards: [],
    loans: [],
    operations: [],
    suggested_cash: 0,
    summary: {
      active_card_count: 0,
      active_loan_count: 0,
      total_credit_limit: 0,
      total_card_debt: 0,
      total_available_credit: 0,
      overall_utilization_pct: 0,
      total_remaining_bill: 0,
      total_loan_principal: 0,
      total_loan_month_remaining: 0,
      total_debt: 0,
      total_fee_spent: 0,
    },
  });

  // Planner parameters & result
  const [cashInput, setCashInput] = useState<string>('');
  const [cashTouched, setCashTouched] = useState(false);
  const [horizonDays, setHorizonDays] = useState<number>(30);
  const [reserveRatio, setReserveRatio] = useState<number>(0.05);
  const [strategy, setStrategy] = useState<'balanced' | 'max_free_days' | 'min_fee'>('balanced');
  const [allowRelay, setAllowRelay] = useState<boolean>(true);
  const [plan, setPlan] = useState<DebtPlanResponse | null>(null);
  const [planning, setPlanning] = useState(false);
  const [syncToFinance, setSyncToFinance] = useState(false);
  const [cashAccountId, setCashAccountId] = useState<string>('');

  // Card form
  const [showCardForm, setShowCardForm] = useState(false);
  const [editingCardId, setEditingCardId] = useState<number | null>(null);
  const [cardForm, setCardForm] = useState(EMPTY_CARD_FORM);

  // Loan form
  const [showLoanForm, setShowLoanForm] = useState(false);
  const [editingLoanId, setEditingLoanId] = useState<number | null>(null);
  const [loanForm, setLoanForm] = useState(EMPTY_LOAN_FORM);

  // Manual quick operation modal/panel
  const [showOpForm, setShowOpForm] = useState(false);
  const [opType, setOpType] = useState<'swipe_repay_card' | 'cash_repay_card' | 'repay_loan' | 'swipe_cash_out'>('swipe_repay_card');
  const [opSourceCardId, setOpSourceCardId] = useState('');
  const [opTargetCardId, setOpTargetCardId] = useState('');
  const [opTargetLoanId, setOpTargetLoanId] = useState('');
  const [opAmount, setOpAmount] = useState('');
  const [opFee, setOpFee] = useState('');
  const [opNote, setOpNote] = useState('');

  const cashAccounts = accounts.filter((a) => a.type === 'cash' || a.type === 'bank');

  const runPlan = async (overrideCash?: number) => {
    setPlanning(true);
    try {
      const cashVal =
        overrideCash !== undefined
          ? overrideCash
          : cashInput.trim() !== ''
            ? Number(cashInput)
            : undefined;
      const res = await api.planDebtRepayment({
        available_cash: Number.isFinite(cashVal as number) ? cashVal : undefined,
        horizon_days: horizonDays,
        reserve_ratio: reserveRatio,
        strategy,
        allow_relay: allowRelay,
      });
      setPlan(res);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '路径规划计算失败');
    } finally {
      setPlanning(false);
    }
  };

  const loadData = async (recalcPlan = true) => {
    try {
      const ov = await api.getDebtOverview();
      setOverview(ov);
      const initialCash = !cashTouched ? ov.suggested_cash : Number(cashInput || 0);
      if (!cashTouched) {
        setCashInput(String(ov.suggested_cash));
      }
      if (!cashAccountId && cashAccounts.length > 0) {
        setCashAccountId(String(cashAccounts[0].id));
      }
      if (recalcPlan) {
        await runPlan(initialCash);
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载卡贷数据失败');
    }
  };

  useEffect(() => {
    loadData(true);
  }, []);

  const openAddCard = () => {
    setEditingCardId(null);
    setCardForm(EMPTY_CARD_FORM);
    setShowCardForm(true);
  };

  const openEditCard = (c: CreditCard) => {
    setEditingCardId(c.id);
    setCardForm({
      name: c.name,
      bank_name: c.bank_name,
      last4: c.last4,
      credit_limit: String(c.credit_limit),
      current_debt: String(c.current_debt),
      statement_bill_amount: String(c.statement_bill_amount),
      paid_amount_this_cycle: String(c.paid_amount_this_cycle),
      min_payment: String(c.min_payment),
      statement_day: String(c.statement_day),
      due_day: String(c.due_day),
      grace_days: String(c.grace_days),
      swipe_fee_rate_pct: (c.swipe_fee_rate * 100).toFixed(2),
      status: c.status,
      notes: c.notes,
    });
    setShowCardForm(true);
  };

  const handleSaveCard = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!cardForm.name.trim()) return;
    const payload: Partial<CreditCard> = {
      name: cardForm.name.trim(),
      bank_name: cardForm.bank_name.trim(),
      last4: cardForm.last4.trim(),
      credit_limit: Number(cardForm.credit_limit || 0),
      current_debt: Number(cardForm.current_debt || 0),
      statement_bill_amount: Number(cardForm.statement_bill_amount || 0),
      paid_amount_this_cycle: Number(cardForm.paid_amount_this_cycle || 0),
      min_payment: Number(cardForm.min_payment || 0),
      statement_day: Math.min(31, Math.max(1, Number(cardForm.statement_day || 5))),
      due_day: Math.min(31, Math.max(1, Number(cardForm.due_day || 23))),
      grace_days: Math.min(10, Math.max(0, Number(cardForm.grace_days || 0))),
      swipe_fee_rate: Math.max(0, Number(cardForm.swipe_fee_rate_pct || 0.6) / 100),
      status: cardForm.status,
      notes: cardForm.notes.trim(),
    };
    try {
      if (editingCardId) {
        await api.updateCreditCard(editingCardId, payload);
        onNotify('信用卡信息已更新');
      } else {
        await api.createCreditCard(payload);
        onNotify('信用卡已添加');
      }
      setShowCardForm(false);
      await loadData(true);
      onRefreshAll();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '保存信用卡失败');
    }
  };

  const openAddLoan = () => {
    setEditingLoanId(null);
    setLoanForm(EMPTY_LOAN_FORM);
    setShowLoanForm(true);
  };

  const openEditLoan = (l: Loan) => {
    setEditingLoanId(l.id);
    setLoanForm({
      name: l.name,
      lender: l.lender,
      total_principal: String(l.total_principal),
      remaining_principal: String(l.remaining_principal),
      monthly_payment: String(l.monthly_payment),
      paid_this_month: String(l.paid_this_month),
      due_day: String(l.due_day),
      apr: String(l.apr),
      remaining_periods: String(l.remaining_periods),
      status: l.status,
      notes: l.notes,
    });
    setShowLoanForm(true);
  };

  const handleSaveLoan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loanForm.name.trim()) return;
    const payload: Partial<Loan> = {
      name: loanForm.name.trim(),
      lender: loanForm.lender.trim(),
      total_principal: Number(loanForm.total_principal || 0),
      remaining_principal: Number(loanForm.remaining_principal || loanForm.total_principal || 0),
      monthly_payment: Number(loanForm.monthly_payment || 0),
      paid_this_month: Number(loanForm.paid_this_month || 0),
      due_day: Math.min(31, Math.max(1, Number(loanForm.due_day || 15))),
      apr: Number(loanForm.apr || 0),
      remaining_periods: Number(loanForm.remaining_periods || 0),
      status: loanForm.status,
      notes: loanForm.notes.trim(),
    };
    try {
      if (editingLoanId) {
        await api.updateLoan(editingLoanId, payload);
        onNotify('贷款信息已更新');
      } else {
        await api.createLoan(payload);
        onNotify('贷款已添加');
      }
      setShowLoanForm(false);
      await loadData(true);
      onRefreshAll();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '保存贷款失败');
    }
  };

  const handleExecutePlanStep = async (step: DebtPlanStep) => {
    try {
      await api.executeDebtOperation({
        op_type: step.op_type,
        source_card_id: step.source_type === 'credit_card' ? step.source_id : null,
        source_account_id:
          step.source_type === 'cash' && syncToFinance && cashAccountId
            ? Number(cashAccountId)
            : null,
        target_card_id: step.target_type === 'credit_card' ? step.target_id : null,
        target_loan_id: step.target_type === 'loan' ? step.target_id : null,
        amount: step.amount,
        fee_amount: step.fee_amount,
        note: step.title,
        sync_to_finance: syncToFinance,
      });
      if (step.source_type === 'cash') {
        const nextCash = Math.max(0, Number(cashInput || 0) - step.amount);
        setCashInput(String(nextCash));
      }
      onNotify(`已执行：${step.title}`);
      await loadData(true);
      onRefreshAll();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '执行失败');
    }
  };

  const handleManualOperation = async (e: React.FormEvent) => {
    e.preventDefault();
    const amt = Number(opAmount);
    if (!amt || amt <= 0) return;
    try {
      await api.executeDebtOperation({
        op_type: opType,
        source_card_id:
          opType === 'swipe_repay_card' || opType === 'swipe_cash_out'
            ? Number(opSourceCardId)
            : null,
        source_account_id:
          (opType === 'cash_repay_card' || opType === 'repay_loan') && syncToFinance && cashAccountId
            ? Number(cashAccountId)
            : null,
        target_card_id:
          opType === 'swipe_repay_card' || opType === 'cash_repay_card'
            ? Number(opTargetCardId)
            : null,
        target_loan_id:
          opType === 'repay_loan' || opType === 'swipe_cash_out'
            ? opTargetLoanId
              ? Number(opTargetLoanId)
              : null
            : null,
        amount: amt,
        fee_amount: Number(opFee || 0),
        note: opNote.trim(),
        sync_to_finance: syncToFinance,
      });
      setShowOpForm(false);
      setOpAmount('');
      setOpFee('');
      setOpNote('');
      onNotify('操作已入账，欠款与剩余账单已更新');
      await loadData(true);
      onRefreshAll();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '操作失败');
    }
  };

  const handleUndoOp = async (opId: number) => {
    if (!window.confirm('确定撤销这笔操作记录吗？相关卡片欠款、账单已还与关联流水将会回滚。')) return;
    try {
      await api.undoDebtOperation(opId);
      onNotify('已撤销操作并回滚欠款');
      await loadData(true);
      onRefreshAll();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '撤销失败');
    }
  };

  const handleSyncReminders = async () => {
    try {
      const res = await api.syncDebtReminders(5);
      onNotify(
        res.created_count > 0
          ? `已为近 5 天内到期的 ${res.created_count} 笔账单/月供生成还款提醒`
          : '近 5 天内无待补发的卡贷还款提醒',
      );
      onRefreshAll();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '同步提醒失败');
    }
  };

  const openQuickSwipeForCard = (targetCard: CreditCard) => {
    const otherCards = overview.cards.filter((c) => c.id !== targetCard.id && c.status === 'active');
    const bestSrc = [...otherCards].sort((a, b) => b.swipe_free_days - a.swipe_free_days)[0];
    setOpType(bestSrc ? 'swipe_repay_card' : 'cash_repay_card');
    setOpTargetCardId(String(targetCard.id));
    setOpSourceCardId(bestSrc ? String(bestSrc.id) : '');
    const amt = targetCard.remaining_bill > 0 ? targetCard.remaining_bill : targetCard.current_debt;
    setOpAmount(amt > 0 ? String(amt) : '');
    setOpFee(bestSrc && amt > 0 ? String(Math.round(amt * bestSrc.swipe_fee_rate * 100) / 100) : '0');
    setShowOpForm(true);
  };

  const activeCards = overview.cards.filter((c) => c.status === 'active');
  const activeLoans = overview.loans.filter((l) => l.status === 'active');

  return (
    <div className="space-y-6">
      {/* Top KPI Strip */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <CreditCardIcon className="h-3.5 w-3.5 text-rose-500" /> 信用卡总欠款 / 额度
          </div>
          <div className="mt-2 text-2xl font-bold text-rose-600">{formatMoney(overview.summary.total_card_debt)}</div>
          <div className="mt-1 text-xs text-slate-400">
            总额度 {formatMoney(overview.summary.total_credit_limit)} · 使用率 {overview.summary.overall_utilization_pct}%
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <CalendarClock className="h-3.5 w-3.5 text-amber-500" /> 本期卡账单待还
          </div>
          <div className="mt-2 text-2xl font-bold text-slate-900">{formatMoney(overview.summary.total_remaining_bill)}</div>
          <div className="mt-1 text-xs text-slate-400">
            可用授信 {formatMoney(overview.summary.total_available_credit)} · {overview.summary.active_card_count} 张活跃卡
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <Landmark className="h-3.5 w-3.5 text-indigo-600" /> 贷款待还本金 / 本月月供
          </div>
          <div className="mt-2 text-2xl font-bold text-indigo-600">
            {formatMoney(overview.summary.total_loan_month_remaining)}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            剩余总本金 {formatMoney(overview.summary.total_loan_principal)} · {overview.summary.active_loan_count} 笔贷款
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs flex flex-col justify-between">
          <div>
            <div className="text-xs text-slate-500">总负债 & 累计周转成本</div>
            <div className="mt-1 text-lg font-bold text-slate-900">{formatMoney(overview.summary.total_debt)}</div>
            <div className="text-[11px] text-slate-400">已记录手续费 {formatMoney(overview.summary.total_fee_spent)}</div>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={handleSyncReminders}
              className="inline-flex items-center gap-1 rounded-lg bg-orange-50 border border-orange-200 px-2.5 py-1 text-[11px] font-medium text-orange-700 hover:bg-orange-100 cursor-pointer"
            >
              <BellRing className="h-3 w-3" /> 同步还款提醒
            </button>
            <button
              type="button"
              onClick={() => setShowOpForm((v) => !v)}
              className="inline-flex items-center gap-1 rounded-lg bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-slate-800 cursor-pointer"
            >
              <Plus className="h-3 w-3" /> 手动记还/刷卡
            </button>
          </div>
        </div>
      </div>

      {/* Quick Manual Operation Form */}
      {showOpForm && (
        <form onSubmit={handleManualOperation} className="rounded-2xl border border-indigo-200 bg-indigo-50/40 p-5 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-900">记录刷卡周转 / 还款操作</h3>
            <button type="button" onClick={() => setShowOpForm(false)} className="text-xs text-slate-500 cursor-pointer">
              收起
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <select
              value={opType}
              onChange={(e) => setOpType(e.target.value as typeof opType)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
            >
              <option value="swipe_repay_card">刷 A 卡还 B 卡</option>
              <option value="cash_repay_card">现金 / 储蓄卡还信用卡</option>
              <option value="repay_loan">现金 / 储蓄卡还贷款月供</option>
              <option value="swipe_cash_out">刷信用卡周转还贷款/补现金</option>
            </select>

            {(opType === 'swipe_repay_card' || opType === 'swipe_cash_out') && (
              <select
                required
                value={opSourceCardId}
                onChange={(e) => {
                  const sid = e.target.value;
                  setOpSourceCardId(sid);
                  const c = activeCards.find((x) => String(x.id) === sid);
                  if (c && opAmount) {
                    setOpFee(String(Math.round(Number(opAmount) * c.swipe_fee_rate * 100) / 100));
                  }
                }}
                className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
              >
                <option value="">选择刷出资金的信用卡...</option>
                {activeCards.map((c) => (
                  <option key={c.id} value={c.id}>
                    刷：{c.name} (可用 {formatMoney(c.available_credit)} · 免息 {c.swipe_free_days}天)
                  </option>
                ))}
              </select>
            )}

            {(opType === 'swipe_repay_card' || opType === 'cash_repay_card') && (
              <select
                required
                value={opTargetCardId}
                onChange={(e) => setOpTargetCardId(e.target.value)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
              >
                <option value="">选择还款目标信用卡...</option>
                {activeCards.map((c) => (
                  <option key={c.id} value={c.id}>
                    还：{c.name} (账单待还 {formatMoney(c.remaining_bill)})
                  </option>
                ))}
              </select>
            )}

            {(opType === 'repay_loan' || opType === 'swipe_cash_out') && (
              <select
                required={opType === 'repay_loan'}
                value={opTargetLoanId}
                onChange={(e) => setOpTargetLoanId(e.target.value)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
              >
                <option value="">{opType === 'repay_loan' ? '选择还款贷款...' : '可选：关联贷款月供'}</option>
                {activeLoans.map((l) => (
                  <option key={l.id} value={l.id}>
                    贷款：{l.name} (本月待还 {formatMoney(l.remaining_month_payment)})
                  </option>
                ))}
              </select>
            )}

            <input
              type="number"
              step="0.01"
              required
              value={opAmount}
              onChange={(e) => {
                const v = e.target.value;
                setOpAmount(v);
                if (opType === 'swipe_repay_card' || opType === 'swipe_cash_out') {
                  const c = activeCards.find((x) => String(x.id) === opSourceCardId);
                  if (c && v) {
                    setOpFee(String(Math.round(Number(v) * c.swipe_fee_rate * 100) / 100));
                  }
                }
              }}
              placeholder="操作金额（元）"
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
            />

            {(opType === 'swipe_repay_card' || opType === 'swipe_cash_out') && (
              <input
                type="number"
                step="0.01"
                value={opFee}
                onChange={(e) => setOpFee(e.target.value)}
                placeholder="手续费（元）"
                className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
              />
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <input
              type="text"
              value={opNote}
              onChange={(e) => setOpNote(e.target.value)}
              placeholder="备注说明（可选）"
              className="flex-1 min-w-[220px] rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs"
            />
            <button
              type="submit"
              className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 cursor-pointer"
            >
              确认执行并更新欠款
            </button>
          </div>
        </form>
      )}

      {/* Section 1: Optimal Swipe & Repayment Path Planner */}
      <div className="rounded-2xl border border-indigo-200 bg-gradient-to-b from-indigo-50/50 to-white p-5 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-indigo-100 pb-3">
          <div className="flex items-center gap-2.5">
            <div className="rounded-xl bg-indigo-600 p-2 text-white">
              <Route className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-slate-900">最优刷卡 & 还款路径规划</h3>
              <p className="text-xs text-slate-500">
                统筹贷款刚性月供与信用卡账单到期顺序，优先现金直还贷款，其余按最长免息期 + 最低费率 + 防风控生成倒卡方案
              </p>
            </div>
          </div>
          <button
            type="button"
            disabled={planning}
            onClick={() => runPlan()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-60 cursor-pointer"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${planning ? 'animate-spin' : ''}`} />
            {planning ? '计算中...' : '重新规划最优路径'}
          </button>
        </div>

        {/* Planner Controls */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5 bg-white rounded-xl border border-slate-200/80 p-3.5 text-xs">
          <div>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">可用现金 / 储蓄余额（元）</label>
            <input
              type="number"
              step="100"
              min={0}
              value={cashInput}
              onChange={(e) => {
                setCashTouched(true);
                setCashInput(e.target.value);
              }}
              placeholder="例如：3000"
              className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs"
            />
          </div>
          <div>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">规划窗口期</label>
            <select
              value={horizonDays}
              onChange={(e) => setHorizonDays(Number(e.target.value))}
              className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs"
            >
              <option value={15}>未来 15 天内到期</option>
              <option value={30}>未来 30 天内到期</option>
              <option value={45}>未来 45 天内到期</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">优化策略</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as typeof strategy)}
              className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs"
            >
              <option value="balanced">综合最优（免息期 + 费率 + 风控）</option>
              <option value="max_free_days">最长免息期优先（腾出最多天数）</option>
              <option value="min_fee">最低刷卡手续费优先</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">单卡保留安全额度</label>
            <select
              value={reserveRatio}
              onChange={(e) => setReserveRatio(Number(e.target.value))}
              className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs"
            >
              <option value={0}>不预留 (0%，极限周转)</option>
              <option value={0.05}>预留 5% 额度防刷爆</option>
              <option value={0.1}>预留 10% 额度防风控</option>
              <option value={0.2}>预留 20% 额度（保守）</option>
            </select>
          </div>
          <div className="flex flex-col justify-between gap-1.5">
            <label className="inline-flex items-center gap-1.5 text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={allowRelay}
                onChange={(e) => setAllowRelay(e.target.checked)}
              />
              <span>允许还清后接力周转</span>
            </label>
            <label className="inline-flex items-center gap-1.5 text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={syncToFinance}
                onChange={(e) => setSyncToFinance(e.target.checked)}
              />
              <span>执行时同步记入财务流水</span>
            </label>
            {syncToFinance && cashAccounts.length > 0 && (
              <select
                value={cashAccountId}
                onChange={(e) => setCashAccountId(e.target.value)}
                className="rounded border border-slate-200 px-2 py-0.5 text-[11px]"
              >
                {cashAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    扣款户：{a.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>

        {/* Plan Output */}
        {plan && (
          <div className="space-y-4">
            {/* Summary Bar */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <div className="rounded-xl bg-white border border-slate-200/80 p-3 text-xs">
                <div className="text-slate-400">窗口内总应还 ({plan.summary.obligation_count} 笔)</div>
                <div className="mt-1 text-base font-bold text-slate-900">{formatMoney(plan.summary.total_due)}</div>
                <div className="text-[10px] text-slate-400">
                  卡 {formatMoney(plan.summary.total_card_due)} · 贷 {formatMoney(plan.summary.total_loan_due)}
                </div>
              </div>
              <div className="rounded-xl bg-white border border-slate-200/80 p-3 text-xs">
                <div className="text-slate-400">现金直还覆盖</div>
                <div className="mt-1 text-base font-bold text-emerald-600">{formatMoney(plan.summary.cash_used)}</div>
                <div className="text-[10px] text-slate-400">剩余现金 {formatMoney(plan.remaining_cash_after)}</div>
              </div>
              <div className="rounded-xl bg-white border border-slate-200/80 p-3 text-xs">
                <div className="text-slate-400">需刷卡周转金额</div>
                <div className="mt-1 text-base font-bold text-indigo-600">{formatMoney(plan.summary.swipe_used)}</div>
                <div className="text-[10px] text-slate-400">平均新获免息 {plan.summary.avg_free_days} 天</div>
              </div>
              <div className="rounded-xl bg-white border border-slate-200/80 p-3 text-xs">
                <div className="text-slate-400">预计刷卡总手续费</div>
                <div className="mt-1 text-base font-bold text-amber-600">{formatMoney(plan.summary.total_fee)}</div>
                <div className="text-[10px] text-slate-400">按各卡设置费率测算</div>
              </div>
              <div className="rounded-xl bg-white border border-slate-200/80 p-3 text-xs">
                <div className="text-slate-400">资金缺口状态</div>
                <div
                  className={`mt-1 text-base font-bold ${
                    plan.summary.fully_covered ? 'text-emerald-600' : 'text-rose-600'
                  }`}
                >
                  {plan.summary.fully_covered ? '可全额覆盖' : `缺 ${formatMoney(plan.summary.total_shortfall)}`}
                </div>
                <div className="text-[10px] text-slate-400">
                  {plan.summary.fully_covered ? '本期无逾期风险' : '需补足现金/最低还款'}
                </div>
              </div>
            </div>

            {plan.warnings.length > 0 && (
              <div className="space-y-1.5">
                {plan.warnings.map((w, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-800"
                  >
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-600" />
                    <span>{w}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              {/* Steps */}
              <div className="lg:col-span-8 space-y-2.5">
                <div className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-indigo-600" />
                  推荐执行步骤 ({plan.steps.length} 步)
                </div>
                {plan.steps.length === 0 ? (
                  <div className="rounded-xl bg-white border border-slate-200/80 p-8 text-center text-xs text-slate-400">
                    {overview.cards.length === 0 && overview.loans.length === 0
                      ? '请先在下方添加你的信用卡和贷款记录，系统会自动为你规划最优还款与刷卡周转路径。'
                      : `未来 ${horizonDays} 天内暂无待还信用卡账单或贷款月供。`}
                  </div>
                ) : (
                  plan.steps.map((step) => (
                    <div
                      key={step.step_no}
                      className="rounded-xl border border-slate-200/80 bg-white p-3.5 flex flex-wrap items-center justify-between gap-3"
                    >
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-[11px] font-bold text-white">
                            {step.step_no}
                          </span>
                          <span className="font-semibold text-sm text-slate-900">{step.title}</span>
                          <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700">
                            {formatMoney(step.amount)}
                          </span>
                          {step.fee_amount > 0 && (
                            <span className="rounded-md bg-amber-50 border border-amber-200 px-2 py-0.5 text-[11px] text-amber-700">
                              手续费 {formatMoney(step.fee_amount)}
                            </span>
                          )}
                          {step.free_days_gained > 0 && (
                            <span className="rounded-md bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[11px] text-emerald-700">
                              +{step.free_days_gained} 天免息（{step.repay_date} 还）
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-slate-600 flex flex-wrap items-center gap-1.5">
                          <span className="text-slate-400">目标到期：{step.due_date} (剩 {step.days_until_due} 天)</span>
                          <span>·</span>
                          <span>{step.reason}</span>
                        </div>
                        {step.risk_tip && (
                          <div className="inline-flex items-center gap-1 rounded-lg bg-rose-50 px-2 py-1 text-[11px] text-rose-700">
                            <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                            <span>{step.risk_tip}</span>
                          </div>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => handleExecutePlanStep(step)}
                        className="shrink-0 inline-flex items-center gap-1 rounded-xl bg-emerald-600 px-3 py-2 text-xs font-medium text-white hover:bg-emerald-700 cursor-pointer"
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" /> 标记已执行
                      </button>
                    </div>
                  ))
                )}
              </div>

              {/* Swipe Card Ranking */}
              <div className="lg:col-span-4 rounded-xl border border-slate-200/80 bg-white p-3.5 space-y-2.5 h-fit">
                <div className="text-xs font-semibold text-slate-700">今日刷卡出款优先级参考</div>
                {plan.swipe_ranking.length === 0 ? (
                  <div className="text-xs text-slate-400 py-4 text-center">暂无活跃信用卡</div>
                ) : (
                  <div className="space-y-2">
                    {plan.swipe_ranking.map((r, idx) => (
                      <div key={r.id} className="rounded-lg bg-slate-50 border border-slate-100 p-2.5 text-xs space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-slate-800">
                            #{idx + 1} {r.name}
                          </span>
                          <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] font-medium text-indigo-700">
                            免息 {r.swipe_free_days} 天
                          </span>
                        </div>
                        <div className="flex justify-between text-[11px] text-slate-500">
                          <span>安全可用 {formatMoney(r.usable_available)}</span>
                          <span>费率 {(r.swipe_fee_rate * 100).toFixed(2)}% · 已用 {r.utilization_pct}%</span>
                        </div>
                        {r.days_to_next_statement <= 4 && r.next_cycle_free_days > r.swipe_free_days && (
                          <div className="text-[10px] text-amber-700">
                            再等 {r.days_to_next_statement + 1} 天（账单日次日）刷可享 {r.next_cycle_free_days} 天免息
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Section 2: Credit Cards Management */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <CreditCardIcon className="h-5 w-5 text-indigo-600" />
            <h3 className="text-base font-semibold text-slate-900">信用卡账单管理 ({overview.cards.length})</h3>
          </div>
          <button
            type="button"
            onClick={openAddCard}
            className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-3.5 py-2 text-xs font-medium text-white hover:bg-slate-800 cursor-pointer"
          >
            <Plus className="h-3.5 w-3.5" /> 新增信用卡
          </button>
        </div>

        {showCardForm && (
          <form onSubmit={handleSaveCard} className="rounded-xl border border-indigo-200 bg-indigo-50/30 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-900">
                {editingCardId ? '编辑信用卡信息' : '新增信用卡'}
              </span>
              <button type="button" onClick={() => setShowCardForm(false)} className="text-xs text-slate-500 cursor-pointer">
                取消
              </button>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 text-xs">
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">卡片名称 *</label>
                <input
                  type="text"
                  required
                  value={cardForm.name}
                  onChange={(e) => setCardForm({ ...cardForm, name: e.target.value })}
                  placeholder="如：招行经典白"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">银行 / 尾号</label>
                <div className="flex gap-1.5">
                  <input
                    type="text"
                    value={cardForm.bank_name}
                    onChange={(e) => setCardForm({ ...cardForm, bank_name: e.target.value })}
                    placeholder="招商银行"
                    className="w-2/3 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                  />
                  <input
                    type="text"
                    value={cardForm.last4}
                    onChange={(e) => setCardForm({ ...cardForm, last4: e.target.value })}
                    placeholder="尾号"
                    className="w-1/3 rounded-lg border border-slate-200 bg-white px-2 py-1.5"
                  />
                </div>
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">授信总额度（元）*</label>
                <input
                  type="number"
                  step="0.01"
                  required
                  value={cardForm.credit_limit}
                  onChange={(e) => setCardForm({ ...cardForm, credit_limit: e.target.value })}
                  placeholder="50000"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">当前总欠款/已用额度（元）</label>
                <input
                  type="number"
                  step="0.01"
                  value={cardForm.current_debt}
                  onChange={(e) => setCardForm({ ...cardForm, current_debt: e.target.value })}
                  placeholder="32000"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">本期已出账单应还（元）</label>
                <input
                  type="number"
                  step="0.01"
                  value={cardForm.statement_bill_amount}
                  onChange={(e) => setCardForm({ ...cardForm, statement_bill_amount: e.target.value })}
                  placeholder="12000"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">本期账单已还（元）</label>
                <input
                  type="number"
                  step="0.01"
                  value={cardForm.paid_amount_this_cycle}
                  onChange={(e) => setCardForm({ ...cardForm, paid_amount_this_cycle: e.target.value })}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">每月账单日 / 还款日</label>
                <div className="flex gap-1.5">
                  <input
                    type="number"
                    min={1}
                    max={31}
                    value={cardForm.statement_day}
                    onChange={(e) => setCardForm({ ...cardForm, statement_day: e.target.value })}
                    title="账单日"
                    className="w-1/2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                  />
                  <input
                    type="number"
                    min={1}
                    max={31}
                    value={cardForm.due_day}
                    onChange={(e) => setCardForm({ ...cardForm, due_day: e.target.value })}
                    title="还款日"
                    className="w-1/2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                  />
                </div>
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">刷卡费率 (%) / 宽限天数</label>
                <div className="flex gap-1.5">
                  <input
                    type="number"
                    step="0.01"
                    value={cardForm.swipe_fee_rate_pct}
                    onChange={(e) => setCardForm({ ...cardForm, swipe_fee_rate_pct: e.target.value })}
                    placeholder="0.60"
                    className="w-1/2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                  />
                  <input
                    type="number"
                    min={0}
                    max={10}
                    value={cardForm.grace_days}
                    onChange={(e) => setCardForm({ ...cardForm, grace_days: e.target.value })}
                    placeholder="3天"
                    className="w-1/2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                  />
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
              <input
                type="text"
                value={cardForm.notes}
                onChange={(e) => setCardForm({ ...cardForm, notes: e.target.value })}
                placeholder="备注（如：单笔限额、积分规则、风控注意事项）"
                className="flex-1 min-w-[200px] rounded-lg border border-slate-200 bg-white px-3 py-1.5"
              />
              <button
                type="submit"
                className="rounded-xl bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 cursor-pointer"
              >
                {editingCardId ? '保存修改' : '确认添加'}
              </button>
            </div>
          </form>
        )}

        {overview.cards.length === 0 ? (
          <div className="rounded-xl bg-slate-50 py-10 text-center text-xs text-slate-400">
            暂无信用卡记录，点击右上角「新增信用卡」添加你的各张卡片额度、账单日、还款日与当前欠款。
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {overview.cards.map((c) => {
              const highUtil = c.utilization_pct >= 80;
              return (
                <div key={c.id} className="rounded-2xl border border-slate-200/80 bg-slate-50/50 p-4 space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-bold text-slate-900">{c.name}</span>
                        {c.last4 && <span className="text-xs font-mono text-slate-400">*{c.last4}</span>}
                        <span
                          className={`rounded-md px-2 py-0.5 text-[11px] font-medium ${
                            c.remaining_bill <= 0
                              ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                              : c.days_until_due <= 3
                                ? 'bg-rose-50 text-rose-700 border border-rose-200'
                                : 'bg-amber-50 text-amber-700 border border-amber-200'
                          }`}
                        >
                          {c.remaining_bill <= 0
                            ? '本期账单已还清'
                            : c.days_until_due === 0
                              ? '今日最后还款日'
                              : `距还款日剩 ${c.days_until_due} 天 (${c.next_due_date.slice(5)})`}
                        </span>
                      </div>
                      <div className="mt-1 text-[11px] text-slate-500">
                        账单日每月 {c.statement_day} 号 · 还款日 {c.due_day} 号（宽限 {c.grace_days} 天）· 今日刷卡免息{' '}
                        <span className="font-semibold text-indigo-700">{c.swipe_free_days} 天</span> · 费率{' '}
                        {(c.swipe_fee_rate * 100).toFixed(2)}%
                      </div>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={() => openEditCard(c)}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-white hover:text-slate-700 cursor-pointer"
                      >
                        <Edit3 className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={async () => {
                          if (!window.confirm(`确定删除信用卡「${c.name}」吗？`)) return;
                          await api.deleteCreditCard(c.id);
                          await loadData(true);
                        }}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 cursor-pointer"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* Utilization bar */}
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-slate-500">
                        已用欠款 <span className="font-semibold text-slate-900">{formatMoney(c.current_debt)}</span> / 总额度{' '}
                        {formatMoney(c.credit_limit)}
                      </span>
                      <span className={`font-semibold ${highUtil ? 'text-rose-600' : 'text-emerald-600'}`}>
                        可用 {formatMoney(c.available_credit)} ({c.utilization_pct}%)
                      </span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-slate-200 overflow-hidden">
                      <div
                        className={`h-full rounded-full ${highUtil ? 'bg-rose-500' : 'bg-indigo-500'}`}
                        style={{ width: `${Math.min(100, c.utilization_pct)}%` }}
                      />
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-white border border-slate-100 p-3 text-xs">
                    <div className="flex flex-wrap gap-4">
                      <div>
                        <span className="text-slate-400">本期应还</span>
                        <div className="font-semibold text-slate-800">{formatMoney(c.statement_bill_amount)}</div>
                      </div>
                      <div>
                        <span className="text-slate-400">已还</span>
                        <div className="font-semibold text-emerald-600">{formatMoney(c.paid_amount_this_cycle)}</div>
                      </div>
                      <div>
                        <span className="text-slate-400">剩余待还</span>
                        <div className={`font-bold ${c.remaining_bill > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                          {formatMoney(c.remaining_bill)}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <button
                        type="button"
                        onClick={() => openQuickSwipeForCard(c)}
                        className="inline-flex items-center gap-1 rounded-lg bg-indigo-50 border border-indigo-200 px-2.5 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 cursor-pointer"
                      >
                        <ArrowRight className="h-3 w-3" /> 刷卡/还款
                      </button>
                      <button
                        type="button"
                        onClick={async () => {
                          const nextBill = window.prompt(
                            `录入「${c.name}」最新一期账单金额（已还金额将重置为 0）：`,
                            String(c.current_debt),
                          );
                          if (nextBill === null) return;
                          const val = Number(nextBill);
                          if (Number.isNaN(val) || val < 0) return;
                          await api.updateCreditCard(c.id, {
                            statement_bill_amount: val,
                            paid_amount_this_cycle: 0,
                          });
                          onNotify(`已开启「${c.name}」新期账单：${formatMoney(val)}`);
                          await loadData(true);
                        }}
                        className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-100 cursor-pointer"
                      >
                        出新账单
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Section 3: Loans Management */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Landmark className="h-5 w-5 text-indigo-600" />
            <h3 className="text-base font-semibold text-slate-900">贷款与月供管理 ({overview.loans.length})</h3>
          </div>
          <button
            type="button"
            onClick={openAddLoan}
            className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-3.5 py-2 text-xs font-medium text-white hover:bg-slate-800 cursor-pointer"
          >
            <Plus className="h-3.5 w-3.5" /> 新增贷款
          </button>
        </div>

        {showLoanForm && (
          <form onSubmit={handleSaveLoan} className="rounded-xl border border-indigo-200 bg-indigo-50/30 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-900">{editingLoanId ? '编辑贷款信息' : '新增贷款'}</span>
              <button type="button" onClick={() => setShowLoanForm(false)} className="text-xs text-slate-500 cursor-pointer">
                取消
              </button>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 text-xs">
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">贷款名称 *</label>
                <input
                  type="text"
                  required
                  value={loanForm.name}
                  onChange={(e) => setLoanForm({ ...loanForm, name: e.target.value })}
                  placeholder="如：招行闪电贷 / 房贷"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">放款机构</label>
                <input
                  type="text"
                  value={loanForm.lender}
                  onChange={(e) => setLoanForm({ ...loanForm, lender: e.target.value })}
                  placeholder="招商银行 / 微众银行"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">剩余待还本金（元）</label>
                <input
                  type="number"
                  step="0.01"
                  value={loanForm.remaining_principal}
                  onChange={(e) => setLoanForm({ ...loanForm, remaining_principal: e.target.value })}
                  placeholder="80000"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">每月应还月供（元）*</label>
                <input
                  type="number"
                  step="0.01"
                  required
                  value={loanForm.monthly_payment}
                  onChange={(e) => setLoanForm({ ...loanForm, monthly_payment: e.target.value })}
                  placeholder="3200"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">本月已还金额（元）</label>
                <input
                  type="number"
                  step="0.01"
                  value={loanForm.paid_this_month}
                  onChange={(e) => setLoanForm({ ...loanForm, paid_this_month: e.target.value })}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">每月还款日 (1-31)</label>
                <input
                  type="number"
                  min={1}
                  max={31}
                  value={loanForm.due_day}
                  onChange={(e) => setLoanForm({ ...loanForm, due_day: e.target.value })}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">年化利率 APR (%) / 剩期数</label>
                <div className="flex gap-1.5">
                  <input
                    type="number"
                    step="0.01"
                    value={loanForm.apr}
                    onChange={(e) => setLoanForm({ ...loanForm, apr: e.target.value })}
                    className="w-1/2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                  />
                  <input
                    type="number"
                    min={0}
                    value={loanForm.remaining_periods}
                    onChange={(e) => setLoanForm({ ...loanForm, remaining_periods: e.target.value })}
                    className="w-1/2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                  />
                </div>
              </div>
              <div>
                <label className="block text-[11px] text-slate-600 mb-1">状态</label>
                <select
                  value={loanForm.status}
                  onChange={(e) => setLoanForm({ ...loanForm, status: e.target.value as Loan['status'] })}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                >
                  <option value="active">还款中</option>
                  <option value="paid_off">已结清</option>
                </select>
              </div>
            </div>
            <div className="flex justify-end">
              <button
                type="submit"
                className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 cursor-pointer"
              >
                {editingLoanId ? '保存修改' : '确认添加'}
              </button>
            </div>
          </form>
        )}

        {overview.loans.length === 0 ? (
          <div className="rounded-xl bg-slate-50 py-8 text-center text-xs text-slate-400">
            暂无贷款记录，若有网贷、消费贷、车贷或房贷，可点击右上角添加，规划器将优先为你预留贷款刚性月供资金。
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {overview.loans.map((l) => (
              <div key={l.id} className="rounded-2xl border border-slate-200/80 bg-slate-50/50 p-4 space-y-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold text-slate-900">{l.name}</span>
                      {l.lender && <span className="text-xs text-slate-500">· {l.lender}</span>}
                      <span
                        className={`rounded-md px-2 py-0.5 text-[11px] font-medium ${
                          l.remaining_month_payment <= 0
                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                            : l.days_until_due <= 3
                              ? 'bg-rose-50 text-rose-700 border border-rose-200'
                              : 'bg-amber-50 text-amber-700 border border-amber-200'
                        }`}
                      >
                        {l.remaining_month_payment <= 0
                          ? '本月月供已还'
                          : `距还款日剩 ${l.days_until_due} 天 (${l.next_due_date.slice(5)})`}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500">
                      每月 {l.due_day} 号还款 · 剩余本金 {formatMoney(l.remaining_principal)} · 年化 {l.apr}% · 剩 {l.remaining_periods} 期
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => openEditLoan(l)}
                      className="rounded-lg p-1.5 text-slate-400 hover:bg-white hover:text-slate-700 cursor-pointer"
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        if (!window.confirm(`确定删除贷款「${l.name}」吗？`)) return;
                        await api.deleteLoan(l.id);
                        await loadData(true);
                      }}
                      className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 cursor-pointer"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-white border border-slate-100 p-3 text-xs">
                  <div className="flex gap-4">
                    <div>
                      <span className="text-slate-400">月供总额</span>
                      <div className="font-semibold text-slate-800">{formatMoney(l.monthly_payment)}</div>
                    </div>
                    <div>
                      <span className="text-slate-400">本月待还</span>
                      <div className={`font-bold ${l.remaining_month_payment > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                        {formatMoney(l.remaining_month_payment)}
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-1.5">
                    {l.remaining_month_payment > 0 && (
                      <button
                        type="button"
                        onClick={async () => {
                          await api.executeDebtOperation({
                            op_type: 'repay_loan',
                            target_loan_id: l.id,
                            source_account_id: syncToFinance && cashAccountId ? Number(cashAccountId) : null,
                            amount: l.remaining_month_payment,
                            sync_to_finance: syncToFinance,
                          });
                          onNotify(`已标记贷款「${l.name}」本月月供已还`);
                          await loadData(true);
                          onRefreshAll();
                        }}
                        className="rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-emerald-700 cursor-pointer"
                      >
                        还本月月供
                      </button>
                    )}
                    {l.paid_this_month > 0 && (
                      <button
                        type="button"
                        onClick={async () => {
                          await api.updateLoan(l.id, { paid_this_month: 0 });
                          onNotify(`已重置「${l.name}」进入下期月供`);
                          await loadData(true);
                        }}
                        className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-100 cursor-pointer"
                      >
                        进入下期
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Section 4: Recent Debt Operations Log */}
      {overview.operations.length > 0 && (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3">
          <h3 className="text-sm font-semibold text-slate-900">近期刷卡周转与还款记录 ({overview.operations.length})</h3>
          <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
            {overview.operations.map((op) => (
              <div
                key={op.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-100 bg-slate-50/70 px-3.5 py-2.5 text-xs"
              >
                <div>
                  <div className="font-medium text-slate-900">{op.note}</div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    {op.op_date} · 本金 {formatMoney(op.amount)}
                    {op.fee_amount > 0 ? ` · 手续费 ${formatMoney(op.fee_amount)}` : ''}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleUndoOp(op.id)}
                  className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-600 hover:bg-rose-50 hover:text-rose-600 cursor-pointer"
                >
                  <RotateCcw className="h-3 w-3" /> 撤销
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
