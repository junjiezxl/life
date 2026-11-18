import React, { useState } from 'react';
import {
  ArrowRight,
  Bell,
  CalendarClock,
  CheckCircle2,
  CreditCard,
  FileText,
  Landmark,
  Receipt,
  Sparkles,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import { api } from '../api';
import type { DashboardData, TabKey } from '../types';
import { CATEGORY_LABELS, formatDueAt, formatMoney, PRIORITY_META, SOURCE_META } from '../utils/format';

interface Props {
  data: DashboardData;
  onRefresh: () => void;
  onNavigate: (tab: TabKey) => void;
  onNotify: (msg: string) => void;
}

export const DashboardView: React.FC<Props> = ({ data, onRefresh, onNavigate, onNotify }) => {
  const [quickInput, setQuickInput] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleQuickSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!quickInput.trim() || submitting) return;
    setSubmitting(true);
    try {
      const res = await api.sendChat(quickInput.trim());
      setQuickInput('');
      const rCount = res.created_reminders.length;
      const tCount = res.created_transactions.length;
      if (rCount || tCount) {
        onNotify(`AI 已自动处理：新增 ${rCount} 条提醒，记录 ${tCount} 笔账目`);
      } else {
        onNotify(res.message.content.split('\n')[0]);
      }
      onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '处理失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleCompleteReminder = async (id: number, hasAmount: boolean) => {
    try {
      if (hasAmount) {
        await api.completeAndRecord(id);
        onNotify('已完成提醒并自动记录关联账目');
      } else {
        await api.updateReminder(id, { status: 'done' });
        onNotify('提醒已标记为完成');
      }
      onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '操作失败');
    }
  };

  const { reminders_summary, finance_summary, recurring_bills, latest_settlement } = data;
  const breakdownEntries = Object.entries(finance_summary.category_breakdown).sort((a, b) => b[1] - a[1]);
  const maxCatVal = breakdownEntries.length > 0 ? breakdownEntries[0][1] : 1;

  return (
    <div className="space-y-6">
      {/* Quick AI Command Bar */}
      <div className="rounded-2xl bg-gradient-to-r from-indigo-600 via-violet-600 to-indigo-700 p-5 text-white shadow-sm">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium text-indigo-100">
              <Sparkles className="h-4 w-4" />
              <span>AI 快捷指令栏 · 自然语言新增提醒 & 顺手记账</span>
            </div>
            <h2 className="mt-1 text-lg font-semibold tracking-tight">
              说一句话，自动拆解日程提醒与个人收支
            </h2>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <button
              type="button"
              onClick={() => setQuickInput('明天下午3点提醒我交水电费180元')}
              className="rounded-full bg-white/15 px-3 py-1 hover:bg-white/25 transition"
            >
              示例：交水电费提醒
            </button>
            <button
              type="button"
              onClick={() => setQuickInput('今天午饭花了45元，顺便周五提醒我复盘周报')}
              className="rounded-full bg-white/15 px-3 py-1 hover:bg-white/25 transition"
            >
              示例：记账 + 提醒
            </button>
          </div>
        </div>

        <form onSubmit={handleQuickSubmit} className="mt-4 flex gap-2">
          <input
            type="text"
            value={quickInput}
            onChange={(e) => setQuickInput(e.target.value)}
            placeholder="例如：「明天上午10点提醒我提交报销，另外刚才打车花了32元」..."
            className="flex-1 rounded-xl bg-white/95 px-4 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-white"
          />
          <button
            type="submit"
            disabled={submitting || !quickInput.trim()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 transition cursor-pointer"
          >
            <Sparkles className="h-4 w-4" />
            {submitting ? '解析中...' : '智能执行'}
          </button>
        </form>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div
          onClick={() => onNavigate('reminders')}
          className="cursor-pointer rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs hover:border-indigo-300 transition"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-500">待办提醒事项</span>
            <span className="rounded-lg bg-indigo-50 p-2 text-indigo-600">
              <Bell className="h-4 w-4" />
            </span>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-slate-900">{reminders_summary.pending_count}</span>
            <span className="text-xs text-slate-500">项待处理</span>
          </div>
          <div className="mt-2 flex items-center gap-3 text-xs">
            <span className="text-amber-600 font-medium">今日到期 {reminders_summary.due_today_count}</span>
            {reminders_summary.overdue_count > 0 && (
              <span className="text-rose-600 font-medium">已逾期 {reminders_summary.overdue_count}</span>
            )}
            <span className="text-slate-400">已完成 {reminders_summary.done_count}</span>
          </div>
        </div>

        <div
          onClick={() => onNavigate('finance')}
          className="cursor-pointer rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs hover:border-emerald-300 transition"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-500">个人净资产</span>
            <span className="rounded-lg bg-emerald-50 p-2 text-emerald-600">
              <Landmark className="h-4 w-4" />
            </span>
          </div>
          <div className="mt-3 text-2xl font-bold text-slate-900">
            {formatMoney(finance_summary.net_worth)}
          </div>
          <div className="mt-2 flex items-center gap-3 text-xs text-slate-500">
            <span>总资产 {formatMoney(finance_summary.total_assets)}</span>
            <span className="text-rose-600">负债 {formatMoney(finance_summary.total_liabilities)}</span>
          </div>
        </div>

        <div
          onClick={() => onNavigate('finance')}
          className="cursor-pointer rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs hover:border-violet-300 transition"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-500">本期未结账现金流</span>
            <span className="rounded-lg bg-violet-50 p-2 text-violet-600">
              <Receipt className="h-4 w-4" />
            </span>
          </div>
          <div
            className={`mt-3 text-2xl font-bold ${
              finance_summary.unsettled_net_cashflow >= 0 ? 'text-emerald-600' : 'text-rose-600'
            }`}
          >
            {formatMoney(finance_summary.unsettled_net_cashflow, true)}
          </div>
          <div className="mt-2 flex items-center gap-3 text-xs text-slate-500">
            <span>待结 {finance_summary.unsettled_count} 笔</span>
            <span>支出 {formatMoney(finance_summary.unsettled_expense)}</span>
          </div>
        </div>

        <div
          onClick={() => onNavigate('notes')}
          className="cursor-pointer rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs hover:border-amber-300 transition"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-500">最近结账 & 思源连接</span>
            <span className="rounded-lg bg-amber-50 p-2 text-amber-600">
              <TrendingUp className="h-4 w-4" />
            </span>
          </div>
          <div className="mt-3 text-base font-semibold text-slate-900 truncate">
            {latest_settlement ? latest_settlement.period_label : '尚未进行周期结账'}
          </div>
          <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
            <span>
              {latest_settlement
                ? `上期结余 ${formatMoney(latest_settlement.net_cashflow, true)}`
                : '点击前往财务结账'}
            </span>
            <span className="text-indigo-600 font-medium inline-flex items-center gap-0.5">
              笔记提取 <ArrowRight className="h-3 w-3" />
            </span>
          </div>
        </div>
      </div>

      {/* Main Bento Grid */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left 7 cols: Upcoming Reminders */}
        <div className="lg:col-span-7 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <div className="flex items-center gap-2">
              <CalendarClock className="h-5 w-5 text-indigo-600" />
              <h3 className="font-semibold text-slate-900">近期提醒事项</h3>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <button
                onClick={() => onNavigate('notes')}
                className="inline-flex items-center gap-1 text-emerald-700 hover:text-emerald-800 font-medium cursor-pointer"
              >
                <FileText className="h-3.5 w-3.5" /> 从思源提取
              </button>
              <button
                onClick={() => onNavigate('reminders')}
                className="inline-flex items-center gap-1 text-indigo-600 hover:text-indigo-700 font-medium cursor-pointer"
              >
                全部与 AI 对话 <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          <div className="mt-4 space-y-2.5">
            {reminders_summary.upcoming.length === 0 ? (
              <div className="rounded-xl bg-slate-50 p-8 text-center text-sm text-slate-500">
                暂无待处理提醒，试试上方 AI 指令栏或从思源笔记提取吧！
              </div>
            ) : (
              reminders_summary.upcoming.map((rem) => {
                const prio = PRIORITY_META[rem.priority] || PRIORITY_META.medium;
                const src = SOURCE_META[rem.source_type] || SOURCE_META.manual;
                return (
                  <div
                    key={rem.id}
                    className="group flex items-start justify-between gap-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3.5 hover:bg-white hover:border-slate-200 transition"
                  >
                    <div className="flex items-start gap-3 min-w-0">
                      <button
                        onClick={() => handleCompleteReminder(rem.id, Boolean(rem.linked_amount))}
                        title={rem.linked_amount ? '完成并自动入账' : '标记完成'}
                        className="mt-0.5 text-slate-400 hover:text-emerald-600 transition cursor-pointer shrink-0"
                      >
                        <CheckCircle2 className="h-5 w-5" />
                      </button>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-slate-900 text-sm">{rem.title}</span>
                          <span className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${prio.cls}`}>
                            {prio.label}
                          </span>
                          <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${src.cls}`}>
                            {src.label}
                          </span>
                          <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                            {CATEGORY_LABELS[rem.category] || rem.category}
                          </span>
                        </div>
                        {rem.description && (
                          <p className="mt-1 text-xs text-slate-500 line-clamp-1">{rem.description}</p>
                        )}
                        <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                          <span>到期：{formatDueAt(rem.due_at)}</span>
                          {rem.linked_amount && (
                            <span className="text-orange-600 font-medium">
                              关联金额：{formatMoney(rem.linked_amount)}（点击勾选可自动入账）
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right 5 cols: Financial Breakdown & Bills */}
        <div className="lg:col-span-5 space-y-6">
          {/* Unsettled Breakdown */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <Wallet className="h-5 w-5 text-emerald-600" />
                <h3 className="font-semibold text-slate-900">本期未结账支出分布</h3>
              </div>
              <button
                onClick={() => onNavigate('finance')}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-700 cursor-pointer"
              >
                去结账 →
              </button>
            </div>

            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-2 gap-3 rounded-xl bg-slate-50 p-3 text-xs">
                <div>
                  <span className="text-slate-500">本期已收</span>
                  <div className="mt-0.5 text-sm font-semibold text-emerald-600">
                    {formatMoney(finance_summary.unsettled_income)}
                  </div>
                </div>
                <div>
                  <span className="text-slate-500">本期已支</span>
                  <div className="mt-0.5 text-sm font-semibold text-rose-600">
                    {formatMoney(finance_summary.unsettled_expense)}
                  </div>
                </div>
              </div>

              {breakdownEntries.length === 0 ? (
                <p className="py-4 text-center text-xs text-slate-400">当前周期暂无待结账支出</p>
              ) : (
                <div className="space-y-2.5">
                  {breakdownEntries.slice(0, 5).map(([cat, amount]) => {
                    const pct = Math.max(8, Math.round((amount / maxCatVal) * 100));
                    return (
                      <div key={cat}>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="font-medium text-slate-700">{cat}</span>
                          <span className="text-slate-600">{formatMoney(amount)}</span>
                        </div>
                        <div className="h-2 w-full rounded-full bg-slate-100 overflow-hidden">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Upcoming Recurring Bills */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <CreditCard className="h-5 w-5 text-orange-500" />
                <h3 className="font-semibold text-slate-900">近期固定账单</h3>
              </div>
              <button
                onClick={() => onNavigate('finance')}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-700 cursor-pointer"
              >
                管理账单 →
              </button>
            </div>

            <div className="mt-3 space-y-2">
              {recurring_bills.length === 0 ? (
                <p className="py-3 text-center text-xs text-slate-400">暂无固定周期账单</p>
              ) : (
                recurring_bills.map((bill) => (
                  <div
                    key={bill.id}
                    className="flex items-center justify-between rounded-xl bg-slate-50 px-3.5 py-2.5 text-sm"
                  >
                    <div>
                      <div className="font-medium text-slate-800">{bill.name}</div>
                      <div className="text-xs text-slate-400">
                        到期日：{bill.next_due_date} · {bill.category}
                      </div>
                    </div>
                    <div className="font-semibold text-slate-900">{formatMoney(bill.amount)}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
