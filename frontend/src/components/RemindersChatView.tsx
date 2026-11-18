import React, { useEffect, useRef, useState } from 'react';
import {
  BellRing,
  Bot,
  CheckCircle2,
  Circle,
  Plus,
  Search,
  Send,
  Sparkles,
  Trash2,
  User,
} from 'lucide-react';
import { api } from '../api';
import type { ChatMessage, Reminder } from '../types';
import { CATEGORY_LABELS, formatDueAt, formatMoney, PRIORITY_META, SOURCE_META } from '../utils/format';

interface Props {
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
}

export const RemindersChatView: React.FC<Props> = ({ onRefreshDashboard, onNotify }) => {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [statusFilter, setStatusFilter] = useState('pending');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [searchQ, setSearchQ] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);

  // Manual form
  const [newTitle, setNewTitle] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newDue, setNewDue] = useState('');
  const [newPriority, setNewPriority] = useState<'high' | 'medium' | 'low'>('medium');
  const [newCategory, setNewCategory] = useState<'work' | 'life' | 'finance' | 'health' | 'study'>('life');
  const [newAmount, setNewAmount] = useState('');

  // Chat
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [sending, setSending] = useState(false);
  const chatScrollRef = useRef<HTMLDivElement>(null);

  const loadReminders = async () => {
    try {
      const list = await api.listReminders({
        status: statusFilter,
        source_type: sourceFilter,
        q: searchQ,
      });
      setReminders(list);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载提醒失败');
    }
  };

  const loadChat = async () => {
    try {
      const list = await api.listChat();
      setMessages(list);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    loadReminders();
  }, [statusFilter, sourceFilter, searchQ]);

  useEffect(() => {
    loadChat();
  }, []);

  useEffect(() => {
    const el = chatScrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const handleAddReminder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    try {
      await api.createReminder({
        title: newTitle.trim(),
        description: newDesc.trim(),
        due_at: newDue || null,
        priority: newPriority,
        category: newCategory,
        linked_amount: newAmount ? Number(newAmount) : null,
        linked_tx_type: newAmount ? 'expense' : null,
      });
      setNewTitle('');
      setNewDesc('');
      setNewDue('');
      setNewAmount('');
      setShowAddForm(false);
      onNotify('已新增提醒事项');
      loadReminders();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '新增失败');
    }
  };

  const handleToggleDone = async (rem: Reminder) => {
    try {
      if (rem.status !== 'done' && rem.linked_amount) {
        await api.completeAndRecord(rem.id);
        onNotify(`已完成「${rem.title}」并自动入账 ${formatMoney(rem.linked_amount)}`);
      } else {
        const nextStatus = rem.status === 'done' ? 'pending' : 'done';
        await api.updateReminder(rem.id, { status: nextStatus });
      }
      loadReminders();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '更新失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.deleteReminder(id);
      loadReminders();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '删除失败');
    }
  };

  const handleSendChat = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim() || sending) return;
    const text = chatInput.trim();
    setChatInput('');
    setSending(true);
    try {
      await api.sendChat(text);
      await loadChat();
      await loadReminders();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '发送失败');
    } finally {
      setSending(false);
    }
  };

  const handleClearChat = async () => {
    await api.clearChat();
    setMessages([]);
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      {/* Left 7 cols: Reminders Manager */}
      <div className="lg:col-span-7 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">个人提醒事项</h2>
            <p className="text-xs text-slate-500">支持手动管理、AI 聊天提取、思源笔记导入与固定账单联动</p>
          </div>
          <button
            onClick={() => setShowAddForm((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-indigo-700 transition cursor-pointer"
          >
            <Plus className="h-4 w-4" /> 手动新建
          </button>
        </div>

        {showAddForm && (
          <form onSubmit={handleAddReminder} className="mt-4 rounded-xl border border-indigo-100 bg-indigo-50/40 p-4 space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <input
                type="text"
                required
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="提醒标题（必填）"
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
              <input
                type="datetime-local"
                value={newDue}
                onChange={(e) => setNewDue(e.target.value)}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <select
                value={newPriority}
                onChange={(e) => setNewPriority(e.target.value as 'high' | 'medium' | 'low')}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
              >
                <option value="high">高优先级</option>
                <option value="medium">常规优先级</option>
                <option value="low">低优先级</option>
              </select>
              <select
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value as 'work' | 'life' | 'finance' | 'health' | 'study')}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
              >
                <option value="life">生活</option>
                <option value="work">工作</option>
                <option value="finance">财务</option>
                <option value="health">健康</option>
                <option value="study">学习</option>
              </select>
              <input
                type="number"
                step="0.01"
                value={newAmount}
                onChange={(e) => setNewAmount(e.target.value)}
                placeholder="关联金额（可选）"
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <input
              type="text"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="备注说明（可选）"
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowAddForm(false)}
                className="rounded-lg px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-200/60"
              >
                取消
              </button>
              <button
                type="submit"
                className="rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 cursor-pointer"
              >
                保存提醒
              </button>
            </div>
          </form>
        )}

        {/* Filters */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <div className="flex rounded-xl bg-slate-100 p-1 text-xs">
            {[
              { key: 'pending', label: '待办' },
              { key: 'done', label: '已完成' },
              { key: 'all', label: '全部' },
            ].map((s) => (
              <button
                key={s.key}
                onClick={() => setStatusFilter(s.key)}
                className={`rounded-lg px-3 py-1 font-medium transition cursor-pointer ${
                  statusFilter === s.key ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700"
          >
            <option value="all">全部来源</option>
            <option value="ai_chat">AI 对话</option>
            <option value="siyuan_note">思源笔记</option>
            <option value="recurring_bill">固定账单</option>
            <option value="manual">手动创建</option>
          </select>

          <div className="relative flex-1 min-w-[160px]">
            <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              placeholder="搜索提醒..."
              className="w-full rounded-xl border border-slate-200 bg-slate-50 pl-8 pr-3 py-1.5 text-xs focus:bg-white focus:border-indigo-400 focus:outline-none"
            />
          </div>
        </div>

        {/* List */}
        <div className="mt-4 space-y-2.5 max-h-[600px] overflow-y-auto pr-1">
          {reminders.length === 0 ? (
            <div className="rounded-xl bg-slate-50 py-12 text-center text-sm text-slate-400">
              当前筛选条件下暂无提醒事项
            </div>
          ) : (
            reminders.map((rem) => {
              const prio = PRIORITY_META[rem.priority] || PRIORITY_META.medium;
              const src = SOURCE_META[rem.source_type] || SOURCE_META.manual;
              const isDone = rem.status === 'done';
              return (
                <div
                  key={rem.id}
                  className={`flex items-start justify-between gap-3 rounded-xl border p-3.5 transition ${
                    isDone ? 'border-slate-100 bg-slate-50/50 opacity-70' : 'border-slate-200/80 bg-white hover:border-indigo-200'
                  }`}
                >
                  <div className="flex items-start gap-3 min-w-0">
                    <button
                      onClick={() => handleToggleDone(rem)}
                      className="mt-0.5 text-slate-400 hover:text-emerald-600 transition cursor-pointer shrink-0"
                    >
                      {isDone ? <CheckCircle2 className="h-5 w-5 text-emerald-600" /> : <Circle className="h-5 w-5" />}
                    </button>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className={`text-sm font-medium ${isDone ? 'line-through text-slate-400' : 'text-slate-900'}`}>
                          {rem.title}
                        </span>
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
                      {rem.description && <p className="mt-1 text-xs text-slate-500">{rem.description}</p>}
                      <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
                        <span>到期：{formatDueAt(rem.due_at)}</span>
                        {rem.source_ref && <span>来源：{rem.source_ref}</span>}
                        {rem.linked_amount && (
                          <span className="text-orange-600 font-medium">
                            关联金额 {formatMoney(rem.linked_amount)}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {!isDone && (
                      <button
                        onClick={async () => {
                          const res = await api.pushReminderBark(rem.id);
                          onNotify(res.message);
                        }}
                        title="立即通过 Bark 推送到手机"
                        className="text-slate-300 hover:text-rose-600 transition cursor-pointer p-1"
                      >
                        <BellRing className="h-4 w-4" />
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(rem.id)}
                      title="删除"
                      className="text-slate-300 hover:text-rose-500 transition cursor-pointer p-1"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Right 5 cols: AI Chat Assistant */}
      <div className="lg:col-span-5 rounded-2xl border border-slate-200/80 bg-white shadow-xs flex flex-col h-[680px]">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="rounded-xl bg-indigo-600 p-2 text-white">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">AI 聊天建提醒 & 记账</h3>
              <p className="text-[11px] text-slate-500">支持多任务拆解、时间识别与金额入账</p>
            </div>
          </div>
          <button
            onClick={handleClearChat}
            className="text-xs text-slate-400 hover:text-slate-600 cursor-pointer"
          >
            清空对话
          </button>
        </div>

        <div ref={chatScrollRef} className="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-50/40">
          {messages.length === 0 && (
            <div className="rounded-xl bg-white border border-slate-100 p-4 text-xs text-slate-500">
              发送一句自然语言试试看，例如：「明天上午9点半提醒我交房租3200元，今天咖啡花了32元」。
            </div>
          )}

          {messages.map((msg) => (
            <div key={msg.id} className={`flex gap-2.5 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
              <div
                className={`h-7 w-7 rounded-full flex items-center justify-center shrink-0 ${
                  msg.role === 'user' ? 'bg-slate-800 text-white' : 'bg-indigo-100 text-indigo-700'
                }`}
              >
                {msg.role === 'user' ? <User className="h-3.5 w-3.5" /> : <Bot className="h-4 w-4" />}
              </div>
              <div className={`max-w-[82%] space-y-2 ${msg.role === 'user' ? 'items-end flex flex-col' : ''}`}>
                <div
                  className={`rounded-2xl px-3.5 py-2.5 text-xs leading-relaxed whitespace-pre-wrap ${
                    msg.role === 'user'
                      ? 'bg-indigo-600 text-white rounded-tr-xs'
                      : 'bg-white border border-slate-200/80 text-slate-800 rounded-tl-xs shadow-2xs'
                  }`}
                >
                  {msg.content}
                </div>
                {msg.actions && msg.actions.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {msg.actions.map((act, idx) => (
                      <span
                        key={idx}
                        className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium ${
                          act.kind === 'reminder'
                            ? 'bg-indigo-50 text-indigo-700 border border-indigo-100'
                            : 'bg-emerald-50 text-emerald-700 border border-emerald-100'
                        }`}
                      >
                        {act.kind === 'reminder'
                          ? `+ 提醒：${act.title}`
                          : `+ ${act.type === 'income' ? '收入' : '支出'} ${formatMoney(act.amount)} (${act.category})`}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="border-t border-slate-100 p-3 bg-white rounded-b-2xl space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {[
              '后天上午10点提醒我交物业费260元',
              '今天打车花了38元',
              '帮我总结目前的待办和财务情况',
            ].map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => setChatInput(prompt)}
                className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-indigo-50 hover:text-indigo-700 transition cursor-pointer"
              >
                {prompt}
              </button>
            ))}
          </div>

          <form onSubmit={handleSendChat} className="flex gap-2">
            <input
              type="text"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="输入想提醒的事或刚刚的消费..."
              className="flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2 text-sm focus:bg-white focus:border-indigo-500 focus:outline-none"
            />
            <button
              type="submit"
              disabled={sending || !chatInput.trim()}
              className="inline-flex items-center justify-center rounded-xl bg-indigo-600 px-4 py-2 text-white hover:bg-indigo-700 disabled:opacity-50 transition cursor-pointer"
            >
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};
