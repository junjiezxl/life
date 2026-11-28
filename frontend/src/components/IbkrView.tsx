import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Briefcase,
  Calendar,
  CheckCircle2,
  Coins,
  ListOrdered,
  PlugZap,
  PowerOff,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldAlert,
  TrendingUp,
  XCircle,
} from 'lucide-react';
import { api } from '../api';
import type { IbGatewayOrder, IbkrOverviewResponse } from '../types';

interface Props {
  onNotify: (msg: string) => void;
  onNavigateSettings: () => void;
}

type SubTab = 'orders' | 'daily' | 'positions' | 'trades' | 'cash';

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
  const [subTab, setSubTab] = useState<SubTab>('orders');
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [cancellingId, setCancellingId] = useState<number | null>(null);

  const [tradeFilter, setTradeFilter] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('ALL');
  const [sideFilter, setSideFilter] = useState('ALL');

  // Test / manual order form
  const [testSymbol, setTestSymbol] = useState('IAUM');
  const [testMarket, setTestMarket] = useState<'US' | 'HK' | 'CN'>('US');
  const [testSide, setTestSide] = useState<'buy' | 'sell'>('buy');
  const [testQty, setTestQty] = useState('1');
  const [testPrice, setTestPrice] = useState('34.0');
  const [submittingOrder, setSubmittingOrder] = useState(false);

  const loadData = async (acct = selectedAccount) => {
    try {
      const res = await api.getIbkrOverview(acct);
      setData(res);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载 IBKR 网关数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData(selectedAccount);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAccount]);

  const handleConnect = async () => {
    setConnecting(true);
    try {
      await api.connectIbGateway();
      await loadData(selectedAccount);
      onNotify('已连接 IB Gateway');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '连接 IB Gateway 失败');
    } finally {
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    try {
      await api.disconnectIbGateway();
      await loadData(selectedAccount);
      onNotify('已断开 IB Gateway 连接');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '断开失败');
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await api.syncIbGateway({ sync_grids: true, reconcile_orders: false });
      await loadData(selectedAccount);
      onNotify(
        `IB Gateway 同步完成：净值 ${res.equity_snapshots} 条、持仓 ${res.positions} 个、新增成交 ${res.trades_inserted} 笔、同步挂单 ${res.open_orders_synced ?? 0} 笔`,
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'IB Gateway 同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const handlePlaceTestOrder = async (dryRun: boolean) => {
    const qty = Number(testQty);
    const lmt = Number(testPrice);
    if (!testSymbol.trim() || !(qty > 0 && lmt > 0)) {
      onNotify('请填写有效的标的代码、数量与限价');
      return;
    }
    setSubmittingOrder(true);
    try {
      const res = await api.placeIbGatewayTestOrder({
        symbol: testSymbol.trim().toUpperCase(),
        market: testMarket,
        side: testSide,
        quantity: qty,
        lmt_price: lmt,
        dry_run: dryRun,
      });
      onNotify(res.message);
      if (!dryRun) {
        await loadData(selectedAccount);
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '提交测试委托失败');
    } finally {
      setSubmittingOrder(false);
    }
  };

  const handleCancelOrder = async (order: IbGatewayOrder) => {
    setCancellingId(order.id);
    try {
      await api.cancelIbGatewayOrder(order.id);
      await loadData(selectedAccount);
      onNotify(`已撤单：${order.action} ${order.symbol} @ ${order.lmt_price}`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '撤单失败');
    } finally {
      setCancellingId(null);
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
  const gw = data?.gateway_status;
  const orders = data?.orders || [];
  const openOrdersCount = orders.filter((o) => !['Filled', 'Cancelled', 'ApiCancelled', 'Inactive'].includes(o.status)).length;
  const ccy = s?.base_currency || 'USD';

  return (
    <div className="space-y-5">
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
        <div className="flex items-center gap-3">
          <div className={`flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-xs ${gw?.connected ? 'bg-emerald-600' : 'bg-blue-600'}`}>
            <TrendingUp className="h-5 w-5" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-bold text-slate-900">盈透证券 IB Gateway 实时控制台 (`ib_async`)</h2>
              {gw?.connected ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 border border-emerald-200 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-700">
                  <CheckCircle2 className="h-3 w-3" />
                  已连接 {gw.host}:{gw.port} · {(gw.trading_mode || 'paper').toUpperCase()}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-medium text-slate-600">
                  <XCircle className="h-3 w-3 text-slate-400" />
                  未连接 ({gw?.cfg_host || '127.0.0.1'}:{gw?.cfg_port || 4002})
                </span>
              )}
              {gw?.paper_only_guard && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                  <ShieldAlert className="h-3 w-3" /> 模拟盘保护锁开启
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              实时同步账户净值、持仓与成交明细，并支撑股票网格自动限价挂单与委托管理。
              {gw?.last_sync_at ? ` · 上次同步 ${gw.last_sync_at.slice(0, 19).replace('T', ' ')}` : ''}
            </p>
            {gw?.last_error && (
              <p className="text-[11px] text-rose-600 mt-0.5">网关状态提示：{gw.last_error}</p>
            )}
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
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
          >
            <Settings className="h-3.5 w-3.5" />
            <span>网关设置</span>
          </button>
          {gw?.connected ? (
            <button
              type="button"
              onClick={handleDisconnect}
              className="inline-flex items-center gap-1 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700 hover:bg-rose-100 cursor-pointer"
            >
              <PowerOff className="h-3.5 w-3.5" /> 断开
            </button>
          ) : (
            <button
              type="button"
              onClick={handleConnect}
              disabled={connecting}
              className="inline-flex items-center gap-1 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 cursor-pointer"
            >
              <PlugZap className="h-3.5 w-3.5" /> {connecting ? '连接中...' : '连接网关'}
            </button>
          )}
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-4 py-2 text-xs font-medium text-white shadow-xs hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncing ? 'animate-spin' : ''}`} />
            <span>{syncing ? '正在同步...' : '立即同步网关'}</span>
          </button>
        </div>
      </div>

      {/* Onboarding guide when not connected & empty */}
      {!loading && !gw?.connected && s && s.trade_count === 0 && s.position_count === 0 && (
        <div className="rounded-2xl border border-blue-200/80 bg-blue-50/70 p-5 space-y-2.5 text-xs text-slate-700">
          <h3 className="text-sm font-semibold text-blue-900">3 步启动同机 Docker IB Gateway + `ib_async` 实时网关</h3>
          <ol className="list-decimal pl-5 space-y-1.5 leading-relaxed">
            <li>
              进入服务器仓库 <code className="rounded bg-white px-1.5 py-0.5 text-blue-800">deploy/ib-gateway/</code> 目录，复制 <code className="rounded bg-white px-1.5 py-0.5 text-blue-800">.env.example</code> 为 <code className="rounded bg-white px-1.5 py-0.5 text-blue-800">.env</code> 并填入 IBKR 用户名与密码（默认 <code className="rounded bg-white px-1.5 py-0.5 text-blue-800">TRADING_MODE=paper</code>，安全绑定 <code className="rounded bg-white px-1.5 py-0.5 text-blue-800">127.0.0.1:4002</code>）。
            </li>
            <li>
              执行 <code className="rounded bg-white px-1.5 py-0.5 text-blue-800">docker compose up -d</code> 并在手机 <strong>IBKR Mobile (IB Key)</strong> 点确认完成登录。
            </li>
            <li>
              前往 <button type="button" onClick={onNavigateSettings} className="font-semibold text-blue-700 underline cursor-pointer">系统设置 → IBKR 盈透</button> 开启后台网关并保存，点击上方「连接网关 / 立即同步网关」即可开始实盘/模拟盘测试！
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
            { key: 'orders', label: `网关委托与模拟测试 (${openOrdersCount} 活动中 / 共 ${orders.length})` },
            { key: 'daily', label: '每日净值与盈亏汇总' },
            { key: 'positions', label: `当前持仓 (${data?.positions.length || 0})` },
            { key: 'trades', label: `成交明细 (${data?.trades.length || 0})` },
            { key: 'cash', label: `历史分红与现金 (${data?.cash_transactions.length || 0})` },
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

      {/* Sub-tab 0: Gateway Orders & Paper Test Order */}
      {subTab === 'orders' && data && (
        <div className="space-y-4">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
                <Send className="h-4 w-4 text-indigo-600" />
                <span>模拟盘 / 网关测试限价委托（支持规则预校验 & 一键发单）</span>
              </div>
              <span className="text-[11px] text-slate-400">
                自动按美股 $0.01 / 港股阶梯最小价位与手数取整；开启模拟盘保护锁时会拦截实盘发送
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-6">
              <select
                value={testMarket}
                onChange={(e) => setTestMarket(e.target.value as 'US' | 'HK' | 'CN')}
                className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
              >
                <option value="US">美股 (US)</option>
                <option value="HK">港股 (HK)</option>
                <option value="CN">A股通 (CN)</option>
              </select>
              <input
                type="text"
                value={testSymbol}
                onChange={(e) => setTestSymbol(e.target.value)}
                placeholder="代码 如 IAUM / 03152"
                className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
              />
              <select
                value={testSide}
                onChange={(e) => setTestSide(e.target.value as 'buy' | 'sell')}
                className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
              >
                <option value="buy">限价买入 (BUY)</option>
                <option value="sell">限价卖出 (SELL)</option>
              </select>
              <input
                type="number"
                step="any"
                value={testPrice}
                onChange={(e) => setTestPrice(e.target.value)}
                placeholder="限价价格"
                className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
              />
              <input
                type="number"
                step="any"
                value={testQty}
                onChange={(e) => setTestQty(e.target.value)}
                placeholder="数量 (股)"
                className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
              />
              <div className="flex gap-1.5">
                <button
                  type="button"
                  disabled={submittingOrder}
                  onClick={() => handlePlaceTestOrder(true)}
                  className="flex-1 rounded-xl border border-slate-200 bg-white px-2.5 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
                >
                  预校验
                </button>
                <button
                  type="button"
                  disabled={submittingOrder}
                  onClick={() => handlePlaceTestOrder(false)}
                  className="flex-1 rounded-xl bg-indigo-600 px-2.5 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
                >
                  发送测试单
                </button>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs overflow-x-auto space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
                <ListOrdered className="h-4 w-4 text-blue-600" />
                <span>IB Gateway 限价挂单流水</span>
              </h3>
              <span className="text-[11px] text-slate-400">包含自动网格限价单 (`LH_GRID_*`) 与手动测试单</span>
            </div>
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-100 text-[11px] text-slate-500">
                <tr>
                  <th className="py-2 pr-3 font-medium">标的</th>
                  <th className="py-2 px-2 font-medium">方向 / 档位</th>
                  <th className="py-2 px-2 font-medium text-right">限价</th>
                  <th className="py-2 px-2 font-medium text-right">委托 / 已成交</th>
                  <th className="py-2 px-2 font-medium">OrderRef</th>
                  <th className="py-2 px-2 font-medium">模式 / 状态</th>
                  <th className="py-2 px-2 font-medium">更新时间</th>
                  <th className="py-2 pl-2 font-medium text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 tabular-nums">
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="py-8 text-center text-slate-400">
                      暂无网关委托单记录；开启网格自动交易或使用上方模拟测试下单后将在此实时显示。
                    </td>
                  </tr>
                ) : (
                  orders.map((ord) => {
                    const isTerminal = ['Filled', 'Cancelled', 'ApiCancelled', 'Inactive'].includes(ord.status);
                    return (
                      <tr key={ord.id} className="hover:bg-slate-50/70">
                        <td className="py-2.5 pr-3">
                          <span className="font-mono font-semibold text-slate-900">{ord.symbol}</span>
                          <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                            {ord.market}
                          </span>
                        </td>
                        <td className="py-2.5 px-2">
                          <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${ord.side === 'buy' ? 'bg-rose-50 text-rose-700' : 'bg-emerald-50 text-emerald-700'}`}>
                            {ord.action}
                          </span>
                          {ord.grid_level !== null && (
                            <span className="ml-1 text-[11px] text-slate-500">第 {ord.grid_level + 1} 档</span>
                          )}
                        </td>
                        <td className="py-2.5 px-2 text-right font-mono font-semibold text-slate-900">
                          {ord.lmt_price}
                        </td>
                        <td className="py-2.5 px-2 text-right font-mono text-slate-700">
                          {ord.total_qty} / {ord.filled_qty}
                        </td>
                        <td className="py-2.5 px-2 font-mono text-[11px] text-slate-500">
                          {ord.order_ref || `#${ord.order_id}`}
                        </td>
                        <td className="py-2.5 px-2">
                          <span className="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                            {ord.trading_mode.toUpperCase()}
                          </span>
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            ord.status === 'Filled'
                              ? 'bg-emerald-100 text-emerald-700'
                              : isTerminal
                                ? 'bg-slate-100 text-slate-500'
                                : 'bg-blue-100 text-blue-700'
                          }`}>
                            {ord.status || 'Submitted'}
                          </span>
                        </td>
                        <td className="py-2.5 px-2 text-slate-400">
                          {ord.updated_at.slice(0, 19).replace('T', ' ')}
                        </td>
                        <td className="py-2.5 pl-2 text-right">
                          {!isTerminal && (
                            <button
                              type="button"
                              disabled={cancellingId === ord.id}
                              onClick={() => handleCancelOrder(ord)}
                              className="rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1 text-[11px] font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50 cursor-pointer"
                            >
                              {cancellingId === ord.id ? '撤单中...' : '撤单'}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

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
