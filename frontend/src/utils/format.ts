export function formatMoney(val: number | null | undefined, showSign = false): string {
  const num = Number(val || 0);
  const absFormatted = Math.abs(num).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (num < 0) return `-¥${absFormatted}`;
  if (showSign && num > 0) return `+¥${absFormatted}`;
  return `¥${absFormatted}`;
}

export function formatDueAt(dueAt: string | null | undefined): string {
  if (!dueAt) return '无截止时间';
  return dueAt.replace('T', ' ').slice(0, 16);
}

export const PRIORITY_META: Record<string, { label: string; cls: string }> = {
  high: { label: '高优先', cls: 'bg-rose-50 text-rose-700 border-rose-200' },
  medium: { label: '常规', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  low: { label: '低优先', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
};

export const CATEGORY_LABELS: Record<string, string> = {
  work: '工作',
  life: '生活',
  finance: '财务',
  health: '健康',
  study: '学习',
};

export const SOURCE_META: Record<string, { label: string; cls: string }> = {
  manual: { label: '手动', cls: 'bg-slate-100 text-slate-600' },
  ai_chat: { label: 'AI对话', cls: 'bg-indigo-50 text-indigo-700' },
  siyuan_note: { label: '思源笔记', cls: 'bg-emerald-50 text-emerald-700' },
  recurring_bill: { label: '固定账单', cls: 'bg-orange-50 text-orange-700' },
  email: { label: '邮件解析', cls: 'bg-sky-50 text-sky-700' },
  alipay: { label: '支付宝', cls: 'bg-blue-50 text-blue-700' },
  wechat: { label: '微信', cls: 'bg-green-50 text-green-700' },
  bank_card: { label: '银行卡账单', cls: 'bg-purple-50 text-purple-700' },
  card_direct: { label: '直接刷卡', cls: 'bg-purple-50 text-purple-700' },
  cash: { label: '现金', cls: 'bg-amber-50 text-amber-700' },
  bill_import: { label: '账单导入', cls: 'bg-violet-50 text-violet-700' },
  phone_sim: { label: '手机保号', cls: 'bg-teal-50 text-teal-700' },
  debt_bill: { label: '卡贷还款', cls: 'bg-rose-50 text-rose-700' },
};

export const CHANNEL_META: Record<string, { label: string; cls: string }> = {
  alipay: { label: '支付宝', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
  wechat: { label: '微信支付', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  card_direct: { label: '直接刷卡', cls: 'bg-purple-50 text-purple-700 border-purple-200' },
  cash: { label: '现金', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  email: { label: '银行邮件', cls: 'bg-sky-50 text-sky-700 border-sky-200' },
  manual: { label: '手动记账', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
  other: { label: '其他', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
};

export const RECONCILE_META: Record<string, { label: string; cls: string }> = {
  matched: { label: '已多方核对', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  direct_card: { label: '直接刷卡独有', cls: 'bg-purple-50 text-purple-700 border-purple-200' },
  reconciled_duplicate: { label: '已核对去重', cls: 'bg-slate-100 text-slate-500 border-slate-200 line-through' },
  cash: { label: '现金消费', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  unmatched: { label: '待比对', cls: 'bg-slate-50 text-slate-500 border-slate-200' },
};

export function formatCurrency(val: number | null | undefined, currency = 'CNY'): string {
  const num = Number(val || 0);
  const symMap: Record<string, string> = {
    CNY: '¥',
    USD: '$',
    HKD: 'HK$',
    GBP: '£',
    JPY: 'JP¥',
    EUR: '€',
    SGD: 'S$',
    PHP: '₱',
    TWD: 'NT$',
  };
  const sym = symMap[currency.toUpperCase()] || `${currency} `;
  return `${sym}${num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export const ACCOUNT_TYPE_LABELS: Record<string, string> = {
  bank: '银行储蓄',
  cash: '现金 / 余额宝',
  investment: '理财 / 基金',
  credit_card: '信用卡',
  loan: '贷款 / 负债',
};
