import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownCircle,
  ArrowUpCircle,
  BellRing,
  CheckCircle2,
  CheckSquare,
  Download,
  Edit3,
  Grid3x3,
  Layers,
  Mail,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  TrendingUp,
  Upload,
  X,
} from 'lucide-react';
import { api } from '../api';
import type {
  EmailAccount,
  EmailCacheStats,
  EmailStockTradeCandidate,
  GridDetailResponse,
  GridEmailScannedItem,
  GridIbkrCandidate,
  GridListItem,
  GridListResponse,
  GridStrategy,
} from '../types';
import { formatCurrency, formatMoney } from '../utils/format';

interface Props {
  onNotify: (msg: string) => void;
}

const MARKET_META: Record<string, { label: string; cls: string; currency: string }> = {
  CN: { label: 'A股', cls: 'bg-rose-50 text-rose-700 border-rose-200', currency: 'CNY' },
  HK: { label: '港股', cls: 'bg-amber-50 text-amber-700 border-amber-200', currency: 'HKD' },
  US: { label: '美股', cls: 'bg-sky-50 text-sky-700 border-sky-200', currency: 'USD' },
};

const FEE_PRESETS = [
  { key: 'cn_etf', label: 'A股 ETF（万1免5·无印花税）', market: 'CN' as const, fee_rate: 0.0001, min_fee: 0, stamp_tax_rate: 0, stamp_tax_side: 'sell' as const, shares: 1000 },
  { key: 'cn_stock', label: 'A股正股（万2.5低5·卖万5税）', market: 'CN' as const, fee_rate: 0.00025, min_fee: 5, stamp_tax_rate: 0.0005, stamp_tax_side: 'sell' as const, shares: 100 },
  { key: 'hk_stock', label: '港股（万3低3·双边千1税）', market: 'HK' as const, fee_rate: 0.0003, min_fee: 3, stamp_tax_rate: 0.001, stamp_tax_side: 'both' as const, shares: 100 },
  { key: 'us_stock', label: '美股（免佣低1美元·无税）', market: 'US' as const, fee_rate: 0, min_fee: 1, stamp_tax_rate: 0, stamp_tax_side: 'sell' as const, shares: 10 },
];

function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return '-';
  const n = Number(v);
  return n >= 100 ? n.toFixed(2) : n >= 10 ? n.toFixed(3) : n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

function nowLocalInput(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const StockGridView: React.FC<Props> = ({ onNotify }) => {
  const [listData, setListData] = useState<GridListResponse | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<GridDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshingQuotes, setRefreshingQuotes] = useState(false);

  const [detailTab, setDetailTab] = useState<'ladder' | 'trades' | 'pairs' | 'lots'>('ladder');

  // Manual current price editor
  const [manualPrice, setManualPrice] = useState('');
  const [savingPrice, setSavingPrice] = useState(false);

  // Quick trade form
  const [tradeSide, setTradeSide] = useState<'buy' | 'sell'>('buy');
  const [tradePrice, setTradePrice] = useState('');
  const [tradeQty, setTradeQty] = useState('');
  const [tradeFee, setTradeFee] = useState('');
  const [tradeTax, setTradeTax] = useState('');
  const [tradeAt, setTradeAt] = useState(nowLocalInput());
  const [tradeNote, setTradeNote] = useState('');
  const [tradeLevel, setTradeLevel] = useState<number | undefined>(undefined);
  const [submittingTrade, setSubmittingTrade] = useState(false);

  // Create/Edit strategy modal
  const [showModal, setShowModal] = useState(false);
  const [editingStrategy, setEditingStrategy] = useState<GridStrategy | null>(null);
  const [formName, setFormName] = useState('');
  const [formSymbol, setFormSymbol] = useState('');
  const [formMarket, setFormMarket] = useState<'CN' | 'HK' | 'US'>('CN');
  const [formGridType, setFormGridType] = useState<'arithmetic' | 'geometric'>('arithmetic');
  const [formLower, setFormLower] = useState('');
  const [formUpper, setFormUpper] = useState('');
  const [formStep, setFormStep] = useState('');
  const [formShares, setFormShares] = useState('1000');
  const [formFeeRate, setFormFeeRate] = useState('0.0001');
  const [formMinFee, setFormMinFee] = useState('0');
  const [formTaxRate, setFormTaxRate] = useState('0');
  const [formTaxSide, setFormTaxSide] = useState<'sell' | 'both'>('sell');
  const [formCurPrice, setFormCurPrice] = useState('');
  const [formIbkrAutoSync, setFormIbkrAutoSync] = useState(true);
  const [formBroker, setFormBroker] = useState('');
  const [formNotes, setFormNotes] = useState('');
  const [savingStrat, setSavingStrat] = useState(false);

  // Email / HSBC import modal
  const [showEmailModal, setShowEmailModal] = useState(false);
  const [emailDays, setEmailDays] = useState(30);
  const [emailKeyword, setEmailKeyword] = useState('');
  const [emailPdfPwd, setEmailPdfPwd] = useState('');
  const [emailMailboxes, setEmailMailboxes] = useState<EmailAccount[]>([]);
  const [emailMailboxId, setEmailMailboxId] = useState('');
  const [emailScanning, setEmailScanning] = useState(false);
  const [emailImporting, setEmailImporting] = useState(false);
  const [emailScannedCount, setEmailScannedCount] = useState<number | null>(null);
  const [emailScannedList, setEmailScannedList] = useState<GridEmailScannedItem[]>([]);
  const [emailCacheStats, setEmailCacheStats] = useState<EmailCacheStats | null>(null);
  const [emailWarnings, setEmailWarnings] = useState<string[]>([]);
  const [emailCandidates, setEmailCandidates] = useState<EmailStockTradeCandidate[]>([]);
  const [emailSelected, setEmailSelected] = useState<Record<number, boolean>>({});
  const [emailAutoCreate, setEmailAutoCreate] = useState(true);
  const [emailDefaultStep, setEmailDefaultStep] = useState('3');
  const [emailPasteText, setEmailPasteText] = useState('');
  const [emailPasteSubj, setEmailPasteSubj] = useState('汇丰股票交易成交确认');

  // IBKR import/sync modal
  const [showIbkrModal, setShowIbkrModal] = useState(false);
  const [ibkrLoading, setIbkrLoading] = useState(false);
  const [ibkrSyncing, setIbkrSyncing] = useState(false);
  const [ibkrConfigured, setIbkrConfigured] = useState(false);
  const [ibkrCandidates, setIbkrCandidates] = useState<GridIbkrCandidate[]>([]);
  const [ibkrSelectedSyms, setIbkrSelectedSyms] = useState<Record<string, boolean>>({});
  const [ibkrRefreshFlexFirst, setIbkrRefreshFlexFirst] = useState(false);
  const [ibkrDefaultStepPct, setIbkrDefaultStepPct] = useState('3');

  const loadList = async (keepSelected?: number | null) => {
    setLoading(true);
    try {
      const res = await api.listGridStrategies();
      setListData(res);
      const targetId = keepSelected !== undefined ? keepSelected : selectedId;
      if (targetId && res.items.some((x) => x.strategy.id === targetId)) {
        setSelectedId(targetId);
      } else if (res.items.length > 0) {
        setSelectedId(res.items[0].strategy.id);
      } else {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载股票网格列表失败');
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = async (id: number) => {
    try {
      const res = await api.getGridStrategy(id);
      setDetail(res);
      setManualPrice(res.strategy.current_price > 0 ? String(res.strategy.current_price) : '');
      setTradeQty(String(res.strategy.shares_per_grid || 100));
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载网格详情失败');
    }
  };

  useEffect(() => {
    loadList();
  }, []);

  useEffect(() => {
    if (selectedId !== null) {
      loadDetail(selectedId);
    }
  }, [selectedId]);

  const openCreateModal = () => {
    setEditingStrategy(null);
    setFormName('');
    setFormSymbol('');
    setFormMarket('CN');
    setFormGridType('arithmetic');
    setFormLower('');
    setFormUpper('');
    setFormStep('');
    setFormShares('1000');
    setFormFeeRate('0.0001');
    setFormMinFee('0');
    setFormTaxRate('0');
    setFormTaxSide('sell');
    setFormCurPrice('');
    setFormIbkrAutoSync(true);
    setFormBroker('');
    setFormNotes('');
    setShowModal(true);
  };

  const openEditModal = (s: GridStrategy) => {
    setEditingStrategy(s);
    setFormName(s.name);
    setFormSymbol(s.symbol);
    setFormMarket(s.market);
    setFormGridType(s.grid_type);
    setFormLower(s.lower_price > 0 ? String(s.lower_price) : '');
    setFormUpper(s.upper_price > 0 ? String(s.upper_price) : '');
    setFormStep(s.step_value > 0 ? String(s.step_value) : '');
    setFormShares(String(s.shares_per_grid));
    setFormFeeRate(String(s.fee_rate));
    setFormMinFee(String(s.min_fee));
    setFormTaxRate(String(s.stamp_tax_rate));
    setFormTaxSide(s.stamp_tax_side);
    setFormCurPrice(s.current_price > 0 ? String(s.current_price) : '');
    setFormIbkrAutoSync(s.ibkr_auto_sync !== 0 && s.ibkr_auto_sync !== false);
    setFormBroker(s.broker || '');
    setFormNotes(s.notes || '');
    setShowModal(true);
  };

  const openEmailModal = async () => {
    setShowEmailModal(true);
    try {
      const [mbs, cs] = await Promise.all([
        api.listEmailAccounts(),
        api.getEmailCacheStats().catch(() => null),
      ]);
      setEmailMailboxes(mbs.filter((m) => m.enabled));
      if (cs) setEmailCacheStats(cs);
    } catch {
      // ignore
    }
  };

  const populateEmailTrades = (rows: EmailStockTradeCandidate[], append = false) => {
    if (append) {
      setEmailCandidates((prev) => {
        const next = [...prev, ...rows];
        const sel: Record<number, boolean> = { ...emailSelected };
        rows.forEach((t, offset) => {
          sel[prev.length + offset] = !t.already_imported;
        });
        setEmailSelected(sel);
        return next;
      });
      return;
    }
    setEmailCandidates(rows);
    const sel: Record<number, boolean> = {};
    rows.forEach((t, idx) => {
      sel[idx] = !t.already_imported;
    });
    setEmailSelected(sel);
  };

  const handleScanEmailTrades = async (source: 'auto' | 'cache' = 'auto') => {
    setEmailScanning(true);
    setEmailWarnings([]);
    try {
      const res = await api.fetchGridEmailTrades({
        days: emailDays,
        limit: emailDays >= 365 ? 1500 : 150,
        keyword: emailKeyword.trim(),
        pdf_password: emailPdfPwd.trim(),
        account_id: emailMailboxId ? Number(emailMailboxId) : undefined,
        include_body_ai: true,
        source,
      });
      setEmailScannedCount(res.emails_scanned);
      setEmailScannedList(res.scanned_emails || []);
      if (res.cache_stats) setEmailCacheStats(res.cache_stats);
      setEmailWarnings(res.warnings || []);
      populateEmailTrades(res.trades || [], false);
      const prefix = source === 'cache' ? '从本地邮件库重扫' : `增量拉取并扫描`;
      onNotify(`${prefix} ${res.emails_scanned} 封邮件，识别到 ${res.trades.length} 笔股票成交`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '扫描邮箱股票成交失败');
    } finally {
      setEmailScanning(false);
    }
  };

  const handleParseEmailTradePaste = async () => {
    if (!emailPasteText.trim()) return;
    setEmailScanning(true);
    try {
      const res = await api.parseGridEmailTradeText({
        subject: emailPasteSubj.trim() || '汇丰股票交易通知',
        sender: '汇丰银行 HSBC',
        body: emailPasteText.trim(),
      });
      populateEmailTrades(res.trades || [], true);
      onNotify(`从正文识别出 ${res.trades.length} 笔股票成交`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '解析邮件正文失败');
    } finally {
      setEmailScanning(false);
    }
  };

  const handleUploadTradePdf = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setEmailScanning(true);
    try {
      const res = await api.parseGridEmailTradePdf(file, emailPdfPwd.trim());
      populateEmailTrades(res.trades || [], true);
      onNotify(`从 PDF「${file.name}」识别出 ${res.trades.length} 笔股票成交`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'PDF 成交单解析失败');
    } finally {
      setEmailScanning(false);
      e.target.value = '';
    }
  };

  const handleCommitEmailTrades = async () => {
    const picked = emailCandidates.filter((_, idx) => emailSelected[idx]);
    if (picked.length === 0) {
      onNotify('请先勾选至少一笔股票成交');
      return;
    }
    setEmailImporting(true);
    try {
      const res = await api.importGridEmailTrades({
        trades: picked,
        auto_create_missing: emailAutoCreate,
        default_step_pct: Number(emailDefaultStep || 3),
      });
      const parts: string[] = [];
      if (res.created_strategies.length > 0) parts.push(`自动新建网格 ${res.created_strategies.length} 个`);
      parts.push(`导入成交 ${res.imported_trades} 笔`);
      if (res.skipped_duplicates > 0) parts.push(`跳过重复 ${res.skipped_duplicates} 笔`);
      if (res.unmatched_count > 0) parts.push(`未匹配跳过 ${res.unmatched_count} 笔`);
      onNotify(`邮件交易同步完成：${parts.join('，')}`);
      const firstCreated = res.created_strategies[0]?.id;
      await loadList(firstCreated ?? selectedId);
      if (firstCreated) {
        setSelectedId(firstCreated);
      } else if (selectedId) {
        await loadDetail(selectedId);
      }
      setEmailCandidates([]);
      setEmailSelected({});
      setShowEmailModal(false);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '导入邮件成交失败');
    } finally {
      setEmailImporting(false);
    }
  };

  const openIbkrModal = async () => {
    setShowIbkrModal(true);
    setIbkrLoading(true);
    try {
      const res = await api.listGridIbkrCandidates();
      setIbkrConfigured(res.configured);
      setIbkrCandidates(res.items || []);
      const sel: Record<string, boolean> = {};
      (res.items || []).forEach((it) => {
        if (it.linked_strategy_id === null && (it.trade_count > 0 || Math.abs(it.position_qty) > 0)) {
          sel[it.symbol] = true;
        }
      });
      setIbkrSelectedSyms(sel);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载 IBKR 标的失败');
    } finally {
      setIbkrLoading(false);
    }
  };

  const handleSyncIbkrAll = async (includeCreate: boolean) => {
    setIbkrSyncing(true);
    try {
      const createSymbols = includeCreate
        ? ibkrCandidates.filter((c) => c.linked_strategy_id === null && ibkrSelectedSyms[c.symbol]).map((c) => c.symbol)
        : [];
      const res = await api.syncGridFromIbkr({
        create_symbols: createSymbols,
        refresh_flex_first: ibkrRefreshFlexFirst,
        default_step_pct: Number(ibkrDefaultStepPct || 3),
      });
      const parts: string[] = [];
      if (res.flex_refreshed) parts.push('已拉取最新 IBKR Flex 报表');
      if (res.created_strategies.length > 0) parts.push(`新建网格 ${res.created_strategies.length} 个`);
      parts.push(`导入成交 ${res.imported_trades} 笔`);
      if (res.skipped_duplicates > 0) parts.push(`跳过已存在 ${res.skipped_duplicates} 笔`);
      onNotify(`IBKR 同步完成：${parts.join('，')}`);
      const firstNewId = res.created_strategies[0]?.id;
      await loadList(firstNewId ?? selectedId);
      if (firstNewId) {
        setSelectedId(firstNewId);
      } else if (selectedId) {
        await loadDetail(selectedId);
      }
      setShowIbkrModal(false);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'IBKR 同步失败');
    } finally {
      setIbkrSyncing(false);
    }
  };

  const handleSyncIbkrSingle = async (strategyId: number) => {
    setIbkrSyncing(true);
    try {
      const res = await api.syncGridFromIbkr({ strategy_id: strategyId });
      if (res.detail) setDetail(res.detail);
      await loadList(strategyId);
      onNotify(
        res.imported_trades > 0
          ? `已从 IBKR 同步导入 ${res.imported_trades} 笔新成交（跳过重复 ${res.skipped_duplicates} 笔）`
          : `IBKR 成交已是最新（已核对 ${res.skipped_duplicates} 笔，无新增）`,
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '同步 IBKR 成交失败');
    } finally {
      setIbkrSyncing(false);
    }
  };

  const applyPreset = (p: (typeof FEE_PRESETS)[number]) => {
    setFormMarket(p.market);
    setFormFeeRate(String(p.fee_rate));
    setFormMinFee(String(p.min_fee));
    setFormTaxRate(String(p.stamp_tax_rate));
    setFormTaxSide(p.stamp_tax_side);
    setFormShares(String(p.shares));
  };

  // Live preview of grid levels & required capital inside the modal
  const modalPreview = useMemo(() => {
    const low = Number(formLower);
    const high = Number(formUpper);
    const step = Number(formStep);
    const shares = Number(formShares || 0);
    if (!(low > 0 && high > low && step > 0)) return null;
    const levels: number[] = [];
    if (formGridType === 'geometric') {
      const r = 1 + step / 100;
      let p = low;
      while (p <= high * 1.000001 && levels.length < 500) {
        levels.push(p);
        p *= r;
      }
    } else {
      let n = 0;
      while (levels.length < 500) {
        const p = low + step * n;
        if (p > high + 1e-9) break;
        levels.push(p);
        n += 1;
      }
    }
    const gridCount = Math.max(0, levels.length - 1);
    const fullCap = levels.slice(0, -1).reduce((acc, p) => acc + p * shares, 0);
    const mid = levels[Math.floor(levels.length / 2)] || low;
    const pct = formGridType === 'geometric' ? step : (step / mid) * 100;
    return { levelCount: levels.length, gridCount, fullCap, pct };
  }, [formLower, formUpper, formStep, formShares, formGridType]);

  const handleSaveStrategy = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formSymbol.trim()) {
      onNotify('请填写股票/ETF代码');
      return;
    }
    const lowNum = Number(formLower || 0);
    const highNum = Number(formUpper || 0);
    const stepNum = Number(formStep || 0);
    if (!Number.isFinite(lowNum) || !Number.isFinite(highNum) || !Number.isFinite(stepNum) || stepNum < 0) {
      onNotify('请输入有效的区间价格与网格步长');
      return;
    }
    if (highNum > 0 && lowNum > 0 && highNum <= lowNum) {
      onNotify('网格上沿价必须大于下沿价');
      return;
    }
    setSavingStrat(true);
    try {
      const payload: Partial<GridStrategy> = {
        name: formName.trim() || formSymbol.trim().toUpperCase(),
        symbol: formSymbol.trim().toUpperCase(),
        market: formMarket,
        grid_type: formGridType,
        lower_price: lowNum,
        upper_price: highNum,
        step_value: stepNum,
        shares_per_grid: Number(formShares || 100),
        fee_rate: Number(formFeeRate || 0),
        min_fee: Number(formMinFee || 0),
        stamp_tax_rate: Number(formTaxRate || 0),
        stamp_tax_side: formTaxSide,
        current_price: Number(formCurPrice || 0),
        ibkr_auto_sync: formIbkrAutoSync,
        broker: formBroker.trim(),
        notes: formNotes.trim(),
      };
      if (editingStrategy) {
        const updated = await api.updateGridStrategy(editingStrategy.id, payload);
        setDetail(updated);
        await loadList(editingStrategy.id);
        onNotify(`已更新「${updated.strategy.name}」网格参数`);
      } else {
        const created = await api.createGridStrategy(payload);
        setDetail(created);
        setSelectedId(created.strategy.id);
        await loadList(created.strategy.id);
        onNotify(`已创建「${created.strategy.name}」网格计划`);
      }
      setShowModal(false);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '保存网格计划失败');
    } finally {
      setSavingStrat(false);
    }
  };

  const handleDeleteStrategy = async (s: GridStrategy) => {
    if (!window.confirm(`确定删除网格「${s.name} (${s.symbol})」及其全部成交记录吗？`)) return;
    try {
      await api.deleteGridStrategy(s.id);
      onNotify(`已删除「${s.name}」`);
      await loadList(null);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '删除失败');
    }
  };

  const handleRefreshQuotes = async (singleId?: number) => {
    setRefreshingQuotes(true);
    try {
      const res = await api.refreshGridQuotes(singleId);
      if (res.errors?.length) {
        onNotify(`已更新 ${res.updated_count} 只报价；提示：${res.errors.slice(0, 2).join('，')}`);
      } else {
        onNotify(`已刷新 ${res.updated_count} 只标的实时行情`);
      }
      await loadList(selectedId);
      if (selectedId) await loadDetail(selectedId);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '刷新行情失败');
    } finally {
      setRefreshingQuotes(false);
    }
  };

  const handleSaveManualPrice = async () => {
    if (!detail) return;
    const p = Number(manualPrice);
    if (!(p > 0)) return;
    setSavingPrice(true);
    try {
      const updated = await api.updateGridStrategy(detail.strategy.id, { current_price: p });
      setDetail(updated);
      await loadList(detail.strategy.id);
      onNotify(`已更新 ${detail.strategy.symbol} 现价为 ${fmtPrice(p)}`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '更新现价失败');
    } finally {
      setSavingPrice(false);
    }
  };

  const prefillTrade = (side: 'buy' | 'sell', price: number, qty: number, level?: number) => {
    setTradeSide(side);
    setTradePrice(String(price));
    setTradeQty(String(qty));
    setTradeFee('');
    setTradeTax('');
    setTradeAt(nowLocalInput());
    setTradeLevel(level);
    setTradeNote(level !== undefined ? `第 ${level + 1} 格${side === 'buy' ? '买入' : '卖出'}` : '');
    setDetailTab('trades');
  };

  const handleSubmitTrade = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!detail) return;
    const price = Number(tradePrice);
    const qty = Number(tradeQty);
    if (!(price > 0 && qty > 0)) return;
    setSubmittingTrade(true);
    try {
      const res = await api.createGridTrade(detail.strategy.id, {
        side: tradeSide,
        price,
        quantity: qty,
        fee: tradeFee.trim() !== '' ? Number(tradeFee) : undefined,
        tax: tradeTax.trim() !== '' ? Number(tradeTax) : undefined,
        traded_at: tradeAt.replace('T', ' '),
        grid_level: tradeLevel,
        note: tradeNote.trim(),
      });
      setDetail(res.detail);
      setTradePrice('');
      setTradeFee('');
      setTradeTax('');
      setTradeNote('');
      setTradeLevel(undefined);
      await loadList(detail.strategy.id);
      onNotify(
        `已记入 ${tradeSide === 'buy' ? '买入' : '卖出'} ${qty} 股 @ ${fmtPrice(price)}（费用 ${formatCurrency(res.trade.fee + res.trade.tax, detail.strategy.currency)}）`,
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '记账失败');
    } finally {
      setSubmittingTrade(false);
    }
  };

  const handleQuickRecordGrid = async (side: 'buy' | 'sell', price: number, qty: number, level: number) => {
    if (!detail) return;
    const label = side === 'buy' ? '买入' : '卖出';
    if (!window.confirm(`确认按第 ${level + 1} 格价格 ${fmtPrice(price)} 一键记入「${label} ${qty} 股」吗？`)) return;
    try {
      const res = await api.createGridTrade(detail.strategy.id, {
        side,
        price,
        quantity: qty,
        grid_level: level,
        note: `第 ${level + 1} 格一键${label}`,
      });
      setDetail(res.detail);
      await loadList(detail.strategy.id);
      onNotify(`已完成第 ${level + 1} 格${label} ${qty} 股 @ ${fmtPrice(price)}`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '一键记账失败');
    }
  };

  const handleDeleteTrade = async (tradeId: number) => {
    if (!window.confirm('确认删除该笔成交记录？网格配对与持仓收益会自动重新计算。')) return;
    try {
      const res = await api.deleteGridTrade(tradeId);
      setDetail(res.detail);
      await loadList(res.strategy_id);
      onNotify('已删除成交记录并重算网格配对');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '删除失败');
    }
  };

  const portfolio = listData?.portfolio;
  const items: GridListItem[] = listData?.items || [];
  const curCcy = detail?.strategy.currency || 'CNY';

  // Auto-estimated fee preview in the quick trade form
  const estFees = useMemo(() => {
    if (!detail) return { fee: 0, tax: 0 };
    const amt = Number(tradePrice || 0) * Number(tradeQty || 0);
    if (!(amt > 0)) return { fee: 0, tax: 0 };
    const fee = Math.max(amt * detail.strategy.fee_rate, detail.strategy.min_fee);
    const tax =
      tradeSide === 'sell' || detail.strategy.stamp_tax_side === 'both'
        ? amt * detail.strategy.stamp_tax_rate
        : 0;
    return { fee: Number(fee.toFixed(2)), tax: Number(tax.toFixed(2)) };
  }, [detail, tradePrice, tradeQty, tradeSide]);

  return (
    <div className="space-y-6">
      {/* Portfolio KPI Strip */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <Layers className="h-3.5 w-3.5 text-indigo-600" /> 网格总持仓市值（折 CNY）
          </div>
          <div className="mt-2 text-2xl font-bold text-slate-900">{formatMoney(portfolio?.market_value_cny ?? 0)}</div>
          <div className="mt-1 text-xs text-slate-400">
            持仓成本 {formatMoney(portfolio?.holding_cost_cny ?? 0)} · {portfolio?.active_count ?? 0} 个运行中
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> 网格已实现利润（折 CNY）
          </div>
          <div
            className={`mt-2 text-2xl font-bold ${
              (portfolio?.realized_pnl_cny ?? 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'
            }`}
          >
            {formatMoney(portfolio?.realized_pnl_cny ?? 0, true)}
          </div>
          <div className="mt-1 text-xs text-slate-400">累计完成 {portfolio?.completed_grids ?? 0} 次网格配对卖出</div>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <TrendingUp className="h-3.5 w-3.5 text-sky-600" /> 当前持仓浮动盈亏（折 CNY）
          </div>
          <div
            className={`mt-2 text-2xl font-bold ${
              (portfolio?.unrealized_pnl_cny ?? 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'
            }`}
          >
            {formatMoney(portfolio?.unrealized_pnl_cny ?? 0, true)}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            总盈亏（已实现+浮盈）：{formatMoney(portfolio?.total_pnl_cny ?? 0, true)}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span className="flex items-center gap-1.5">
              <BellRing className="h-3.5 w-3.5 text-amber-500" /> 到价触发信号
            </span>
            <span className="font-bold text-slate-900">{portfolio?.triggered_count ?? 0} 只</span>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => handleRefreshQuotes()}
              disabled={refreshingQuotes || items.length === 0}
              className="flex-1 inline-flex items-center justify-center gap-1 rounded-xl border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-45 cursor-pointer"
            >
              <RefreshCw className={`h-3 w-3 ${refreshingQuotes ? 'animate-spin' : ''}`} />
              刷价
            </button>
            <button
              type="button"
              onClick={openIbkrModal}
              className="flex-1 inline-flex items-center justify-center gap-1 rounded-xl border border-sky-200 bg-sky-50 px-2 py-1.5 text-[11px] font-medium text-sky-700 hover:bg-sky-100 cursor-pointer"
            >
              <Download className="h-3 w-3" /> IBKR
            </button>
            <button
              type="button"
              onClick={openEmailModal}
              className="flex-1 inline-flex items-center justify-center gap-1 rounded-xl border border-rose-200 bg-rose-50 px-2 py-1.5 text-[11px] font-medium text-rose-700 hover:bg-rose-100 cursor-pointer"
            >
              <Mail className="h-3 w-3" /> 汇丰/邮件
            </button>
            <button
              type="button"
              onClick={openCreateModal}
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-xl bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 cursor-pointer"
            >
              <Plus className="h-3.5 w-3.5" /> 新建网格计划
            </button>
          </div>
        </div>
      </div>

      {/* Main Split Layout */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: Strategy List */}
        <div className="lg:col-span-4 space-y-3">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs space-y-3">
            <div className="flex items-center justify-between border-b border-slate-100 pb-2.5">
              <div className="flex items-center gap-2">
                <Grid3x3 className="h-4 w-4 text-indigo-600" />
                <h3 className="text-sm font-semibold text-slate-900">我的网格标的 ({items.length})</h3>
              </div>
              <button
                type="button"
                onClick={openCreateModal}
                className="text-xs font-medium text-indigo-600 hover:underline cursor-pointer"
              >
                + 添加标的
              </button>
            </div>

            {loading && items.length === 0 ? (
              <div className="py-10 text-center text-xs text-slate-400">加载中...</div>
            ) : items.length === 0 ? (
              <div className="rounded-xl bg-slate-50 py-10 px-4 text-center space-y-3">
                <p className="text-xs text-slate-500">
                  还没有网格标的。点击上方「新建网格」，支持 A股 ETF、A股正股、港股与美股的等差/等比网格记账与自动配对。
                </p>
                <button
                  type="button"
                  onClick={openCreateModal}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 cursor-pointer"
                >
                  <Plus className="h-3.5 w-3.5" /> 创建第一个股票网格
                </button>
              </div>
            ) : (
              <div className="space-y-2 max-h-[640px] overflow-y-auto pr-1">
                {items.map(({ strategy: s, summary: sm, signals: sig }) => {
                  const active = selectedId === s.id;
                  const mk = MARKET_META[s.market] || MARKET_META.CN;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => setSelectedId(s.id)}
                      className={`w-full text-left rounded-xl border p-3.5 transition cursor-pointer ${
                        active
                          ? 'border-indigo-400 bg-indigo-50/60 ring-1 ring-indigo-300'
                          : 'border-slate-100 bg-slate-50/50 hover:border-slate-300'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${mk.cls}`}>{mk.label}</span>
                            {s.broker && (
                              <span className="rounded bg-slate-200/80 px-1.5 py-0.5 text-[10px] font-medium text-slate-700 shrink-0">
                                {s.broker}
                              </span>
                            )}
                            <span className="font-semibold text-sm text-slate-900 truncate">{s.name}</span>
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-400 font-mono">
                            {s.symbol} · {s.grid_type === 'geometric' ? `等比 ${s.step_value}%` : `等差 ${s.step_value}`}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-sm font-bold text-slate-900 font-mono">
                            {s.current_price > 0 ? fmtPrice(s.current_price) : '未设现价'}
                          </div>
                          {sm.day_change_pct !== null && (
                            <div
                              className={`text-[11px] font-medium ${
                                sm.day_change_pct >= 0 ? 'text-rose-600' : 'text-emerald-600'
                              }`}
                            >
                              {sm.day_change_pct >= 0 ? '+' : ''}
                              {sm.day_change_pct}%
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="mt-2.5 flex items-center justify-between text-[11px]">
                        <span className="text-slate-500">
                          持仓 <b className="text-slate-800">{sm.holding_qty}</b> 股 · 完成 <b>{sm.completed_grids}</b> 格
                        </span>
                        <span className={`font-semibold ${sm.realized_pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                          已实现 {sm.realized_pnl >= 0 ? '+' : ''}
                          {formatCurrency(sm.realized_pnl, sm.currency)}
                        </span>
                      </div>

                      {(sig.buy_triggered || sig.sell_triggered) && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {sig.buy_triggered && sig.next_buy && (
                            <span className="rounded-md bg-rose-100 border border-rose-200 px-2 py-0.5 text-[10px] font-semibold text-rose-700">
                              触发买点 ≤ {fmtPrice(sig.next_buy.price)}
                            </span>
                          )}
                          {sig.sell_triggered && sig.next_sell && (
                            <span className="rounded-md bg-emerald-100 border border-emerald-200 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                              触发卖点 ≥ {fmtPrice(sig.next_sell.price)}
                            </span>
                          )}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Right: Selected Strategy Workbench */}
        <div className="lg:col-span-8 space-y-5">
          {!detail ? (
            <div className="rounded-2xl border border-slate-200/80 bg-white p-14 text-center text-xs text-slate-400">
              请在左侧选择一个网格标的，或点击「新建网格」开始记录你的股票网格交易。
            </div>
          ) : (
            <>
              {/* Strategy Header & Action Signals */}
              <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-md border px-2 py-0.5 text-[11px] font-medium ${
                          (MARKET_META[detail.strategy.market] || MARKET_META.CN).cls
                        }`}
                      >
                        {(MARKET_META[detail.strategy.market] || MARKET_META.CN).label}
                      </span>
                      <h2 className="text-lg font-bold text-slate-900">{detail.strategy.name}</h2>
                      <span className="text-xs font-mono text-slate-400">{detail.strategy.symbol}</span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      区间 {fmtPrice(detail.strategy.lower_price)} ~ {fmtPrice(detail.strategy.upper_price)} ·{' '}
                      {detail.strategy.grid_type === 'geometric'
                        ? `等比步长 ${detail.strategy.step_value}%`
                        : `等差步长 ${detail.strategy.step_value}`}{' '}
                      · 共 {detail.summary.grid_count} 格 · 每格 {detail.strategy.shares_per_grid} 股 · 满格资金约{' '}
                      {formatCurrency(detail.summary.full_grid_capital, curCcy)}
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-slate-50 px-2 py-1">
                      <span className="text-[11px] text-slate-500">现价</span>
                      <input
                        type="number"
                        step="0.0001"
                        value={manualPrice}
                        onChange={(e) => setManualPrice(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSaveManualPrice()}
                        className="w-20 bg-white rounded-lg border border-slate-200 px-2 py-0.5 text-xs font-mono"
                      />
                      <button
                        type="button"
                        onClick={handleSaveManualPrice}
                        disabled={savingPrice}
                        className="rounded-lg bg-slate-800 px-2 py-0.5 text-[11px] text-white hover:bg-slate-700 cursor-pointer"
                      >
                        设价
                      </button>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleSyncIbkrSingle(detail.strategy.id)}
                      disabled={ibkrSyncing}
                      title="从 IBKR Flex 同步该标的成交记录"
                      className="inline-flex items-center gap-1 rounded-xl border border-sky-200 bg-sky-50 px-2.5 py-1.5 text-xs font-medium text-sky-700 hover:bg-sky-100 disabled:opacity-50 cursor-pointer"
                    >
                      <Download className="h-3.5 w-3.5" /> 同步IBKR
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRefreshQuotes(detail.strategy.id)}
                      disabled={refreshingQuotes}
                      title="拉取实时行情"
                      className="rounded-xl border border-slate-200 bg-white p-2 text-slate-600 hover:bg-slate-50 cursor-pointer"
                    >
                      <RefreshCw className={`h-3.5 w-3.5 ${refreshingQuotes ? 'animate-spin' : ''}`} />
                    </button>
                    <button
                      type="button"
                      onClick={() => openEditModal(detail.strategy)}
                      title="编辑网格参数"
                      className="rounded-xl border border-slate-200 bg-white p-2 text-slate-600 hover:bg-slate-50 cursor-pointer"
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDeleteStrategy(detail.strategy)}
                      title="删除网格"
                      className="rounded-xl border border-slate-200 bg-white p-2 text-slate-400 hover:text-rose-600 hover:border-rose-200 cursor-pointer"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>

                {/* Key Metrics Row */}
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div className="rounded-xl bg-slate-50 p-3">
                    <div className="text-[11px] text-slate-400">当前持仓 / 均价</div>
                    <div className="mt-1 text-sm font-bold text-slate-900">
                      {detail.summary.holding_qty} 股{' '}
                      <span className="text-xs font-normal text-slate-500">
                        (@ {detail.summary.avg_cost > 0 ? fmtPrice(detail.summary.avg_cost) : '-'})
                      </span>
                    </div>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3">
                    <div className="text-[11px] text-slate-400">网格已实现净利</div>
                    <div
                      className={`mt-1 text-sm font-bold ${
                        detail.summary.realized_pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'
                      }`}
                    >
                      {detail.summary.realized_pnl >= 0 ? '+' : ''}
                      {formatCurrency(detail.summary.realized_pnl, curCcy)}
                      <span className="ml-1 text-[11px] font-normal text-slate-400">
                        ({detail.summary.completed_grids} 格)
                      </span>
                    </div>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3">
                    <div className="text-[11px] text-slate-400">持仓浮盈 / 浮盈率</div>
                    <div
                      className={`mt-1 text-sm font-bold ${
                        detail.summary.unrealized_pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'
                      }`}
                    >
                      {detail.summary.unrealized_pnl >= 0 ? '+' : ''}
                      {formatCurrency(detail.summary.unrealized_pnl, curCcy)}{' '}
                      <span className="text-[11px] font-normal">({detail.summary.unrealized_pct}%)</span>
                    </div>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3">
                    <div className="text-[11px] text-slate-400">峰值资金 / 累计费税</div>
                    <div className="mt-1 text-sm font-bold text-slate-900">
                      {formatCurrency(detail.summary.peak_capital, curCcy)}
                      <span className="ml-1 text-[11px] font-normal text-slate-400">
                        (费 {formatCurrency(detail.summary.total_fees, curCcy)})
                      </span>
                    </div>
                  </div>
                </div>

                {/* Next Buy / Next Sell Signal Strip */}
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div
                    className={`rounded-xl border p-3.5 flex items-center justify-between gap-2 ${
                      detail.signals.buy_triggered
                        ? 'border-rose-300 bg-rose-50/80'
                        : 'border-slate-200/80 bg-slate-50/60'
                    }`}
                  >
                    <div>
                      <div className="text-[11px] font-medium text-rose-700 flex items-center gap-1">
                        <ArrowDownCircle className="h-3.5 w-3.5" />
                        下一买入触发价 {detail.signals.buy_triggered ? '（已到价！）' : ''}
                      </div>
                      <div className="mt-1 text-base font-bold font-mono text-slate-900">
                        {detail.signals.next_buy
                          ? `${fmtPrice(detail.signals.next_buy.price)} × ${detail.signals.next_buy.quantity} 股`
                          : '已达网格最底格 / 未设网格'}
                      </div>
                    </div>
                    {detail.signals.next_buy && (
                      <button
                        type="button"
                        onClick={() =>
                          prefillTrade(
                            'buy',
                            detail.signals.next_buy!.price,
                            detail.signals.next_buy!.quantity,
                            detail.signals.next_buy!.level,
                          )
                        }
                        className="rounded-xl bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 cursor-pointer shrink-0"
                      >
                        记一笔买入
                      </button>
                    )}
                  </div>

                  <div
                    className={`rounded-xl border p-3.5 flex items-center justify-between gap-2 ${
                      detail.signals.sell_triggered
                        ? 'border-emerald-300 bg-emerald-50/80'
                        : 'border-slate-200/80 bg-slate-50/60'
                    }`}
                  >
                    <div>
                      <div className="text-[11px] font-medium text-emerald-700 flex items-center gap-1">
                        <ArrowUpCircle className="h-3.5 w-3.5" />
                        下一卖出触发价 {detail.signals.sell_triggered ? '（已到价！）' : ''}
                      </div>
                      <div className="mt-1 text-base font-bold font-mono text-slate-900">
                        {detail.signals.next_sell
                          ? `${fmtPrice(detail.signals.next_sell.price)} × ${detail.signals.next_sell.quantity} 股`
                          : '暂无待卖持仓格'}
                      </div>
                    </div>
                    {detail.signals.next_sell && (
                      <button
                        type="button"
                        onClick={() =>
                          prefillTrade(
                            'sell',
                            detail.signals.next_sell!.price,
                            detail.signals.next_sell!.quantity,
                            detail.signals.next_sell!.level,
                          )
                        }
                        className="rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 cursor-pointer shrink-0"
                      >
                        记一笔卖出
                      </button>
                    )}
                  </div>
                </div>

                {detail.signals.hints.length > 0 && (
                  <div className="rounded-xl bg-amber-50/80 border border-amber-200/80 px-3.5 py-2 text-xs text-amber-800 space-y-0.5">
                    {detail.signals.hints.map((h, i) => (
                      <div key={i}>• {h}</div>
                    ))}
                  </div>
                )}
              </div>

              {/* Detail Sub-tabs */}
              <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
                  <div className="flex flex-wrap gap-1.5 rounded-xl bg-slate-100 p-1 text-xs">
                    {[
                      { key: 'ladder', label: `网格阶梯 (${detail.levels.length})` },
                      { key: 'trades', label: `记账 & 成交明细 (${detail.trades.length})` },
                      { key: 'pairs', label: `配对收益 (${detail.pairs.length})` },
                      { key: 'lots', label: `持仓批次 (${detail.open_lots.length})` },
                    ].map((t) => (
                      <button
                        key={t.key}
                        type="button"
                        onClick={() => setDetailTab(t.key as typeof detailTab)}
                        className={`rounded-lg px-3 py-1.5 font-medium transition cursor-pointer ${
                          detailTab === t.key ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-600 hover:text-slate-900'
                        }`}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setTradePrice(detail.strategy.current_price > 0 ? String(detail.strategy.current_price) : '');
                      setTradeQty(String(detail.strategy.shares_per_grid || 100));
                      setTradeAt(nowLocalInput());
                      setTradeLevel(undefined);
                      setDetailTab('trades');
                    }}
                    className="inline-flex items-center gap-1 rounded-xl bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 cursor-pointer"
                  >
                    <Plus className="h-3.5 w-3.5" /> 记一笔成交
                  </button>
                </div>

                {/* Tab 1: Grid Ladder */}
                {detailTab === 'ladder' && (
                  <div className="space-y-1.5 max-h-[500px] overflow-y-auto pr-1">
                    {detail.levels.length === 0 ? (
                      <div className="rounded-xl bg-slate-50 py-10 text-center text-xs text-slate-400">
                        尚未设置网格下沿、上沿和步长。点击右上角编辑按钮配置后即可自动生成网格价格阶梯。
                      </div>
                    ) : (
                      [...detail.levels].reverse().map((lv) => {
                        const hasHold = lv.holding_qty > 0;
                        const isNextBuy = detail.signals.next_buy?.level === lv.index;
                        const isNextSell = detail.signals.next_sell?.level === lv.index;
                        return (
                          <div
                            key={lv.index}
                            className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border px-3.5 py-2.5 text-xs ${
                              lv.is_current
                                ? 'border-indigo-300 bg-indigo-50/70'
                                : hasHold
                                  ? 'border-emerald-200 bg-emerald-50/40'
                                  : 'border-slate-100 bg-slate-50/40'
                            }`}
                          >
                            <div className="flex flex-wrap items-center gap-2.5">
                              <span className="w-10 text-[11px] font-mono text-slate-400">G{lv.index + 1}</span>
                              <span className="font-bold font-mono text-sm text-slate-900 w-20">
                                {fmtPrice(lv.price)}
                              </span>
                              {lv.is_current && (
                                <span className="rounded bg-indigo-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
                                  现价附近
                                </span>
                              )}
                              {isNextBuy && (
                                <span className="rounded bg-rose-100 border border-rose-200 px-1.5 py-0.5 text-[10px] font-medium text-rose-700">
                                  下买点
                                </span>
                              )}
                              {isNextSell && (
                                <span className="rounded bg-emerald-100 border border-emerald-200 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                                  下卖点
                                </span>
                              )}
                              {hasHold ? (
                                <span className="rounded-md bg-white border border-emerald-200 px-2 py-0.5 text-[11px] font-medium text-emerald-800">
                                  持有 {lv.holding_qty} 股
                                  {lv.sell_target ? ` → 卖目标 ${fmtPrice(lv.sell_target)}` : ''}
                                </span>
                              ) : (
                                <span className="text-[11px] text-slate-400">空仓</span>
                              )}
                              {lv.distance_pct !== null && (
                                <span className="text-[11px] text-slate-400 font-mono">
                                  ({lv.distance_pct >= 0 ? '+' : ''}
                                  {lv.distance_pct}%)
                                </span>
                              )}
                            </div>

                            <div className="flex items-center gap-1.5">
                              <button
                                type="button"
                                onClick={() =>
                                  handleQuickRecordGrid('buy', lv.price, detail.strategy.shares_per_grid || 100, lv.index)
                                }
                                className="rounded-lg border border-rose-200 bg-white px-2.5 py-1 text-[11px] font-medium text-rose-600 hover:bg-rose-50 cursor-pointer"
                              >
                                买一格
                              </button>
                              {hasHold && lv.sell_target && (
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleQuickRecordGrid('sell', lv.sell_target!, lv.holding_qty, lv.index + 1)
                                  }
                                  className="rounded-lg border border-emerald-200 bg-white px-2.5 py-1 text-[11px] font-medium text-emerald-700 hover:bg-emerald-50 cursor-pointer"
                                >
                                  按 {fmtPrice(lv.sell_target)} 卖
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                )}

                {/* Tab 2: Quick Trade Entry + Trades Table */}
                {detailTab === 'trades' && (
                  <div className="space-y-4">
                    <form
                      onSubmit={handleSubmitTrade}
                      className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-4 space-y-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="inline-flex rounded-xl bg-white border border-slate-200 p-0.5 text-xs">
                          <button
                            type="button"
                            onClick={() => setTradeSide('buy')}
                            className={`rounded-lg px-4 py-1.5 font-medium cursor-pointer ${
                              tradeSide === 'buy' ? 'bg-rose-600 text-white' : 'text-slate-600'
                            }`}
                          >
                            买入（加一格）
                          </button>
                          <button
                            type="button"
                            onClick={() => setTradeSide('sell')}
                            className={`rounded-lg px-4 py-1.5 font-medium cursor-pointer ${
                              tradeSide === 'sell' ? 'bg-emerald-600 text-white' : 'text-slate-600'
                            }`}
                          >
                            卖出（收一格）
                          </button>
                        </div>
                        <span className="text-[11px] text-slate-500">
                          预估佣金 {formatCurrency(estFees.fee, curCcy)} · 印花税 {formatCurrency(estFees.tax, curCcy)}（留空则自动按费率计算）
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-6">
                        <input
                          type="number"
                          step="0.0001"
                          required
                          value={tradePrice}
                          onChange={(e) => setTradePrice(e.target.value)}
                          placeholder="成交价"
                          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono"
                        />
                        <input
                          type="number"
                          step="0.0001"
                          required
                          value={tradeQty}
                          onChange={(e) => setTradeQty(e.target.value)}
                          placeholder="数量（股）"
                          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono"
                        />
                        <input
                          type="number"
                          step="0.01"
                          value={tradeFee}
                          onChange={(e) => setTradeFee(e.target.value)}
                          placeholder={`佣金(默认${estFees.fee})`}
                          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono"
                        />
                        <input
                          type="number"
                          step="0.01"
                          value={tradeTax}
                          onChange={(e) => setTradeTax(e.target.value)}
                          placeholder={`税费(默认${estFees.tax})`}
                          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono"
                        />
                        <input
                          type="datetime-local"
                          value={tradeAt}
                          onChange={(e) => setTradeAt(e.target.value)}
                          className="rounded-xl border border-slate-200 bg-white px-2.5 py-2 text-xs sm:col-span-2"
                        />
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <input
                          type="text"
                          value={tradeNote}
                          onChange={(e) => setTradeNote(e.target.value)}
                          placeholder="备注（可选，如：跌破3.90补仓一格）"
                          className="flex-1 min-w-[200px] rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
                        />
                        <button
                          type="submit"
                          disabled={submittingTrade}
                          className={`rounded-xl px-5 py-2 text-xs font-semibold text-white cursor-pointer disabled:opacity-50 ${
                            tradeSide === 'buy'
                              ? 'bg-rose-600 hover:bg-rose-700'
                              : 'bg-emerald-600 hover:bg-emerald-700'
                          }`}
                        >
                          {submittingTrade ? '提交中...' : `确认记入${tradeSide === 'buy' ? '买入' : '卖出'}`}
                        </button>
                      </div>
                    </form>

                    <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                      {detail.trades.length === 0 ? (
                        <div className="py-10 text-center text-xs text-slate-400">暂无成交记录，在上方记入第一笔买入吧</div>
                      ) : (
                        detail.trades.map((t) => (
                          <div
                            key={t.id}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-100 bg-slate-50/50 px-3.5 py-2.5 text-xs"
                          >
                            <div className="flex flex-wrap items-center gap-2 min-w-0">
                              <span
                                className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${
                                  t.side === 'buy'
                                    ? 'bg-rose-100 text-rose-700'
                                    : 'bg-emerald-100 text-emerald-700'
                                }`}
                              >
                                {t.side === 'buy' ? '买入' : '卖出'}
                              </span>
                              {t.source_type === 'ibkr' && (
                                <span className="rounded bg-sky-50 border border-sky-200 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700">
                                  IBKR
                                </span>
                              )}
                              {t.source_type === 'email' && (
                                <span className="rounded bg-rose-50 border border-rose-200 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700">
                                  邮件
                                </span>
                              )}
                              <span className="font-mono font-bold text-slate-900">
                                {fmtPrice(t.price)} × {t.quantity} 股
                              </span>
                              <span className="text-slate-500">
                                额 {formatCurrency(t.amount, curCcy)} · 费 {formatCurrency(t.fee + t.tax, curCcy)}
                              </span>
                              {t.grid_level !== null && t.grid_level !== undefined && (
                                <span className="rounded bg-slate-200/80 px-1.5 py-0.5 text-[10px] font-mono text-slate-600">
                                  G{t.grid_level + 1}
                                </span>
                              )}
                              {t.side === 'buy' && t.pair_status && (
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                    t.pair_status === 'closed'
                                      ? 'bg-slate-200 text-slate-600'
                                      : 'bg-amber-50 text-amber-700 border border-amber-200'
                                  }`}
                                >
                                  {t.pair_status === 'closed'
                                    ? '已配对卖出'
                                    : `待卖 ${t.remaining_qty} 股${t.sell_target ? ` → ${fmtPrice(t.sell_target)}` : ''}`}
                                </span>
                              )}
                              {t.side === 'sell' && t.realized_profit !== undefined && (
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                                    t.realized_profit >= 0
                                      ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                      : 'bg-rose-50 text-rose-700 border border-rose-200'
                                  }`}
                                >
                                  净盈亏 {t.realized_profit >= 0 ? '+' : ''}
                                  {formatCurrency(t.realized_profit, curCcy)}
                                </span>
                              )}
                              {t.note && <span className="text-slate-400 truncate max-w-[180px]">{t.note}</span>}
                            </div>

                            <div className="flex items-center gap-3 shrink-0">
                              <span className="text-[11px] text-slate-400 font-mono">{t.traded_at}</span>
                              <button
                                type="button"
                                onClick={() => handleDeleteTrade(t.id)}
                                className="text-slate-300 hover:text-rose-500 cursor-pointer"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}

                {/* Tab 3: Matched Grid Pairs */}
                {detailTab === 'pairs' && (
                  <div className="space-y-2 max-h-[480px] overflow-y-auto pr-1">
                    {detail.pairs.length === 0 ? (
                      <div className="rounded-xl bg-slate-50 py-10 text-center text-xs text-slate-400">
                        暂无已完成的网格配对。每记录一笔卖出，系统会自动匹配下方最近的一格买入并核算扣费后净利润。
                      </div>
                    ) : (
                      detail.pairs.map((p, idx) => (
                        <div
                          key={`${p.buy_trade_id}-${p.sell_trade_id}-${idx}`}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-100 bg-emerald-50/30 px-4 py-3 text-xs"
                        >
                          <div className="space-y-1">
                            <div className="flex flex-wrap items-center gap-2">
                              {p.buy_level !== null && (
                                <span className="rounded bg-indigo-50 border border-indigo-200 px-1.5 py-0.5 text-[10px] font-mono text-indigo-700">
                                  G{p.buy_level + 1}
                                </span>
                              )}
                              <span className="font-mono font-semibold text-slate-900">
                                买 {fmtPrice(p.buy_price)} → 卖 {fmtPrice(p.sell_price)}
                              </span>
                              <span className="text-slate-500">{p.quantity} 股</span>
                              {p.holding_days !== null && (
                                <span className="text-[11px] text-slate-400">持有 {p.holding_days} 天</span>
                              )}
                            </div>
                            <div className="text-[11px] text-slate-400 font-mono">
                              {p.buy_at} → {p.sell_at}
                            </div>
                          </div>
                          <div className="text-right">
                            <div
                              className={`text-sm font-bold ${
                                p.profit >= 0 ? 'text-emerald-600' : 'text-rose-600'
                              }`}
                            >
                              {p.profit >= 0 ? '+' : ''}
                              {formatCurrency(p.profit, curCcy)}
                            </div>
                            {p.profit_pct !== null && (
                              <div className="text-[11px] text-slate-400">
                                {p.profit_pct >= 0 ? '+' : ''}
                                {p.profit_pct}%
                              </div>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {/* Tab 4: Open Lots */}
                {detailTab === 'lots' && (
                  <div className="space-y-2 max-h-[480px] overflow-y-auto pr-1">
                    {detail.open_lots.length === 0 ? (
                      <div className="rounded-xl bg-slate-50 py-10 text-center text-xs text-slate-400">
                        当前空仓，没有未平仓的网格买入批次。
                      </div>
                    ) : (
                      detail.open_lots.map((lot) => {
                        const cur = detail.strategy.current_price;
                        const uPnl = cur > 0 ? (cur - lot.cost_ps) * lot.qty : 0;
                        const sellTarget =
                          lot.level !== null && lot.level + 1 < detail.levels.length
                            ? detail.levels[lot.level + 1].price
                            : null;
                        return (
                          <div
                            key={lot.trade_id}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-3 text-xs"
                          >
                            <div>
                              <div className="flex flex-wrap items-center gap-2">
                                {lot.level !== null && (
                                  <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-mono text-slate-700">
                                    G{lot.level + 1}
                                  </span>
                                )}
                                <span className="font-mono font-semibold text-slate-900">
                                  买价 {fmtPrice(lot.price)}（含费成本 {fmtPrice(lot.cost_ps)}）
                                </span>
                                <span className="text-slate-600">剩余 {lot.qty} 股</span>
                              </div>
                              <div className="mt-0.5 text-[11px] text-slate-400 font-mono">
                                买入时间：{lot.traded_at}
                                {sellTarget ? ` · 目标卖价：${fmtPrice(sellTarget)}` : ''}
                              </div>
                            </div>
                            <div className="flex items-center gap-3">
                              {cur > 0 && (
                                <span
                                  className={`font-semibold ${
                                    uPnl >= 0 ? 'text-emerald-600' : 'text-rose-600'
                                  }`}
                                >
                                  {uPnl >= 0 ? '+' : ''}
                                  {formatCurrency(uPnl, curCcy)}
                                </span>
                              )}
                              <button
                                type="button"
                                onClick={() =>
                                  prefillTrade(
                                    'sell',
                                    sellTarget || cur || lot.price,
                                    lot.qty,
                                    lot.level !== null ? lot.level + 1 : undefined,
                                  )
                                }
                                className="rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-emerald-700 cursor-pointer"
                              >
                                去卖出
                              </button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Create / Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[1px]">
          <form
            noValidate
            onSubmit={handleSaveStrategy}
            className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-xl space-y-4 max-h-[90vh] overflow-y-auto"
          >
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-base font-bold text-slate-900">
                {editingStrategy ? `编辑网格参数：${editingStrategy.name}` : '新建股票网格交易计划'}
              </h3>
              <button
                type="button"
                onClick={() => setShowModal(false)}
                className="text-slate-400 hover:text-slate-700 cursor-pointer"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Fee Presets */}
            <div className="space-y-1.5">
              <div className="text-[11px] font-medium text-slate-500">一键套用市场费率模板</div>
              <div className="flex flex-wrap gap-1.5">
                {FEE_PRESETS.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => applyPreset(p)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-700 hover:border-indigo-300 hover:bg-indigo-50 cursor-pointer"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <label className="text-[11px] text-slate-500">股票/ETF 代码 *</label>
                <input
                  type="text"
                  required
                  value={formSymbol}
                  onChange={(e) => setFormSymbol(e.target.value)}
                  placeholder="如 510300 / 00700 / AAPL"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">名称（留空自动填）</label>
                <input
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="如 沪深300ETF"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">市场</label>
                <select
                  value={formMarket}
                  onChange={(e) => setFormMarket(e.target.value as 'CN' | 'HK' | 'US')}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
                >
                  <option value="CN">A股 / 场内ETF (CNY)</option>
                  <option value="HK">港股 (HKD)</option>
                  <option value="US">美股 (USD)</option>
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div>
                <label className="text-[11px] text-slate-500">网格类型</label>
                <select
                  value={formGridType}
                  onChange={(e) => setFormGridType(e.target.value as 'arithmetic' | 'geometric')}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
                >
                  <option value="arithmetic">等差网格（固定价差）</option>
                  <option value="geometric">等比网格（固定百分比%）</option>
                </select>
              </div>
              <div>
                <label className="text-[11px] text-slate-500">区间下沿价</label>
                <input
                  type="number"
                  step="any"
                  value={formLower}
                  onChange={(e) => setFormLower(e.target.value)}
                  placeholder="如 3.50"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">区间上沿价</label>
                <input
                  type="number"
                  step="any"
                  value={formUpper}
                  onChange={(e) => setFormUpper(e.target.value)}
                  placeholder="如 4.50"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">
                  {formGridType === 'geometric' ? '每格涨幅 (%)' : '每格价差'}
                </label>
                <input
                  type="number"
                  step="any"
                  value={formStep}
                  onChange={(e) => setFormStep(e.target.value)}
                  placeholder={formGridType === 'geometric' ? '如 3 (即3%)' : '如 0.10'}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div>
                <label className="text-[11px] text-slate-500">每格股数</label>
                <input
                  type="number"
                  step="any"
                  value={formShares}
                  onChange={(e) => setFormShares(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">佣金率（如 0.0001=万1）</label>
                <input
                  type="number"
                  step="any"
                  value={formFeeRate}
                  onChange={(e) => setFormFeeRate(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">最低佣金</label>
                <input
                  type="number"
                  step="any"
                  value={formMinFee}
                  onChange={(e) => setFormMinFee(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">印花税率（0.0005=万5）</label>
                <input
                  type="number"
                  step="any"
                  value={formTaxRate}
                  onChange={(e) => setFormTaxRate(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <label className="text-[11px] text-slate-500">印花税方向</label>
                <select
                  value={formTaxSide}
                  onChange={(e) => setFormTaxSide(e.target.value as 'sell' | 'both')}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
                >
                  <option value="sell">仅卖出收取（A股）</option>
                  <option value="both">买卖双边收取（港股）</option>
                </select>
              </div>
              <div>
                <label className="text-[11px] text-slate-500">当前参考价（可选）</label>
                <input
                  type="number"
                  step="any"
                  value={formCurPrice}
                  onChange={(e) => setFormCurPrice(e.target.value)}
                  placeholder="留空可稍后一键刷新"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">券商标签（如 汇丰/IBKR）</label>
                <input
                  type="text"
                  value={formBroker}
                  onChange={(e) => setFormBroker(e.target.value)}
                  placeholder="如 汇丰 (HSBC) / IBKR"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-500">备注</label>
                <input
                  type="text"
                  value={formNotes}
                  onChange={(e) => setFormNotes(e.target.value)}
                  placeholder="策略思路或账户备注"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
                />
              </div>
            </div>

            <label className="inline-flex items-center gap-2 text-xs text-slate-600 cursor-pointer">
              <input
                type="checkbox"
                checked={formIbkrAutoSync}
                onChange={(e) => setFormIbkrAutoSync(e.target.checked)}
              />
              <span>每次 IBKR Flex 同步时，自动将同代码的新成交记录写入该网格（自动去重）</span>
            </label>

            {modalPreview ? (
              <div className="rounded-xl bg-indigo-50/70 border border-indigo-200 p-3 text-xs text-indigo-900 flex flex-wrap gap-x-5 gap-y-1">
                <span>
                  网格档位：<b>{modalPreview.levelCount}</b> 档（<b>{modalPreview.gridCount}</b> 格）
                </span>
                <span>
                  单格理论涨幅：<b>~{modalPreview.pct.toFixed(2)}%</b>
                </span>
                <span>
                  满格最大需资金：<b>{formatCurrency(modalPreview.fullCap, (MARKET_META[formMarket] || MARKET_META.CN).currency)}</b>
                </span>
              </div>
            ) : Number(formStep || 0) > 0 && (Number(formLower || 0) <= 0 || Number(formUpper || 0) <= Number(formLower || 0)) ? (
              <div className="rounded-xl bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
                请填写有效的区间下沿价与上沿价（上沿价需大于下沿价），网格步长才能生成对应买卖档位。
              </div>
            ) : null}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowModal(false)}
                className="rounded-xl border border-slate-200 px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 cursor-pointer"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={savingStrat}
                className="rounded-xl bg-slate-900 px-5 py-2 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50 cursor-pointer"
              >
                {savingStrat ? '保存中...' : editingStrategy ? '保存修改' : '创建网格计划'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* IBKR Flex Import & Sync Modal */}
      {showIbkrModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[1px]">
          <div className="w-full max-w-2xl rounded-2xl bg-white p-6 shadow-xl space-y-4 max-h-[88vh] flex flex-col">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="text-base font-bold text-slate-900">从 IBKR 盈透同步标的与成交到网格账本</h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  直接复用「IBKR 每日汇总」拉取的真实成交（精准成交价、股数、佣金）与持仓市价，按交易号自动去重
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowIbkrModal(false)}
                className="text-slate-400 hover:text-slate-700 cursor-pointer"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-sky-50/70 border border-sky-200/80 px-3.5 py-2.5 text-xs">
              <label className="inline-flex items-center gap-2 text-slate-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={ibkrRefreshFlexFirst}
                  onChange={(e) => setIbkrRefreshFlexFirst(e.target.checked)}
                  disabled={!ibkrConfigured}
                />
                <span>同步前先向 IBKR Flex 拉取最新报表{!ibkrConfigured ? '（未配置 Flex Token）' : ''}</span>
              </label>
              <div className="flex items-center gap-1.5">
                <span className="text-slate-500">新建网格默认等比步长</span>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  max="20"
                  value={ibkrDefaultStepPct}
                  onChange={(e) => setIbkrDefaultStepPct(e.target.value)}
                  className="w-16 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-mono"
                />
                <span className="text-slate-500">%</span>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto space-y-2 pr-1">
              {ibkrLoading ? (
                <div className="py-10 text-center text-xs text-slate-400">正在读取 IBKR 持仓与成交标的...</div>
              ) : ibkrCandidates.length === 0 ? (
                <div className="rounded-xl bg-slate-50 py-10 px-4 text-center text-xs text-slate-500 space-y-2">
                  <div>本地 IBKR 数据库里还没有股票/ETF 持仓或成交记录。</div>
                  <div>请先在「IBKR 每日汇总」完成一次同步，或勾选上方「先向 IBKR Flex 拉取最新报表」后直接点同步。</div>
                </div>
              ) : (
                ibkrCandidates.map((c) => {
                  const isLinked = c.linked_strategy_id !== null;
                  const checked = Boolean(ibkrSelectedSyms[c.symbol]);
                  const mk = MARKET_META[c.market] || MARKET_META.US;
                  return (
                    <div
                      key={`${c.market}-${c.symbol}`}
                      className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border px-3.5 py-2.5 text-xs ${
                        isLinked
                          ? 'border-emerald-200 bg-emerald-50/30'
                          : checked
                            ? 'border-sky-300 bg-sky-50/40'
                            : 'border-slate-100 bg-slate-50/50'
                      }`}
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        {isLinked ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
                        ) : (
                          <button
                            type="button"
                            onClick={() => setIbkrSelectedSyms((p) => ({ ...p, [c.symbol]: !p[c.symbol] }))}
                            className="text-sky-600 cursor-pointer shrink-0"
                          >
                            {checked ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4 text-slate-400" />}
                          </button>
                        )}
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${mk.cls}`}>
                              {mk.label}
                            </span>
                            <span className="font-mono font-bold text-slate-900">{c.symbol}</span>
                            {c.description && (
                              <span className="text-slate-600 truncate max-w-[200px]">{c.description}</span>
                            )}
                            {isLinked ? (
                              <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                                已关联：{c.linked_strategy_name}
                              </span>
                            ) : (
                              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                                勾选自动建网格
                              </span>
                            )}
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-400">
                            持仓 {c.position_qty} 股 · 市价 {c.mark_price > 0 ? fmtPrice(c.mark_price) : '-'} {c.currency} · IBKR 总成交 {c.trade_count} 笔
                          </div>
                        </div>
                      </div>

                      <div className="text-right shrink-0">
                        {c.unimported_trade_count > 0 ? (
                          <span className="rounded-md bg-amber-50 border border-amber-200 px-2 py-0.5 text-[11px] font-semibold text-amber-700">
                            待导入 {c.unimported_trade_count} 笔
                          </span>
                        ) : (
                          <span className="text-[11px] text-slate-400">成交已同步</span>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 pt-3">
              <button
                type="button"
                onClick={() => setShowIbkrModal(false)}
                className="rounded-xl border border-slate-200 px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                disabled={ibkrSyncing}
                onClick={() => handleSyncIbkrAll(false)}
                className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-2 text-xs font-medium text-sky-700 hover:bg-sky-100 disabled:opacity-50 cursor-pointer"
              >
                {ibkrSyncing ? '同步中...' : '仅同步已有网格新成交'}
              </button>
              <button
                type="button"
                disabled={ibkrSyncing}
                onClick={() => handleSyncIbkrAll(true)}
                className="rounded-xl bg-slate-900 px-4 py-2 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50 cursor-pointer"
              >
                {ibkrSyncing ? '导入中...' : '勾选标的建网格并导入全部成交'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* HSBC / Broker Email & PDF Contract Note Import Modal */}
      {showEmailModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[1px]">
          <div className="w-full max-w-3xl rounded-2xl bg-white p-6 shadow-xl space-y-4 max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="text-base font-bold text-slate-900">从汇丰 / 券商邮件与 PDF 成交单导入股票交易</h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  自动扫描已接入邮箱中的汇丰 (HSBC) 股票成交确认邮件或 PDF 成交单据（支持简体/繁体/英文），自动提取价格、股数、佣金与印花税并按单去重
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowEmailModal(false)}
                className="text-slate-400 hover:text-slate-700 cursor-pointer"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Mailbox scan bar */}
            <div className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 space-y-2.5">
              {emailCacheStats && (
                <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-600 bg-white/80 rounded-lg border border-slate-200/70 px-2.5 py-1.5">
                  <span>
                    本地邮件库已缓存 <strong>{emailCacheStats.total_cached}</strong> 封（含{' '}
                    <strong>{emailCacheStats.stock_hint_count}</strong> 封券商线索）
                    {emailCacheStats.auto_sync_enabled ? ` · 定时增量每 ${emailCacheStats.sync_interval_min} 分钟` : ' · 未开定时拉取'}
                  </span>
                  {emailCacheStats.last_sync_at && (
                    <span className="text-slate-400">
                      上次同步：{emailCacheStats.last_sync_at.slice(0, 16).replace('T', ' ')}
                    </span>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
                <select
                  value={emailMailboxId}
                  onChange={(e) => setEmailMailboxId(e.target.value)}
                  className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs"
                >
                  <option value="">全部已启用邮箱 ({emailMailboxes.length})</option>
                  {emailMailboxes.map((m) => (
                    <option key={m.id} value={String(m.id)}>
                      {m.name || m.username}
                    </option>
                  ))}
                </select>
                <select
                  value={emailDays}
                  onChange={(e) => setEmailDays(Number(e.target.value))}
                  className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs"
                >
                  <option value={7}>最近 7 天</option>
                  <option value={14}>最近 14 天</option>
                  <option value={30}>最近 30 天</option>
                  <option value={90}>最近 90 天</option>
                  <option value={180}>最近 180 天</option>
                  <option value={365}>最近 1 年</option>
                  <option value={730}>最近 2 年</option>
                </select>
                <input
                  type="text"
                  value={emailKeyword}
                  onChange={(e) => setEmailKeyword(e.target.value)}
                  placeholder="关键词（可留空，如 HSBC/汇丰）"
                  className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs"
                />
                <input
                  type="password"
                  value={emailPdfPwd}
                  onChange={(e) => setEmailPdfPwd(e.target.value)}
                  placeholder="PDF 加密密码（可选）"
                  className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs"
                />
                <button
                  type="button"
                  disabled={emailScanning}
                  onClick={() => handleScanEmailTrades('cache')}
                  className="inline-flex items-center justify-center gap-1 rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50 cursor-pointer"
                >
                  本地库秒扫
                </button>
                <button
                  type="button"
                  disabled={emailScanning}
                  onClick={() => handleScanEmailTrades('auto')}
                  className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50 cursor-pointer"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${emailScanning ? 'animate-spin' : ''}`} />
                  {emailScanning ? '扫描中...' : '增量扫描'}
                </button>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-slate-200/60">
                <label className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 cursor-pointer">
                  <Upload className="h-3.5 w-3.5 text-indigo-600" />
                  <span>直接上传汇丰成交单据 (.pdf)</span>
                  <input type="file" accept=".pdf" onChange={handleUploadTradePdf} className="hidden" />
                </label>
                {emailScannedCount !== null && (
                  <span className="text-[11px] text-slate-500">本次扫描 {emailScannedCount} 封邮件</span>
                )}
              </div>
            </div>

            {/* Quick paste box for HSBC confirmation emails */}
            <details className="rounded-xl border border-slate-200/80 bg-slate-50/40 px-3.5 py-2 text-xs">
              <summary className="cursor-pointer font-medium text-slate-700">
                或直接粘贴汇丰成交确认邮件正文测试/导入
              </summary>
              <div className="mt-2.5 space-y-2">
                <input
                  type="text"
                  value={emailPasteSubj}
                  onChange={(e) => setEmailPasteSubj(e.target.value)}
                  placeholder="邮件主题（如：汇丰银行证券交易成交通知）"
                  className="w-full rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs"
                />
                <textarea
                  rows={3}
                  value={emailPasteText}
                  onChange={(e) => setEmailPasteText(e.target.value)}
                  placeholder="粘贴汇丰股票买卖成交确认文本（如：买入 00700 腾讯控股 100股，成交价 HKD 388.50，佣金 HKD 50.00，印花税 HKD 39.00）"
                  className="w-full rounded-xl border border-slate-200 bg-white p-2.5 text-xs"
                />
                <button
                  type="button"
                  disabled={emailScanning || !emailPasteText.trim()}
                  onClick={handleParseEmailTradePaste}
                  className="rounded-xl bg-indigo-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
                >
                  解析正文成交
                </button>
              </div>
            </details>

            {emailWarnings.length > 0 && (
              <div className="rounded-xl bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800 space-y-0.5">
                {emailWarnings.map((w, i) => (
                  <div key={i}>• {w}</div>
                ))}
              </div>
            )}

            {emailScannedList.length > 0 && (
              <details
                open={emailCandidates.length === 0}
                className="rounded-xl border border-slate-200/80 bg-slate-50/60 px-3.5 py-2 text-xs"
              >
                <summary className="cursor-pointer font-medium text-slate-700 flex items-center justify-between">
                  <span>
                    已扫描邮件列表（共 {emailScannedList.length} 封
                    {emailScannedList.some((e) => e.stock_hint)
                      ? ` · 其中 ${emailScannedList.filter((e) => e.stock_hint).length} 封含汇丰/证券关键词`
                      : ''}
                    ）
                  </span>
                  <span className="text-[11px] font-normal text-slate-400">可展开查看原文或填入正文框排查</span>
                </summary>
                <div className="mt-2 max-h-44 overflow-y-auto space-y-1.5 pr-1">
                  {emailScannedList.map((em) => (
                    <div
                      key={`${em.uid}-${em.received_at}`}
                      className={`rounded-lg border px-2.5 py-1.5 ${
                        em.stock_hint ? 'border-rose-200 bg-rose-50/40' : 'border-slate-200 bg-white'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0 truncate font-medium text-slate-800">
                          {em.stock_hint && (
                            <span className="mr-1 rounded bg-rose-100 px-1 py-0.5 text-[10px] text-rose-700">券商线索</span>
                          )}
                          {em.subject}
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setEmailPasteSubj(em.subject || '汇丰股票交易通知');
                            setEmailPasteText(em.body_preview || '');
                            onNotify('已将该邮件内容填入下方粘贴框，可直接核对或点击解析正文');
                          }}
                          className="shrink-0 rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-700 hover:bg-slate-200 cursor-pointer"
                        >
                          填入正文排查
                        </button>
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                        <span>{em.received_at}</span>
                        <span className="truncate max-w-[200px]">{em.sender}</span>
                        {em.attachments && em.attachments.length > 0 && (
                          <span className="text-indigo-600">附件: {em.attachments.join(', ')}</span>
                        )}
                      </div>
                      {em.stock_hint && em.body_preview && (
                        <div className="mt-1 line-clamp-2 text-[11px] text-slate-500">
                          {em.body_preview}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            )}

            {/* Options */}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-indigo-50/50 border border-indigo-100 px-3.5 py-2 text-xs">
              <label className="inline-flex items-center gap-2 text-slate-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={emailAutoCreate}
                  onChange={(e) => setEmailAutoCreate(e.target.checked)}
                />
                <span>若某只标的尚无网格，自动以券商标签（如汇丰 HSBC）新建网格计划</span>
              </label>
              <div className="flex items-center gap-1.5">
                <span className="text-slate-500">默认等比步长</span>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  max="20"
                  value={emailDefaultStep}
                  onChange={(e) => setEmailDefaultStep(e.target.value)}
                  className="w-14 rounded-lg border border-slate-200 bg-white px-2 py-0.5 text-xs font-mono"
                />
                <span className="text-slate-500">%</span>
              </div>
            </div>

            {/* Candidate trades list */}
            <div className="flex-1 overflow-y-auto space-y-2 pr-1">
              {emailCandidates.length === 0 ? (
                <div className="rounded-xl bg-slate-50 py-10 text-center text-xs text-slate-400">
                  暂无待导入的邮件股票成交。点击上方「扫描邮箱交易」、上传 PDF 成交单或粘贴汇丰邮件正文即可提取。
                </div>
              ) : (
                emailCandidates.map((tr, idx) => {
                  const checked = Boolean(emailSelected[idx]);
                  const mk = MARKET_META[tr.market] || MARKET_META.HK;
                  return (
                    <div
                      key={`${tr.external_ref}-${idx}`}
                      className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border px-3.5 py-2.5 text-xs ${
                        tr.already_imported
                          ? 'border-slate-100 bg-slate-50 opacity-55'
                          : checked
                            ? 'border-rose-200 bg-rose-50/30'
                            : 'border-slate-200 bg-white'
                      }`}
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <button
                          type="button"
                          onClick={() => setEmailSelected((p) => ({ ...p, [idx]: !p[idx] }))}
                          className="text-rose-600 cursor-pointer shrink-0"
                        >
                          {checked ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4 text-slate-400" />}
                        </button>
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                                tr.side === 'buy' ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'
                              }`}
                            >
                              {tr.side === 'buy' ? '买入' : '卖出'}
                            </span>
                            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${mk.cls}`}>
                              {mk.label}
                            </span>
                            <span className="font-mono font-bold text-slate-900">{tr.symbol}</span>
                            {tr.name && tr.name !== tr.symbol && (
                              <span className="text-slate-700 truncate max-w-[150px]">{tr.name}</span>
                            )}
                            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                              {tr.broker}
                            </span>
                            {tr.matched_strategy_name ? (
                              <span className="rounded bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 text-[10px] text-emerald-700">
                                归属：{tr.matched_strategy_name}
                              </span>
                            ) : (
                              <span className="rounded bg-indigo-50 border border-indigo-200 px-1.5 py-0.5 text-[10px] text-indigo-700">
                                将自动新建网格
                              </span>
                            )}
                            {tr.already_imported && (
                              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700">
                                已导入
                              </span>
                            )}
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-400">
                            {tr.traded_at} · 佣金 {formatCurrency(tr.fee, tr.currency)} · 税费 {formatCurrency(tr.tax, tr.currency)}
                            {tr.email_subject ? ` · 来自《${tr.email_subject.slice(0, 26)}》` : ''}
                          </div>
                        </div>
                      </div>

                      <div className="text-right shrink-0 font-mono font-bold text-slate-900">
                        {fmtPrice(tr.price)} × {tr.quantity} 股
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 pt-3">
              <button
                type="button"
                onClick={() => setShowEmailModal(false)}
                className="rounded-xl border border-slate-200 px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                disabled={emailImporting || emailCandidates.length === 0}
                onClick={handleCommitEmailTrades}
                className="rounded-xl bg-slate-900 px-5 py-2 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-45 cursor-pointer"
              >
                {emailImporting ? '导入中...' : '确认导入选中成交到股票网格'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
