import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckSquare,
  Download,
  FileSpreadsheet,
  Inbox,
  Mail,
  RefreshCw,
  Sparkles,
  Square,
  Upload,
} from 'lucide-react';
import { api } from '../api';
import type {
  Account,
  DetectedCardSummary,
  EmailAccount,
  EmailCacheStats,
  EmailScanSummary,
  EmailSyncLog,
  EmailSyncStatus,
  NoteCandidate,
  ParsedBillTransaction,
} from '../types';
import { CHANNEL_META, formatCurrency, formatMoney, SOURCE_META } from '../utils/format';

interface Props {
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
}

export const EmailBillsView: React.FC<Props> = ({ onRefreshDashboard, onNotify }) => {
  const [days, setDays] = useState(14);
  const [keyword, setKeyword] = useState('');
  const [zipPassword, setZipPassword] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [includeBodyAi, setIncludeBodyAi] = useState(true);
  const [scanning, setScanning] = useState(false);

  const [accounts, setAccounts] = useState<Account[]>([]);
  const [targetAccountId, setTargetAccountId] = useState<string>('');
  const [autoCreateAccounts, setAutoCreateAccounts] = useState<boolean>(true);
  const [autoReconcile, setAutoReconcile] = useState<boolean>(true);
  const [detectedCards, setDetectedCards] = useState<DetectedCardSummary[]>([]);
  const [mailboxes, setMailboxes] = useState<EmailAccount[]>([]);
  const [selectedMailboxId, setSelectedMailboxId] = useState<string>('');
  const [cacheStats, setCacheStats] = useState<EmailCacheStats | null>(null);
  const [syncingCache, setSyncingCache] = useState(false);
  const [syncStatus, setSyncStatus] = useState<EmailSyncStatus | null>(null);

  const [emails, setEmails] = useState<EmailScanSummary[]>([]);
  const [expandedEmailUid, setExpandedEmailUid] = useState<string | null>(null);
  const [txCandidates, setTxCandidates] = useState<ParsedBillTransaction[]>([]);
  const [txSelected, setTxSelected] = useState<Record<number, boolean>>({});
  const [remCandidates, setRemCandidates] = useState<NoteCandidate[]>([]);
  const [remSelected, setRemSelected] = useState<Record<number, boolean>>({});
  const [warnings, setWarnings] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);

  // Manual paste email test
  const [pasteSubject, setPasteSubject] = useState('');
  const [pasteBody, setPasteBody] = useState('');
  const [parsingText, setParsingText] = useState(false);

  // Sync logs
  const [logs, setLogs] = useState<EmailSyncLog[]>([]);

  const loadMeta = async () => {
    try {
      const [accRes, logRes, mbRes, csRes, stRes] = await Promise.all([
        api.listAccounts(),
        api.listEmailLogs(),
        api.listEmailAccounts().catch(() => [] as EmailAccount[]),
        api.getEmailCacheStats().catch(() => null),
        api.getEmailSyncStatus().catch(() => null),
      ]);
      setAccounts(accRes.accounts);
      setMailboxes(mbRes.filter((m) => m.enabled));
      setLogs(logRes);
      if (csRes) setCacheStats(csRes);
      if (stRes) {
        setSyncStatus(stRes);
        setSyncingCache(Boolean(stRes.running));
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    loadMeta();
  }, []);

  useEffect(() => {
    if (!syncStatus?.running) return;
    const timer = window.setInterval(async () => {
      try {
        const st = await api.getEmailSyncStatus();
        setSyncStatus(st);
        if (st.cache_stats) setCacheStats(st.cache_stats);
        if (!st.running) {
          setSyncingCache(false);
          window.clearInterval(timer);
          if (st.phase === 'error' && st.error) {
            onNotify(`同步失败：${st.error}`);
          } else if (st.message) {
            onNotify(st.message);
          }
        }
      } catch {
        // ignore transient polling failure
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [syncStatus?.running]);

  const populateCandidates = (
    txs: ParsedBillTransaction[],
    rems: NoteCandidate[],
    append = false,
  ) => {
    if (append) {
      setTxCandidates((prev) => {
        const next = [...prev, ...txs];
        const sel: Record<number, boolean> = { ...txSelected };
        txs.forEach((t, offset) => {
          sel[prev.length + offset] = !t.already_imported;
        });
        setTxSelected(sel);
        return next;
      });
      setRemCandidates((prev) => {
        const next = [...prev, ...rems];
        const sel: Record<number, boolean> = { ...remSelected };
        rems.forEach((_, offset) => {
          sel[prev.length + offset] = true;
        });
        setRemSelected(sel);
        return next;
      });
      return;
    }

    setTxCandidates(txs);
    const tSel: Record<number, boolean> = {};
    txs.forEach((t, idx) => {
      tSel[idx] = !t.already_imported;
    });
    setTxSelected(tSel);

    setRemCandidates(rems);
    const rSel: Record<number, boolean> = {};
    rems.forEach((_, idx) => {
      rSel[idx] = true;
    });
    setRemSelected(rSel);
  };

  const handleScanInbox = async (source: 'auto' | 'cache' = 'auto') => {
    setScanning(true);
    setWarnings([]);
    try {
      const res = await api.fetchParseEmails({
        days,
        limit: days >= 365 ? 1500 : 120,
        keyword,
        zip_password: zipPassword,
        include_body_ai: includeBodyAi,
        unread_only: unreadOnly,
        account_id: selectedMailboxId ? Number(selectedMailboxId) : undefined,
        source,
      });
      setEmails(res.emails || []);
      setWarnings(res.warnings || []);
      setDetectedCards(res.detected_cards || []);
      if (res.cache_stats) setCacheStats(res.cache_stats);
      populateCandidates(res.transactions || [], res.reminders || [], false);
      const modeTag = res.from_cache && source === 'cache' ? '（从本地缓存秒读）' : '';
      onNotify(
        `已解析 ${res.emails.length} 封邮件${modeTag}，识别出 ${res.transactions.length} 笔消费账目与 ${res.reminders.length} 条提醒`,
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '扫描邮箱失败');
    } finally {
      setScanning(false);
    }
  };

  const handleSyncCacheOnly = async (overrideDays?: number, overrideLimit?: number) => {
    setSyncingCache(true);
    const targetDays = overrideDays ?? Math.max(days, 30);
    const targetLimit = overrideLimit ?? (targetDays >= 365 ? 2500 : 120);
    try {
      const res = await api.syncEmailCache({
        days: targetDays,
        limit: targetLimit,
        account_id: selectedMailboxId ? Number(selectedMailboxId) : undefined,
        zip_password: zipPassword,
        background: true,
      });
      if (res.status) {
        setSyncStatus(res.status);
        setSyncingCache(Boolean(res.status.running));
      } else {
        const st = await api.getEmailSyncStatus();
        setSyncStatus(st);
        setSyncingCache(Boolean(st.running));
      }
      if (res.already_running) {
        onNotify('后台已有同步任务正在运行，已为你实时展示同步进度');
      } else {
        onNotify(targetDays >= 365 ? '已启动最近 2 年历史邮件后台拉取，上方将实时显示进度' : '已启动后台增量同步，请查看实时进度');
      }
    } catch (err) {
      setSyncingCache(false);
      onNotify(err instanceof Error ? err.message : '启动增量同步失败');
    }
  };

  const handleToggleAutoSync = async (enabled: boolean, intervalMin?: number) => {
    try {
      await api.updateSettings({
        email_auto_sync_enabled: enabled ? '1' : '0',
        ...(intervalMin ? { email_sync_interval_min: String(intervalMin) } : {}),
      });
      const cs = await api.getEmailCacheStats();
      setCacheStats(cs);
      onNotify(enabled ? `已开启后台定时增量拉取（每 ${intervalMin || cs.sync_interval_min} 分钟）` : '已关闭后台定时增量拉取');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '保存定时同步设置失败');
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const res = await api.parseBillUpload(file, zipPassword);
      setWarnings(res.errors || []);
      if (res.detected_cards?.length) {
        setDetectedCards((prev) => {
          const map = new Map<string, DetectedCardSummary>();
          [...prev, ...res.detected_cards!].forEach((c) => {
            const k = `${c.card_kind}:${c.card_last4 || c.account_display_name}`;
            const ex = map.get(k);
            if (ex) {
              ex.count += c.count;
              ex.expense = Number((ex.expense + c.expense).toFixed(2));
              ex.income = Number((ex.income + c.income).toFixed(2));
            } else {
              map.set(k, { ...c });
            }
          });
          return Array.from(map.values()).sort((a, b) => b.count - a.count);
        });
      }
      populateCandidates(res.transactions || [], [], true);
      onNotify(
        `已从 ${file.name} 解析出 ${res.transactions.length} 笔账目` +
          (res.detected_cards?.length ? `，识别到 ${res.detected_cards.length} 个支付卡/账户` : ''),
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '账单文件解析失败');
    } finally {
      e.target.value = '';
    }
  };

  const handleParsePaste = async () => {
    if (!pasteBody.trim()) return;
    setParsingText(true);
    try {
      const res = await api.parseEmailText({
        subject: pasteSubject.trim() || '邮件通知',
        sender: '手动粘贴',
        body: pasteBody.trim(),
      });
      if (res.detected_cards?.length) {
        setDetectedCards((prev) => [...prev, ...res.detected_cards!]);
      }
      populateCandidates(res.transactions || [], res.reminders || [], true);
      onNotify(`从正文识别出 ${res.transactions.length} 笔账目、${res.reminders.length} 条提醒`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '解析失败');
    } finally {
      setParsingText(false);
    }
  };

  const handleCommitImport = async () => {
    const pickedTxs = txCandidates.filter((_, idx) => txSelected[idx]);
    const pickedRems = remCandidates.filter((_, idx) => remSelected[idx]);
    if (pickedTxs.length === 0 && pickedRems.length === 0) {
      onNotify('请先勾选至少一条账目或提醒');
      return;
    }
    setImporting(true);
    try {
      const res = await api.importEmailItems({
        transactions: pickedTxs,
        reminders: pickedRems,
        account_id: targetAccountId ? Number(targetAccountId) : null,
        skip_duplicates: true,
        auto_create_accounts: autoCreateAccounts,
        auto_reconcile: autoReconcile,
        email_logs: emails.map((em) => ({
          message_uid: em.uid,
          subject: em.subject,
          sender: em.sender,
          received_at: em.received_at,
          tx_imported: em.parsed_tx_count,
          rem_imported: em.parsed_rem_count,
        })),
      });
      const extra: string[] = [];
      if (res.auto_created_cards?.length) extra.push(`自动建信用卡 ${res.auto_created_cards.length} 张`);
      if (res.auto_created_accounts?.length) extra.push(`自动建账户 ${res.auto_created_accounts.length} 个`);
      if (res.reconciliation?.newly_matched_count) extra.push(`多方核对去重 ${res.reconciliation.newly_matched_count} 笔`);
      onNotify(
        `导入完成：新增 ${res.imported_tx_count} 笔账目（跳过重复 ${res.skipped_duplicate_count} 笔），新增 ${res.imported_rem_count} 条提醒` +
          (extra.length ? `；${extra.join('，')}` : ''),
      );
      setTxCandidates([]);
      setRemCandidates([]);
      setDetectedCards([]);
      setTxSelected({});
      setRemSelected({});
      await loadMeta();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '导入失败');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Bar: IMAP Scan + File Upload */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-8 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Mail className="h-5 w-5 text-sky-600" />
              <div>
                <h2 className="text-base font-semibold text-slate-900">自动扫描解析邮箱数据 (IMAP)</h2>
                <p className="text-xs text-slate-500">
                  自动解析邮件附件里的支付宝/微信账单（.zip/.csv/.xlsx），以及正文中的信用卡账单、缴费与行程提醒
                </p>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-sky-100 bg-sky-50/60 px-3.5 py-2 text-xs text-slate-700">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-medium text-sky-900">
                本地邮件库：{cacheStats ? `${cacheStats.total_cached} 封（含附件 ${cacheStats.attachment_count} 份）` : '加载中...'}
              </span>
              {cacheStats?.last_sync_at && (
                <span className="text-[11px] text-slate-500">
                  上次同步：{cacheStats.last_sync_at.slice(0, 16).replace('T', ' ')}
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2.5">
              <label className="inline-flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={Boolean(cacheStats?.auto_sync_enabled)}
                  onChange={(e) => handleToggleAutoSync(e.target.checked)}
                />
                <span>定时增量拉取</span>
              </label>
              <select
                value={String(cacheStats?.sync_interval_min || 30)}
                onChange={(e) => handleToggleAutoSync(Boolean(cacheStats?.auto_sync_enabled ?? true), Number(e.target.value))}
                className="rounded-lg border border-slate-200 bg-white px-2 py-0.5 text-[11px]"
              >
                <option value="5">每 5 分钟</option>
                <option value="15">每 15 分钟</option>
                <option value="30">每 30 分钟</option>
                <option value="60">每 60 分钟</option>
              </select>
              <button
                type="button"
                disabled={syncingCache}
                onClick={() => handleSyncCacheOnly()}
                className="inline-flex items-center gap-1 rounded-lg border border-sky-200 bg-white px-2.5 py-1 text-[11px] font-medium text-sky-700 hover:bg-sky-100/60 disabled:opacity-50 cursor-pointer"
              >
                <RefreshCw className={`h-3 w-3 ${syncingCache ? 'animate-spin' : ''}`} />
                {syncingCache ? '同步中...' : '仅增量拉取新邮件'}
              </button>
              <button
                type="button"
                disabled={syncingCache}
                onClick={() => handleSyncCacheOnly(730, 2500)}
                className="inline-flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 cursor-pointer"
                title="一次性拉取最近 2 年（730天）的历史邮件到本地邮件库，之后增量同步即可"
              >
                <Download className="h-3 w-3" />
                {syncingCache ? '后台拉取中...' : '拉取最近 2 年历史'}
              </button>
            </div>
          </div>

          {syncStatus && (syncStatus.running || syncStatus.phase === 'done' || syncStatus.phase === 'error') && (
            <div
              className={`rounded-xl border px-3.5 py-2.5 text-xs space-y-1.5 ${
                syncStatus.phase === 'error'
                  ? 'border-rose-200 bg-rose-50/70 text-rose-800'
                  : syncStatus.running
                    ? 'border-indigo-200 bg-indigo-50/70 text-indigo-900'
                    : 'border-emerald-200 bg-emerald-50/70 text-emerald-900'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 font-medium">
                  <RefreshCw className={`h-3.5 w-3.5 ${syncStatus.running ? 'animate-spin text-indigo-600' : ''}`} />
                  <span>{syncStatus.message || (syncStatus.running ? '正在同步邮件...' : '同步完成')}</span>
                </div>
                <span className="font-mono text-[11px] opacity-80">
                  {syncStatus.target_fetch > 0
                    ? `${syncStatus.fetched_done}/${syncStatus.target_fetch} (${Math.min(100, Math.round((syncStatus.fetched_done / syncStatus.target_fetch) * 100))}%)`
                    : syncStatus.running
                      ? '连接检索中...'
                      : `共 ${syncStatus.total_cached} 封`}
                </span>
              </div>
              {syncStatus.running && (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/80">
                  <div
                    className={`h-full rounded-full bg-indigo-600 transition-all duration-300 ${
                      syncStatus.target_fetch <= 0 ? 'w-1/3 animate-pulse' : ''
                    }`}
                    style={
                      syncStatus.target_fetch > 0
                        ? { width: `${Math.max(4, Math.min(100, Math.round((syncStatus.fetched_done / syncStatus.target_fetch) * 100)))}%` }
                        : undefined
                    }
                  />
                </div>
              )}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] opacity-80">
                {syncStatus.total_in_range > 0 && <span>时间窗口共 {syncStatus.total_in_range} 封</span>}
                {syncStatus.skipped_existing > 0 && <span>跳过已缓存 {syncStatus.skipped_existing} 封</span>}
                <span>本次新入库 {syncStatus.saved_new} 封</span>
                <span>本地邮件库总计 {syncStatus.total_cached} 封</span>
                {syncStatus.last_subject && (
                  <span className="truncate max-w-[260px]">最新：《{syncStatus.last_subject}》</span>
                )}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-5">
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
            >
              <option value={7}>最近 7 天邮件</option>
              <option value={14}>最近 14 天邮件</option>
              <option value={30}>最近 30 天邮件</option>
              <option value={90}>最近 90 天邮件</option>
              <option value={365}>最近 1 年邮件</option>
              <option value={730}>最近 2 年邮件</option>
            </select>
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="关键词过滤（如 支付宝/账单）"
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
            />
            <input
              type="password"
              value={zipPassword}
              onChange={(e) => setZipPassword(e.target.value)}
              placeholder="账单压缩包解压密码（可选）"
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
            />
            <button
              type="button"
              onClick={() => handleScanInbox('cache')}
              disabled={scanning}
              className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
            >
              <Sparkles className="h-3.5 w-3.5 text-indigo-600" />
              本地库秒析
            </button>
            <button
              type="button"
              onClick={() => handleScanInbox('auto')}
              disabled={scanning}
              className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-sky-600 px-3 py-2 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50 cursor-pointer"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${scanning ? 'animate-spin' : ''}`} />
              {scanning ? '拉取解析中...' : '增量拉取并解析'}
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-5 text-xs text-slate-600">
            {mailboxes.length > 0 && (
              <select
                value={selectedMailboxId}
                onChange={(e) => setSelectedMailboxId(e.target.value)}
                className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-700"
              >
                <option value="">全部已启用邮箱 ({mailboxes.length} 个)</option>
                {mailboxes.map((m) => (
                  <option key={m.id} value={String(m.id)}>
                    {m.name || m.username} ({m.username})
                  </option>
                ))}
              </select>
            )}
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={includeBodyAi}
                onChange={(e) => setIncludeBodyAi(e.target.checked)}
              />
              <span>AI 解析邮件正文（信用卡账单 / 缴费 / 行程 / 消费通知）</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={unreadOnly}
                onChange={(e) => setUnreadOnly(e.target.checked)}
              />
              <span>仅扫描未读邮件</span>
            </label>
          </div>
        </div>

        {/* Local Alipay/WeChat File Upload */}
        <div className="lg:col-span-4 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2">
              <FileSpreadsheet className="h-5 w-5 text-emerald-600" />
              <h3 className="text-base font-semibold text-slate-900">本地账单文件直传</h3>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              直接上传支付宝/微信导出的 <code className="text-slate-700">.csv</code>、<code className="text-slate-700">.xlsx</code> 或加密 <code className="text-slate-700">.zip</code>
            </p>
          </div>

          <label className="mt-4 flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/80 py-5 px-4 text-center hover:border-emerald-400 hover:bg-emerald-50/30 transition cursor-pointer">
            <Upload className="h-5 w-5 text-emerald-600 mb-1.5" />
            <span className="text-xs font-medium text-slate-700">点击选择账单文件（支持多平台自动识别）</span>
            <span className="text-[11px] text-slate-400 mt-0.5">需密码时请先在左侧填入解压密码</span>
            <input type="file" accept=".csv,.xlsx,.zip" onChange={handleFileUpload} className="hidden" />
          </label>
        </div>
      </div>

      {warnings.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-800 space-y-1">
          {warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-2">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-amber-600" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* Main Review Area */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left 4 cols: Scanned Emails + Paste Text Tester */}
        <div className="lg:col-span-4 space-y-5">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <div className="flex items-center gap-2 border-b border-slate-100 pb-3">
              <Inbox className="h-4 w-4 text-slate-600" />
              <h3 className="text-sm font-semibold text-slate-900">本次扫描邮件 ({emails.length})</h3>
            </div>
            <div className="mt-3 space-y-2.5 max-h-[280px] overflow-y-auto pr-1">
              {emails.length === 0 ? (
                <p className="py-8 text-center text-xs text-slate-400">
                  请先在「系统设置」配置邮箱 IMAP 后点击扫描
                </p>
              ) : (
                emails.map((em) => (
                  <div key={em.uid} className="rounded-xl border border-slate-100 bg-slate-50/70 p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-slate-900 truncate">{em.subject}</span>
                      {em.already_synced && (
                        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] text-slate-600 shrink-0">
                          曾同步
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-400 truncate">{em.sender} · {em.received_at}</div>
                    {em.attachments.length > 0 && (
                      <div className="mt-1 text-[11px] text-sky-700">附件：{em.attachments.join(', ')}</div>
                    )}
                    <div className="mt-1 flex items-center justify-between text-[11px] text-slate-600">
                      <span>识别：{em.parsed_tx_count} 笔账目 / {em.parsed_rem_count} 条提醒</span>
                      {em.body_preview && (
                        <button
                          type="button"
                          onClick={() => setExpandedEmailUid(expandedEmailUid === em.uid ? null : em.uid)}
                          className="text-sky-600 hover:underline cursor-pointer"
                        >
                          {expandedEmailUid === em.uid ? '收起正文' : '看解析正文'}
                        </button>
                      )}
                    </div>
                    {expandedEmailUid === em.uid && em.body_preview && (
                      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-slate-200 bg-white p-2 text-[10px] text-slate-600 font-mono">
                        {em.body_preview}
                      </pre>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Manual Email Paste */}
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-3">
            <h3 className="text-sm font-semibold text-slate-900">粘贴邮件正文快速测试</h3>
            <input
              type="text"
              value={pasteSubject}
              onChange={(e) => setPasteSubject(e.target.value)}
              placeholder="邮件主题（如：招商银行信用卡11月电子账单）"
              className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
            />
            <textarea
              rows={4}
              value={pasteBody}
              onChange={(e) => setPasteBody(e.target.value)}
              placeholder="粘贴银行账单、消费小票、缴费通知或机票行程邮件正文..."
              className="w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs"
            />
            <button
              onClick={handleParsePaste}
              disabled={parsingText || !pasteBody.trim()}
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-xl bg-indigo-600 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {parsingText ? 'AI 解析中...' : '智能解析该邮件'}
            </button>
          </div>
        </div>

        {/* Right 8 cols: Extracted Transactions & Reminders Preview */}
        <div className="lg:col-span-8 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
            <div>
              <h3 className="text-base font-semibold text-slate-900">
                待确认入账流水 ({txCandidates.length}) & 提醒 ({remCandidates.length})
              </h3>
              <p className="text-xs text-slate-500">已自动检测并默认取消勾选重复流水（基于交易单号指纹去重）</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={targetAccountId}
                onChange={(e) => setTargetAccountId(e.target.value)}
                className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs"
              >
                <option value="">入账方式：按卡尾号/支付方式智能自动分配（推荐）</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>强制全部入账到：{a.name}</option>
                ))}
              </select>
              <button
                onClick={handleCommitImport}
                disabled={importing || (txCandidates.length === 0 && remCandidates.length === 0)}
                className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-4 py-2 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-40 cursor-pointer"
              >
                <Download className="h-3.5 w-3.5" />
                {importing ? '正在导入...' : '一键确认入账并多方核对'}
              </button>
            </div>
          </div>

          {/* Smart Auto-Account & Reconciliation Options */}
          <div className="flex flex-wrap items-center gap-4 rounded-xl bg-indigo-50/50 border border-indigo-100 px-3.5 py-2.5 text-xs text-slate-700">
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={autoCreateAccounts}
                onChange={(e) => setAutoCreateAccounts(e.target.checked)}
              />
              <span>自动按支付方式/卡尾号匹配并创建对应信用卡与账户</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={autoReconcile}
                onChange={(e) => setAutoReconcile(e.target.checked)}
              />
              <span>导入后自动与邮箱信用卡账单/支付宝/微信多方交叉核对去重</span>
            </label>
          </div>

          {/* Detected Cards / Accounts Summary */}
          {detectedCards.length > 0 && (
            <div className="rounded-xl border border-emerald-200/80 bg-emerald-50/40 p-3 space-y-2">
              <div className="text-xs font-semibold text-emerald-800">
                识别到 {detectedCards.length} 个支付卡/钱包账户（导入时自动归属或新建）：
              </div>
              <div className="flex flex-wrap gap-1.5">
                {detectedCards.map((c, idx) => (
                  <span
                    key={`${c.account_display_name}-${idx}`}
                    className="inline-flex items-center gap-1 rounded-lg bg-white border border-emerald-200 px-2.5 py-1 text-[11px] text-slate-700"
                  >
                    <span className="font-medium text-slate-900">{c.account_display_name}</span>
                    {c.card_kind === 'credit_card' && (
                      <span className="rounded bg-rose-50 px-1 text-[10px] text-rose-600">信用卡</span>
                    )}
                    <span className="text-slate-400">
                      {c.count}笔 · {formatMoney(c.expense)}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Reminders candidates */}
          {remCandidates.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-semibold text-indigo-700">识别到的待办 / 账单提醒 ({remCandidates.length})</div>
              {remCandidates.map((rem, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-2.5 rounded-xl border border-indigo-100 bg-indigo-50/40 p-3 text-xs"
                >
                  <button
                    type="button"
                    onClick={() => setRemSelected((p) => ({ ...p, [idx]: !p[idx] }))}
                    className="mt-0.5 text-indigo-600 cursor-pointer"
                  >
                    {remSelected[idx] ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4 text-slate-400" />}
                  </button>
                  <div className="flex-1">
                    <div className="font-medium text-slate-900">{rem.title}</div>
                    <div className="text-slate-500 mt-0.5">{rem.description}</div>
                    <div className="mt-1 flex gap-3 text-[11px] text-slate-400">
                      <span>到期：{rem.due_at || '未指定'}</span>
                      {rem.linked_amount && <span className="text-orange-600 font-medium">金额 {formatMoney(rem.linked_amount)}</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Transactions candidates */}
          <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
            {txCandidates.length === 0 ? (
              <div className="rounded-xl bg-slate-50 py-14 text-center text-xs text-slate-400">
                暂无待确认账目。可通过上方「扫描邮箱」、上传支付宝/微信账单文件或粘贴邮件正文来提取。
              </div>
            ) : (
              txCandidates.map((tx, idx) => {
                const src = SOURCE_META[tx.source_type] || SOURCE_META.bill_import;
                const checked = Boolean(txSelected[idx]);
                return (
                  <div
                    key={idx}
                    className={`flex items-center justify-between gap-3 rounded-xl border px-3.5 py-2.5 text-xs transition ${
                      tx.already_imported
                        ? 'border-slate-100 bg-slate-50 opacity-55'
                        : checked
                          ? 'border-sky-200 bg-sky-50/30'
                          : 'border-slate-200 bg-white'
                    }`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <button
                        type="button"
                        onClick={() => setTxSelected((p) => ({ ...p, [idx]: !p[idx] }))}
                        className="text-sky-600 cursor-pointer shrink-0"
                      >
                        {checked ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4 text-slate-400" />}
                      </button>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${src.cls}`}>{src.label}</span>
                          {tx.payment_channel && CHANNEL_META[tx.payment_channel] && tx.payment_channel !== tx.source_type && (
                            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${CHANNEL_META[tx.payment_channel].cls}`}>
                              {CHANNEL_META[tx.payment_channel].label}
                            </span>
                          )}
                          {(tx.account_display_name || tx.card_last4) && (
                            <span className="rounded bg-indigo-50 border border-indigo-200 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
                              {tx.account_display_name || `${tx.bank_name || '卡'}(${tx.card_last4})`}
                            </span>
                          )}
                          <span className="font-medium text-slate-900 truncate max-w-[240px]">{tx.description}</span>
                          {tx.already_imported && (
                            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                              已存在（自动去重）
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 text-[11px] text-slate-400">
                          {tx.occurred_at}
                          {tx.payment_method ? ` · ${tx.payment_method}` : ''}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2.5 shrink-0">
                      <select
                        value={tx.category}
                        onChange={(e) =>
                          setTxCandidates((prev) =>
                            prev.map((item, i) => (i === idx ? { ...item, category: e.target.value } : item)),
                          )
                        }
                        className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px]"
                      >
                        {['餐饮', '交通', '购物', '住房', '订阅服务', '通讯', '医疗', '娱乐', '工资收入', '报销回款', '理财收益', '其他', '其他收入'].map(
                          (c) => (
                            <option key={c} value={c}>{c}</option>
                          ),
                        )}
                      </select>
                      <span className={`font-semibold w-20 text-right ${tx.type === 'income' ? 'text-emerald-600' : 'text-slate-900'}`}>
                        {tx.type === 'income' ? `+${formatMoney(tx.amount)}` : `-${formatMoney(tx.amount)}`}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {logs.length > 0 && (
            <div className="border-t border-slate-100 pt-3">
              <div className="text-xs font-medium text-slate-500 mb-2">最近邮件同步归档记录</div>
              <div className="flex flex-wrap gap-2">
                {logs.slice(0, 6).map((l) => (
                  <span key={l.id} className="rounded-lg bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600">
                    {l.subject.slice(0, 22)}（账目 {l.tx_imported} / 提醒 {l.rem_imported}）
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
