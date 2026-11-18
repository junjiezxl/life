import React, { useState } from 'react';
import {
  AlertCircle,
  CheckSquare,
  Download,
  FileText,
  PlugZap,
  RefreshCw,
  Sparkles,
  Square,
} from 'lucide-react';
import { api } from '../api';
import type { NoteCandidate, NoteSnippet } from '../types';
import { formatMoney } from '../utils/format';

interface Props {
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
}

export const NotesExtractorView: React.FC<Props> = ({ onRefreshDashboard, onNotify }) => {
  const [keyword, setKeyword] = useState('');
  const [notes, setNotes] = useState<NoteSnippet[]>([]);
  const [rawText, setRawText] = useState('');
  const [fetchInfo, setFetchInfo] = useState<{ source?: string; error?: string; demo?: boolean } | null>(null);
  const [fetching, setFetching] = useState(false);

  const [candidates, setCandidates] = useState<NoteCandidate[]>([]);
  const [selectedIdx, setSelectedIdx] = useState<Record<number, boolean>>({});
  const [extracting, setExtracting] = useState(false);
  const [importing, setImporting] = useState(false);

  const handleFetchNotes = async () => {
    setFetching(true);
    try {
      const res = await api.fetchSiyuanNotes(keyword);
      setNotes(res.notes || []);
      setFetchInfo({ source: res.source, error: res.error, demo: res.demo_fallback });
      if (res.notes && res.notes.length > 0) {
        await runExtract(res.notes, rawText);
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '拉取思源笔记失败');
    } finally {
      setFetching(false);
    }
  };

  const runExtract = async (targetNotes: NoteSnippet[], text: string) => {
    setExtracting(true);
    try {
      const res = await api.extractNotes(targetNotes, text);
      setCandidates(res.candidates || []);
      const initSel: Record<number, boolean> = {};
      (res.candidates || []).forEach((_, idx) => {
        initSel[idx] = true;
      });
      setSelectedIdx(initSel);
      if (res.warning) {
        onNotify(res.warning);
      } else {
        onNotify(`AI 已识别出 ${res.candidates.length} 条可导入提醒`);
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'AI 提取失败');
    } finally {
      setExtracting(false);
    }
  };

  const handleImportSelected = async () => {
    const picked = candidates.filter((_, idx) => selectedIdx[idx]);
    if (picked.length === 0) {
      onNotify('请先勾选至少一条待导入提醒');
      return;
    }
    setImporting(true);
    try {
      const res = await api.batchImportReminders(picked);
      onNotify(`成功导入 ${res.imported_count} 条思源笔记提醒！`);
      setCandidates([]);
      setSelectedIdx({});
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '导入失败');
    } finally {
      setImporting(false);
    }
  };

  const updateCandidate = (idx: number, patch: Partial<NoteCandidate>) => {
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  return (
    <div className="space-y-6">
      {/* Header Controls */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <PlugZap className="h-5 w-5 text-emerald-600" />
              <h2 className="text-lg font-semibold text-slate-900">思源笔记 MCP 提醒提取中心</h2>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              通过本地 MCP（stdio / HTTP）或思源内核 API 扫描待办卡片与笔记块，由 AI 提炼为结构化提醒事项
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="筛选关键词（留空则扫描待办/提醒块）"
              className="w-64 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2 text-xs focus:bg-white focus:border-emerald-500 focus:outline-none"
            />
            <button
              onClick={handleFetchNotes}
              disabled={fetching}
              className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50 transition cursor-pointer"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${fetching ? 'animate-spin' : ''}`} />
              {fetching ? '正在拉取思源笔记...' : '连接 MCP 拉取并智能提取'}
            </button>
          </div>
        </div>

        {fetchInfo?.error && (
          <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-amber-600" />
            <div>
              <div className="font-medium">{fetchInfo.error}</div>
              <div className="mt-0.5 text-amber-700">
                可前往「系统设置 → 思源笔记 MCP 配置」填写你的本地 `siyuan-mcp` 命令、端口或 API Token，或直接在下方粘贴笔记文本体验提取。
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Split Workspace */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: Fetched Notes & Manual Paste */}
        <div className="lg:col-span-6 space-y-5">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-slate-600" />
                <h3 className="text-sm font-semibold text-slate-900">
                  已加载思源笔记片段 ({notes.length})
                </h3>
              </div>
              {fetchInfo?.demo && (
                <span className="rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                  示例演示数据
                </span>
              )}
            </div>

            <div className="mt-3 space-y-3 max-h-[340px] overflow-y-auto pr-1">
              {notes.length === 0 ? (
                <div className="rounded-xl bg-slate-50 py-10 text-center text-xs text-slate-400">
                  点击上方「连接 MCP 拉取并智能提取」，将自动读取你的思源待办块
                </div>
              ) : (
                notes.map((note, idx) => (
                  <div key={note.id || idx} className="rounded-xl border border-slate-200/70 bg-slate-50/70 p-3.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-semibold text-slate-800">{note.title}</span>
                      <span className="text-slate-400">{note.path}</span>
                    </div>
                    <pre className="mt-2 whitespace-pre-wrap font-sans text-xs text-slate-600 leading-relaxed">
                      {note.content}
                    </pre>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <h3 className="text-sm font-semibold text-slate-900">补充粘贴 / 临时笔记片段</h3>
            <p className="mt-0.5 text-xs text-slate-500">直接粘贴思源笔记 Markdown，点击提取即可识别待办与金额</p>
            <textarea
              rows={5}
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder={'例如：\n- [ ] 周四前报销差旅车票 450元\n提醒：下周二上午10点项目评审'}
              className="mt-3 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs focus:bg-white focus:border-indigo-500 focus:outline-none"
            />
            <div className="mt-3 flex justify-end">
              <button
                onClick={() => runExtract(notes, rawText)}
                disabled={extracting || (notes.length === 0 && !rawText.trim())}
                className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
              >
                <Sparkles className="h-3.5 w-3.5" />
                {extracting ? 'AI 提取中...' : '重新 AI 提取候选提醒'}
              </button>
            </div>
          </div>
        </div>

        {/* Right: Extracted Candidates */}
        <div className="lg:col-span-6 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">
                AI 识别的待导入提醒 ({candidates.length})
              </h3>
              <p className="text-[11px] text-slate-500">导入前可微调标题、时间与优先级</p>
            </div>
            <button
              onClick={handleImportSelected}
              disabled={importing || candidates.length === 0}
              className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-4 py-2 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-40 cursor-pointer"
            >
              <Download className="h-3.5 w-3.5" />
              {importing ? '导入中...' : '一键导入选中项'}
            </button>
          </div>

          <div className="mt-4 flex-1 space-y-3 max-h-[540px] overflow-y-auto pr-1">
            {candidates.length === 0 ? (
              <div className="rounded-xl bg-slate-50 py-16 text-center text-xs text-slate-400">
                暂无提取结果。拉取思源笔记或粘贴 Markdown 文本后点击提取。
              </div>
            ) : (
              candidates.map((cand, idx) => {
                const checked = Boolean(selectedIdx[idx]);
                return (
                  <div
                    key={idx}
                    className={`rounded-xl border p-3.5 transition ${
                      checked ? 'border-indigo-300 bg-indigo-50/30' : 'border-slate-200 bg-slate-50/40 opacity-65'
                    }`}
                  >
                    <div className="flex items-start gap-2.5">
                      <button
                        type="button"
                        onClick={() => setSelectedIdx((p) => ({ ...p, [idx]: !p[idx] }))}
                        className="mt-1 text-indigo-600 cursor-pointer"
                      >
                        {checked ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4 text-slate-400" />}
                      </button>
                      <div className="flex-1 space-y-2">
                        <input
                          type="text"
                          value={cand.title}
                          onChange={(e) => updateCandidate(idx, { title: e.target.value })}
                          className="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm font-medium text-slate-900 focus:border-indigo-500 focus:outline-none"
                        />
                        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 text-xs">
                          <input
                            type="text"
                            value={cand.due_at || ''}
                            onChange={(e) => updateCandidate(idx, { due_at: e.target.value || null })}
                            placeholder="YYYY-MM-DDTHH:MM"
                            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs"
                          />
                          <select
                            value={cand.priority}
                            onChange={(e) =>
                              updateCandidate(idx, { priority: e.target.value as 'high' | 'medium' | 'low' })
                            }
                            className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs"
                          >
                            <option value="high">高优先</option>
                            <option value="medium">常规</option>
                            <option value="low">低优先</option>
                          </select>
                          <select
                            value={cand.category}
                            onChange={(e) =>
                              updateCandidate(idx, {
                                category: e.target.value as 'work' | 'life' | 'finance' | 'health' | 'study',
                              })
                            }
                            className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs"
                          >
                            <option value="work">工作</option>
                            <option value="life">生活</option>
                            <option value="finance">财务</option>
                            <option value="health">健康</option>
                            <option value="study">学习</option>
                          </select>
                        </div>
                        <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
                          <span>{cand.source_ref}</span>
                          {cand.reason && <span className="text-indigo-600">依据：{cand.reason}</span>}
                          {cand.linked_amount && (
                            <span className="text-orange-600 font-medium">
                              识别金额：{formatMoney(cand.linked_amount)}
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
      </div>
    </div>
  );
};
