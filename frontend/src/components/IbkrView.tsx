import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Briefcase,
  Calendar,
  Coins,
  RefreshCw,
  Search,
  Settings,
  TrendingUp,
} from 'lucide-react';
import { api } from '../api';
import type { IbkrOverviewResponse } from '../types';

interface Props {
  onNotify: (msg: string) => void;
  onNavigateSettings: () => void;
}

type SubTab = 'daily' | 'positions' | 'trades' | 'cash';

function formatMoney(value: number, currency = 'USD', withSign = false): string {
  const abs = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const sym = currency === 'USD' ? '$' : `${currency} `;
  if (value < 0) return `-${sym}${abs}`;
  if (withSign && value > 0) return `+${sym}${abs}`;
  return `${sym}${abs}`;
}

export const IbkrView: React.FC<Props> = ({ onNotify, onNavigateSettings }) => {
  const [data, setData] = useState<IbkrOverviewResponse | null>(null);
  const [selectedAccount, setSelectedAccount] = useState('');
  const [subTab, setSubTab] = useState<SubTab>('daily');
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  const [tradeFilter, setTradeFilter] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('ALL');
  const [sideFilter, setSideFilter] = useState('ALL');

  const loadData = async (acct = selectedAccount) => {
    try {
      const res = await api.getIbkrOverview(acct);
      setData(res);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载 IBKR 数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData(selectedAccount);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAccount]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await api.syncIbkrFlex();
      await loadData(selectedAccount);
      onNotify(
        `IBKR Flex 同步完成：净值 ${res.equity_snapshots} 条、持仓 ${res.positions} 个、新增成交 ${res.trades_inserted} 笔`,
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'IBKR Flex 同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const filteredTrades = useMemo(() => {
    if (!data) return [];
    const q = tradeFilter.trim().toLowerCase();
    return data.trades.filter((t) => {
      if (categoryFilter !== 'ALL' && t.asset_category !== categoryFilter) return false;
      if (sideFilter !== 'ALL' && t.side !== sideFilter) return false;
      if (q) {
        const hay = `${t.symbol} ${t.description} ${t.trade_date} ${t.account_id}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [data, tradeFilter, categoryFilter, sideFilter]);

  const tradeCategories = useMemo(() => {
    if (!data) return [];
    return Array.from(new Set(data.trades.map((t) => t.asset_category).filter(Boolean))).sort();
  }, [data]);

  const s = data?.summary;
  const ccy = s?.base_currency || 'USD';

  return (
    <div className="space-y-5">
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white shadow-xs">
            <TrendingUp className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-slate-900">盈透证券 (IBKR) 每日账户与交易汇总</h2>
              {s?.latest_report_date && (
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-medium text-slate-600">
                  最新报告日：{s.latest_report_date}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">
              基于官方 Flex Web Service（免挂机 / 免 2FA），汇总每日净值、当前持仓、已实现盈亏与分红流水。
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {data && data.accounts.length > 1 && (
            <select
              value={selectedAccount}
              onChange={(e) => setSelectedAccount(e.target.value)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 outline-none focus:border-blue-500"
            >
              <option value="">全部账户 ({data.accounts.length})</option>
              {data.accounts.map((a) => (
                <option key={a} value={a}>
                  账户 {a}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={onNavigateSettings}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
          >
            <Settings className="h-3.5 w-3.5" />
            <span>Flex 配置</span>
          </button>
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-4 py-2 text-xs font-medium text-white shadow-xs hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncing ? 'animate-spin' : ''}`} />
            <span>{syncing ? '正在拉取 Flex...' : '立即同步 Flex 报告'}</span>
          </button>
        </div>
      </div>

      {/* Onboarding guide when not configured or empty */}
      {!loading && s && !s.configured && s.trade_count === 0 && s.position_count === 0 && (
        <div className="rounded-2xl border border-blue-200/80 bg-blue-50/70 p-5 space-y-2.5 text-xs text-slate-700">
          <h3 className="text-sm font-semibold text-blue-900">4 步接入 IBKR Flex 每日汇总（约 2 分钟）</h3>
          <ol className="list-decimal pl-5 space-y-1.5 leading-relaxed">
            <li>
              登录 IBKR 网页端 → 顶部菜单 <strong>业绩与报告 → 自主查询</strong>（英文界面为 Flex Queries），在 <strong>活动自主查询</strong>（Activity Flex Queries）旁点击 <strong>+</strong> 新建，交付格式务必选 <strong>XML</strong>。
            </li>
            <li>
              勾选以下 4 个部分并选择全部字段（Full / 全选）：
              <code className="mx-1 rounded bg-white px-1.5 py-0.5 text-blue-800">基础货币权益汇总 (Equity Summary in Base)</code>、
              <code className="mx-1 rounded bg-white px-1.5 py-0.5 text-blue-800">未平仓头寸 (Open Positions)</code>、
              <code className="mx-1 rounded bg-white px-1.5 py-0.5 text-blue-800">交易 / 成交 (Trades)</code>、
              <code className="mx-1 rounded bg-white px-1.5 py-0.5 text-blue-800">现金交易 (Cash Transactions)</code>，周期建议选 <strong>最近 30 个日历日 (Last 30 Calendar Days)</strong>。
            </li>
            <li>
              保存后点进刚创建的查询或查看列表右侧的 <strong>Query ID（查询编号）</strong>；然后在同页面点开 <strong>Flex Web 服务配置（自主网络服务配置）</strong>，开启服务并点 <strong>生成新口令（Generate New Token）</strong>（有效期最长可设 1 年，IP 限制可留空）。
            </li>
            <li>
              前往 <button type="button" onClick={onNavigateSettings} className="font-semibold text-blue-700 underline cursor-pointer">系统设置 → 盈透证券 (IBKR) Flex 配置</button> 填入 Token 与 Query ID，保存后点「立即同步 Flex 报告」即可。
            </li>
          </ol>
        </div>
      )}

      {/* KPI Summary Cards */}
      {s && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>账户总净值 (NAV)</span>
              <Briefcase className="h-4 w-4 text-blue-600" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-900 tabular-nums">
              {formatMoney(s.total_nav, ccy)}
            </div>
            <div className="mt-1 flex items-center gap-1 text-xs tabular-nums">
              {s.day_change >= 0 ? (
                <span className="inline-flex items-center font-medium text-emerald-600">
                  <ArrowUpRight className="h-3.5 w-3.5" />
                  {formatMoney(s.day_change, ccy, true)} ({s.day_change_pct >= 0 ? '+' : ''}{s.day_change_pct.toFixed(2)}%)
                </span>
              ) : (
                <span className="inline-flex items-center font-medium text-rose-600">
                  <ArrowDownRight className="h-3.5 w-3.5" />
                  {formatMoney(s.day_change, ccy)} ({s.day_change_pct.toFixed(2)}%)
                </span>
              )}
              <span className="text-slate-400">较前一日</span>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>证券持仓与现金分布</span>
              <BarChart3 className="h-4 w-4 text-indigo-600" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-900 tabular-nums">
              {formatMoney(s.stock_value + s.options_value + s.futures_value + s.other_value, ccy)}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-500 tabular-nums">
              <span>现金：{formatMoney(s.total_cash, ccy)}</span>
              <span>股票：{formatMoney(s.stock_value, ccy)}</span>
              {Math.abs(s.options_value) > 0 && <span>期权：{formatMoney(s.options_value, ccy)}</span>}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>当前持仓浮盈 ({s.position_count} 个标的)</span>
              <TrendingUp className="h-4 w-4 text-emerald-600" />
            </div>
            <div
              className={`mt-2 text-2xl font-bold tabular-nums ${
                s.unrealized_pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'
              }`}
            >
              {formatMoney(s.unrealized_pnl, ccy, true)}
            </div>
            <div className="mt-1 text-[11px] text-slate-500 tabular-nums">
              累计股息分红：{formatMoney(s.total_dividends, ccy)}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>已实现净盈亏 ({s.trade_count} 笔成交)</span>
              <Coins className="h-4 w-4 text-amber-600" />
            </div>
            <div
              className={`mt-2 text-2xl font-bold tabular-nums ${
                s.total_net_pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'
              }`}
            >
              {formatMoney(s.total_net_pnl, ccy, true)}
            </div>
            <div className="mt-1 text-[11px] text-slate-500 tabular-nums">
              毛利：{formatMoney(s.total_realized_pnl, ccy, true)} · 佣金：{formatMoney(s.total_commission, ccy)}
            </div>
          </div>
        </div>
      )}

      {/* Sub-tabs */}
      <div className="flex flex-wrap gap-2">
        {(
          [
            { key: 'daily', label: '每日净值与盈亏汇总' },
            { key: 'positions', label: `当前持仓 (${data?.positions.length || 0})` },
            { key: 'trades', label: `历史成交 (${data?.trades.length || 0})` },
            { key: 'cash', label: `分红与现金流水 (${data?.cash_transactions.length || 0})` },
          ] as Array<{ key: SubTab; label: string }>
        ).map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setSubTab(tab.key)}
            className={`rounded-xl px-4 py-2 text-xs font-medium transition cursor-pointer ${
              subTab === tab.key
                ? 'bg-slate-900 text-white shadow-xs'
                : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Sub-tab 1: Daily Equity & Daily Trade Aggregation */}
      {subTab === 'daily' && data && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {/* Daily NAV table */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
                <Calendar className="h-4 w-4 text-blue-600" />
                每日净值走势明细
              </h3>
              <span className="text-[11px] text-slate-400">按报告日期倒序</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-slate-100 text-[11px] text-slate-500">
                  <tr>
                    <th className="py-2 pr-3 font-medium">报告日期</th>
                    <th className="py-2 px-2 font-medium text-right">总净值 (NAV)</th>
                    <th className="py-2 px-2 font-medium text-right">日盈亏</th>
                    <th className="py-2 px-2 font-medium text-right">持仓市值</th>
                    <th className="py-2 pl-2 font-medium text-right">现金</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 tabular-nums">
                  {data.daily_equity.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-8 text-center text-slate-400">
                        暂无每日净值快照，请在 Flex Query 中勾选 Equity Summary in Base 并同步
                      </td>
                    </tr>
                  ) : (
                    data.daily_equity.map((eq) => {
                      const posMkt = eq.stock_value + eq.options_value + eq.futures_value + eq.other_value;
                      return (
                        <tr key={eq.report_date} className="hover:bg-slate-50/70">
                          <td className="py-2.5 pr-3 font-medium text-slate-800">{eq.report_date}</td>
                          <td className="py-2.5 px-2 text-right font-semibold text-slate-900">
                            {formatMoney(eq.total_nav, eq.currency)}
                          </td>
                          <td
                            className={`py-2.5 px-2 text-right font-medium ${
                              eq.day_change > 0
                                ? 'text-emerald-600'
                                : eq.day_change < 0
                                  ? 'text-rose-600'
                                  : 'text-slate-400'
                            }`}
                          >
                            {eq.day_change === 0
                              ? '--'
                              : `${formatMoney(eq.day_change, eq.currency, true)} (${eq.day_change_pct >= 0 ? '+' : ''}${eq.day_change_pct.toFixed(2)}%)`}
                          </td>
                          <td className="py-2.5 px-2 text-right text-slate-600">{formatMoney(posMkt, eq.currency)}</td>
                          <td className="py-2.5 pl-2 text-right text-slate-600">{formatMoney(eq.total_cash, eq.currency)}</td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Daily Trade Summary table */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
                <BarChart3 className="h-4 w-4 text-emerald-600" />
                每日成交与已实现盈亏汇总
              </h3>
              <span className="text-[11px] text-slate-400">含交易笔数与佣金</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-slate-100 text-[11px] text-slate-500">
                  <tr>
                    <th className="py-2 pr-3 font-medium">交易日期</th>
                    <th className="py-2 px-2 font-medium text-center">笔数 (买/卖)</th>
                    <th className="py-2 px-2 font-medium">标的</th>
                    <th className="py-2 px-2 font-medium text-right">佣金</th>
                    <th className="py-2 pl-2 font-medium text-right">当日已实现净盈亏</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 tabular-nums">
                  {data.daily_trades.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-8 text-center text-slate-400">
                        暂无每日成交记录
                      </td>
                    </tr>
                  ) : (
                    data.daily_trades.map((dt) => (
                      <tr key={dt.trade_date} className="hover:bg-slate-50/70">
                        <td className="py-2.5 pr-3 font-medium text-slate-800">{dt.trade_date}</td>
                        <td className="py-2.5 px-2 text-center text-slate-600">
                          {dt.trade_count} ({dt.buy_count}/{dt.sell_count})
                        </td>
                        <td className="py-2.5 px-2 text-slate-600 max-w-[160px] truncate" title={dt.symbols.join(', ')}>
                          {dt.symbols.join(', ') || '--'}
                        </td>
                        <td className="py-2.5 px-2 text-right text-slate-500">{formatMoney(dt.commission, ccy)}</td>
                        <td
                          className={`py-2.5 pl-2 text-right font-semibold ${
                            dt.net_pnl > 0
                              ? 'text-emerald-600'
                              : dt.net_pnl < 0
                                ? 'text-rose-600'
                                : 'text-slate-600'
                          }`}
                        >
                          {formatMoney(dt.net_pnl, ccy, true)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Sub-tab 2: Positions */}
      {subTab === 'positions' && data && (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-100 text-[11px] text-slate-500">
              <tr>
                <th className="py-2 pr-3 font-medium">标的 / 描述</th>
                <th className="py-2 px-2 font-medium">类别</th>
                <th className="py-2 px-2 font-medium text-right">持仓数量</th>
                <th className="py-2 px-2 font-medium text-right">开仓成本</th>
                <th className="py-2 px-2 font-medium text-right">当前标记价</th>
                <th className="py-2 px-2 font-medium text-right">市值 (本位币)</th>
                <th className="py-2 pl-2 font-medium text-right">未实现盈亏</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 tabular-nums">
              {data.positions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-400">
                    当前无持仓记录
                  </td>
                </tr>
              ) : (
                data.positions.map((p) => {
                  const pct =
                    Math.abs(p.cost_basis) > 1e-6
                      ? (p.unrealized_pnl / Math.abs(p.cost_basis)) * 100
                      : 0;
                  return (
                    <tr key={`${p.account_id}-${p.conid}`} className="hover:bg-slate-50/70">
                      <td className="py-2.5 pr-3">
                        <div className="font-semibold text-slate-900">{p.symbol}</div>
                        {p.description && <div className="text-[11px] text-slate-400">{p.description}</div>}
                      </td>
                      <td className="py-2.5 px-2">
                        <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700">
                          {p.asset_category}
                        </span>
                      </td>
                      <td className="py-2.5 px-2 text-right font-medium text-slate-800">{p.position}</td>
                      <td className="py-2.5 px-2 text-right text-slate-600">{p.open_price.toFixed(2)}</td>
                      <td className="py-2.5 px-2 text-right text-slate-800">{p.mark_price.toFixed(2)}</td>
                      <td className="py-2.5 px-2 text-right font-medium text-slate-900">
                        {formatMoney(p.position_value_base, ccy)}
                      </td>
                      <td
                        className={`py-2.5 pl-2 text-right font-semibold ${
                          p.unrealized_pnl_base >= 0 ? 'text-emerald-600' : 'text-rose-600'
                        }`}
                      >
                        {formatMoney(p.unrealized_pnl_base, ccy, true)}
                        {pct !== 0 && <span className="ml-1 text-[11px]">({pct >= 0 ? '+' : ''}{pct.toFixed(2)}%)</span>}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Sub-tab 3: Trade History */}
      {subTab === 'trades' && data && (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
              <input
                type="text"
                value={tradeFilter}
                onChange={(e) => setTradeFilter(e.target.value)}
                placeholder="搜索代码、描述或日期..."
                className="w-full rounded-xl border border-slate-200 pl-8 pr-3 py-1.5 text-xs outline-none focus:border-blue-500"
              />
            </div>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs outline-none"
            >
              <option value="ALL">全部资产类别</option>
              {tradeCategories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <select
              value={sideFilter}
              onChange={(e) => setSideFilter(e.target.value)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs outline-none"
            >
              <option value="ALL">全部方向</option>
              <option value="BUY">买入 (BUY)</option>
              <option value="SELL">卖出 (SELL)</option>
            </select>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-100 text-[11px] text-slate-500">
                <tr>
                  <th className="py-2 pr-3 font-medium">成交时间</th>
                  <th className="py-2 px-2 font-medium">标的</th>
                  <th className="py-2 px-2 font-medium">方向 / 开平</th>
                  <th className="py-2 px-2 font-medium text-right">数量</th>
                  <th className="py-2 px-2 font-medium text-right">成交价</th>
                  <th className="py-2 px-2 font-medium text-right">佣金</th>
                  <th className="py-2 pl-2 font-medium text-right">已实现盈亏</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 tabular-nums">
                {filteredTrades.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-slate-400">
                      无匹配成交记录
                    </td>
                  </tr>
                ) : (
                  filteredTrades.map((t) => (
                    <tr key={t.trade_key} className="hover:bg-slate-50/70">
                      <td className="py-2.5 pr-3 text-slate-700">{t.executed_at || t.trade_date}</td>
                      <td className="py-2.5 px-2">
                        <span className="font-semibold text-slate-900">{t.symbol}</span>
                        <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                          {t.asset_category}
                        </span>
                      </td>
                      <td className="py-2.5 px-2">
                        <span
                          className={`rounded-md px-2 py-0.5 text-[11px] font-medium ${
                            t.side === 'BUY'
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'bg-rose-50 text-rose-700'
                          }`}
                        >
                          {t.side}
                        </span>
                        {t.open_close && (
                          <span className="ml-1 text-[11px] text-slate-400">({t.open_close})</span>
                        )}
                      </td>
                      <td className="py-2.5 px-2 text-right font-medium text-slate-800">{t.quantity}</td>
                      <td className="py-2.5 px-2 text-right text-slate-700">{t.trade_price}</td>
                      <td className="py-2.5 px-2 text-right text-slate-500">{formatMoney(t.commission_base, ccy)}</td>
                      <td
                        className={`py-2.5 pl-2 text-right font-semibold ${
                          t.realized_pnl_base > 0
                            ? 'text-emerald-600'
                            : t.realized_pnl_base < 0
                              ? 'text-rose-600'
                              : 'text-slate-400'
                        }`}
                      >
                        {t.realized_pnl_base === 0 ? '--' : formatMoney(t.realized_pnl_base, ccy, true)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Sub-tab 4: Cash & Dividends */}
      {subTab === 'cash' && data && (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-100 text-[11px] text-slate-500">
              <tr>
                <th className="py-2 pr-3 font-medium">日期</th>
                <th className="py-2 px-2 font-medium">类型</th>
                <th className="py-2 px-2 font-medium">标的</th>
                <th className="py-2 px-2 font-medium">说明</th>
                <th className="py-2 pl-2 font-medium text-right">金额 (本位币)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 tabular-nums">
              {data.cash_transactions.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-slate-400">
                    暂无分红、利息或出入金记录
                  </td>
                </tr>
              ) : (
                data.cash_transactions.map((c) => (
                  <tr key={c.tx_key} className="hover:bg-slate-50/70">
                    <td className="py-2.5 pr-3 text-slate-700">{c.date_time || c.report_date}</td>
                    <td className="py-2.5 px-2">
                      <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] text-slate-700">
                        {c.tx_type || '--'}
                      </span>
                    </td>
                    <td className="py-2.5 px-2 font-medium text-slate-800">{c.symbol || '--'}</td>
                    <td className="py-2.5 px-2 text-slate-600">{c.description}</td>
                    <td
                      className={`py-2.5 pl-2 text-right font-semibold ${
                        c.amount_base >= 0 ? 'text-emerald-600' : 'text-rose-600'
                      }`}
                    >
                      {formatMoney(c.amount_base, ccy, true)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
