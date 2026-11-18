import React, { useEffect, useState } from 'react';
import {
  BellRing,
  CheckCircle2,
  Coins,
  Copy,
  Edit3,
  Eye,
  EyeOff,
  Globe,
  Plus,
  ShieldCheck,
  Smartphone,
  Trash2,
} from 'lucide-react';
import { api } from '../api';
import type { Account, PhoneNumber, PhoneNumbersResponse } from '../types';
import { formatCurrency, formatMoney } from '../utils/format';

interface Props {
  onRefreshDashboard: () => void;
  onNotify: (msg: string) => void;
}

const REGION_PRESETS = [
  {
    region: '中国大陆 (+86)',
    currency: 'CNY',
    rate: '1.0',
    carrier: '',
    cycleDays: '0',
    remindDays: '7',
    rule: '正常月租扣费即可，无需额外保号动作',
  },
  {
    region: '菲律宾 (+63)',
    currency: 'PHP',
    rate: '0.125',
    carrier: 'Globe / Smart',
    cycleDays: '365',
    remindDays: '15',
    rule: '每 365 天至少充值一次（如 ₱10~₱50）防止 SIM 卡过期销号',
  },
  {
    region: '中国香港 (+852)',
    currency: 'HKD',
    rate: '0.93',
    carrier: 'ClubSIM / 3HK',
    cycleDays: '365',
    remindDays: '15',
    rule: '每 365 天充值或购买一次短信/流量包续期保号',
  },
  {
    region: '中国台湾 (+886)',
    currency: 'TWD',
    rate: '0.22',
    carrier: '中华电信 / 台湾大哥大',
    cycleDays: '180',
    remindDays: '15',
    rule: '预付卡需每 180 天充值一次延长有效期',
  },
  {
    region: '美国 (+1)',
    currency: 'USD',
    rate: '7.20',
    carrier: 'Tello / Ultra Mobile / Google Voice',
    cycleDays: '90',
    remindDays: '7',
    rule: '按周期充值或发送短信/通话保持活跃防回收',
  },
  {
    region: '英国 (+44)',
    currency: 'GBP',
    rate: '9.15',
    carrier: 'giffgaff / Three',
    cycleDays: '180',
    remindDays: '15',
    rule: '每 180 天至少发一条短信、通话或充值一次防停卡',
  },
  {
    region: '日本 (+81)',
    currency: 'JPY',
    rate: '0.047',
    carrier: 'Rakuten / Docomo',
    cycleDays: '0',
    remindDays: '7',
    rule: '按月套餐自动扣费',
  },
  {
    region: '新加坡 (+65)',
    currency: 'SGD',
    rate: '5.40',
    carrier: 'Singtel / StarHub',
    cycleDays: '90',
    remindDays: '10',
    rule: '预付卡需在有效期内充值续期',
  },
  {
    region: '欧洲 (+33/+49)',
    currency: 'EUR',
    rate: '7.80',
    carrier: '',
    cycleDays: '180',
    remindDays: '15',
    rule: '按运营商有效期定期充值保号',
  },
];

const SIM_TYPE_LABELS: Record<string, string> = {
  physical: '实体 SIM 卡',
  esim: 'eSIM',
  virtual: '虚拟号 (VoIP)',
};

const STATUS_META: Record<string, { label: string; cls: string }> = {
  active: { label: '主力使用中', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  standby: { label: '保号待机', cls: 'bg-sky-50 text-sky-700 border-sky-200' },
  paused: { label: '已停机', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  cancelled: { label: '已注销', cls: 'bg-slate-100 text-slate-500 border-slate-200' },
};

const EMPTY_FORM = {
  label: '',
  phone_number: '',
  country_region: '中国大陆 (+86)',
  carrier: '',
  sim_type: 'physical' as PhoneNumber['sim_type'],
  device_slot: '',
  monthly_fee: '0',
  currency: 'CNY',
  cny_rate: '1.0',
  balance: '0',
  extra_comm_cost: '0',
  total_comm_spent: '0',
  billing_day: '1',
  keep_alive_rule: '',
  keep_alive_cycle_days: '0',
  remind_before_days: '7',
  last_keep_alive_date: '',
  next_keep_alive_date: '',
  expiry_date: '',
  bound_services: '',
  status: 'active' as PhoneNumber['status'],
  notes: '',
};

function maskNumber(num: string, masked: boolean): string {
  if (!masked) return num;
  if (num.length <= 6) return num;
  return `${num.slice(0, 3)}****${num.slice(-4)}`;
}

export const PhoneNumbersView: React.FC<Props> = ({ onRefreshDashboard, onNotify }) => {
  const [data, setData] = useState<PhoneNumbersResponse>({
    items: [],
    summary: {
      total_count: 0,
      active_count: 0,
      monthly_cny_total: 0,
      annual_cny_total: 0,
      extra_cny_total: 0,
      urgent_keep_alive_count: 0,
    },
  });
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [maskNumbers, setMaskNumbers] = useState(false);
  const [regionFilter, setRegionFilter] = useState<'all' | 'cn' | 'overseas' | 'urgent'>('all');

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);

  // Quick cost/topup drawer per phone
  const [costPhone, setCostPhone] = useState<PhoneNumber | null>(null);
  const [costKind, setCostKind] = useState<'topup' | 'monthly_fee' | 'extra_cost'>('topup');
  const [costAmount, setCostAmount] = useState('');
  const [costDesc, setCostDesc] = useState('');
  const [costSyncFinance, setCostSyncFinance] = useState(true);
  const [costMarkKeepAlive, setCostMarkKeepAlive] = useState(true);
  const [costAccountId, setCostAccountId] = useState('');

  const loadAll = async () => {
    try {
      const [phoneRes, accRes] = await Promise.all([api.listPhoneNumbers(), api.listAccounts()]);
      setData(phoneRes);
      setAccounts(accRes.accounts);
      if (!costAccountId && accRes.accounts.length > 0) {
        setCostAccountId(String(accRes.accounts[0].id));
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '加载手机号码数据失败');
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const openCreateForm = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  };

  const openEditForm = (item: PhoneNumber) => {
    setEditingId(item.id);
    setForm({
      label: item.label,
      phone_number: item.phone_number,
      country_region: item.country_region,
      carrier: item.carrier,
      sim_type: item.sim_type,
      device_slot: item.device_slot,
      monthly_fee: String(item.monthly_fee),
      currency: item.currency,
      cny_rate: String(item.cny_rate),
      balance: String(item.balance),
      extra_comm_cost: String(item.extra_comm_cost),
      total_comm_spent: String(item.total_comm_spent),
      billing_day: String(item.billing_day),
      keep_alive_rule: item.keep_alive_rule,
      keep_alive_cycle_days: String(item.keep_alive_cycle_days),
      remind_before_days: String(item.remind_before_days ?? 7),
      last_keep_alive_date: item.last_keep_alive_date,
      next_keep_alive_date: item.next_keep_alive_date,
      expiry_date: item.expiry_date,
      bound_services: item.bound_services,
      status: item.status,
      notes: item.notes,
    });
    setShowForm(true);
  };

  const handleRegionChange = (region: string) => {
    const preset = REGION_PRESETS.find((r) => r.region === region);
    setForm((prev) => ({
      ...prev,
      country_region: region,
      currency: preset ? preset.currency : prev.currency,
      cny_rate: preset ? preset.rate : prev.cny_rate,
      carrier: !editingId && preset?.carrier && !prev.carrier ? preset.carrier : prev.carrier,
      keep_alive_cycle_days: !editingId && preset ? preset.cycleDays : prev.keep_alive_cycle_days,
      remind_before_days: !editingId && preset ? preset.remindDays : prev.remind_before_days,
      keep_alive_rule: !editingId && preset && !prev.keep_alive_rule ? preset.rule : prev.keep_alive_rule,
    }));
  };

  const handleSaveForm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.label.trim() || !form.phone_number.trim()) return;
    const payload: Partial<PhoneNumber> = {
      label: form.label.trim(),
      phone_number: form.phone_number.trim(),
      country_region: form.country_region.trim(),
      carrier: form.carrier.trim(),
      sim_type: form.sim_type,
      device_slot: form.device_slot.trim(),
      monthly_fee: Number(form.monthly_fee || 0),
      currency: form.currency.trim().toUpperCase() || 'CNY',
      cny_rate: Number(form.cny_rate || 1),
      balance: Number(form.balance || 0),
      extra_comm_cost: Number(form.extra_comm_cost || 0),
      total_comm_spent: Number(form.total_comm_spent || 0),
      billing_day: Math.min(Math.max(Number(form.billing_day || 1), 1), 31),
      keep_alive_rule: form.keep_alive_rule.trim(),
      keep_alive_cycle_days: Number(form.keep_alive_cycle_days || 0),
      remind_before_days: Number(form.remind_before_days || 7),
      last_keep_alive_date: form.last_keep_alive_date,
      next_keep_alive_date: form.next_keep_alive_date,
      expiry_date: form.expiry_date,
      bound_services: form.bound_services.trim(),
      status: form.status,
      notes: form.notes.trim(),
    };
    try {
      if (editingId) {
        await api.updatePhoneNumber(editingId, payload);
        onNotify('手机号码信息已更新');
      } else {
        await api.createPhoneNumber(payload);
        onNotify('已添加手机号码');
      }
      setShowForm(false);
      await loadAll();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '保存失败');
    }
  };

  const handleKeepAlive = async (item: PhoneNumber) => {
    const updated = await api.keepAlivePhone(item.id);
    onNotify(`「${updated.label}」保号打卡成功！下次截止：${updated.next_keep_alive_date || '无固定周期'}`);
    await loadAll();
    onRefreshDashboard();
  };

  const handleSyncReminders = async () => {
    const res = await api.syncPhoneReminders();
    onNotify(res.created_count > 0 ? `已按各号码保号规则生成 ${res.created_count} 条提前提醒` : '当前各号码均未进入提前提醒期');
    onRefreshDashboard();
  };

  const handleRecordCost = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!costPhone || !costAmount) return;
    try {
      const shouldKeepAlive = costKind === 'topup' && costMarkKeepAlive && costPhone.keep_alive_cycle_days > 0;
      await api.recordPhoneCost(costPhone.id, {
        kind: costKind,
        amount: Number(costAmount),
        description: costDesc,
        sync_to_finance: costSyncFinance,
        account_id: costAccountId ? Number(costAccountId) : null,
        mark_keep_alive: shouldKeepAlive,
      });
      onNotify(
        shouldKeepAlive
          ? '已记录充值并自动完成本次保号打卡，下期保号日期已顺延！'
          : costSyncFinance
            ? '已更新通信费用并同步写入个人财务流水'
            : '已更新该号码话费/通信费用',
      );
      setCostPhone(null);
      setCostAmount('');
      setCostDesc('');
      await loadAll();
      onRefreshDashboard();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : '记录失败');
    }
  };

  const filteredItems = data.items.filter((item) => {
    if (regionFilter === 'cn') return item.country_region.includes('大陆');
    if (regionFilter === 'overseas') return !item.country_region.includes('大陆');
    if (regionFilter === 'urgent')
      return item.keep_alive_days_left !== null && item.keep_alive_days_left <= (item.remind_before_days ?? 7);
    return true;
  });

  return (
    <div className="space-y-6">
      {/* KPI Summary */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <Smartphone className="h-3.5 w-3.5 text-indigo-600" /> 在管号码数量
          </div>
          <div className="mt-2 text-2xl font-bold text-slate-900">{data.summary.active_count}</div>
          <div className="mt-1 text-xs text-slate-400">共 {data.summary.total_count} 个号码记录</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <Coins className="h-3.5 w-3.5 text-emerald-600" /> 折合人民币月费合计
          </div>
          <div className="mt-2 text-2xl font-bold text-slate-900">{formatMoney(data.summary.monthly_cny_total)}</div>
          <div className="mt-1 text-xs text-slate-400">年化基础套餐 {formatMoney(data.summary.annual_cny_total)}</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <Globe className="h-3.5 w-3.5 text-sky-600" /> 额外通信费累计
          </div>
          <div className="mt-2 text-2xl font-bold text-slate-900">{formatMoney(data.summary.extra_cny_total)}</div>
          <div className="mt-1 text-xs text-slate-400">漫游 / 短信 / 额外流量费用</div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
          <div className="text-xs text-slate-500 flex items-center gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 text-orange-500" /> 需提前提醒保号
          </div>
          <div className={`mt-2 text-2xl font-bold ${data.summary.urgent_keep_alive_count > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
            {data.summary.urgent_keep_alive_count}
          </div>
          <div className="mt-1 text-xs text-slate-400">按各号码提前天数自动生成提醒</div>
        </div>
      </div>

      {/* Action Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-xl bg-slate-100 p-1 text-xs">
            {[
              { key: 'all', label: '全部号码' },
              { key: 'cn', label: '中国大陆' },
              { key: 'overseas', label: '港澳台及海外' },
              { key: 'urgent', label: '近期需保号' },
            ].map((f) => (
              <button
                key={f.key}
                onClick={() => setRegionFilter(f.key as typeof regionFilter)}
                className={`rounded-lg px-3 py-1.5 font-medium cursor-pointer transition ${
                  regionFilter === f.key ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setMaskNumbers((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 cursor-pointer"
          >
            {maskNumbers ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
            {maskNumbers ? '显示完整号码' : '隐藏中间4位'}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleSyncReminders}
            className="inline-flex items-center gap-1.5 rounded-xl border border-orange-200 bg-orange-50 px-3.5 py-2 text-xs font-medium text-orange-700 hover:bg-orange-100 cursor-pointer"
          >
            <BellRing className="h-3.5 w-3.5" /> 同步保号提醒
          </button>
          <button
            onClick={openCreateForm}
            className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 cursor-pointer"
          >
            <Plus className="h-4 w-4" /> 新增手机号码
          </button>
        </div>
      </div>

      {/* Add / Edit Form */}
      {showForm && (
        <form onSubmit={handleSaveForm} className="rounded-2xl border border-indigo-200 bg-white p-5 shadow-xs space-y-4">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <h3 className="text-base font-semibold text-slate-900">
              {editingId ? '编辑手机号码信息' : '新增手机号码'}
            </h3>
            <button type="button" onClick={() => setShowForm(false)} className="text-xs text-slate-400 hover:text-slate-700">
              收起
            </button>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">号码名称/备注 *</label>
              <input
                type="text"
                required
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="例如：主力移动 / 港卡 ClubSIM"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">手机号码 *</label>
              <input
                type="text"
                required
                value={form.phone_number}
                onChange={(e) => setForm({ ...form, phone_number: e.target.value })}
                placeholder="+86 138... / +852 ..."
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-mono"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">所属国家 / 地区</label>
              <select
                value={REGION_PRESETS.some((p) => p.region === form.country_region) ? form.country_region : '__custom'}
                onChange={(e) => {
                  if (e.target.value !== '__custom') handleRegionChange(e.target.value);
                }}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              >
                {REGION_PRESETS.map((p) => (
                  <option key={p.region} value={p.region}>{p.region}</option>
                ))}
                <option value="__custom">自定义输入...</option>
              </select>
              {!REGION_PRESETS.some((p) => p.region === form.country_region) && (
                <input
                  type="text"
                  value={form.country_region}
                  onChange={(e) => setForm({ ...form, country_region: e.target.value })}
                  placeholder="输入国家/地区"
                  className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-1.5 text-xs"
                />
              )}
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">运营商</label>
              <input
                type="text"
                value={form.carrier}
                onChange={(e) => setForm({ ...form, carrier: e.target.value })}
                placeholder="中国移动 / giffgaff / Tello"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>

            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">卡形态</label>
              <select
                value={form.sim_type}
                onChange={(e) => setForm({ ...form, sim_type: e.target.value as PhoneNumber['sim_type'] })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              >
                <option value="physical">实体 SIM 卡</option>
                <option value="esim">eSIM</option>
                <option value="virtual">虚拟号 (Google Voice 等)</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">所在设备 / 卡槽</label>
              <input
                type="text"
                value={form.device_slot}
                onChange={(e) => setForm({ ...form, device_slot: e.target.value })}
                placeholder="iPhone 卡槽1 / 备用机 / 5ber eSIM"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">使用状态</label>
              <select
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value as PhoneNumber['status'] })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              >
                <option value="active">主力使用中</option>
                <option value="standby">保号待机</option>
                <option value="paused">已停机</option>
                <option value="cancelled">已注销</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">每月扣费日 (1-31)</label>
              <input
                type="number"
                min={1}
                max={31}
                value={form.billing_day}
                onChange={(e) => setForm({ ...form, billing_day: e.target.value })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>

            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">月费（原币种）</label>
              <div className="flex gap-1.5">
                <input
                  type="number"
                  step="0.01"
                  value={form.monthly_fee}
                  onChange={(e) => setForm({ ...form, monthly_fee: e.target.value })}
                  className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
                />
                <input
                  type="text"
                  value={form.currency}
                  onChange={(e) => setForm({ ...form, currency: e.target.value })}
                  className="w-16 rounded-xl border border-slate-200 px-2 py-2 text-xs text-center uppercase"
                />
              </div>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">折合人民币汇率</label>
              <input
                type="number"
                step="0.001"
                value={form.cny_rate}
                onChange={(e) => setForm({ ...form, cny_rate: e.target.value })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">当前话费余额</label>
              <input
                type="number"
                step="0.01"
                value={form.balance}
                onChange={(e) => setForm({ ...form, balance: e.target.value })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">额外通信费用（漫游/短信）</label>
              <input
                type="number"
                step="0.01"
                value={form.extra_comm_cost}
                onChange={(e) => setForm({ ...form, extra_comm_cost: e.target.value })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>

            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">保号周期（天，0=无需）</label>
              <input
                type="number"
                min={0}
                value={form.keep_alive_cycle_days}
                onChange={(e) => setForm({ ...form, keep_alive_cycle_days: e.target.value })}
                placeholder="如 90 / 180 / 365"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">提前提醒天数（到点自动建提醒）</label>
              <input
                type="number"
                min={0}
                max={180}
                value={form.remind_before_days}
                onChange={(e) => setForm({ ...form, remind_before_days: e.target.value })}
                placeholder="如 7 / 15 / 30"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">上次充值/保号日期</label>
              <input
                type="date"
                value={form.last_keep_alive_date}
                onChange={(e) => setForm({ ...form, last_keep_alive_date: e.target.value })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">下次保号截止（留空自动算）</label>
              <input
                type="date"
                value={form.next_keep_alive_date}
                onChange={(e) => setForm({ ...form, next_keep_alive_date: e.target.value })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">套餐/号码有效期</label>
              <input
                type="date"
                value={form.expiry_date}
                onChange={(e) => setForm({ ...form, expiry_date: e.target.value })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">保号规则详细说明</label>
              <input
                type="text"
                value={form.keep_alive_rule}
                onChange={(e) => setForm({ ...form, keep_alive_rule: e.target.value })}
                placeholder="例如：每 180 天内发送一条短信或充值 £10 保号"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">绑定重要账号/用途（逗号分隔）</label>
              <input
                type="text"
                value={form.bound_services}
                onChange={(e) => setForm({ ...form, bound_services: e.target.value })}
                placeholder="例如：微信、招商银行、Apple ID、ChatGPT"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
              />
            </div>
          </div>

          <div>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">其他备注（漫游资费/客服电话/套餐内容等）</label>
            <input
              type="text"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs"
            />
          </div>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-xl px-4 py-2 text-xs text-slate-600 hover:bg-slate-100"
            >
              取消
            </button>
            <button
              type="submit"
              className="rounded-xl bg-indigo-600 px-5 py-2 text-xs font-medium text-white hover:bg-indigo-700 cursor-pointer"
            >
              {editingId ? '保存修改' : '确认添加'}
            </button>
          </div>
        </form>
      )}

      {/* Record Cost / Topup Inline Modal */}
      {costPhone && (
        <form onSubmit={handleRecordCost} className="rounded-2xl border border-emerald-200 bg-emerald-50/40 p-5 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-900">
              记录通信费用 / 充值 — {costPhone.label} ({costPhone.currency})
            </h3>
            <button type="button" onClick={() => setCostPhone(null)} className="text-xs text-slate-500">关闭</button>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
            <select
              value={costKind}
              onChange={(e) => setCostKind(e.target.value as typeof costKind)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
            >
              <option value="topup">话费充值（增加余额）</option>
              <option value="monthly_fee">套餐月租扣费（扣减余额）</option>
              <option value="extra_cost">额外通信费（漫游/短信/超额）</option>
            </select>
            <input
              type="number"
              step="0.01"
              required
              value={costAmount}
              onChange={(e) => setCostAmount(e.target.value)}
              placeholder={`金额（${costPhone.currency}）`}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
            />
            <input
              type="text"
              value={costDesc}
              onChange={(e) => setCostDesc(e.target.value)}
              placeholder="备注说明（可选）"
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
            />
            <select
              value={costAccountId}
              onChange={(e) => setCostAccountId(e.target.value)}
              disabled={!costSyncFinance}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs disabled:opacity-50"
            >
              <option value="">不绑定财务账户</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>扣款账户：{a.name}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-4">
              <label className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={costSyncFinance}
                  onChange={(e) => setCostSyncFinance(e.target.checked)}
                />
                <span>同步折算为人民币记入「个人财务 & 结账」流水</span>
              </label>
              {costKind === 'topup' && costPhone.keep_alive_cycle_days > 0 && (
                <label className="inline-flex items-center gap-2 text-xs text-teal-700 font-medium cursor-pointer">
                  <input
                    type="checkbox"
                    checked={costMarkKeepAlive}
                    onChange={(e) => setCostMarkKeepAlive(e.target.checked)}
                  />
                  <span>本次充值视为完成保号（自动顺延 {costPhone.keep_alive_cycle_days} 天）</span>
                </label>
              )}
            </div>
            <button
              type="submit"
              className="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-medium text-white hover:bg-emerald-700 cursor-pointer"
            >
              确认入账
            </button>
          </div>
        </form>
      )}

      {/* Cards Grid */}
      {filteredItems.length === 0 ? (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-14 text-center text-sm text-slate-400">
          暂无手机号码记录，点击右上角「新增手机号码」开始管理你的国内外号码与保号规则。
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {filteredItems.map((item) => {
            const st = STATUS_META[item.status] || STATUS_META.active;
            const daysLeft = item.keep_alive_days_left;
            const services = item.bound_services
              ? item.bound_services.split(/[,，、]/).map((s) => s.trim()).filter(Boolean)
              : [];

            return (
              <div key={item.id} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs space-y-4">
                {/* Header */}
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-lg bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700">
                        {item.country_region}
                      </span>
                      <h3 className="text-base font-bold text-slate-900">{item.label}</h3>
                      <span className={`rounded-md border px-2 py-0.5 text-[11px] font-medium ${st.cls}`}>
                        {st.label}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <span>{item.carrier || '未填运营商'}</span>
                      <span>·</span>
                      <span>{SIM_TYPE_LABELS[item.sim_type] || item.sim_type}</span>
                      {item.device_slot && (
                        <>
                          <span>·</span>
                          <span>{item.device_slot}</span>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => openEditForm(item)}
                      title="编辑"
                      className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                    >
                      <Edit3 className="h-4 w-4" />
                    </button>
                    <button
                      onClick={async () => {
                        if (!window.confirm(`确定删除「${item.label}」吗？`)) return;
                        await api.deletePhoneNumber(item.id);
                        await loadAll();
                        onRefreshDashboard();
                      }}
                      title="删除"
                      className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 cursor-pointer"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Number Row */}
                <div className="flex items-center justify-between rounded-xl bg-slate-50 px-3.5 py-2.5">
                  <span className="font-mono text-base font-semibold tracking-wide text-slate-900">
                    {maskNumber(item.phone_number, maskNumbers)}
                  </span>
                  <button
                    onClick={() => {
                      navigator.clipboard?.writeText(item.phone_number);
                      onNotify(`已复制号码 ${item.phone_number}`);
                    }}
                    className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-100 cursor-pointer"
                  >
                    <Copy className="h-3 w-3" /> 复制
                  </button>
                </div>

                {/* Fee & Comm Cost Grid */}
                <div className="grid grid-cols-3 gap-2.5 text-xs">
                  <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-3">
                    <div className="text-slate-400">套餐月费</div>
                    <div className="mt-1 font-semibold text-slate-900">
                      {formatCurrency(item.monthly_fee, item.currency)}
                    </div>
                    {item.currency !== 'CNY' && (
                      <div className="text-[10px] text-slate-400">约 {formatMoney(item.monthly_fee_cny)}</div>
                    )}
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-3">
                    <div className="text-slate-400">当前余额</div>
                    <div className="mt-1 font-semibold text-emerald-600">
                      {formatCurrency(item.balance, item.currency)}
                    </div>
                    <div className="text-[10px] text-slate-400">每月 {item.billing_day} 号扣费</div>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-3">
                    <div className="text-slate-400">额外 / 累计通信费</div>
                    <div className="mt-1 font-semibold text-slate-900">
                      {formatCurrency(item.extra_comm_cost, item.currency)}
                    </div>
                    <div className="text-[10px] text-slate-400">
                      累计 {formatCurrency(item.total_comm_spent, item.currency)}
                    </div>
                  </div>
                </div>

                {/* Keep-Alive Rule Box */}
                <div className="rounded-xl border border-teal-100 bg-teal-50/40 p-3.5 space-y-2 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-teal-800 flex items-center gap-1.5">
                      <ShieldCheck className="h-4 w-4" /> 保号规则
                      {item.keep_alive_cycle_days > 0 && `（每 ${item.keep_alive_cycle_days} 天 · 提前 ${item.remind_before_days ?? 7} 天提醒）`}
                    </span>
                    {daysLeft !== null && (
                      <span
                        className={`rounded-md px-2 py-0.5 text-[11px] font-medium ${
                          daysLeft <= 0
                            ? 'bg-rose-100 text-rose-700'
                            : daysLeft <= (item.remind_before_days ?? 7)
                              ? 'bg-amber-100 text-amber-800'
                              : 'bg-teal-100 text-teal-800'
                        }`}
                      >
                        {daysLeft < 0
                          ? `已逾期 ${Math.abs(daysLeft)} 天`
                          : daysLeft === 0
                            ? '今日截止保号'
                            : `剩余 ${daysLeft} 天`}
                      </span>
                    )}
                  </div>
                  <p className="text-slate-700">
                    {item.keep_alive_rule || '未设置保号规则说明'}
                  </p>
                  <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-[11px] text-slate-500">
                    <div className="flex flex-wrap gap-3">
                      {item.last_keep_alive_date && <span>上次保号：{item.last_keep_alive_date}</span>}
                      {item.next_keep_alive_date && <span>下次截止：{item.next_keep_alive_date}</span>}
                      {item.expiry_date && <span>有效期至：{item.expiry_date}</span>}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleKeepAlive(item)}
                        className="inline-flex items-center gap-1 rounded-lg bg-teal-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-teal-700 cursor-pointer"
                      >
                        <CheckCircle2 className="h-3 w-3" /> 保号打卡
                      </button>
                      <button
                        onClick={() => setCostPhone(item)}
                        className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
                      >
                        <Coins className="h-3 w-3" /> 记通信费/充值
                      </button>
                    </div>
                  </div>
                </div>

                {/* Bound services & notes */}
                {(services.length > 0 || item.notes) && (
                  <div className="space-y-2 text-xs">
                    {services.length > 0 && (
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-slate-400">绑定服务：</span>
                        {services.map((s) => (
                          <span key={s} className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] text-slate-700">
                            {s}
                          </span>
                        ))}
                      </div>
                    )}
                    {item.notes && <div className="text-[11px] text-slate-500">备注：{item.notes}</div>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
