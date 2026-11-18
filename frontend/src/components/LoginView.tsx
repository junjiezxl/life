import React, { useState } from 'react';
import { Eye, EyeOff, Lock, ShieldCheck, Sparkles, User } from 'lucide-react';
import { api } from '../api';

interface Props {
  setupRequired: boolean;
  notice?: string | null;
  onAuthenticated: (username: string) => void;
}

export const LoginView: React.FC<Props> = ({ setupRequired, notice, onAuthenticated }) => {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const cleanName = username.trim();
    if (!cleanName) {
      setError('请输入用户名');
      return;
    }

    if (setupRequired) {
      if (password.length < 6) {
        setError('初始密码长度至少为 6 位');
        return;
      }
      if (password !== confirmPassword) {
        setError('两次输入的密码不一致');
        return;
      }
    } else if (!password) {
      setError('请输入密码');
      return;
    }

    setSubmitting(true);
    try {
      const res = setupRequired
        ? await api.setupAuth(cleanName, password)
        : await api.login(cleanName, password, rememberMe);
      onAuthenticated(res.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-3xl border border-slate-200/80 bg-white p-8 shadow-sm">
        <div className="flex items-center gap-3 mb-6">
          <div className="h-11 w-11 rounded-2xl bg-gradient-to-br from-indigo-600 to-violet-600 text-white flex items-center justify-center shadow-xs">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <div className="text-lg font-bold text-slate-900">LifeHub</div>
            <div className="text-xs text-slate-500">个人提醒 · 笔记 · 财务中心</div>
          </div>
        </div>

        <div className="mb-5">
          <h1 className="text-base font-semibold text-slate-900 flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-indigo-600" />
            {setupRequired ? '初始化管理员账号' : '登录 LifeHub'}
          </h1>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">
            {setupRequired
              ? '首次开启访问保护：请设置管理员用户名与登录密码（至少 6 位）。设置完成后所有个人数据与设置接口均需登录后访问。'
              : '请输入你的账号密码以访问个人提醒、思源笔记与财务数据。'}
          </p>
        </div>

        {notice && !error && (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-800">
            {notice}
          </div>
        )}

        {error && (
          <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">用户名</label>
            <div className="relative">
              <User className="h-4 w-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                className="w-full rounded-xl border border-slate-200 pl-9 pr-3 py-2.5 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              {setupRequired ? '设置密码（至少 6 位）' : '密码'}
            </label>
            <div className="relative">
              <Lock className="h-4 w-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type={showPassword ? 'text' : 'password'}
                autoComplete={setupRequired ? 'new-password' : 'current-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={setupRequired ? '请输入至少 6 位登录密码' : '请输入登录密码'}
                className="w-full rounded-xl border border-slate-200 pl-9 pr-10 py-2.5 text-sm focus:border-indigo-500 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 text-slate-400 hover:text-slate-600 cursor-pointer"
                title={showPassword ? '隐藏密码' : '显示密码'}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>

          {setupRequired ? (
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1.5">确认密码</label>
              <div className="relative">
                <Lock className="h-4 w-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="请再次输入密码"
                  className="w-full rounded-xl border border-slate-200 pl-9 pr-3 py-2.5 text-sm focus:border-indigo-500 focus:outline-none"
                />
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between text-xs text-slate-600">
              <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                />
                <span>30 天内保持登录状态</span>
              </label>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-xl bg-indigo-600 py-2.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60 transition cursor-pointer"
          >
            {submitting
              ? setupRequired
                ? '正在初始化...'
                : '正在登录...'
              : setupRequired
                ? '创建账号并进入系统'
                : '登 录'}
          </button>
        </form>
      </div>
    </div>
  );
};
