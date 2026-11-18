import React, { useEffect, useState } from 'react';
import {
  Bell,
  FileText,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  Settings,
  Smartphone,
  Sparkles,
  User,
  Wallet,
} from 'lucide-react';
import { api, setUnauthorizedHandler } from './api';
import { DashboardView } from './components/DashboardView';
import { FinanceSettlementView } from './components/FinanceSettlementView';
import { LoginView } from './components/LoginView';
import { NotesExtractorView } from './components/NotesExtractorView';
import { PhoneNumbersView } from './components/PhoneNumbersView';
import { RemindersChatView } from './components/RemindersChatView';
import { SettingsView } from './components/SettingsView';
import type { DashboardData, TabKey } from './types';

const NAV_ITEMS: Array<{ key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { key: 'dashboard', label: '展示面板', icon: LayoutDashboard },
  { key: 'reminders', label: '提醒 & AI 对话', icon: Bell },
  { key: 'notes', label: '思源笔记提取', icon: FileText },
  { key: 'phones', label: '手机号码管理', icon: Smartphone },
  { key: 'finance', label: '个人财务 & 结账', icon: Wallet },
  { key: 'settings', label: '系统设置', icon: Settings },
];

export function App() {
  const [activeTab, setActiveTab] = useState<TabKey>('dashboard');
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<string | null>(null);

  const [authChecking, setAuthChecking] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [setupRequired, setSetupRequired] = useState(false);
  const [username, setUsername] = useState<string>('admin');
  const [authNotice, setAuthNotice] = useState<string | null>(null);

  const notify = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => {
      setToast((curr) => (curr === msg ? null : curr));
    }, 4000);
  };

  const loadDashboard = async () => {
    try {
      const data = await api.getDashboard();
      setDashboard(data);
    } catch (err) {
      notify(err instanceof Error ? err.message : '连接后端服务失败');
    } finally {
      setLoading(false);
    }
  };

  const checkAuthAndLoad = async () => {
    setAuthChecking(true);
    try {
      const status = await api.getAuthStatus();
      setSetupRequired(status.setup_required);
      setAuthenticated(status.authenticated);
      if (status.username) setUsername(status.username);
      if (status.authenticated) {
        await loadDashboard();
      } else {
        setLoading(false);
      }
    } catch (err) {
      setAuthNotice(err instanceof Error ? err.message : '连接后端服务失败');
      setLoading(false);
    } finally {
      setAuthChecking(false);
    }
  };

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAuthenticated(false);
      setSetupRequired(false);
      setDashboard(null);
      setAuthNotice('登录状态已失效，请重新登录');
    });
    checkAuthAndLoad();
    return () => setUnauthorizedHandler(null);
  }, []);

  const handleAuthenticated = async (loggedInUser: string) => {
    setUsername(loggedInUser);
    setAuthenticated(true);
    setSetupRequired(false);
    setAuthNotice(null);
    setLoading(true);
    await loadDashboard();
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch {
      // ignore
    }
    setAuthenticated(false);
    setDashboard(null);
    setAuthNotice('已安全退出登录');
  };

  if (authChecking) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4 text-sm text-slate-500">
        正在检查登录状态...
      </div>
    );
  }

  if (!authenticated) {
    return (
      <LoginView
        setupRequired={setupRequired}
        notice={authNotice}
        onAuthenticated={handleAuthenticated}
      />
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col lg:flex-row">
      {/* Sidebar */}
      <aside className="w-full lg:w-64 bg-white border-b lg:border-b-0 lg:border-r border-slate-200/80 flex flex-col shrink-0">
        <div className="px-5 py-4 flex items-center justify-between lg:justify-start gap-3 border-b border-slate-100">
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-indigo-600 to-violet-600 text-white flex items-center justify-center shadow-xs">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <div className="font-bold text-slate-900 leading-tight">LifeHub</div>
              <div className="text-[11px] text-slate-400">个人提醒 · 笔记 · 财务中心</div>
            </div>
          </div>
          <div className="flex items-center gap-1 lg:hidden">
            <button
              onClick={loadDashboard}
              title="刷新数据"
              className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 cursor-pointer"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
            <button
              onClick={handleLogout}
              title="退出登录"
              className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-rose-600 cursor-pointer"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>

        <nav className="flex lg:flex-col gap-1 p-3 overflow-x-auto">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = activeTab === item.key;
            return (
              <button
                key={item.key}
                onClick={() => setActiveTab(item.key)}
                className={`flex items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-sm font-medium whitespace-nowrap transition cursor-pointer ${
                  active
                    ? 'bg-indigo-50 text-indigo-700'
                    : 'text-slate-600 hover:bg-slate-100/80 hover:text-slate-900'
                }`}
              >
                <Icon className={`h-4 w-4 ${active ? 'text-indigo-600' : 'text-slate-400'}`} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="hidden lg:block mt-auto p-4 border-t border-slate-100 space-y-2.5 text-[11px] text-slate-500">
          {dashboard && (
            <>
              <div className="flex items-center justify-between">
                <span>AI 引擎</span>
                <span className="rounded-md bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
                  {dashboard.config_status.ai_provider === 'local_fallback'
                    ? '本地智能解析'
                    : dashboard.config_status.ai_provider}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span>思源接入</span>
                <span className="rounded-md bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
                  {dashboard.config_status.siyuan_mode}
                </span>
              </div>
            </>
          )}
          <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
            <span className="inline-flex items-center gap-1.5 font-medium text-slate-700">
              <User className="h-3.5 w-3.5 text-indigo-600" />
              {username}
            </span>
            <button
              onClick={handleLogout}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-slate-500 hover:bg-rose-50 hover:text-rose-600 transition cursor-pointer"
            >
              <LogOut className="h-3.5 w-3.5" />
              退出
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 min-w-0 p-4 sm:p-6 lg:p-8 max-w-[1440px] w-full mx-auto">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900">
              {NAV_ITEMS.find((i) => i.key === activeTab)?.label}
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {new Date().toLocaleDateString('zh-CN', {
                year: 'numeric',
                month: 'long',
                day: 'numeric',
                weekday: 'long',
              })}
            </p>
          </div>
          <button
            onClick={loadDashboard}
            className="hidden sm:inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 cursor-pointer"
          >
            <RefreshCw className="h-3.5 w-3.5" /> 刷新面板
          </button>
        </header>

        {toast && (
          <div className="mb-5 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-xs font-medium text-indigo-800 shadow-xs">
            {toast}
          </div>
        )}

        {loading || !dashboard ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-12 text-center text-sm text-slate-400">
            正在加载个人面板数据...
          </div>
        ) : (
          <>
            {activeTab === 'dashboard' && (
              <DashboardView
                data={dashboard}
                onRefresh={loadDashboard}
                onNavigate={setActiveTab}
                onNotify={notify}
              />
            )}
            {activeTab === 'reminders' && (
              <RemindersChatView onRefreshDashboard={loadDashboard} onNotify={notify} />
            )}
            {activeTab === 'notes' && (
              <NotesExtractorView onRefreshDashboard={loadDashboard} onNotify={notify} />
            )}
            {activeTab === 'phones' && (
              <PhoneNumbersView onRefreshDashboard={loadDashboard} onNotify={notify} />
            )}
            {activeTab === 'finance' && (
              <FinanceSettlementView onRefreshDashboard={loadDashboard} onNotify={notify} />
            )}
            {activeTab === 'settings' && (
              <SettingsView
                currentUsername={username}
                onUsernameChanged={setUsername}
                onRefreshDashboard={loadDashboard}
                onNotify={notify}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}

export default App;
