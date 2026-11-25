import React, { useEffect, useState } from 'react';
import {
  ArrowRightLeft,
  Banknote,
  CheckCircle2,
  CreditCard as CreditCardIcon,
  Layers,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Unlink,
  Upload,
} from 'lucide-react';
import { api } from '../api';
import type { ReconcileOverviewResponse } from '../types';
import { CHANNEL_META, formatMoney, RECONCILE_META } from '../utils/format';

interface Props {
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
  onJumpToImport: () => void;
}

const QUICK_CATEGORIES = ['餐饮', '购物', '交通', '住房', '订阅服务', '医疗', '通讯', '娱乐', '其他'];

export const BillReconcileView: React.FC<Props> = ({ onRefreshDashboard, onNotify, onJumpToImport }) => {
  const [overview, setOverview] = useState<ReconcileOverviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [reconciling, setReconciling] = useState(false);

  // Filters
  const [month, setMonth] = useState('');
  const [channel, setChannel] = useState('all');
  const [cardLast4, setCardLast4] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [searchQ, setSearchQ] = useState('');
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [viewMode, setViewMode] = useState<'ledger' | 'pairs' | 'direct'>('ledger');

  // Quick Cash / Direct Card entry
  const [quickChannel, setQuickChannel] = useState<'cash' | 'card_direct'>('cash');
  const [quickAmount, setQuickAmount] = useState('');
  const [quickCategory, setQuickCategory] = useState('餐饮');
  const [quickBank, setQuickBank] = useState('');
  const [quickLast4, setQuickLast4] = useState('');
  const [quickDesc, setQuickDesc] = useState('');
  const [submittingQuick, setSubmittingQuick] = useState(false);

  const loadOverview = async () => {
    setLoading(true);
    try {
      const res = await api.getReconcileOverview({
        month: month || undefined,
        channel,
        card_last4: cardLast4 || undefined,
        reconcile_status: statusFilter,
        q: searchQ.trim() || undefined,
        include_duplicates: includeDuplicates,
      });
      setOverview(res);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载全渠道账单失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadOverview();
  }, [month, channel, cardLast4, statusFilter, includeDuplicates]);

  const handleRunReconcile = async () => {
    setReconciling(true);
    try {
      const res = await api.runReconcile({ backfill_accounts: true, auto_create_accounts: true });
      const rec = res.reconciliation;
      const parts: string[] = [];
      if (rec.newly_matched_count > 0) parts.push(`新匹配去重 ${rec.newly_matched_count} 笔`);
      if (rec.auto_created_cards?.length) parts.push(`自动建信用卡 ${rec.auto_created_cards.length} 张`);
      if (rec.auto_created_accounts?.length) parts.push(`自动建账户 ${rec.auto_created_accounts.length} 个`);
      if (rec.backfilled_count > 0) parts.push(`补全归属 ${rec.backfilled_count} 笔`);
      onNotify(
        parts.length > 0
          ? `多方对账完成：${parts.join('，')}`
          : `已完成核对：累计已匹配 ${rec.total_matched_count} 笔，直接刷卡独有 ${rec.total_direct_card_count} 笔`,
      );
      await loadOverview();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '多方核对失败');
    } finally {
      setReconciling(false);
    }
  };

  const handleUnmatch = async (txId: number) => {
    try {
      await api.unmatchReconciledPair(txId);
      onNotify('已取消该对流水匹配，两笔记录将独立计入账单');
      await loadOverview();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '取消匹配失败');
    }
  };

  const handleQuickAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const amt = Number(quickAmount);
    if (!amt || amt <= 0) return;
    setSubmittingQuick(true);
    try {
      const pm =
        quickChannel === 'cash'
          ? '现金'
          : quickLast4.trim()
            ? `${quickBank.trim() || '银行'}信用卡(${quickLast4.trim()})`
            : `${quickBank.trim() || '银行卡'}直接刷卡`;
      await api.createTransaction({
        type: 'expense',
        amount: amt,
        category: quickCategory,
        description: quickDesc.trim() || (quickChannel === 'cash' ? '现金消费' : `${quickBank.trim() || '银行卡'}直接刷卡`),
        source_type: quickChannel === 'cash' ? 'cash' : 'card_direct',
        payment_channel: quickChannel,
        payment_method: pm,
        bank_name: quickChannel === 'card_direct' ? quickBank.trim() : '现金',
        card_last4: quickChannel === 'card_direct' ? quickLast4.trim() : '',
      } as Record<string, unknown>);
      setQuickAmount('');
      setQuickDesc('');
      onNotify(
        quickChannel === 'cash'
          ? `已记入现金支出 ${formatMoney(amt)}（自动绑定现金钱包）`
          : `已记入直接刷卡 ${formatMoney(amt)}（自动匹配/创建对应卡片账户）`,
      );
      await loadOverview();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '记账失败');
    } finally {
      setSubmittingQuick(false);
    }
  };

  const summary = overview?.summary;
  const channels = overview?.channel_breakdown || [];
  const cards = overview?.card_breakdown || [];
  const pairs = overview?.matched_pairs || [];
  const directCards = overview?.direct_card_transactions || [];
  const txs = overview?.transactions || [];

  const getChannelExpense = (k: string) => channels.find((c) => c.channel === k)?.expense ?? 0;
  const getChannelCount = (k: string) => channels.find((c) => c.channel === k)?.count ?? 0;

  return (
    <div className="space-y-6">
      {/* Top Action Header */}
      <div className="rounded-2xl border border-indigo-200/80 bg-gradient-to-r from-indigo-50/70 via-white to-emerald-50/50 p-5 shadow-xs flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-indigo-600" />
            <h2 className="text-base font-semibold text-slate-900">全渠道统一消费账单 & 多方核对中心</h2>
          </div>
          <p className="text-xs text-slate-500">
            自动整合支付宝、微信、邮箱信用卡/银行卡账单与现金流水；按卡尾号 + 时间 + 金额多方交叉核对去重，精准保留直接刷卡并自动创建对应账户与信用卡
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onJumpToImport}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
          >
            <Upload className="h-3.5 w-3.5 text-emerald-600" />
            导入支付宝/微信/邮箱账单
          </button>
          <button
            type="button"
            onClick={handleRunReconcile}
            disabled={reconciling}
            className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${reconciling ? 'animate-spin' : ''}`} />
            {reconciling ? '正在多方比对...' : '一键多方比对 & 补建卡片账户'}
          </button>
        </div>
      </div>

      {/* Channel KPI Cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <button
          type="button"
          onClick={() => setChannel(channel === 'alipay' ? 'all' : 'alipay')}
          className={`text-left rounded-2xl border p-4 transition cursor-pointer ${
            channel === 'alipay' ? 'border-blue-400 bg-blue-50/70 ring-1 ring-blue-300' : 'border-slate-200/80 bg-white hover:border-blue-200'
          }`}
        >
          <div className="text-xs text-blue-700 font-medium">支付宝消费</div>
          <div className="mt-1.5 text-xl font-bold text-slate-900">{formatMoney(getChannelExpense('alipay'))}</div>
          <div className="mt-0.5 text-[11px] text-slate-400">{getChannelCount('alipay')} 笔有效流水</div>
        </button>
        <button
          type="button"
          onClick={() => setChannel(channel === 'wechat' ? 'all' : 'wechat')}
          className={`text-left rounded-2xl border p-4 transition cursor-pointer ${
            channel === 'wechat' ? 'border-emerald-400 bg-emerald-50/70 ring-1 ring-emerald-300' : 'border-slate-200/80 bg-white hover:border-emerald-200'
          }`}
        >
          <div className="text-xs text-emerald-700 font-medium">微信支付</div>
          <div className="mt-1.5 text-xl font-bold text-slate-900">{formatMoney(getChannelExpense('wechat'))}</div>
          <div className="mt-0.5 text-[11px] text-slate-400">{getChannelCount('wechat')} 笔有效流水</div>
        </button>
        <button
          type="button"
          onClick={() => setChannel(channel === 'card_direct' ? 'all' : 'card_direct')}
          className={`text-left rounded-2xl border p-4 transition cursor-pointer ${
            channel === 'card_direct' ? 'border-purple-400 bg-purple-50/70 ring-1 ring-purple-300' : 'border-slate-200/80 bg-white hover:border-purple-200'
          }`}
        >
          <div className="text-xs text-purple-700 font-medium">直接刷卡 / 银行卡</div>
          <div className="mt-1.5 text-xl font-bold text-slate-900">{formatMoney(getChannelExpense('card_direct'))}</div>
          <div className="mt-0.5 text-[11px] text-slate-400">{getChannelCount('card_direct')} 笔（POS/Apple Pay/外币卡）</div>
        </button>
        <button
          type="button"
          onClick={() => setChannel(channel === 'cash' ? 'all' : 'cash')}
          className={`text-left rounded-2xl border p-4 transition cursor-pointer ${
            channel === 'cash' ? 'border-amber-400 bg-amber-50/70 ring-1 ring-amber-300' : 'border-slate-200/80 bg-white hover:border-amber-200'
          }`}
        >
          <div className="text-xs text-amber-700 font-medium">现金消费</div>
          <div className="mt-1.5 text-xl font-bold text-slate-900">{formatMoney(getChannelExpense('cash'))}</div>
          <div className="mt-0.5 text-[11px] text-slate-400">{getChannelCount('cash')} 笔现金流水</div>
        </button>
        <button
          type="button"
          onClick={() => setViewMode(viewMode === 'pairs' ? 'ledger' : 'pairs')}
          className={`text-left rounded-2xl border p-4 transition cursor-pointer ${
            viewMode === 'pairs' ? 'border-indigo-400 bg-indigo-50/70 ring-1 ring-indigo-300' : 'border-slate-200/80 bg-white hover:border-indigo-200'
          }`}
        >
          <div className="text-xs text-indigo-700 font-medium flex items-center gap-1">
            <ArrowRightLeft className="h-3.5 w-3.5" /> 多方核对去重
          </div>
          <div className="mt-1.5 text-xl font-bold text-emerald-600">{summary?.matched_count ?? 0} 对</div>
          <div className="mt-0.5 text-[11px] text-slate-400">
            防重复计入 {formatMoney(summary?.deduped_expense_saved ?? 0)}
          </div>
        </button>
      </div>

      {/* Quick Cash / Direct Card Entry Bar */}
      <form
        onSubmit={handleQuickAdd}
        className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs flex flex-wrap items-center gap-2.5"
      >
        <div className="inline-flex rounded-xl bg-slate-100 p-1 text-xs shrink-0">
          <button
            type="button"
            onClick={() => setQuickChannel('cash')}
            className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 font-medium cursor-pointer ${
              quickChannel === 'cash' ? 'bg-white text-amber-700 shadow-xs' : 'text-slate-600'
            }`}
          >
            <Banknote className="h-3.5 w-3.5" /> 记现金
          </button>
          <button
            type="button"
            onClick={() => setQuickChannel('card_direct')}
            className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 font-medium cursor-pointer ${
              quickChannel === 'card_direct' ? 'bg-white text-purple-700 shadow-xs' : 'text-slate-600'
            }`}
          >
            <CreditCardIcon className="h-3.5 w-3.5" /> 记直接刷卡
          </button>
        </div>

        <input
          type="number"
          step="0.01"
          required
          value={quickAmount}
          onChange={(e) => setQuickAmount(e.target.value)}
          placeholder="金额（元）"
          className="w-28 rounded-xl border border-slate-200 px-3 py-1.5 text-xs focus:border-indigo-500 focus:outline-none"
        />
        <select
          value={quickCategory}
          onChange={(e) => setQuickCategory(e.target.value)}
          className="rounded-xl border border-slate-200 px-2.5 py-1.5 text-xs"
        >
          {QUICK_CATEGORIES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {quickChannel === 'card_direct' && (
          <>
            <input
              type="text"
              value={quickBank}
              onChange={(e) => setQuickBank(e.target.value)}
              placeholder="银行（如 广发/招行）"
              className="w-36 rounded-xl border border-slate-200 px-3 py-1.5 text-xs focus:border-indigo-500 focus:outline-none"
            />
            <input
              type="text"
              maxLength={4}
              value={quickLast4}
              onChange={(e) => setQuickLast4(e.target.value.replace(/\D/g, '').slice(0, 4))}
              placeholder="尾号4位"
              className="w-24 rounded-xl border border-slate-200 px-3 py-1.5 text-xs focus:border-indigo-500 focus:outline-none"
            />
          </>
        )}
        <input
          type="text"
          value={quickDesc}
          onChange={(e) => setQuickDesc(e.target.value)}
          placeholder={quickChannel === 'cash' ? '消费备注（如 菜市场买菜）' : '商户/消费备注（如 超市POS刷卡）'}
          className="flex-1 min-w-[160px] rounded-xl border border-slate-200 px-3 py-1.5 text-xs focus:border-indigo-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={submittingQuick}
          className="inline-flex items-center gap-1 rounded-xl bg-slate-900 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50 cursor-pointer"
        >
          <Plus className="h-3.5 w-3.5" /> 快速入账
        </button>
      </form>

      {/* Card & Account Distribution Strip */}
      {cards.length > 0 && (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Layers className="h-4 w-4 text-indigo-600" />
              <h3 className="text-sm font-semibold text-slate-900">
                按卡片 / 账户归集消费（{cards.length} 个账户，点击可按卡筛选）
              </h3>
            </div>
            {cardLast4 && (
              <button
                type="button"
                onClick={() => setCardLast4('')}
                className="text-xs text-indigo-600 hover:underline cursor-pointer"
              >
                清除卡尾号筛选 ({cardLast4})
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
            {cards.map((c) => {
              const active = Boolean(c.card_last4 && cardLast4 === c.card_last4);
              return (
                <button
                  key={c.key}
                  type="button"
                  onClick={() => {
                    if (c.card_last4) setCardLast4(active ? '' : c.card_last4);
                  }}
                  className={`text-left rounded-xl border p-3 transition ${
                    c.card_last4 ? 'cursor-pointer' : 'cursor-default'
                  } ${
                    active
                      ? 'border-indigo-400 bg-indigo-50/70 ring-1 ring-indigo-300'
                      : 'border-slate-100 bg-slate-50/60 hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <span className="text-xs font-semibold text-slate-900 truncate">{c.name}</span>
                    {c.is_credit_card && (
                      <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-medium text-rose-700 shrink-0">
                        信用卡
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 flex items-baseline justify-between">
                    <span className="text-sm font-bold text-slate-900">{formatMoney(c.expense)}</span>
                    <span className="text-[11px] text-slate-400">{c.count} 笔</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
                    {c.card_last4 && <span className="rounded bg-white border border-slate-200 px-1.5 py-0.5">尾号 {c.card_last4}</span>}
                    {c.matched_count > 0 && <span className="text-emerald-600">核对 {c.matched_count} 笔</span>}
                    {c.direct_count > 0 && <span className="text-purple-600">直刷 {c.direct_count} 笔</span>}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Main View Tabs & Filters */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
          <div className="flex flex-wrap items-center gap-1.5 rounded-xl bg-slate-100 p-1 text-xs">
            {[
              { key: 'ledger', label: `完整个人消费账单 (${summary?.total_effective_count ?? 0})` },
              { key: 'pairs', label: `多方核对对照 (${pairs.length})` },
              { key: 'direct', label: `直接刷卡独有 (${directCards.length})` },
            ].map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setViewMode(tab.key as 'ledger' | 'pairs' | 'direct')}
                className={`rounded-lg px-3 py-1.5 font-medium transition cursor-pointer ${
                  viewMode === tab.key ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <select
              value={month}
              onChange={(e) => setMonth(e.target.value)}
              className="rounded-xl border border-slate-200 px-2.5 py-1.5 text-xs"
            >
              <option value="">全部月份</option>
              {(overview?.available_months || []).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              className="rounded-xl border border-slate-200 px-2.5 py-1.5 text-xs"
            >
              <option value="all">全部渠道</option>
              <option value="alipay">支付宝</option>
              <option value="wechat">微信支付</option>
              <option value="card_direct">直接刷卡 / 银行邮件</option>
              <option value="cash">现金</option>
              <option value="other">其他</option>
            </select>
            {viewMode === 'ledger' && (
              <>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="rounded-xl border border-slate-200 px-2.5 py-1.5 text-xs"
                >
                  <option value="all">全部核对状态</option>
                  <option value="matched">已多方核对</option>
                  <option value="direct_card">直接刷卡独有</option>
                  <option value="cash">现金消费</option>
                  <option value="unmatched">待比对</option>
                </select>
                <label className="inline-flex items-center gap-1 text-xs text-slate-500 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeDuplicates}
                    onChange={(e) => setIncludeDuplicates(e.target.checked)}
                  />
                  <span>显示已去重银行副本</span>
                </label>
                <div className="relative">
                  <Search className="h-3.5 w-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    value={searchQ}
                    onChange={(e) => setSearchQ(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && loadOverview()}
                    placeholder="搜商户/卡尾号/方式"
                    className="rounded-xl border border-slate-200 pl-7 pr-2.5 py-1.5 text-xs w-40 focus:border-indigo-500 focus:outline-none"
                  />
                </div>
              </>
            )}
          </div>
        </div>

        {loading && !overview ? (
          <div className="py-12 text-center text-xs text-slate-400">正在加载全渠道账单数据...</div>
        ) : viewMode === 'pairs' ? (
          <div className="space-y-3 max-h-[540px] overflow-y-auto pr-1">
            {pairs.length === 0 ? (
              <div className="rounded-xl bg-slate-50 py-12 text-center text-xs text-slate-400">
                暂无已核对匹配的跨渠道记录。同时导入支付宝/微信账单与邮箱信用卡/刷卡通知后，系统会自动按卡尾号 + 时间 + 金额核对去重。
              </div>
            ) : (
              pairs.map((p) => (
                <div
                  key={`${p.primary_tx.id}-${p.duplicate_tx?.id || 0}`}
                  className="rounded-xl border border-emerald-200 bg-emerald-50/30 p-3.5 text-xs space-y-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                      <span className="font-semibold text-emerald-800">
                        多方核对一致 · {p.bank_name || '银行卡'} {p.card_last4 ? `尾号 ${p.card_last4}` : ''}
                      </span>
                      <span className="rounded-md bg-white border border-emerald-200 px-2 py-0.5 font-bold text-slate-900">
                        {formatMoney(p.amount)}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleUnmatch(p.primary_tx.id)}
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-500 hover:text-rose-600 cursor-pointer"
                    >
                      <Unlink className="h-3 w-3" /> 取消匹配
                    </button>
                  </div>
                  <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    <div className="rounded-lg bg-white border border-slate-200/80 p-2.5">
                      <div className="text-[10px] font-semibold text-blue-700 mb-1">
                        主账单保留（{CHANNEL_META[p.primary_tx.payment_channel || p.primary_tx.source_type]?.label || p.primary_tx.source_type}）
                      </div>
                      <div className="font-medium text-slate-900">{p.primary_tx.description}</div>
                      <div className="mt-1 text-[11px] text-slate-400">
                        {p.primary_tx.occurred_at} · {p.primary_tx.payment_method || p.primary_tx.account_name} · {p.primary_tx.category}
                      </div>
                    </div>
                    {p.duplicate_tx && (
                      <div className="rounded-lg bg-slate-50 border border-slate-200/80 p-2.5 opacity-85">
                        <div className="text-[10px] font-semibold text-slate-500 mb-1">
                          银行/邮件副本（已自动去重不重复统计）
                        </div>
                        <div className="font-medium text-slate-700">{p.duplicate_tx.description}</div>
                        <div className="mt-1 text-[11px] text-slate-400">
                          {p.duplicate_tx.occurred_at} · 折合 {formatMoney(p.duplicate_tx.amount)}
                          {p.duplicate_tx.original_currency && p.duplicate_tx.original_currency !== 'CNY'
                            ? ` (${p.duplicate_tx.original_currency} ${p.duplicate_tx.original_amount})`
                            : ''}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        ) : viewMode === 'direct' ? (
          <div className="space-y-2 max-h-[540px] overflow-y-auto pr-1">
            {directCards.length === 0 ? (
              <div className="rounded-xl bg-slate-50 py-12 text-center text-xs text-slate-400">
                暂无直接刷卡独有记录。扫描银行消费邮件、导入银行卡账单或在上方点击「记直接刷卡」后，未走微信/支付宝的刷卡流水会汇集于此。
              </div>
            ) : (
              directCards.map((tx) => (
                <div
                  key={tx.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-purple-100 bg-purple-50/30 px-4 py-3 text-xs"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="rounded-md bg-purple-100 px-1.5 py-0.5 text-[10px] font-semibold text-purple-700">
                        直接刷卡
                      </span>
                      {tx.card_last4 && (
                        <span className="rounded-md bg-white border border-purple-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-700">
                          {tx.bank_name || '银行卡'} ({tx.card_last4})
                        </span>
                      )}
                      <span className="font-medium text-slate-900 truncate">{tx.description}</span>
                    </div>
                    <div className="mt-1 text-[11px] text-slate-400">
                      {tx.occurred_at} · {tx.category} · {tx.account_name || '已自动建卡'}
                      {tx.original_currency && tx.original_currency !== 'CNY'
                        ? ` · 原币种 ${tx.original_currency} ${tx.original_amount}`
                        : ''}
                    </div>
                  </div>
                  <span className={`font-bold text-sm ${tx.type === 'income' ? 'text-emerald-600' : 'text-slate-900'}`}>
                    {tx.type === 'income' ? `+${formatMoney(tx.amount)}` : `-${formatMoney(tx.amount)}`}
                  </span>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="space-y-2 max-h-[540px] overflow-y-auto pr-1">
            {txs.length === 0 ? (
              <div className="rounded-xl bg-slate-50 py-12 text-center text-xs text-slate-400">
                当前筛选条件下暂无流水记录
              </div>
            ) : (
              txs.map((tx) => {
                const chMeta = CHANNEL_META[tx.payment_channel || tx.source_type] || CHANNEL_META.other;
                const recMeta = RECONCILE_META[tx.reconcile_status || 'unmatched'] || RECONCILE_META.unmatched;
                const isDup = tx.reconcile_status === 'reconciled_duplicate';
                return (
                  <div
                    key={tx.id}
                    className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border px-3.5 py-2.5 text-xs ${
                      isDup ? 'border-slate-200 bg-slate-50/70 opacity-60' : 'border-slate-100 bg-slate-50/40'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${chMeta.cls}`}>
                          {chMeta.label}
                        </span>
                        <span className="rounded-md bg-white border border-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-700">
                          {tx.category}
                        </span>
                        {tx.card_last4 && (
                          <span className="rounded-md bg-indigo-50 border border-indigo-200 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
                            {tx.bank_name || '卡'}({tx.card_last4})
                          </span>
                        )}
                        <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${recMeta.cls}`}>
                          {recMeta.label}
                        </span>
                        <span className="font-medium text-slate-900 truncate max-w-[340px]">{tx.description}</span>
                      </div>
                      <div className="mt-1 text-[11px] text-slate-400">
                        {tx.occurred_at} · 账户：{tx.account_name || '未绑定'}
                        {tx.payment_method ? ` · 方式：${tx.payment_method}` : ''}
                      </div>
                    </div>
                    <span className={`font-semibold text-sm shrink-0 ${tx.type === 'income' ? 'text-emerald-600' : 'text-slate-900'}`}>
                      {tx.type === 'income' ? `+${formatMoney(tx.amount)}` : `-${formatMoney(tx.amount)}`}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
};
