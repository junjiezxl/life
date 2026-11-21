import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Check,
  Copy,
  History,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  Square,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { api } from '../api';
import { CTraderDeal, CTraderMarketStatus, CTraderQuote, CTraderStats, CTraderSyncStatus } from '../types';

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
  const [subTab, setSubTab] = useState<'orders' | 'market'>('orders');
  const [deals, setDeals] = useState<CTraderDeal[]>([]);
  const [dealStats, setDealStats] = useState<CTraderStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<CTraderSyncStatus | null>(null);
  const [showHistoryPanel, setShowHistoryPanel] = useState(false);
  const [historyStartDate, setHistoryStartDate] = useState<string>('2026-10-01');
  const [historyEndDate, setHistoryEndDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [historyPushBark, setHistoryPushBark] = useState(false);

  // Market live tick state
  const [marketStatus, setMarketStatus] = useState<CTraderMarketStatus | null>(null);
  const [recentTicks, setRecentTicks] = useState<CTraderQuote[]>([]);
  const [symbolsInput, setSymbolsInput] = useState<string>('XAUUSD,EURUSD');
  const [symbolsDirty, setSymbolsDirty] = useState(false);
  const [tickSymbolFilter, setTickSymbolFilter] = useState<string>('ALL');
  const [marketBusy, setMarketBusy] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [apiTestOutput, setApiTestOutput] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

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
      const res = await api.getCTraderDeals(5000);
      setDeals(res.deals);
      setDealStats(res.stats);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载 cTrader 订单失败');
    } finally {
      setLoading(false);
    }
  }, [onNotify]);

  const pollSyncStatus = useCallback(async () => {
    try {
      const st = await api.getCTraderSyncStatus();
      setSyncStatus(st);
      return st;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    loadDeals();
    pollSyncStatus();
    const timer = window.setInterval(loadDeals, 15000);
    return () => window.clearInterval(timer);
  }, [loadDeals, pollSyncStatus]);

  useEffect(() => {
    if (!syncStatus?.running) return;
    let active = true;
    const timer = window.setInterval(async () => {
      if (!active) return;
      const st = await pollSyncStatus();
      if (!st || !active) return;
      if (st.phase === 'enriching_labels') {
        loadDeals();
      }
      if (!st.running) {
        await loadDeals();
        if (st.last_error) {
          onNotify(`历史拉取异常：${st.last_error}`);
        } else if (st.last_result) {
          onNotify(
            `历史拉取完成：共获取 ${st.last_result.fetched ?? 0} 笔，新增 ${st.last_result.inserted ?? 0} 笔，更新 ${st.last_result.updated ?? 0} 笔`,
          );
        }
      }
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [syncStatus?.running, pollSyncStatus, loadDeals, onNotify]);

  const loadMarketData = useCallback(async (withTicks = false) => {
    try {
      if (withTicks) {
        const res = await api.getCTraderRecentTicks(tickSymbolFilter === 'ALL' ? '' : tickSymbolFilter, 80);
        setMarketStatus(res.status);
        setRecentTicks(res.ticks);
        if (!symbolsDirty && res.status.symbols?.length) {
          setSymbolsInput(res.status.symbols.join(','));
        }
      } else {
        const st = await api.getCTraderMarketStatus();
        setMarketStatus(st);
        if (!symbolsDirty && st.symbols?.length) {
          setSymbolsInput(st.symbols.join(','));
        }
      }
    } catch {
      // ignore transient polling failure
    }
  }, [tickSymbolFilter, symbolsDirty]);

  useEffect(() => {
    loadMarketData(subTab === 'market');
    const timer = window.setInterval(
      () => loadMarketData(subTab === 'market' && !wsConnected),
      subTab === 'market' ? 1500 : 3000,
    );
    return () => window.clearInterval(timer);
  }, [loadMarketData, subTab, wsConnected]);

  useEffect(() => {
    if (subTab !== 'market' || !marketStatus?.running) {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setWsConnected(false);
      return;
    }

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws`;
    let closedByCleanup = false;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
    };
    ws.onmessage = (ev) => {
      try {
        const tick = JSON.parse(ev.data) as CTraderQuote;
        if (!tick || !tick.symbol) return;
        setMarketStatus((prev) => {
          if (!prev) return prev;
          const nextCounts = { ...prev.tick_counts, [tick.symbol]: (prev.tick_counts[tick.symbol] || 0) + 1 };
          return {
            ...prev,
            quotes: { ...prev.quotes, [tick.symbol]: tick },
            tick_counts: nextCounts,
            total_ticks: prev.total_ticks + 1,
          };
        });
        setRecentTicks((prev) => {
          if (
            tickSymbolFilter !== 'ALL' &&
            tick.symbol.toUpperCase() !== tickSymbolFilter.toUpperCase() &&
            !tick.symbol.toUpperCase().startsWith(tickSymbolFilter.toUpperCase())
          ) {
            return prev;
          }
          return [tick, ...prev].slice(0, 80);
        });
      } catch {
        // ignore invalid payload
      }
    };
    ws.onclose = () => {
      setWsConnected(false);
      if (wsRef.current === ws) wsRef.current = null;
    };
    ws.onerror = () => {
      if (!closedByCleanup) setWsConnected(false);
    };

    return () => {
      closedByCleanup = true;
      ws.close();
      if (wsRef.current === ws) wsRef.current = null;
      setWsConnected(false);
    };
  }, [subTab, marketStatus?.running, tickSymbolFilter]);

  const handleMarketStart = async () => {
    setMarketBusy(true);
    try {
      const res = await api.startCTraderMarket(symbolsInput.trim() || undefined);
      setMarketStatus(res.status);
      setSymbolsDirty(false);
      onNotify('实时 Tick 行情采集已启动');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '启动实时 Tick 失败');
    } finally {
      setMarketBusy(false);
    }
  };

  const handleMarketStop = async () => {
    setMarketBusy(true);
    try {
      const res = await api.stopCTraderMarket();
      setMarketStatus(res.status);
      onNotify('已停止实时 Tick 行情采集');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '停止实时 Tick 失败');
    } finally {
      setMarketBusy(false);
    }
  };

  const handleMarketRestart = async () => {
    setMarketBusy(true);
    try {
      const res = await api.restartCTraderMarket();
      setMarketStatus(res.status);
      onNotify('已重新连接 cTrader 实时 Tick 行情');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '重连实时 Tick 失败');
    } finally {
      setMarketBusy(false);
    }
  };

  const copyEndpoint = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((curr) => (curr === key ? null : curr)), 2000);
    } catch {
      onNotify('复制失败，请手动复制');
    }
  };

  const handleTestQuotesApi = async () => {
    try {
      const resp = await fetch('/quotes', { cache: 'no-store', credentials: 'same-origin' });
      const data = await resp.json();
      setApiTestOutput(JSON.stringify(data, null, 2));
    } catch (err) {
      setApiTestOutput(`请求失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await api.syncCTraderDeals({
        mode: 'incremental',
        days: 7,
        overlap_seconds: 3,
        push_new: true,
      });
      const strategyLabel =
        res.sync_strategy === 'incremental_from_last' ? '增量（上一笔提前3秒）' : '首次7天回补';
      onNotify(
        `${strategyLabel}完成：拉取 ${res.fetched ?? 0} 笔，新增 ${res.inserted ?? 0} 笔，推送 ${res.pushed ?? 0} 条`,
      );
      await loadDeals();
      await pollSyncStatus();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '拉取 cTrader 订单失败');
    } finally {
      setSyncing(false);
    }
  };

  const applyHistoryDaysPreset = (presetDays: number) => {
    const end = new Date();
    const start = new Date(end.getTime() - presetDays * 86400 * 1000);
    setHistoryStartDate(start.toISOString().slice(0, 10));
    setHistoryEndDate(end.toISOString().slice(0, 10));
  };

  const handleHistoryBackfill = async () => {
    if (!historyStartDate) {
      onNotify('请先选择开始日期');
      return;
    }
    try {
      const res = await api.syncCTraderDeals({
        mode: 'range',
        start_date: historyStartDate,
        end_date: historyEndDate || undefined,
        push_new: historyPushBark,
        background: true,
      });
      if (res.status) {
        setSyncStatus(res.status);
      } else {
        await pollSyncStatus();
      }
      if (res.already_running) {
        onNotify('已有拉取任务正在执行，请等待完成');
      } else {
        onNotify(`已启动 ${historyStartDate} 至 ${historyEndDate || '今天'} 历史全量拉取`);
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '启动历史数据拉取失败');
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

  const quoteEntries = useMemo(() => {
    const q = marketStatus?.quotes || {};
    return Object.values(q).sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [marketStatus]);

  const statusMeta = useMemo(() => {
    const s = marketStatus?.status || 'stopped';
    if (s === 'streaming') return { label: '实时推送中', cls: 'bg-emerald-100 text-emerald-700 border-emerald-200' };
    if (s === 'connecting') return { label: '连接中...', cls: 'bg-amber-100 text-amber-700 border-amber-200' };
    if (s === 'reconnecting') return { label: '断线重连中', cls: 'bg-amber-100 text-amber-700 border-amber-200' };
    if (s === 'unconfigured') return { label: '未配置凭据', cls: 'bg-slate-100 text-slate-600 border-slate-200' };
    return { label: '已停止', cls: 'bg-slate-100 text-slate-600 border-slate-200' };
  }, [marketStatus?.status]);

  const httpOrigin = typeof window !== 'undefined' ? window.location.origin : '';
  const wsOrigin =
    typeof window !== 'undefined'
      ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
      : '';

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Activity className="h-5 w-5 text-amber-500" />
            cTrader 实时行情 & 订单
            <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${statusMeta.cls}`}>
              {statusMeta.label}
            </span>
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            订阅 cTrader 实时 Tick 行情并对外提供 REST / WebSocket 接口，同时支持订单拉取、筛选汇总与 Bark 推送。
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-xl border border-slate-200 bg-white p-1">
            <button
              type="button"
              onClick={() => setSubTab('orders')}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition cursor-pointer ${
                subTab === 'orders' ? 'bg-amber-500 text-white' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              交易订单 ({filteredDeals.length})
            </button>
            <button
              type="button"
              onClick={() => setSubTab('market')}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition cursor-pointer ${
                subTab === 'market' ? 'bg-amber-500 text-white' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              <Radio className="h-3.5 w-3.5" />
              实时 Tick & 对外接口
            </button>
          </div>
          {subTab === 'orders' && (
            <>
              <button
                type="button"
                onClick={handleSync}
                disabled={syncing || !!syncStatus?.running}
                title="自动从库内最新一笔成交时间提前 3 秒开始增量拉取"
                className="inline-flex items-center gap-1.5 rounded-xl bg-amber-500 px-4 py-2 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-60 cursor-pointer"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${syncing ? 'animate-spin' : ''}`} />
                {syncing ? '增量拉取中...' : '增量拉取最新'}
              </button>
              <button
                type="button"
                onClick={() => setShowHistoryPanel((v) => !v)}
                className={`inline-flex items-center gap-1.5 rounded-xl border px-3.5 py-2 text-xs font-medium transition cursor-pointer ${
                  showHistoryPanel || syncStatus?.running
                    ? 'border-amber-300 bg-amber-50 text-amber-800'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                <History className="h-3.5 w-3.5" />
                拉取历史数据
              </button>
            </>
          )}
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

      {/* Live Quote Strip */}
      {quoteEntries.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200/80 bg-white px-4 py-2.5 shadow-xs text-xs">
          <span className="inline-flex items-center gap-1.5 font-semibold text-slate-700">
            <span
              className={`h-2 w-2 rounded-full ${
                marketStatus?.status === 'streaming' ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'
              }`}
            />
            内存实时报价
          </span>
          {quoteEntries.map((q) => (
            <div
              key={q.symbol}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200/80 bg-slate-50 px-3 py-1 font-mono"
            >
              <span className="font-bold text-slate-800">{q.symbol}</span>
              <span className="text-emerald-700">Bid {q.bid.toFixed(q.bid > 50 ? 2 : 5)}</span>
              <span className="text-rose-700">Ask {q.ask.toFixed(q.ask > 50 ? 2 : 5)}</span>
              <span className="text-slate-400">Spread {q.spread.toFixed(q.bid > 50 ? 2 : 5)}</span>
            </div>
          ))}
          {marketStatus && (
            <span className="ml-auto text-[11px] text-slate-400">
              {marketStatus.ticks_per_sec} t/s · WS {marketStatus.active_ws_clients} 连接
            </span>
          )}
        </div>
      )}

      {subTab === 'market' ? (
        <div className="space-y-5">
          {/* Stream Control Panel */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">实时 Tick 订阅节点</h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  复用 cTrader OpenAPI 凭据直连实时 Spot 行情，自动断线重连并缓存在内存中供外部调用。
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-slate-700">
                  模式: {marketStatus?.mode || 'STOPPED'}
                </span>
                <span className="rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-slate-700">
                  速率: {marketStatus?.ticks_per_sec ?? 0} t/s
                </span>
                <span className="rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-slate-700">
                  累计: {(marketStatus?.total_ticks ?? 0).toLocaleString()} ticks
                </span>
                <span className="rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-slate-700">
                  WS 客户端: {marketStatus?.active_ws_clients ?? 0}
                </span>
              </div>
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[220px]">
                <label className="mb-1 block text-[11px] font-medium text-slate-500">
                  订阅品种（逗号分隔，例如 XAUUSD,EURUSD）
                </label>
                <input
                  type="text"
                  value={symbolsInput}
                  onChange={(e) => {
                    setSymbolsDirty(true);
                    setSymbolsInput(e.target.value);
                  }}
                  placeholder="XAUUSD,EURUSD"
                  className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs text-slate-800 outline-none focus:border-amber-500"
                />
              </div>
              <button
                type="button"
                onClick={handleMarketStart}
                disabled={marketBusy}
                className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-60 cursor-pointer"
              >
                <Play className="h-3.5 w-3.5" />
                {marketStatus?.running ? '更新品种并重连' : '启动采集'}
              </button>
              {marketStatus?.running && (
                <>
                  <button
                    type="button"
                    onClick={handleMarketRestart}
                    disabled={marketBusy}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60 cursor-pointer"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    重连
                  </button>
                  <button
                    type="button"
                    onClick={handleMarketStop}
                    disabled={marketBusy}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-xs font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-60 cursor-pointer"
                  >
                    <Square className="h-3.5 w-3.5" />
                    停止
                  </button>
                </>
              )}
            </div>

            {marketStatus?.last_error && (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700">
                {marketStatus.last_error}
              </div>
            )}
          </div>

          {/* Live Symbol Cards */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {quoteEntries.length === 0 ? (
              <div className="sm:col-span-2 lg:col-span-4 rounded-2xl border border-dashed border-slate-200 bg-white p-8 text-center text-xs text-slate-500">
                暂无内存报价数据。请确认已在「系统设置」配置 cTrader OpenAPI 凭据并点击「启动采集」。
              </div>
            ) : (
              quoteEntries.map((q) => {
                const digits = q.bid > 50 ? 2 : 5;
                return (
                  <div
                    key={q.symbol}
                    className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs space-y-2"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-bold text-slate-900">{q.symbol}</span>
                      <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-mono text-slate-600">
                        {(marketStatus?.tick_counts?.[q.symbol] || 0).toLocaleString()} ticks
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 font-mono">
                      <div>
                        <div className="text-[11px] text-slate-400">Bid 卖出价</div>
                        <div className="text-lg font-bold text-emerald-600">{q.bid.toFixed(digits)}</div>
                      </div>
                      <div>
                        <div className="text-[11px] text-slate-400">Ask 买入价</div>
                        <div className="text-lg font-bold text-rose-600">{q.ask.toFixed(digits)}</div>
                      </div>
                    </div>
                    <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-[11px] text-slate-500 font-mono">
                      <span>点差 {q.spread.toFixed(digits)}</span>
                      <span>{q.datetime_utc} UTC</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* External API Endpoints & Quick Test */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">对外提供实时行情接口（兼容原 gold.36xt.com）</h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  {marketStatus?.has_api_token
                    ? '当前已启用接口 Token 保护，外部调用请携带 Header `Authorization: Bearer <Token>`、`X-API-Key: <Token>` 或参数 `?token=<Token>`。'
                    : '当前为直接开放模式（未设置接口 Token），外部脚本与策略可直接访问以下 REST 与 WebSocket 接口；如需鉴权可在「系统设置」填写对外行情接口访问 Token。'}
                </p>
              </div>
              <button
                type="button"
                onClick={handleTestQuotesApi}
                className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 cursor-pointer"
              >
                在线测试 GET /quotes
              </button>
            </div>

            <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
              {[
                { key: 'status', method: 'GET', url: `${httpOrigin}/status`, desc: '节点运行模式、订阅品种、Tick 计数与 WS 连接数' },
                { key: 'quotes', method: 'GET', url: `${httpOrigin}/quotes`, desc: '获取所有已订阅品种的最新内存 Bid / Ask / Spread' },
                { key: 'quote_sym', method: 'GET', url: `${httpOrigin}/quote/XAUUSD`, desc: '获取指定品种最新报价（支持前缀匹配）' },
                { key: 'ws_all', method: 'WS', url: `${wsOrigin}/ws`, desc: '全量实时 Tick 毫秒级广播推流' },
                { key: 'ws_sym', method: 'WS', url: `${wsOrigin}/ws/XAUUSD`, desc: '指定品种实时 Tick 推流' },
              ].map((ep) => (
                <div
                  key={ep.key}
                  className="flex items-center justify-between gap-3 rounded-xl border border-slate-200/80 bg-slate-50/70 px-3.5 py-2.5"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                          ep.method === 'WS' ? 'bg-indigo-100 text-indigo-700' : 'bg-emerald-100 text-emerald-700'
                        }`}
                      >
                        {ep.method}
                      </span>
                      <code className="truncate text-xs font-mono text-slate-800">{ep.url}</code>
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">{ep.desc}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => copyEndpoint(ep.key, ep.url)}
                    className="shrink-0 rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 hover:text-slate-800 cursor-pointer"
                    title="复制接口地址"
                  >
                    {copiedKey === ep.key ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
                  </button>
                </div>
              ))}
            </div>

            {apiTestOutput && (
              <pre className="max-h-60 overflow-auto rounded-xl bg-slate-900 p-3.5 text-[11px] font-mono text-emerald-300">
                {apiTestOutput}
              </pre>
            )}
          </div>

          {/* Live Tick Stream Table */}
          <div className="rounded-2xl border border-slate-200/80 bg-white shadow-xs overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/80 px-5 py-3.5">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-slate-900">实时 Tick 明细流（最近 80 条）</span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    wsConnected ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'
                  }`}
                >
                  {wsConnected ? 'WS 实时推流中' : '轮询同步'}
                </span>
              </div>
              <select
                value={tickSymbolFilter}
                onChange={(e) => setTickSymbolFilter(e.target.value)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none"
              >
                <option value="ALL">全部品种</option>
                {(marketStatus?.symbols || ['XAUUSD', 'EURUSD']).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            {recentTicks.length === 0 ? (
              <div className="p-8 text-center text-xs text-slate-500">
                暂无实时 Tick 采样，等待行情推流到达...
              </div>
            ) : (
              <div className="overflow-x-auto max-h-[420px]">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="sticky top-0 bg-slate-50 text-slate-500 border-b border-slate-200/80">
                    <tr>
                      <th className="px-4 py-2.5 font-medium">UTC 时间</th>
                      <th className="px-4 py-2.5 font-medium">品种</th>
                      <th className="px-4 py-2.5 font-medium">买入价 (Bid)</th>
                      <th className="px-4 py-2.5 font-medium">卖出价 (Ask)</th>
                      <th className="px-4 py-2.5 font-medium">点差 (Spread)</th>
                      <th className="px-4 py-2.5 font-medium">来源</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {recentTicks.map((t, idx) => {
                      const digits = t.bid > 50 ? 2 : 5;
                      return (
                        <tr key={`${t.symbol}-${t.timestamp}-${idx}`} className="hover:bg-slate-50/70">
                          <td className="px-4 py-2 text-slate-600 whitespace-nowrap">{t.datetime_utc}</td>
                          <td className="px-4 py-2 font-bold text-slate-900">{t.symbol}</td>
                          <td className="px-4 py-2 text-emerald-600 font-semibold">{t.bid.toFixed(digits)}</td>
                          <td className="px-4 py-2 text-rose-600 font-semibold">{t.ask.toFixed(digits)}</td>
                          <td className="px-4 py-2 text-slate-700">{t.spread.toFixed(digits)}</td>
                          <td className="px-4 py-2 text-slate-400">{t.source}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      ) : (
        <>

      {/* Historical Backfill & Sync Progress Panel */}
      {(showHistoryPanel || syncStatus?.running) && (
        <div className="rounded-2xl border border-amber-200/80 bg-amber-50/40 p-4 shadow-xs space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
                <History className="h-4 w-4 text-amber-600" />
                拉取指定周期历史成交数据（自动按 7 天分段 + 限频防截断）
              </h3>
              <p className="text-[11px] text-slate-500 mt-0.5">
                默认「增量拉取最新」从库内最后一笔提前 3 秒开始，避免同一毫秒并发遗漏；如需补齐更早记录，可在此拉取完整区间。
                {dealStats && dealStats.total_deals > 0 && (
                  <span className="ml-1 text-slate-700 font-medium">
                    当前库内共 {dealStats.total_deals} 笔（
                    {(dealStats.earliest_executed_at || '').replace('T', ' ').slice(0, 16) || '—'} ~{' '}
                    {(dealStats.latest_executed_at || '').replace('T', ' ').slice(0, 16) || '—'}）
                  </span>
                )}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <button
                type="button"
                onClick={() => {
                  setHistoryStartDate('2026-10-01');
                  setHistoryEndDate(new Date().toISOString().slice(0, 10));
                }}
                className="rounded-lg border border-amber-200 bg-white px-2.5 py-1 font-medium text-amber-800 hover:bg-amber-100 cursor-pointer"
              >
                2026-10-01 至今
              </button>
              <button
                type="button"
                onClick={() => applyHistoryDaysPreset(30)}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-slate-700 hover:bg-slate-100 cursor-pointer"
              >
                最近 30 天
              </button>
              <button
                type="button"
                onClick={() => applyHistoryDaysPreset(60)}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-slate-700 hover:bg-slate-100 cursor-pointer"
              >
                最近 60 天
              </button>
              <button
                type="button"
                onClick={() => applyHistoryDaysPreset(90)}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-slate-700 hover:bg-slate-100 cursor-pointer"
              >
                最近 90 天
              </button>
              <button
                type="button"
                onClick={() => applyHistoryDaysPreset(180)}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-slate-700 hover:bg-slate-100 cursor-pointer"
              >
                最近 180 天
              </button>
            </div>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="mb-1 block text-[11px] font-medium text-slate-600">开始日期</label>
              <input
                type="date"
                value={historyStartDate}
                onChange={(e) => setHistoryStartDate(e.target.value)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-amber-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] font-medium text-slate-600">结束日期</label>
              <input
                type="date"
                value={historyEndDate}
                onChange={(e) => setHistoryEndDate(e.target.value)}
                className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-amber-500"
              />
            </div>
            <label className="inline-flex items-center gap-1.5 text-xs text-slate-600 pb-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={historyPushBark}
                onChange={(e) => setHistoryPushBark(e.target.checked)}
              />
              <span>新入库历史订单也发 Bark 推送</span>
            </label>
            <button
              type="button"
              onClick={handleHistoryBackfill}
              disabled={!!syncStatus?.running}
              className="ml-auto inline-flex items-center gap-1.5 rounded-xl bg-amber-600 px-4 py-2 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-60 cursor-pointer"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${syncStatus?.running ? 'animate-spin' : ''}`} />
              {syncStatus?.running ? '后台全量拉取中...' : '拉取指定周期全部数据'}
            </button>
          </div>

          {syncStatus && (syncStatus.running || syncStatus.phase === 'completed' || syncStatus.last_error) && (
            <div className="rounded-xl border border-slate-200/80 bg-white px-3.5 py-2.5 text-xs space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-slate-800">
                  {syncStatus.running
                    ? syncStatus.phase === 'fetching_deals'
                      ? `正在分段拉取历史成交（第 ${syncStatus.chunks_done}/${Math.max(1, syncStatus.chunks_total)} 段）...`
                      : syncStatus.phase === 'enriching_labels'
                        ? `已保存 ${syncStatus.deals_fetched} 笔成交，正在补充算法标签（${syncStatus.labels_done}/${syncStatus.labels_total}）...`
                        : '正在连接 cTrader OpenAPI...'
                    : syncStatus.last_error
                      ? `拉取失败：${syncStatus.last_error}`
                      : `最近一次同步完成：共获取 ${syncStatus.last_result?.fetched ?? syncStatus.deals_fetched} 笔，新增 ${syncStatus.last_result?.inserted ?? syncStatus.inserted} 笔，更新 ${syncStatus.last_result?.updated ?? syncStatus.updated} 笔`}
                </span>
                {syncStatus.deals_fetched > 0 && (
                  <span className="font-mono text-[11px] text-slate-500">
                    已获取 {syncStatus.deals_fetched} 笔 · 新增 {syncStatus.inserted} / 更新 {syncStatus.updated}
                  </span>
                )}
              </div>
              {syncStatus.running && syncStatus.chunks_total > 0 && (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full bg-amber-500 transition-all duration-300"
                    style={{
                      width: `${
                        syncStatus.phase === 'enriching_labels' && syncStatus.labels_total > 0
                          ? Math.round((syncStatus.labels_done / syncStatus.labels_total) * 100)
                          : Math.round((syncStatus.chunks_done / syncStatus.chunks_total) * 100)
                      }%`,
                    }}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      )}

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
        </>
      )}
    </div>
  );
};
