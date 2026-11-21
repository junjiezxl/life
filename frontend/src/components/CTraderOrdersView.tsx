import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, RefreshCw, RotateCcw, Search, Settings, TrendingDown, TrendingUp } from 'lucide-react';
import { api } from '../api';
import { CTraderDeal } from '../types';

interface CTraderOrdersViewProps {
  onNotify: (msg: string) => void;
  onNavigateSettings: () => void;
}

const getAlgoKey = (deal: CTraderDeal) => {
  const lbl = (deal.label || '').trim();
  return lbl || '手动订单';
};

export const CTraderOrdersView: React.FC<CTraderOrdersViewProps> = ({
  onNotify,
  onNavigateSettings,
}) => {
  const [deals, setDeals] = useState<CTraderDeal[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [days, setDays] = useState(7);

  // Filters
  const [algoFilter, setAlgoFilter] = useState<string>('ALL');
  const [symbolFilter, setSymbolFilter] = useState<string>('ALL');
  const [sideFilter, setSideFilter] = useState<string>('ALL');
  const [pnlFilter, setPnlFilter] = useState<'ALL' | 'WIN' | 'LOSS'>('ALL');
  const [startDate, setStartDate] = useState<string>('');
  const [endDate, setEndDate] = useState<string>('');
  const [searchText, setSearchText] = useState<string>('');

  const loadDeals = useCallback(async () => {
    try {
      const res = await api.getCTraderDeals(2000);
      setDeals(res.deals);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载 cTrader 订单失败');
    } finally {
      setLoading(false);
    }
  }, [onNotify]);

  useEffect(() => {
    loadDeals();
    const timer = window.setInterval(loadDeals, 15000);
    return () => window.clearInterval(timer);
  }, [loadDeals]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await api.syncCTraderDeals(days, true);
      onNotify(
        `同步完成：拉取 ${res.fetched} 笔，新增 ${res.inserted} 笔，推送 ${res.pushed} 条`
      );
      await loadDeals();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '拉取 cTrader 订单失败');
    } finally {
      setSyncing(false);
    }
  };

  const algoOptions = useMemo(() => {
    const set = new Set<string>();
    deals.forEach((d) => set.add(getAlgoKey(d)));
    return Array.from(set).sort((a, b) => {
      if (a === '手动订单') return 1;
      if (b === '手动订单') return -1;
      return a.localeCompare(b);
    });
  }, [deals]);

  const symbolOptions = useMemo(() => {
    const set = new Set<string>();
    deals.forEach((d) => {
      if (d.symbol_name) set.add(d.symbol_name);
    });
    return Array.from(set).sort();
  }, [deals]);

  const filteredDeals = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    return deals.filter((d) => {
      if (algoFilter !== 'ALL' && getAlgoKey(d) !== algoFilter) return false;
      if (symbolFilter !== 'ALL' && d.symbol_name !== symbolFilter) return false;
      if (sideFilter !== 'ALL' && d.side !== sideFilter) return false;
      if (pnlFilter === 'WIN' && d.net_profit <= 0) return false;
      if (pnlFilter === 'LOSS' && d.net_profit >= 0) return false;

      const dateStr = (d.executed_at || '').slice(0, 10);
      if (startDate && dateStr < startDate) return false;
      if (endDate && dateStr > endDate) return false;

      if (q) {
        const hay = `${d.deal_id} ${d.order_id} ${d.position_id} ${d.symbol_name} ${d.label || ''} ${d.comment || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [deals, algoFilter, symbolFilter, sideFilter, pnlFilter, startDate, endDate, searchText]);

  const summary = useMemo(() => {
    let netProfit = 0;
    let grossWin = 0;
    let grossLoss = 0;
    let winCount = 0;
    let lossCount = 0;

    const algoMap = new Map<
      string,
      { key: string; net: number; count: number; wins: number; losses: number }
    >();

    for (const d of filteredDeals) {
      netProfit += d.net_profit;
      if (d.net_profit > 0) {
        grossWin += d.net_profit;
        winCount += 1;
      } else if (d.net_profit < 0) {
        grossLoss += Math.abs(d.net_profit);
        lossCount += 1;
      }

      const ak = getAlgoKey(d);
      const cur = algoMap.get(ak) || { key: ak, net: 0, count: 0, wins: 0, losses: 0 };
      cur.net += d.net_profit;
      cur.count += 1;
      if (d.net_profit > 0) cur.wins += 1;
      else if (d.net_profit < 0) cur.losses += 1;
      algoMap.set(ak, cur);
    }

    const total = filteredDeals.length;
    const winRate = total > 0 ? (winCount / total) * 100 : 0;
    const avgWin = winCount > 0 ? grossWin / winCount : 0;
    const avgLoss = lossCount > 0 ? grossLoss / lossCount : 0;
    const profitFactor = grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? 99.99 : 0;

    const algoList = Array.from(algoMap.values()).sort((a, b) => {
      if (a.key === '手动订单') return 1;
      if (b.key === '手动订单') return -1;
      return a.key.localeCompare(b.key);
    });
    const winModels = algoList.filter((a) => a.net >= 0).length;
    const lossModels = algoList.filter((a) => a.net < 0).length;

    return {
      total,
      netProfit,
      grossWin,
      grossLoss,
      winCount,
      lossCount,
      winRate,
      avgWin,
      avgLoss,
      profitFactor,
      algoList,
      winModels,
      lossModels,
    };
  }, [filteredDeals]);

  const clearFilters = () => {
    setAlgoFilter('ALL');
    setSymbolFilter('ALL');
    setSideFilter('ALL');
    setPnlFilter('ALL');
    setStartDate('');
    setEndDate('');
    setSearchText('');
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Activity className="h-5 w-5 text-amber-500" />
            cTrader 交易订单
            <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">
              {filteredDeals.length} 笔
            </span>
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            从 cTrader OpenAPI 拉取成交订单，支持按算法编号/标签筛选汇总与 Bark 实时推送（每 15 秒自动刷新）。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 outline-none"
          >
            <option value={1}>最近 1 天</option>
            <option value={7}>最近 7 天</option>
            <option value={30}>最近 30 天</option>
          </select>
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="inline-flex items-center gap-1.5 rounded-xl bg-amber-500 px-4 py-2 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-60 cursor-pointer"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncing ? 'animate-spin' : ''}`} />
            {syncing ? '拉取中...' : '立即拉取订单'}
          </button>
          <button
            type="button"
            onClick={onNavigateSettings}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
          >
            <Settings className="h-3.5 w-3.5" />
            API 配置
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1 block text-[11px] font-medium text-slate-500">算法 / 模型</label>
            <select
              value={algoFilter}
              onChange={(e) => setAlgoFilter(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 outline-none focus:border-amber-500"
            >
              <option value="ALL">全部算法</option>
              {algoOptions.map((a) => (
                <option key={a} value={a}>
                  {a === '手动订单' ? '手动订单' : `模型 ${a}`}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium text-slate-500">交易品种</label>
            <select
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 outline-none focus:border-amber-500"
            >
              <option value="ALL">全部品种</option>
              {symbolOptions.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium text-slate-500">交易方向</label>
            <select
              value={sideFilter}
              onChange={(e) => setSideFilter(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 outline-none focus:border-amber-500"
            >
              <option value="ALL">全部方向</option>
              <option value="BUY">BUY（做多）</option>
              <option value="SELL">SELL（做空）</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium text-slate-500">盈亏条件</label>
            <select
              value={pnlFilter}
              onChange={(e) => setPnlFilter(e.target.value as 'ALL' | 'WIN' | 'LOSS')}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 outline-none focus:border-amber-500"
            >
              <option value="ALL">全部盈亏</option>
              <option value="WIN">仅盈利</option>
              <option value="LOSS">仅亏损</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium text-slate-500">开始日期</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs text-slate-700 outline-none focus:border-amber-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium text-slate-500">结束日期</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs text-slate-700 outline-none focus:border-amber-500"
            />
          </div>
          <div className="sm:col-span-2 flex items-end gap-2">
            <div className="flex-1">
              <label className="mb-1 block text-[11px] font-medium text-slate-500">搜索订单或标签</label>
              <div className="relative">
                <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
                <input
                  type="text"
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  placeholder="输入订单号、标签、备注..."
                  className="w-full rounded-xl border border-slate-200 pl-8 pr-3 py-2 text-xs text-slate-700 outline-none focus:border-amber-500"
                />
              </div>
            </div>
            <button
              type="button"
              onClick={clearFilters}
              className="inline-flex items-center gap-1 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-100 cursor-pointer"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              清空条件
            </button>
          </div>
        </div>
      </div>

      {/* Period Overall Summary */}
      <div className="space-y-2">
        <div className="text-xs font-semibold text-slate-700">周期整体汇总</div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <div
            className={`rounded-2xl border p-4 shadow-xs ${
              summary.netProfit >= 0
                ? 'border-emerald-200 bg-emerald-50/40'
                : 'border-rose-200 bg-rose-50/40'
            }`}
          >
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>净盈亏</span>
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  summary.netProfit >= 0
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-rose-100 text-rose-700'
                }`}
              >
                {summary.netProfit >= 0 ? '盈利' : '亏损'}
              </span>
            </div>
            <div
              className={`mt-1 text-xl font-bold ${
                summary.netProfit >= 0 ? 'text-emerald-600' : 'text-rose-600'
              }`}
            >
              {summary.netProfit >= 0 ? '+' : ''}
              ${summary.netProfit.toFixed(2)}
            </div>
            <div className="mt-1 text-[11px] text-slate-500">
              总盈 +${summary.grossWin.toFixed(2)} / 总亏 -${summary.grossLoss.toFixed(2)}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="text-xs text-slate-500">综合胜率</div>
            <div className="mt-1 text-xl font-bold text-slate-900">{summary.winRate.toFixed(1)}%</div>
            <div className="mt-1 text-[11px] text-slate-500">
              <span className="text-emerald-600">{summary.winCount} 胜</span> /{' '}
              <span className="text-rose-600">{summary.lossCount} 负</span>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="text-xs text-slate-500">盈亏比 (PF)</div>
            <div className="mt-1 text-xl font-bold text-slate-900">{summary.profitFactor.toFixed(2)}</div>
            <div className="mt-1 text-[11px] text-slate-500">
              均盈 +${summary.avgWin.toFixed(2)} / 均亏 -${summary.avgLoss.toFixed(2)}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="text-xs text-slate-500">交易笔数</div>
            <div className="mt-1 text-xl font-bold text-slate-900">{summary.total} 笔</div>
            <div className="mt-1 text-[11px] text-slate-500">当前筛选匹配</div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
            <div className="text-xs text-slate-500">参与模型</div>
            <div className="mt-1 text-xl font-bold text-slate-900">{summary.algoList.length} 个</div>
            <div className="mt-1 text-[11px] text-slate-500">
              <span className="text-emerald-600">{summary.winModels} 盈</span> /{' '}
              <span className="text-rose-600">{summary.lossModels} 亏</span>
            </div>
          </div>
        </div>
      </div>

      {/* Algorithm PnL Summary */}
      {summary.algoList.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-700">
              算法盈亏汇总 <span className="font-normal text-slate-400">（点击卡片可筛选对应算法）</span>
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {summary.algoList.map((a) => {
              const isWin = a.net >= 0;
              const isSelected = algoFilter === a.key;
              const rate = a.count > 0 ? ((a.wins / a.count) * 100).toFixed(1) : '0.0';
              return (
                <button
                  key={a.key}
                  type="button"
                  onClick={() => setAlgoFilter(isSelected ? 'ALL' : a.key)}
                  className={`text-left rounded-2xl border p-3.5 transition cursor-pointer ${
                    isSelected
                      ? 'ring-2 ring-amber-500 border-amber-400 bg-amber-50/40'
                      : isWin
                        ? 'border-emerald-200/80 bg-emerald-50/30 hover:bg-emerald-50/60'
                        : 'border-rose-200/80 bg-rose-50/30 hover:bg-rose-50/60'
                  }`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-xs font-bold text-slate-800 truncate">
                      {a.key === '手动订单' ? '手动订单' : `模型 ${a.key}`}
                    </span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium shrink-0 ${
                        isWin ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'
                      }`}
                    >
                      {isWin ? '盈利' : '亏损'}
                    </span>
                  </div>
                  <div
                    className={`mt-1.5 text-base font-bold ${
                      isWin ? 'text-emerald-600' : 'text-rose-600'
                    }`}
                  >
                    {isWin ? '+' : ''}${a.net.toFixed(2)}
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    成交 {a.count} 笔（胜率 {rate}%）
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Orders Table */}
      <div className="rounded-2xl border border-slate-200/80 bg-white shadow-xs overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-xs text-slate-500">加载中...</div>
        ) : filteredDeals.length === 0 ? (
          <div className="p-10 text-center space-y-2">
            <p className="text-sm font-medium text-slate-700">暂无匹配的 cTrader 成交订单</p>
            <p className="text-xs text-slate-500">
              可尝试调整筛选条件，或点击右上角「立即拉取订单」同步最新成交。
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 border-b border-slate-200/80">
                <tr>
                  <th className="px-4 py-3 font-medium">成交时间</th>
                  <th className="px-4 py-3 font-medium">标签 / 算法</th>
                  <th className="px-4 py-3 font-medium">品种</th>
                  <th className="px-4 py-3 font-medium">方向</th>
                  <th className="px-4 py-3 font-medium">手数</th>
                  <th className="px-4 py-3 font-medium">成交价</th>
                  <th className="px-4 py-3 font-medium">净盈亏</th>
                  <th className="px-4 py-3 font-medium">推送状态</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredDeals.map((d) => {
                  const isBuy = d.side === 'BUY';
                  const isWin = d.net_profit >= 0;
                  const algo = getAlgoKey(d);
                  return (
                    <tr key={d.deal_id} className="hover:bg-slate-50/70">
                      <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                        {d.executed_at.replace('T', ' ')}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-medium ${
                            algo === '手动订单'
                              ? 'bg-slate-100 text-slate-600'
                              : 'bg-indigo-50 text-indigo-700'
                          }`}
                        >
                          {algo}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-semibold text-slate-900">{d.symbol_name}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium ${
                            isBuy ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
                          }`}
                        >
                          {isBuy ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                          {d.side}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-700">{d.volume}</td>
                      <td className="px-4 py-3 text-slate-700">{d.price}</td>
                      <td
                        className={`px-4 py-3 font-semibold ${
                          isWin ? 'text-emerald-600' : 'text-rose-600'
                        }`}
                      >
                        {isWin ? '+' : ''}
                        {d.net_profit.toFixed(2)}
                      </td>
                      <td className="px-4 py-3">
                        {d.pushed_at ? (
                          <span className="rounded-md bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700">
                            已推送
                          </span>
                        ) : (
                          <span className="text-slate-400">未推送</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
