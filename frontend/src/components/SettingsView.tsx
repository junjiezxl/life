import React, { useEffect, useState } from 'react';
import { BellRing, Bot, CheckCircle2, Copy, Database, ExternalLink, Globe, KeyRound, LogOut, Mail, PlugZap, RefreshCw, Save, ShieldCheck, XCircle } from 'lucide-react';
import { api } from '../api';
import type { AppSettings } from '../types';

interface Props {
  currentUsername: string;
  onUsernameChanged: (username: string) => void;
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
}

export const SettingsView: React.FC<Props> = ({
  currentUsername,
  onUsernameChanged,
  onRefreshDashboard,
  onNotify,
}) => {
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
  const [proxyTestResult, setProxyTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [ctraderTestResult, setCtraderTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [ibkrTestResult, setIbkrTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [ctraderOAuthScope, setCtraderOAuthScope] = useState<'accounts' | 'trading'>('accounts');
  const [ctraderOAuthCode, setCtraderOAuthCode] = useState('');
  const [ctraderRefreshing, setCtraderRefreshing] = useState(false);
  const [ctraderExchanging, setCtraderExchanging] = useState(false);

  // Account security state
  const [newUsername, setNewUsername] = useState(currentUsername);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  const [updatingAuth, setUpdatingAuth] = useState(false);
  const [activeSessions, setActiveSessions] = useState<number>(1);
  const [authFeedback, setAuthFeedback] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    api
      .getSettings()
      .then((data) => {
        const defaultUri =
          typeof window !== 'undefined' ? `${window.location.origin}/api/ctrader/oauth/callback` : '';
        if (!data.ctrader_oauth_redirect_uri && defaultUri) {
          setSettings({ ...data, ctrader_oauth_redirect_uri: defaultUri });
        } else {
          setSettings(data);
        }
      })
      .catch(() => {});
    api.getAuthStatus().then((st) => setActiveSessions(st.active_sessions || 1)).catch(() => {});
  }, []);

  useEffect(() => {
    setNewUsername(currentUsername);
  }, [currentUsername]);

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

  const handleTestProxy = async () => {
    setProxyTesting(true);
    setProxyTestResult(null);
    try {
      await api.updateSettings(settings);
      const res = await api.testProxy();
      setProxyTestResult(res);
    } catch (err) {
      setProxyTestResult({
        ok: false,
        message: err instanceof Error ? err.message : '代理测试失败',
      });
    } finally {
      setProxyTesting(false);
    }
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

  const handleTestCTrader = async () => {
    await api.updateSettings(settings);
    try {
      const res = await api.testCTrader();
      setCtraderTestResult({ ok: res.ok, message: res.message });
    } catch (err) {
      setCtraderTestResult({
        ok: false,
        message: err instanceof Error ? err.message : 'cTrader 连接失败',
      });
    }
  };

  const handleTestIbkr = async () => {
    await api.updateSettings(settings);
    setIbkrTestResult(null);
    try {
      const res = await api.testIbkr();
      setIbkrTestResult({ ok: res.ok, message: res.message });
    } catch (err) {
      setIbkrTestResult({
        ok: false,
        message: err instanceof Error ? err.message : 'IBKR Flex 连接失败',
      });
    }
  };

  const defaultCTraderRedirectUri =
    typeof window !== 'undefined' ? `${window.location.origin}/api/ctrader/oauth/callback` : '';

  const effectiveCTraderRedirectUri =
    (settings.ctrader_oauth_redirect_uri || '').trim() || defaultCTraderRedirectUri;

  const handleOpenCTraderAuthorize = async () => {
    setCtraderTestResult(null);
    try {
      const updated = await api.updateSettings({
        ...settings,
        ctrader_oauth_redirect_uri: effectiveCTraderRedirectUri,
      });
      setSettings(updated);
      const res = await api.getCTraderAuthorizeUrl(effectiveCTraderRedirectUri, ctraderOAuthScope);
      window.open(res.url, '_blank', 'noopener,noreferrer');
      onNotify('已打开 cTrader OAuth 授权页，授权完成后将自动回填 Token');
    } catch (err) {
      setCtraderTestResult({
        ok: false,
        message: err instanceof Error ? err.message : '生成 cTrader 授权链接失败',
      });
    }
  };

  const handleExchangeCTraderCode = async () => {
    if (!ctraderOAuthCode.trim()) {
      setCtraderTestResult({ ok: false, message: '请先粘贴 cTrader 回调链接或 code 参数' });
      return;
    }
    setCtraderExchanging(true);
    setCtraderTestResult(null);
    try {
      await api.updateSettings({
        ...settings,
        ctrader_oauth_redirect_uri: effectiveCTraderRedirectUri,
      });
      const res = await api.exchangeCTraderCode(ctraderOAuthCode.trim(), effectiveCTraderRedirectUri);
      const latest = await api.getSettings();
      setSettings(latest);
      setCtraderOAuthCode('');
      setCtraderTestResult({ ok: true, message: res.message });
      onNotify('cTrader Token 已换取并保存');
    } catch (err) {
      setCtraderTestResult({
        ok: false,
        message: err instanceof Error ? err.message : 'cTrader 授权码换取 Token 失败',
      });
    } finally {
      setCtraderExchanging(false);
    }
  };

  const handleRefreshCTraderToken = async () => {
    setCtraderRefreshing(true);
    setCtraderTestResult(null);
    try {
      await api.updateSettings(settings);
      const res = await api.refreshCTraderToken();
      const latest = await api.getSettings();
      setSettings(latest);
      setCtraderTestResult({ ok: true, message: res.message });
      onNotify('cTrader Token 续期成功');
    } catch (err) {
      setCtraderTestResult({
        ok: false,
        message: err instanceof Error ? err.message : 'cTrader Token 续期失败',
      });
    } finally {
      setCtraderRefreshing(false);
    }
  };

  const renderCTraderExpiryHint = () => {
    const raw = (settings.ctrader_token_expires_at || '').trim();
    if (!raw) {
      return (
        <span className="text-slate-400">
          尚未记录过期时间（通过下方 OAuth 授权或续期后将自动追踪并在到期前 5 天续期）
        </span>
      );
    }
    const ts = Date.parse(raw);
    if (Number.isNaN(ts)) {
      return <span className="text-slate-500">预计过期：{raw}</span>;
    }
    const diffDays = Math.round(((ts - Date.now()) / 86400000) * 10) / 10;
    const formatted = new Date(ts).toLocaleString('zh-CN', { hour12: false });
    if (diffDays <= 0) {
      return <span className="font-medium text-rose-600">已过期（{formatted}），请点击「立即续期 Token」或重新授权</span>;
    }
    if (diffDays <= 5) {
      return <span className="font-medium text-amber-600">即将到期（{formatted}，剩余 {diffDays} 天，后台将自动续期）</span>;
    }
    return <span className="text-emerald-600">有效至 {formatted}（剩余 {diffDays} 天，到期前 5 天自动续期）</span>;
  };

  const handleReset = async (reseed: boolean) => {
    const label = reseed ? '恢复默认示例数据' : '清空全部个人数据';
    if (!window.confirm(`确定要${label}吗？`)) return;
    await api.resetData(reseed);
    onNotify(reseed ? '已恢复初始示例数据' : '已清空所有记录，准备开始真实使用');
    onRefreshDashboard();
  };

  const handleChangeCredentials = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthFeedback(null);
    if (!currentPassword) {
      setAuthFeedback({ ok: false, message: '请输入当前密码进行验证' });
      return;
    }
    if (newPassword && newPassword.length < 6) {
      setAuthFeedback({ ok: false, message: '新密码长度至少需要 6 位' });
      return;
    }
    if (newPassword !== confirmNewPassword) {
      setAuthFeedback({ ok: false, message: '两次输入的新密码不一致' });
      return;
    }
    const trimmedUser = newUsername.trim();
    if (!trimmedUser) {
      setAuthFeedback({ ok: false, message: '用户名不能为空' });
      return;
    }

    setUpdatingAuth(true);
    try {
      const res = await api.changePassword({
        current_password: currentPassword,
        new_password: newPassword || undefined,
        new_username: trimmedUser !== currentUsername ? trimmedUser : undefined,
      });
      onUsernameChanged(res.username);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmNewPassword('');
      setActiveSessions(1);
      setAuthFeedback({
        ok: true,
        message: `账号凭证已更新（已自动下线其他 ${res.revoked_other_sessions} 个会话）`,
      });
      onNotify('登录账号/密码已更新');
    } catch (err) {
      setAuthFeedback({
        ok: false,
        message: err instanceof Error ? err.message : '更新失败',
      });
    } finally {
      setUpdatingAuth(false);
    }
  };

  const handleLogoutOthers = async () => {
    try {
      const res = await api.logoutAll(false);
      setActiveSessions(1);
      setAuthFeedback({
        ok: true,
        message: `已下线其他 ${res.revoked_count} 台设备的登录会话`,
      });
      onNotify('已清理其他设备登录态');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '操作失败');
    }
  };

  return (
    <div className="space-y-6 max-w-4xl">
    <form onSubmit={handleSave} className="space-y-6">
      {/* Global Outbound Proxy Config */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Globe className="h-5 w-5 text-cyan-600" />
            <div>
              <h3 className="text-base font-semibold text-slate-900">全局网络代理配置（独立出站网关）</h3>
              <p className="text-xs text-slate-500">
                统一管理代理服务器地址，并可按功能独立控制哪些服务走代理（例如 Gmail 邮箱、Gemini AI、cTrader OAuth、IBKR）
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleTestProxy}
            disabled={proxyTesting}
            className="rounded-xl border border-cyan-200 bg-cyan-50 px-3.5 py-1.5 text-xs font-medium text-cyan-700 hover:bg-cyan-100 disabled:opacity-50 cursor-pointer"
          >
            {proxyTesting ? '测试中...' : '测试代理连通性'}
          </button>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 items-end">
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium text-slate-700 mb-1">
              全局代理服务器地址（推荐 HTTP 代理端口，兼容全部 API 与 Gmail IMAP）
            </label>
            <input
              type="text"
              value={settings.global_proxy_url || ''}
              onChange={(e) => updateField('global_proxy_url', e.target.value)}
              placeholder="例如 http://10.0.0.x:7890（需局域网设备开启允许局域网连接）"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-mono outline-none focus:border-cyan-500"
            />
          </div>
          <div className="pb-2">
            <label className="inline-flex items-center gap-2 text-xs font-semibold text-slate-800 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.global_proxy_enabled !== '0'}
                onChange={(e) => updateField('global_proxy_enabled', e.target.checked ? '1' : '0')}
              />
              <span>启用全局代理总开关</span>
            </label>
          </div>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 space-y-2">
          <div className="text-xs font-semibold text-slate-700">按功能开启代理（勾选后对应功能将通过上方代理出站）：</div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 md:grid-cols-3 text-xs text-slate-700">
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.email_use_proxy === '1'}
                onChange={(e) => updateField('email_use_proxy', e.target.checked ? '1' : '0')}
              />
              <span>Google Gmail / 个人邮箱</span>
            </label>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.ai_use_proxy === '1'}
                onChange={(e) => updateField('ai_use_proxy', e.target.checked ? '1' : '0')}
              />
              <span>Google Gemini / AI 接口</span>
            </label>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.ctrader_oauth_use_proxy !== '0'}
                onChange={(e) => updateField('ctrader_oauth_use_proxy', e.target.checked ? '1' : '0')}
              />
              <span>cTrader OAuth Token 续期</span>
            </label>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.ibkr_use_proxy === '1'}
                onChange={(e) => updateField('ibkr_use_proxy', e.target.checked ? '1' : '0')}
              />
              <span>盈透证券 (IBKR) Flex</span>
            </label>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.bark_use_proxy === '1'}
                onChange={(e) => updateField('bark_use_proxy', e.target.checked ? '1' : '0')}
              />
              <span>Bark 消息推送</span>
            </label>
          </div>
        </div>

        {proxyTestResult && (
          <div
            className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs ${
              proxyTestResult.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
            }`}
          >
            {proxyTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span>{proxyTestResult.message}</span>
          </div>
        )}
      </div>

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
          <div className="sm:col-span-2">
            <label className="inline-flex items-center gap-2 text-xs font-medium text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.ai_use_proxy === '1'}
                onChange={(e) => updateField('ai_use_proxy', e.target.checked ? '1' : '0')}
              />
              <span>通过全局代理访问 AI 接口（国内使用 Google Gemini 推荐开启）</span>
            </label>
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

        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500 py-1">常用预设：</span>
            {[
              { label: 'Gmail (Google 邮箱)', host: 'imap.gmail.com', port: '993', suggestProxy: true },
              { label: 'QQ 邮箱', host: 'imap.qq.com', port: '993', suggestProxy: false },
              { label: '163 邮箱', host: 'imap.163.com', port: '993', suggestProxy: false },
              { label: '腾讯企业邮', host: 'imap.exmail.qq.com', port: '993', suggestProxy: false },
            ].map((preset) => (
              <button
                key={preset.host}
                type="button"
                onClick={() => {
                  updateField('email_imap_host', preset.host);
                  updateField('email_imap_port', preset.port);
                  updateField('email_use_ssl', '1');
                  if (preset.suggestProxy) {
                    updateField('email_use_proxy', '1');
                  }
                }}
                className={`rounded-lg px-2.5 py-1 cursor-pointer ${
                  settings.email_imap_host === preset.host
                    ? 'bg-sky-600 text-white'
                    : 'bg-slate-100 text-slate-700 hover:bg-sky-50 hover:text-sky-700'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <label className="inline-flex items-center gap-2 font-medium text-slate-700 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.email_use_proxy === '1'}
              onChange={(e) => updateField('email_use_proxy', e.target.checked ? '1' : '0')}
            />
            <span>通过全局代理连接邮箱（Gmail 必开）</span>
          </label>
        </div>

        {(settings.email_imap_host || '').toLowerCase().includes('gmail') && (
          <div className="rounded-xl border border-sky-200/80 bg-sky-50/70 p-3 text-[11px] text-sky-900 space-y-1">
            <div className="font-semibold">Gmail 接入 3 步指引：</div>
            <div>
              1. 前往 Google 账号安全设置开启「两步验证」，然后打开{' '}
              <span className="font-mono underline">myaccount.google.com/apppasswords</span> 创建一个「应用专用密码」（16 位字母）。
            </div>
            <div>2. 邮箱账号填完整 Gmail 地址（如 xxx@gmail.com），上方密码框填刚才生成的 16 位应用专用密码。</div>
            <div>3. 若服务器位于国内，请确保顶部「全局网络代理配置」已填可用代理并勾选「通过全局代理连接邮箱」。</div>
          </div>
        )}

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
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.bark_push_on_update !== '0'}
              onChange={(e) => updateField('bark_push_on_update', e.target.checked ? '1' : '0')}
            />
            <span>版本更新上线后自动发送 Bark 通知（同一版本只推一次）</span>
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

      {/* cTrader OpenAPI */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">cTrader OpenAPI 配置（订单拉取与实时推送）</h3>
            <p className="text-xs text-slate-500">
              替代原有的文件配置，直接在页面管理 cTrader API Key、OAuth 自动续期与自动同步推送。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleRefreshCTraderToken}
              disabled={ctraderRefreshing}
              className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 cursor-pointer"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${ctraderRefreshing ? 'animate-spin' : ''}`} />
              <span>{ctraderRefreshing ? '续期中...' : '立即续期 Token'}</span>
            </button>
            <button
              type="button"
              onClick={handleTestCTrader}
              className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 cursor-pointer"
            >
              测试连接
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-xs text-slate-700">
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ctrader_auto_sync_enabled === '1'}
              onChange={(e) => updateField('ctrader_auto_sync_enabled', e.target.checked ? '1' : '0')}
            />
            <span>开启后台自动拉取新订单</span>
          </label>
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ctrader_bark_push_enabled !== '0'}
              onChange={(e) => updateField('ctrader_bark_push_enabled', e.target.checked ? '1' : '0')}
            />
            <span>新成交订单通过 Bark 实时推送</span>
          </label>
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ctrader_market_enabled !== '0'}
              onChange={(e) => updateField('ctrader_market_enabled', e.target.checked ? '1' : '0')}
            />
            <span>开启实时 Tick 行情订阅与对外接口</span>
          </label>
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ctrader_auto_refresh_enabled !== '0'}
              onChange={(e) => updateField('ctrader_auto_refresh_enabled', e.target.checked ? '1' : '0')}
            />
            <span>Access Token 到期前自动续期</span>
          </label>
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ctrader_oauth_use_proxy !== '0'}
              onChange={(e) => updateField('ctrader_oauth_use_proxy', e.target.checked ? '1' : '0')}
            />
            <span>OAuth 换取/续期走全局代理</span>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">Client ID</label>
            <input
              type="text"
              value={settings.ctrader_client_id || ''}
              onChange={(e) => updateField('ctrader_client_id', e.target.value)}
              placeholder="cTrader OpenAPI Client ID"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">Client Secret</label>
            <input
              type="password"
              value={settings.ctrader_client_secret || ''}
              onChange={(e) => updateField('ctrader_client_secret', e.target.value)}
              placeholder="cTrader OpenAPI Client Secret"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
            />
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <label className="text-xs font-medium text-slate-600">Access Token（约 30 天有效）</label>
            </div>
            <input
              type="password"
              value={settings.ctrader_access_token || ''}
              onChange={(e) => updateField('ctrader_access_token', e.target.value)}
              placeholder="cTrader OpenAPI Access Token"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
            />
            <p className="mt-1 text-[11px]">{renderCTraderExpiryHint()}</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              Refresh Token（官方支持无限次轮换自动续期）
            </label>
            <input
              type="password"
              value={settings.ctrader_refresh_token || ''}
              onChange={(e) => updateField('ctrader_refresh_token', e.target.value)}
              placeholder="通过下方 OAuth 一键授权自动获取，也可手动填入"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
            />
            <p className="mt-1 text-[11px] text-slate-400">
              每次续期都会自动保存新 Access Token 与新 Refresh Token，遇失效错误也会自动无缝重试。
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">Account ID（账户号 / ctidTraderAccountId）</label>
            <input
              type="text"
              value={settings.ctrader_account_id || ''}
              onChange={(e) => updateField('ctrader_account_id', e.target.value)}
              placeholder="例如 1396743"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">环境</label>
              <select
                value={settings.ctrader_environment || 'demo'}
                onChange={(e) => updateField('ctrader_environment', e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-amber-500"
              >
                <option value="demo">Demo（模拟）</option>
                <option value="live">Live（实盘）</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">轮询间隔（秒）</label>
              <input
                type="number"
                min={15}
                value={settings.ctrader_sync_interval_sec || '60'}
                onChange={(e) => updateField('ctrader_sync_interval_sec', e.target.value)}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              实时 Tick 订阅品种（逗号分隔）
            </label>
            <input
              type="text"
              value={settings.ctrader_market_symbols ?? 'XAUUSD,EURUSD'}
              onChange={(e) => updateField('ctrader_market_symbols', e.target.value)}
              placeholder="XAUUSD,EURUSD"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              对外行情接口访问 Token（可选，留空则直接开放 /quotes 与 /ws）
            </label>
            <input
              type="password"
              value={settings.ctrader_market_api_token || ''}
              onChange={(e) => updateField('ctrader_market_api_token', e.target.value)}
              placeholder="留空不校验；填写后支持 Bearer / X-API-Key / ?token="
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-amber-500"
            />
          </div>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-xs font-semibold text-slate-800">
                OAuth 2.0 一键授权（首次获取 Refresh Token，之后永久自动续期）
              </h4>
              <p className="text-[11px] text-slate-500">
                cTrader 严格校验 Redirect URI 一致性：请先将下方回调地址添加到 OpenAPI 应用后台（openapi.ctrader.com/apps → Edit → Redirect URIs），或直接填写后台已有的回调地址后粘贴带 ?code= 的跳转链接。
              </p>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={ctraderOAuthScope}
                onChange={(e) => setCtraderOAuthScope(e.target.value as 'accounts' | 'trading')}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs outline-none focus:border-amber-500"
              >
                <option value="accounts">权限：accounts（行情与订单同步）</option>
                <option value="trading">权限：trading（含交易指令）</option>
              </select>
              <button
                type="button"
                onClick={handleOpenCTraderAuthorize}
                className="inline-flex items-center gap-1 rounded-xl bg-amber-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-600 cursor-pointer"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                <span>前往 cTrader 授权</span>
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-[11px] font-medium text-slate-600">
                Redirect URI（必须与 cTrader 应用后台配置完全一致）
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={settings.ctrader_oauth_redirect_uri ?? defaultCTraderRedirectUri}
                  onChange={(e) => updateField('ctrader_oauth_redirect_uri', e.target.value)}
                  placeholder={defaultCTraderRedirectUri}
                  className="flex-1 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs outline-none focus:border-amber-500"
                />
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard?.writeText(effectiveCTraderRedirectUri);
                    onNotify('已复制 Redirect URI，请粘贴到 cTrader OpenAPI 应用后台');
                  }}
                  className="inline-flex items-center gap-1 rounded-xl border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 cursor-pointer"
                  title="复制回调地址"
                >
                  <Copy className="h-3.5 w-3.5" />
                  <span>复制</span>
                </button>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] font-medium text-slate-600">
                手动粘贴回调 URL 或 code（若授权后未自动回填可使用）
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={ctraderOAuthCode}
                  onChange={(e) => setCtraderOAuthCode(e.target.value)}
                  placeholder="粘贴 ?code=... 完整链接或 code 值"
                  className="flex-1 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs outline-none focus:border-amber-500"
                />
                <button
                  type="button"
                  onClick={handleExchangeCTraderCode}
                  disabled={ctraderExchanging}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50 cursor-pointer"
                >
                  {ctraderExchanging ? '换取中...' : '换取 Token'}
                </button>
              </div>
            </div>
          </div>
        </div>

        {ctraderTestResult && (
          <div
            className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs ${
              ctraderTestResult.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
            }`}
          >
            {ctraderTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span>{ctraderTestResult.message}</span>
          </div>
        )}
      </div>

      {/* IBKR Flex Web Service */}
      <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">盈透证券 (IBKR) Flex Web Service 配置（每日汇总）</h3>
            <p className="text-xs text-slate-500">
              免挂机与二次验证，通过官方 Flex Token + Query ID 定时拉取每日净值、持仓、成交与分红报表。
            </p>
          </div>
          <button
            type="button"
            onClick={handleTestIbkr}
            className="rounded-xl border border-blue-200 bg-blue-50 px-3.5 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 cursor-pointer"
          >
            测试 Flex 拉取
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-xs text-slate-700">
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ibkr_auto_sync_enabled === '1'}
              onChange={(e) => updateField('ibkr_auto_sync_enabled', e.target.checked ? '1' : '0')}
            />
            <span>开启后台定时自动拉取 Flex 报表</span>
          </label>
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ibkr_bark_daily_summary === '1'}
              onChange={(e) => updateField('ibkr_bark_daily_summary', e.target.checked ? '1' : '0')}
            />
            <span>出现新报告日时通过 Bark 发送每日净值汇总</span>
          </label>
          <label className="inline-flex items-center gap-2 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.ibkr_use_proxy === '1'}
              onChange={(e) => updateField('ibkr_use_proxy', e.target.checked ? '1' : '0')}
            />
            <span>使用全局代理拉取 Flex</span>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">Flex Token</label>
            <input
              type="password"
              value={settings.ibkr_flex_token || ''}
              onChange={(e) => updateField('ibkr_flex_token', e.target.value)}
              placeholder="IBKR Client Portal 中生成的 Flex Web Service Token"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">Activity Flex Query ID</label>
            <input
              type="text"
              value={settings.ibkr_flex_query_id || ''}
              onChange={(e) => updateField('ibkr_flex_query_id', e.target.value)}
              placeholder="例如 1048293"
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">自动拉取间隔（分钟）</label>
            <input
              type="number"
              min={15}
              value={settings.ibkr_sync_interval_min || '360'}
              onChange={(e) => updateField('ibkr_sync_interval_min', e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-500"
            />
          </div>
        </div>

        {ibkrTestResult && (
          <div
            className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs ${
              ibkrTestResult.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
            }`}
          >
            {ibkrTestResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span>{ibkrTestResult.message}</span>
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

    {/* Account & Public Security */}
    <form onSubmit={handleChangeCredentials} className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-indigo-600" />
          <div>
            <h3 className="text-base font-semibold text-slate-900">登录账号与公网访问安全</h3>
            <p className="text-xs text-slate-500">
              当前账号 <span className="font-medium text-slate-700">{currentUsername}</span> · 活跃登录设备/会话：{activeSessions} 个（密码错误 5 次自动锁定 5 分钟）
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleLogoutOthers}
          className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 px-3.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
        >
          <LogOut className="h-3.5 w-3.5" />
          下线其他设备
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">登录用户名</label>
          <input
            type="text"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">当前密码（验证身份）*</label>
          <input
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            placeholder="请输入当前登录密码"
            className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">新密码（不改密码可留空）</label>
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="至少 6 位"
            className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">确认新密码</label>
          <input
            type="password"
            autoComplete="new-password"
            value={confirmNewPassword}
            onChange={(e) => setConfirmNewPassword(e.target.value)}
            placeholder="再次输入新密码"
            className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          />
        </div>
      </div>

      {authFeedback && (
        <div
          className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs ${
            authFeedback.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
          }`}
        >
          {authFeedback.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
          <span>{authFeedback.message}</span>
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={updatingAuth}
          className="inline-flex items-center gap-1.5 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-2 text-xs font-medium text-indigo-700 hover:bg-indigo-100 cursor-pointer"
        >
          <KeyRound className="h-3.5 w-3.5" />
          {updatingAuth ? '更新中...' : '修改用户名 / 密码'}
        </button>
      </div>
    </form>
    </div>
  );
};
