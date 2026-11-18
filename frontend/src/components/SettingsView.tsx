import React, { useEffect, useState } from 'react';
import { BellRing, Bot, CheckCircle2, Database, Mail, PlugZap, Save, XCircle } from 'lucide-react';
import { api } from '../api';
import type { AppSettings } from '../types';

interface Props {
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
}

export const SettingsView: React.FC<Props> = ({ onRefreshDashboard, onNotify }) => {
  const [settings, setSettings] = useState<AppSettings>({});
  const [saving, setSaving] = useState(false);
  const [aiTestResult, setAiTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [mcpTestResult, setMcpTestResult] = useState<{
    ok: boolean;
    message: string;
    tools: Array<{ name: string; description: string }>;
  } | null>(null);
  const [emailTestResult, setEmailTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [barkTestResult, setBarkTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => {});
  }, []);

  const updateField = (key: string, value: string) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await api.updateSettings(settings);
      setSettings(updated);
      onNotify('配置已保存');
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleTestAi = async () => {
    await api.updateSettings(settings);
    const res = await api.testAi();
    setAiTestResult(res);
  };

  const handleTestSiyuan = async () => {
    await api.updateSettings(settings);
    const res = await api.testSiyuan();
    setMcpTestResult(res);
  };

  const handleTestEmail = async () => {
    await api.updateSettings(settings);
    const res = await api.testEmail();
    setEmailTestResult(res);
  };

  const handleTestBark = async () => {
    await api.updateSettings(settings);
    const res = await api.testBark();
    setBarkTestResult(res);
  };

  const handleReset = async (reseed: boolean) => {
    const label = reseed ? '恢复默认示例数据' : '清空全部个人数据';
    if (!window.confirm(`确定要${label}吗？`)) return;
    await api.resetData(reseed);
    onNotify(reseed ? '已恢复初始示例数据' : '已清空所有记录，准备开始真实使用');
    onRefreshDashboard();
  };

  return (
    <form onSubmit={handleSave} className="space-y-6 max-w-4xl">
      {/* AI Config */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-indigo-600" />
            <div>
              <h3 className="text-base font-semibold text-slate-900">AI 大模型接入配置</h3>
              <p className="text-xs text-slate-500">用于聊天新增提醒、记账、思源笔记提取与结账总结</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleTestAi}
            className="rounded-xl border border-indigo-200 bg-indigo-50 px-3.5 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 cursor-pointer"
          >
            测试 AI 连接
          </button>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">AI 提供方</label>
            <select
              value={settings.ai_provider || 'local_fallback'}
              onChange={(e) => updateField('ai_provider', e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            >
              <option value="local_fallback">本地智能中文规则引擎（无需 Key）</option>
              <option value="gemini">Google Gemini API</option>
              <option value="openai_compatible">OpenAI 兼容接口（OpenAI / DeepSeek / Ollama）</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">模型名称</label>
            <input
              type="text"
              value={settings.ai_model || ''}
              onChange={(e) => updateField('ai_model', e.target.value)}
              placeholder="gemini-2.5-flash / gpt-4o-mini / deepseek-chat"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">API Key</label>
            <input
              type="password"
              value={settings.ai_api_key || ''}
              onChange={(e) => updateField('ai_api_key', e.target.value)}
              placeholder="本地模式或 Ollama 可留空"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Base URL（OpenAI 兼容时生效）</label>
            <input
              type="text"
              value={settings.ai_base_url || ''}
              onChange={(e) => updateField('ai_base_url', e.target.value)}
              placeholder="https://api.openai.com/v1 或 http://127.0.0.1:11434/v1"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            />
          </div>
        </div>

        {aiTestResult && (
          <div
            className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs ${
              aiTestResult.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
            }`}
          >
            {aiTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span>{aiTestResult.message}</span>
          </div>
        )}
      </div>

      {/* SiYuan MCP Config */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <PlugZap className="h-5 w-5 text-emerald-600" />
            <div>
              <h3 className="text-base font-semibold text-slate-900">本地思源笔记 MCP 配置</h3>
              <p className="text-xs text-slate-500">支持本地 MCP stdio 进程、MCP HTTP/SSE 或直连思源内核 API (6806)</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleTestSiyuan}
            className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 cursor-pointer"
          >
            测试连接 & 列出工具
          </button>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">连接方式</label>
          <select
            value={settings.siyuan_mode || 'mcp_stdio'}
            onChange={(e) => updateField('siyuan_mode', e.target.value)}
            className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          >
            <option value="mcp_stdio">本地 MCP Stdio（如 npx -y siyuan-mcp@latest）</option>
            <option value="mcp_http">本地 MCP HTTP / Streamable-HTTP</option>
            <option value="siyuan_api">直连思源内核 API（http://127.0.0.1:6806）</option>
          </select>
        </div>

        {(settings.siyuan_mode || 'mcp_stdio') === 'mcp_stdio' && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">MCP 启动命令 (command)</label>
              <input
                type="text"
                value={settings.mcp_command || ''}
                onChange={(e) => updateField('mcp_command', e.target.value)}
                placeholder="npx 或 uvx / python3"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">命令参数 JSON 数组 (args)</label>
              <input
                type="text"
                value={settings.mcp_args_json || ''}
                onChange={(e) => updateField('mcp_args_json', e.target.value)}
                placeholder='["-y", "siyuan-mcp@latest"]'
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-xs font-medium text-slate-700 mb-1">环境变量 JSON (env，填写 SIYUAN_TOKEN 等)</label>
              <textarea
                rows={2}
                value={settings.mcp_env_json || ''}
                onChange={(e) => updateField('mcp_env_json', e.target.value)}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
              />
            </div>
          </div>
        )}

        {settings.siyuan_mode === 'mcp_http' && (
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">MCP HTTP Endpoint</label>
            <input
              type="text"
              value={settings.mcp_url || ''}
              onChange={(e) => updateField('mcp_url', e.target.value)}
              placeholder="http://127.0.0.1:3000/mcp"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
            />
          </div>
        )}

        {settings.siyuan_mode === 'siyuan_api' && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">思源内核地址</label>
              <input
                type="text"
                value={settings.siyuan_api_url || ''}
                onChange={(e) => updateField('siyuan_api_url', e.target.value)}
                placeholder="http://127.0.0.1:6806"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">API Token（思源 设置 → 关于 → API Token）</label>
              <input
                type="password"
                value={settings.siyuan_token || ''}
                onChange={(e) => updateField('siyuan_token', e.target.value)}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
              />
            </div>
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">默认思源待办扫描 SQL</label>
          <textarea
            rows={2}
            value={settings.siyuan_sql_filter || ''}
            onChange={(e) => updateField('siyuan_sql_filter', e.target.value)}
            className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
          />
        </div>

        {mcpTestResult && (
          <div
            className={`rounded-xl px-4 py-3 text-xs space-y-2 ${
              mcpTestResult.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-700'
            }`}
          >
            <div className="flex items-center gap-2 font-medium">
              {mcpTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
              <span>{mcpTestResult.message}</span>
            </div>
            {mcpTestResult.tools && mcpTestResult.tools.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {mcpTestResult.tools.map((t) => (
                  <span key={t.name} className="rounded-md bg-white/80 px-2 py-0.5 text-[11px] font-mono border border-emerald-200">
                    {t.name}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Email IMAP Config */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Mail className="h-5 w-5 text-sky-600" />
            <div>
              <h3 className="text-base font-semibold text-slate-900">个人邮箱 IMAP 自动解析配置</h3>
              <p className="text-xs text-slate-500">支持自动扫描支付宝/微信账单附件及信用卡、缴费、行程邮件（仅连接个人邮箱本地解析）</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleTestEmail}
            className="rounded-xl border border-sky-200 bg-sky-50 px-3.5 py-1.5 text-xs font-medium text-sky-700 hover:bg-sky-100 cursor-pointer"
          >
            测试邮箱连接
          </button>
        </div>

        <div className="flex flex-wrap gap-2 text-xs">
          <span className="text-slate-500 py-1">常用预设：</span>
          {[
            { label: 'QQ 邮箱', host: 'imap.qq.com', port: '993' },
            { label: '163 邮箱', host: 'imap.163.com', port: '993' },
            { label: 'Gmail', host: 'imap.gmail.com', port: '993' },
            { label: '腾讯企业邮', host: 'imap.exmail.qq.com', port: '993' },
          ].map((preset) => (
            <button
              key={preset.host}
              type="button"
              onClick={() => {
                updateField('email_imap_host', preset.host);
                updateField('email_imap_port', preset.port);
                updateField('email_use_ssl', '1');
              }}
              className="rounded-lg bg-slate-100 px-2.5 py-1 text-slate-700 hover:bg-sky-50 hover:text-sky-700 cursor-pointer"
            >
              {preset.label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">IMAP 服务器地址</label>
            <input
              type="text"
              value={settings.email_imap_host || ''}
              onChange={(e) => updateField('email_imap_host', e.target.value)}
              placeholder="imap.qq.com"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">端口（SSL 默认 993）</label>
            <input
              type="text"
              value={settings.email_imap_port || '993'}
              onChange={(e) => updateField('email_imap_port', e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">邮箱账号</label>
            <input
              type="text"
              value={settings.email_username || ''}
              onChange={(e) => updateField('email_username', e.target.value)}
              placeholder="your_email@qq.com"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">IMAP 授权码 / 应用专用密码</label>
            <input
              type="password"
              value={settings.email_password || ''}
              onChange={(e) => updateField('email_password', e.target.value)}
              placeholder="QQ/163/Gmail 请填 IMAP 授权码"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">默认账单压缩包密码（可选）</label>
            <input
              type="password"
              value={settings.email_bill_zip_password || ''}
              onChange={(e) => updateField('email_bill_zip_password', e.target.value)}
              placeholder="微信/支付宝发送加密 zip 时使用"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            />
          </div>
          <div className="flex items-end gap-3 pb-1">
            <label className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.email_auto_sync_enabled === '1'}
                onChange={(e) => updateField('email_auto_sync_enabled', e.target.checked ? '1' : '0')}
              />
              <span>开启后台定时自动解析邮箱</span>
            </label>
            <select
              value={settings.email_sync_interval_min || '30'}
              onChange={(e) => updateField('email_sync_interval_min', e.target.value)}
              className="rounded-lg border border-slate-200 px-2 py-1 text-xs"
            >
              <option value="15">每 15 分钟</option>
              <option value="30">每 30 分钟</option>
              <option value="60">每 60 分钟</option>
            </select>
          </div>
        </div>

        {emailTestResult && (
          <div
            className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs ${
              emailTestResult.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
            }`}
          >
            {emailTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span>{emailTestResult.message}</span>
          </div>
        )}
      </div>

      {/* Bark Push Config */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <BellRing className="h-5 w-5 text-rose-600" />
            <div>
              <h3 className="text-base font-semibold text-slate-900">Bark 手机提醒推送配置</h3>
              <p className="text-xs text-slate-500">
                所有新建提醒、手机保号提前提醒、账单与日程到期均自动通过 Bark 推送到你的 iPhone
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleTestBark}
            className="rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-100 cursor-pointer"
          >
            发送测试推送
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-xs text-slate-700">
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.bark_enabled === '1'}
              onChange={(e) => updateField('bark_enabled', e.target.checked ? '1' : '0')}
            />
            <span>启用 Bark 提醒推送</span>
          </label>
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.bark_push_on_create !== '0'}
              onChange={(e) => updateField('bark_push_on_create', e.target.checked ? '1' : '0')}
            />
            <span>新增/触发提醒时立即推送一条确认通知</span>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Bark 服务器地址</label>
            <input
              type="text"
              value={settings.bark_server_url || 'https://api.day.app'}
              onChange={(e) => updateField('bark_server_url', e.target.value)}
              placeholder="https://api.day.app 或自建域名"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Bark Device Key（或直接粘贴完整 Bark 推送链接）
            </label>
            <input
              type="password"
              value={settings.bark_device_key || ''}
              onChange={(e) => updateField('bark_device_key', e.target.value)}
              placeholder="例如：AbCdEfGhIjK12345"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">推送分组名称 (group)</label>
            <input
              type="text"
              value={settings.bark_group || 'LifeHub'}
              onChange={(e) => updateField('bark_group', e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">提示铃声</label>
              <select
                value={settings.bark_sound || 'bell'}
                onChange={(e) => updateField('bark_sound', e.target.value)}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              >
                <option value="bell">bell（清脆铃声）</option>
                <option value="alarm">alarm（强提醒）</option>
                <option value="calypso">calypso</option>
                <option value="minuet">minuet</option>
                <option value="">系统默认</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">到期提前推送（分钟）</label>
              <input
                type="number"
                min={0}
                max={1440}
                value={settings.bark_due_advance_min || '15'}
                onChange={(e) => updateField('bark_due_advance_min', e.target.value)}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              />
            </div>
          </div>
        </div>

        {barkTestResult && (
          <div
            className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs ${
              barkTestResult.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
            }`}
          >
            {barkTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span>{barkTestResult.message}</span>
          </div>
        )}
      </div>

      {/* Save + Data management */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
        <div className="flex items-center gap-2 text-xs">
          <Database className="h-4 w-4 text-slate-500" />
          <button
            type="button"
            onClick={() => handleReset(false)}
            className="rounded-lg border border-rose-200 px-3 py-1.5 text-rose-600 hover:bg-rose-50 cursor-pointer"
          >
            清空示例数据（开始真实使用）
          </button>
          <button
            type="button"
            onClick={() => handleReset(true)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-600 hover:bg-slate-50 cursor-pointer"
          >
            恢复演示数据
          </button>
        </div>

        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-slate-800 cursor-pointer"
        >
          <Save className="h-4 w-4" />
          {saving ? '保存中...' : '保存全部设置'}
        </button>
      </div>
    </form>
  );
};
