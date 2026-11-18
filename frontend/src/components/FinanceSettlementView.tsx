import React, { useEffect, useState } from 'react';
import {
  BellRing,
  CheckCheck,
  CreditCard,
  Landmark,
  Plus,
  Receipt,
  Sparkles,
  Trash2,
  TrendingDown,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import { api } from '../api';
import type { Account, RecurringBill, Settlement, Transaction } from '../types';
import { ACCOUNT_TYPE_LABELS, formatMoney } from '../utils/format';

interface Props {
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
}

type SubTab = 'checkout' | 'transactions' | 'accounts' | 'bills';

export const FinanceSettlementView: React.FC<Props> = ({ onRefreshDashboard, onNotify }) => {
  const [subTab, setSubTab] = useState<SubTab>('checkout');

  const [accounts, setAccounts] = useState<Account[]>([]);
  const [totalAssets, setTotalAssets] = useState(0);
  const [totalLiabilities, setTotalLiabilities] = useState(0);
  const [netWorth, setNetWorth] = useState(0);

  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [unsettledSummary, setUnsettledSummary] = useState({
    count: 0,
    income: 0,
    expense: 0,
    net_cashflow: 0,
    category_breakdown: {} as Record<string, number>,
  });
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [bills, setBills] = useState<RecurringBill[]>([]);

  // Checkout state
  const [periodLabel, setPeriodLabel] = useState('');
  const [settling, setSettling] = useState(false);

  // Add transaction state
  const [txType, setTxType] = useState<'expense' | 'income'>('expense');
  const [txAmount, setTxAmount] = useState('');
  const [txCategory, setTxCategory] = useState('餐饮');
  const [txAccountId, setTxAccountId] = useState<string>('');
  const [txDesc, setTxDesc] = useState('');

  // Add account state
  const [accName, setAccName] = useState('');
  const [accType, setAccType] = useState<'bank' | 'cash' | 'investment' | 'credit_card' | 'loan'>('bank');
  const [accBalance, setAccBalance] = useState('');

  // Add bill state
  const [billName, setBillName] = useState('');
  const [billAmount, setBillAmount] = useState('');
  const [billCategory, setBillCategory] = useState('住房');
  const [billDay, setBillDay] = useState('15');
  const [billDue, setBillDue] = useState(new Date().toISOString().slice(0, 10));
  const [billAccountId, setBillAccountId] = useState('');

  const loadAll = async () => {
    try {
      const [accRes, txRes, setRes, billRes] = await Promise.all([
        api.listAccounts(),
        api.listTransactions(),
        api.listSettlements(),
        api.listRecurringBills(),
      ]);
      setAccounts(accRes.accounts);
      setTotalAssets(accRes.total_assets);
      setTotalLiabilities(accRes.total_liabilities);
      setNetWorth(accRes.net_worth);
      if (!txAccountId && accRes.accounts.length > 0) {
        setTxAccountId(String(accRes.accounts[0].id));
        setBillAccountId(String(accRes.accounts[0].id));
      }
      setTransactions(txRes.transactions);
      setUnsettledSummary(txRes.unsettled_summary);
      setSettlements(setRes);
      setBills(billRes);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载财务数据失败');
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleSettle = async () => {
    if (unsettledSummary.count === 0) {
      onNotify('当前没有未结账流水');
      return;
    }
    setSettling(true);
    try {
      const res = await api.createSettlement(periodLabel);
      setPeriodLabel('');
      onNotify(`结账完成！已归档 ${res.tx_count} 笔账目，净结余 ${formatMoney(res.net_cashflow, true)}`);
      await loadAll();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '结账失败');
    } finally {
      setSettling(false);
    }
  };

  const handleAddTx = async (e: React.FormEvent) => {
    e.preventDefault();
    const amt = Number(txAmount);
    if (!amt || amt <= 0) return;
    try {
      await api.createTransaction({
        type: txType,
        amount: amt,
        category: txCategory,
        account_id: txAccountId ? Number(txAccountId) : null,
        description: txDesc,
      });
      setTxAmount('');
      setTxDesc('');
      onNotify('记账成功，账户余额与待结账池已更新');
      await loadAll();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '记账失败');
    }
  };

  const handleDeleteTx = async (id: number) => {
    await api.deleteTransaction(id);
    await loadAll();
    onRefreshDashboard();
  };

  const handleAddAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accName.trim()) return;
    await api.createAccount({
      name: accName.trim(),
      type: accType,
      balance: Number(accBalance || 0),
    });
    setAccName('');
    setAccBalance('');
    onNotify('账户已添加');
    await loadAll();
    onRefreshDashboard();
  };

  const handleUpdateBalance = async (acc: Account, newBalStr: string | null) => {
    if (newBalStr === null) return;
    const val = Number(newBalStr);
    if (Number.isNaN(val)) return;
    await api.updateAccount(acc.id, { balance: val });
    await loadAll();
    onRefreshDashboard();
  };

  const handleAddBill = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!billName.trim() || !billAmount) return;
    await api.createRecurringBill({
      name: billName.trim(),
      amount: Number(billAmount),
      category: billCategory,
      account_id: billAccountId ? Number(billAccountId) : null,
      billing_day: Number(billDay || 1),
      cycle: 'monthly',
      next_due_date: billDue,
      auto_remind_days: 3,
    });
    setBillName('');
    setBillAmount('');
    onNotify('周期固定账单已添加');
    await loadAll();
    onRefreshDashboard();
  };

  const handleSyncBills = async () => {
    const res = await api.syncBillReminders(true);
    onNotify(res.created_count > 0 ? `已为 ${res.created_count} 笔账单生成到期提醒` : '所有周期账单均有对应待办提醒');
    onRefreshDashboard();
  };

  const handlePayBill = async (billId: number) => {
    const res = await api.payBill(billId);
    onNotify(`已扣款入账 ${formatMoney(res.transaction.amount)}，下期到期日推进至 ${res.bill.next_due_date}`);
    await loadAll();
    onRefreshDashboard();
  };

  const breakdownEntries = Object.entries(unsettledSummary.category_breakdown).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-6">
      {/* Top Asset / Liability / Unsettled Strip */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <Landmark className="h-3.5 w-3.5 text-emerald-600" /> 个人净资产
          </div>
          <div className="mt-2 text-2xl font-bold text-slate-900">{formatMoney(netWorth)}</div>
          <div className="mt-1 text-xs text-slate-400">总资产 {formatMoney(totalAssets)} · 负债 {formatMoney(totalLiabilities)}</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <TrendingUp className="h-3.5 w-3.5 text-emerald-600" /> 待结账收入
          </div>
          <div className="mt-2 text-2xl font-bold text-emerald-600">{formatMoney(unsettledSummary.income)}</div>
          <div className="mt-1 text-xs text-slate-400">待结账总数：{unsettledSummary.count} 笔</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <TrendingDown className="h-3.5 w-3.5 text-rose-500" /> 待结账支出
          </div>
          <div className="mt-2 text-2xl font-bold text-rose-600">{formatMoney(unsettledSummary.expense)}</div>
          <div className="mt-1 text-xs text-slate-400">覆盖 {breakdownEntries.length} 个支出分类</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <Receipt className="h-3.5 w-3.5 text-indigo-600" /> 本期预估结余
          </div>
          <div className={`mt-2 text-2xl font-bold ${unsettledSummary.net_cashflow >= 0 ? 'text-indigo-600' : 'text-rose-600'}`}>
            {formatMoney(unsettledSummary.net_cashflow, true)}
          </div>
          <div className="mt-1 text-xs text-slate-400">结账后将生成资产快照</div>
        </div>
      </div>

      {/* Sub-navigation */}
      <div className="flex flex-wrap gap-2 rounded-2xl bg-white p-1.5 border border-slate-200/80 w-fit">
        {[
          { key: 'checkout', label: '一键结账 & 财务总结' },
          { key: 'transactions', label: '记账与流水明细' },
          { key: 'accounts', label: '资产与负债账户' },
          { key: 'bills', label: '周期固定账单' },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setSubTab(t.key as SubTab)}
            className={`rounded-xl px-4 py-2 text-xs font-medium transition cursor-pointer ${
              subTab === t.key ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* SubTab 1: Checkout & Settlement Reports */}
      {subTab === 'checkout' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* Left: Current Checkout Pool */}
          <div className="lg:col-span-5 rounded-2xl border border-indigo-200 bg-gradient-to-b from-indigo-50/60 to-white p-5 shadow-xs space-y-4">
            <div className="flex items-center gap-2">
              <div className="rounded-xl bg-indigo-600 p-2 text-white">
                <CheckCheck className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-slate-900">本期收支结账台</h3>
                <p className="text-xs text-slate-500">结算当前所有未归档流水，生成本期财务总结与净资产快照</p>
              </div>
            </div>

            <div className="rounded-xl bg-white border border-slate-200/80 p-4 space-y-2.5 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-500">待结账流水笔数</span>
                <span className="font-semibold">{unsettledSummary.count} 笔</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">本期总收入</span>
                <span className="font-semibold text-emerald-600">{formatMoney(unsettledSummary.income)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">本期总支出</span>
                <span className="font-semibold text-rose-600">{formatMoney(unsettledSummary.expense)}</span>
              </div>
              <div className="border-t border-slate-100 pt-2 flex justify-between text-base">
                <span className="font-medium text-slate-700">净现金流结余</span>
                <span className={`font-bold ${unsettledSummary.net_cashflow >= 0 ? 'text-indigo-600' : 'text-rose-600'}`}>
                  {formatMoney(unsettledSummary.net_cashflow, true)}
                </span>
              </div>
            </div>

            {breakdownEntries.length > 0 && (
              <div className="rounded-xl bg-white border border-slate-200/80 p-4">
                <div className="text-xs font-semibold text-slate-700 mb-2.5">待结支出分类构成</div>
                <div className="space-y-2">
                  {breakdownEntries.map(([cat, amt]) => (
                    <div key={cat} className="flex items-center justify-between text-xs">
                      <span className="text-slate-600">{cat}</span>
                      <span className="font-medium text-slate-900">{formatMoney(amt)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-2.5">
              <input
                type="text"
                value={periodLabel}
                onChange={(e) => setPeriodLabel(e.target.value)}
                placeholder="结账周期名称（如：2026年11月结账，留空自动命名）"
                className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm focus:border-indigo-500 focus:outline-none"
              />
              <button
                onClick={handleSettle}
                disabled={settling || unsettledSummary.count === 0}
                className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-45 transition cursor-pointer"
              >
                <Sparkles className="h-4 w-4" />
                {settling ? '正在结账并生成 AI 总结...' : '确认结账并生成个人财务总结'}
              </button>
            </div>
          </div>

          {/* Right: Settlement History */}
          <div className="lg:col-span-7 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <h3 className="text-base font-semibold text-slate-900 border-b border-slate-100 pb-3">
              历史结账报告与财务总结 ({settlements.length})
            </h3>
            <div className="mt-4 space-y-4 max-h-[580px] overflow-y-auto pr-1">
              {settlements.length === 0 ? (
                <div className="rounded-xl bg-slate-50 py-12 text-center text-xs text-slate-400">
                  暂无历史结账记录，在左侧完成第一次周期结账吧
                </div>
              ) : (
                settlements.map((st) => (
                  <div key={st.id} className="rounded-2xl border border-slate-200/80 bg-slate-50/50 p-4 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <span className="font-semibold text-slate-900">{st.period_label}</span>
                        <span className="ml-2 text-xs text-slate-400">
                          {st.start_date} ~ {st.end_date} · {st.tx_count} 笔
                        </span>
                      </div>
                      <span className="rounded-lg bg-emerald-50 border border-emerald-200 px-2.5 py-1 text-xs font-medium text-emerald-700">
                        时点净资产 {formatMoney(st.net_worth_snapshot)}
                      </span>
                    </div>

                    <div className="grid grid-cols-3 gap-2 rounded-xl bg-white p-3 text-xs border border-slate-100">
                      <div>
                        <span className="text-slate-400">收入</span>
                        <div className="mt-0.5 font-semibold text-emerald-600">{formatMoney(st.total_income)}</div>
                      </div>
                      <div>
                        <span className="text-slate-400">支出</span>
                        <div className="mt-0.5 font-semibold text-rose-600">{formatMoney(st.total_expense)}</div>
                      </div>
                      <div>
                        <span className="text-slate-400">净结余</span>
                        <div className={`mt-0.5 font-semibold ${st.net_cashflow >= 0 ? 'text-indigo-600' : 'text-rose-600'}`}>
                          {formatMoney(st.net_cashflow, true)}
                        </div>
                      </div>
                    </div>

                    <div className="rounded-xl bg-indigo-50/70 border border-indigo-100 p-3 text-xs text-slate-700 leading-relaxed">
                      <span className="font-semibold text-indigo-700">AI 财务总结：</span>
                      {st.ai_summary}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* SubTab 2: Transactions */}
      {subTab === 'transactions' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <form onSubmit={handleAddTx} className="lg:col-span-4 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3 h-fit">
            <h3 className="text-base font-semibold text-slate-900">快速记一笔</h3>
            <div className="grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1 text-xs">
              <button
                type="button"
                onClick={() => {
                  setTxType('expense');
                  setTxCategory('餐饮');
                }}
                className={`rounded-lg py-1.5 font-medium cursor-pointer ${
                  txType === 'expense' ? 'bg-white text-rose-600 shadow-xs' : 'text-slate-600'
                }`}
              >
                支出
              </button>
              <button
                type="button"
                onClick={() => {
                  setTxType('income');
                  setTxCategory('工资收入');
                }}
                className={`rounded-lg py-1.5 font-medium cursor-pointer ${
                  txType === 'income' ? 'bg-white text-emerald-600 shadow-xs' : 'text-slate-600'
                }`}
              >
                收入
              </button>
            </div>
            <input
              type="number"
              step="0.01"
              required
              value={txAmount}
              onChange={(e) => setTxAmount(e.target.value)}
              placeholder="金额（元）"
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
            <select
              value={txCategory}
              onChange={(e) => setTxCategory(e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm"
            >
              {(txType === 'expense'
                ? ['餐饮', '交通', '住房', '购物', '订阅服务', '通讯', '医疗', '娱乐', '其他']
                : ['工资收入', '奖金', '报销回款', '理财收益', '其他收入']
              ).map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <select
              value={txAccountId}
              onChange={(e) => setTxAccountId(e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm"
            >
              <option value="">不关联特定账户</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name} ({formatMoney(a.balance)})</option>
              ))}
            </select>
            <input
              type="text"
              value={txDesc}
              onChange={(e) => setTxDesc(e.target.value)}
              placeholder="消费/收入说明"
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
            <button
              type="submit"
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-xl bg-slate-900 py-2.5 text-sm font-medium text-white hover:bg-slate-800 cursor-pointer"
            >
              <Plus className="h-4 w-4" /> 保存账目
            </button>
          </form>

          <div className="lg:col-span-8 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <h3 className="text-base font-semibold text-slate-900 border-b border-slate-100 pb-3">
              收支流水明细 ({transactions.length})
            </h3>
            <div className="mt-3 space-y-2 max-h-[540px] overflow-y-auto pr-1">
              {transactions.map((tx) => (
                <div
                  key={tx.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-3 text-sm"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-900">{tx.category}</span>
                      {tx.description && <span className="text-xs text-slate-500 truncate">{tx.description}</span>}
                      <span
                        className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
                          tx.settlement_id ? 'bg-slate-200 text-slate-600' : 'bg-amber-50 text-amber-700 border border-amber-200'
                        }`}
                      >
                        {tx.settlement_id ? '已结账' : '待结账'}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] text-slate-400">
                      {tx.occurred_at} · {tx.account_name || '未关联账户'}
                    </div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className={`font-semibold ${tx.type === 'income' ? 'text-emerald-600' : 'text-slate-900'}`}>
                      {tx.type === 'income' ? `+${formatMoney(tx.amount)}` : `-${formatMoney(tx.amount)}`}
                    </span>
                    {!tx.settlement_id && (
                      <button
                        onClick={() => handleDeleteTx(tx.id)}
                        className="text-slate-300 hover:text-rose-500 cursor-pointer"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* SubTab 3: Accounts */}
      {subTab === 'accounts' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <form onSubmit={handleAddAccount} className="lg:col-span-4 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3 h-fit">
            <h3 className="text-base font-semibold text-slate-900">新增资产 / 负债账户</h3>
            <input
              type="text"
              required
              value={accName}
              onChange={(e) => setAccName(e.target.value)}
              placeholder="账户名称（如：微信零钱、工商银行）"
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm"
            />
            <select
              value={accType}
              onChange={(e) => setAccType(e.target.value as Account['type'])}
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm"
            >
              <option value="bank">银行储蓄</option>
              <option value="cash">现金 / 余额宝 / 微信</option>
              <option value="investment">理财 / 基金 / 股票</option>
              <option value="credit_card">信用卡（负债填负数）</option>
              <option value="loan">贷款 / 花呗白条（填负数）</option>
            </select>
            <input
              type="number"
              step="0.01"
              required
              value={accBalance}
              onChange={(e) => setAccBalance(e.target.value)}
              placeholder="当前余额（负债请填负数，如 -2500）"
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm"
            />
            <button
              type="submit"
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-xl bg-slate-900 py-2.5 text-sm font-medium text-white hover:bg-slate-800 cursor-pointer"
            >
              <Plus className="h-4 w-4" /> 添加账户
            </button>
          </form>

          <div className="lg:col-span-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {accounts.map((acc) => (
              <div key={acc.id} className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs flex flex-col justify-between">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <Wallet className="h-4 w-4 text-indigo-600" />
                      <span className="font-semibold text-slate-900">{acc.name}</span>
                    </div>
                    <span className="mt-1 inline-block rounded-md bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                      {ACCOUNT_TYPE_LABELS[acc.type] || acc.type}
                    </span>
                  </div>
                  <button
                    onClick={async () => {
                      await api.deleteAccount(acc.id);
                      await loadAll();
                      onRefreshDashboard();
                    }}
                    className="text-slate-300 hover:text-rose-500 cursor-pointer"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
                <div className="mt-4 flex items-end justify-between">
                  <div className={`text-xl font-bold ${acc.balance >= 0 ? 'text-slate-900' : 'text-rose-600'}`}>
                    {formatMoney(acc.balance)}
                  </div>
                  <button
                    onClick={() => {
                      const val = window.prompt(`更新「${acc.name}」余额为：`, String(acc.balance));
                      handleUpdateBalance(acc, val);
                    }}
                    className="text-xs text-indigo-600 hover:underline cursor-pointer"
                  >
                    调整余额
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SubTab 4: Recurring Bills */}
      {subTab === 'bills' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <form onSubmit={handleAddBill} className="lg:col-span-4 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3 h-fit">
            <h3 className="text-base font-semibold text-slate-900">新增周期性固定账单</h3>
            <input
              type="text"
              required
              value={billName}
              onChange={(e) => setBillName(e.target.value)}
              placeholder="账单名称（如：房租、宽带、会员）"
              className="w-full rounded-xl border border-slate-200 px-3.5 py-2 text-sm"
            />
            <div className="grid grid-cols-2 gap-2">
              <input
                type="number"
                step="0.01"
                required
                value={billAmount}
                onChange={(e) => setBillAmount(e.target.value)}
                placeholder="金额（元）"
                className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
              />
              <input
                type="text"
                value={billCategory}
                onChange={(e) => setBillCategory(e.target.value)}
                placeholder="分类"
                className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input
                type="number"
                min={1}
                max={31}
                value={billDay}
                onChange={(e) => setBillDay(e.target.value)}
                placeholder="每月账单日"
                className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
              />
              <input
                type="date"
                value={billDue}
                onChange={(e) => setBillDue(e.target.value)}
                className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
              />
            </div>
            <select
              value={billAccountId}
              onChange={(e) => setBillAccountId(e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            >
              <option value="">默认扣款账户</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            <button
              type="submit"
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-xl bg-slate-900 py-2.5 text-sm font-medium text-white hover:bg-slate-800 cursor-pointer"
            >
              <Plus className="h-4 w-4" /> 添加固定账单
            </button>
          </form>

          <div className="lg:col-span-8 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <CreditCard className="h-5 w-5 text-orange-500" />
                <h3 className="text-base font-semibold text-slate-900">固定账单与到期提醒联动</h3>
              </div>
              <button
                onClick={handleSyncBills}
                className="inline-flex items-center gap-1.5 rounded-xl bg-orange-50 border border-orange-200 px-3.5 py-1.5 text-xs font-medium text-orange-700 hover:bg-orange-100 cursor-pointer"
              >
                <BellRing className="h-3.5 w-3.5" /> 一键生成待办提醒
              </button>
            </div>

            <div className="space-y-2.5">
              {bills.map((b) => (
                <div key={b.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-100 bg-slate-50/70 p-4">
                  <div>
                    <div className="font-semibold text-slate-900">{b.name}</div>
                    <div className="mt-1 text-xs text-slate-500">
                      金额 <span className="font-medium text-slate-800">{formatMoney(b.amount)}</span> · 下次到期：
                      <span className="text-orange-600 font-medium">{b.next_due_date}</span> · {b.account_name || '默认账户'}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handlePayBill(b.id)}
                      className="rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 cursor-pointer"
                    >
                      扣款入账并顺延下期
                    </button>
                    <button
                      onClick={async () => {
                        await api.deleteRecurringBill(b.id);
                        await loadAll();
                        onRefreshDashboard();
                      }}
                      className="p-1.5 text-slate-300 hover:text-rose-500 cursor-pointer"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
