import React, { useCallback, useEffect, useState } from 'react';
import { Activity, RefreshCw, Settings, TrendingDown, TrendingUp } from 'lucide-react';
import { api } from '../api';
import { CTraderDeal, CTraderStats } from '../types';

interface CTraderOrdersViewProps {
  onNotify: (msg: string) => void;
  onNavigateSettings: () => void;
}

export const CTraderOrdersView: React.FC<CTraderOrdersViewProps> = ({
  onNotify,
  onNavigateSettings,
}) => {
  const [deals, setDeals] = useState<CTraderDeal[]>([]);
  const [stats, setStats] = useState<CTraderStats>({
    total_deals: 0,
    total_net_profit: 0,
    total_volume: 0,
    win_rate: 0,
  });
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [days, setDays] = useState(7);

  const loadDeals = useCallback(async () => {
    try {
      const res = await api.getCTraderDeals(200);
      setDeals(res.deals);
      setStats(res.stats);
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Activity className="h-5 w-5 text-amber-500" />
            cTrader 交易订单
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            从 cTrader OpenAPI 拉取成交订单，支持新订单 Bark 实时推送与自动轮询（每 15 秒刷新）。
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

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500">总成交笔数</div>
          <div className="mt-1 text-2xl font-bold text-slate-900">{stats.total_deals}</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500">累计净盈亏</div>
          <div
            className={`mt-1 text-2xl font-bold ${
              stats.total_net_profit >= 0 ? 'text-emerald-600' : 'text-rose-600'
            }`}
          >
            {stats.total_net_profit >= 0 ? '+' : ''}
            {stats.total_net_profit.toFixed(2)}
          </div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500">胜率</div>
          <div className="mt-1 text-2xl font-bold text-slate-900">{stats.win_rate}%</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500">累计手数</div>
          <div className="mt-1 text-2xl font-bold text-slate-900">{stats.total_volume}</div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200/80 bg-white shadow-xs overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-xs text-slate-500">加载中...</div>
        ) : deals.length === 0 ? (
          <div className="p-10 text-center space-y-2">
            <p className="text-sm font-medium text-slate-700">暂无 cTrader 成交订单</p>
            <p className="text-xs text-slate-500">
              请先点击右上角「API 配置」填写 cTrader OpenAPI Key，然后点击「立即拉取订单」。
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 border-b border-slate-200/80">
                <tr>
                  <th className="px-4 py-3 font-medium">成交时间</th>
                  <th className="px-4 py-3 font-medium">品种</th>
                  <th className="px-4 py-3 font-medium">方向</th>
                  <th className="px-4 py-3 font-medium">手数</th>
                  <th className="px-4 py-3 font-medium">成交价</th>
                  <th className="px-4 py-3 font-medium">净盈亏</th>
                  <th className="px-4 py-3 font-medium">推送状态</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {deals.map((d) => {
                  const isBuy = d.side === 'BUY';
                  const isWin = d.net_profit >= 0;
                  return (
                    <tr key={d.deal_id} className="hover:bg-slate-50/70">
                      <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                        {d.executed_at.replace('T', ' ')}
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
