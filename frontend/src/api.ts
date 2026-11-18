import type {
  Account,
  AppSettings,
  ChatMessage,
  DashboardData,
  EmailFetchResponse,
  EmailSyncLog,
  NoteCandidate,
  NoteSnippet,
  ParsedBillTransaction,
  PhoneNumber,
  PhoneNumbersResponse,
  RecurringBill,
  Reminder,
  Settlement,
  Transaction,
} from './types';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  });
  if (!resp.ok) {
    let msg = `请求失败 (${resp.status})`;
    try {
      const data = await resp.json();
      msg = data.detail || data.message || msg;
    } catch {
      // ignore
    }
    throw new Error(msg);
  }
  return resp.json();
}

export const api = {
  getDashboard: () => request<DashboardData>('/api/dashboard'),

  // Reminders
  listReminders: (params?: { status?: string; category?: string; source_type?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.category) qs.set('category', params.category);
    if (params?.source_type) qs.set('source_type', params.source_type);
    if (params?.q) qs.set('q', params.q);
    return request<Reminder[]>(`/api/reminders?${qs.toString()}`);
  },
  createReminder: (payload: Partial<Reminder>) =>
    request<Reminder>('/api/reminders', { method: 'POST', body: JSON.stringify(payload) }),
  updateReminder: (id: number, payload: Partial<Reminder>) =>
    request<Reminder>(`/api/reminders/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  completeAndRecord: (id: number, accountId?: number) =>
    request<{ reminder: Reminder; transaction: Transaction | null }>(
      `/api/reminders/${id}/complete-and-record${accountId ? `?account_id=${accountId}` : ''}`,
      { method: 'POST' },
    ),
  deleteReminder: (id: number) => request<{ deleted: boolean }>(`/api/reminders/${id}`, { method: 'DELETE' }),
  pushReminderBark: (id: number) =>
    request<{ ok: boolean; message: string }>(`/api/reminders/${id}/push-bark`, { method: 'POST' }),
  batchImportReminders: (reminders: NoteCandidate[]) =>
    request<{ imported_count: number; items: Reminder[] }>('/api/reminders/batch-import', {
      method: 'POST',
      body: JSON.stringify({ reminders }),
    }),

  // Chat
  listChat: () => request<ChatMessage[]>('/api/chat'),
  clearChat: () => request<{ cleared: boolean }>('/api/chat', { method: 'DELETE' }),
  sendChat: (message: string) =>
    request<{
      message: ChatMessage;
      created_reminders: Reminder[];
      created_transactions: Transaction[];
      engine: string;
    }>('/api/chat', { method: 'POST', body: JSON.stringify({ message }) }),

  // SiYuan MCP & Notes
  testSiyuan: () =>
    request<{ ok: boolean; mode: string; message: string; tools: Array<{ name: string; description: string }> }>(
      '/api/siyuan/test',
      { method: 'POST' },
    ),
  fetchSiyuanNotes: (keyword: string, sql = '', limit = 20) =>
    request<{ ok: boolean; source: string; demo_fallback: boolean; error?: string; notes: NoteSnippet[] }>(
      '/api/siyuan/fetch-notes',
      { method: 'POST', body: JSON.stringify({ keyword, sql, limit }) },
    ),
  extractNotes: (notes: NoteSnippet[], rawText: string) =>
    request<{ candidates: NoteCandidate[]; engine: string; warning?: string }>('/api/notes/extract', {
      method: 'POST',
      body: JSON.stringify({ notes, raw_text: rawText }),
    }),

  // Finance
  listAccounts: () =>
    request<{ accounts: Account[]; total_assets: number; total_liabilities: number; net_worth: number }>('/api/accounts'),
  createAccount: (payload: Partial<Account>) =>
    request<Account>('/api/accounts', { method: 'POST', body: JSON.stringify(payload) }),
  updateAccount: (id: number, payload: Partial<Account>) =>
    request<Account>(`/api/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAccount: (id: number) => request<{ deleted: boolean }>(`/api/accounts/${id}`, { method: 'DELETE' }),

  listTransactions: (settled?: 'unsettled' | 'settled') =>
    request<{
      transactions: Transaction[];
      unsettled_summary: {
        count: number;
        income: number;
        expense: number;
        net_cashflow: number;
        category_breakdown: Record<string, number>;
      };
    }>(`/api/transactions${settled ? `?settled=${settled}` : ''}`),
  createTransaction: (payload: Partial<Transaction>) =>
    request<Transaction>('/api/transactions', { method: 'POST', body: JSON.stringify(payload) }),
  deleteTransaction: (id: number) => request<{ deleted: boolean }>(`/api/transactions/${id}`, { method: 'DELETE' }),

  listRecurringBills: () => request<RecurringBill[]>('/api/recurring-bills'),
  createRecurringBill: (payload: Partial<RecurringBill>) =>
    request<RecurringBill>('/api/recurring-bills', { method: 'POST', body: JSON.stringify(payload) }),
  deleteRecurringBill: (id: number) =>
    request<{ deleted: boolean }>(`/api/recurring-bills/${id}`, { method: 'DELETE' }),
  syncBillReminders: (forceAll = true) =>
    request<{ created_count: number; reminders: Reminder[] }>(
      `/api/recurring-bills/sync-reminders?force_all=${forceAll}`,
      { method: 'POST' },
    ),
  payBill: (billId: number) =>
    request<{ transaction: Transaction; bill: RecurringBill }>(`/api/recurring-bills/${billId}/pay`, {
      method: 'POST',
    }),

  listSettlements: () => request<Settlement[]>('/api/settlements'),
  createSettlement: (periodLabel: string) =>
    request<Settlement>('/api/settlements/settle', {
      method: 'POST',
      body: JSON.stringify({ period_label: periodLabel }),
    }),

  // Bills & Emails
  parseBillUpload: async (file: File, zipPassword = '') => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('zip_password', zipPassword);
    const resp = await fetch('/api/bills/parse-upload', { method: 'POST', body: fd });
    if (!resp.ok) {
      throw new Error(`上传解析失败 (${resp.status})`);
    }
    return resp.json() as Promise<{
      transactions: ParsedBillTransaction[];
      platforms: string[];
      errors: string[];
    }>;
  },
  batchImportTransactions: (
    transactions: ParsedBillTransaction[],
    accountId: number | null,
    skipDuplicates = true,
  ) =>
    request<{ imported_tx_count: number; skipped_duplicate_count: number }>('/api/transactions/batch-import', {
      method: 'POST',
      body: JSON.stringify({ transactions, account_id: accountId, skip_duplicates: skipDuplicates }),
    }),
  testEmail: () => request<{ ok: boolean; message: string; inbox_count: number }>('/api/emails/test', { method: 'POST' }),
  fetchParseEmails: (params: {
    days: number;
    limit: number;
    keyword: string;
    zip_password: string;
    include_body_ai: boolean;
    unread_only: boolean;
  }) => request<EmailFetchResponse>('/api/emails/fetch-parse', { method: 'POST', body: JSON.stringify(params) }),
  parseEmailText: (params: { subject: string; sender: string; body: string }) =>
    request<{ transactions: ParsedBillTransaction[]; reminders: NoteCandidate[]; engine: string; warning?: string }>(
      '/api/emails/parse-text',
      { method: 'POST', body: JSON.stringify(params) },
    ),
  importEmailItems: (params: {
    transactions: ParsedBillTransaction[];
    reminders: NoteCandidate[];
    account_id: number | null;
    skip_duplicates: boolean;
    email_logs: Array<{
      message_uid: string;
      subject: string;
      sender: string;
      received_at: string;
      tx_imported: number;
      rem_imported: number;
    }>;
  }) =>
    request<{ imported_tx_count: number; skipped_duplicate_count: number; imported_rem_count: number }>(
      '/api/emails/import',
      { method: 'POST', body: JSON.stringify(params) },
    ),
  listEmailLogs: () => request<EmailSyncLog[]>('/api/emails/logs'),

  // Phone Numbers
  listPhoneNumbers: () => request<PhoneNumbersResponse>('/api/phone-numbers'),
  createPhoneNumber: (payload: Partial<PhoneNumber>) =>
    request<PhoneNumber>('/api/phone-numbers', { method: 'POST', body: JSON.stringify(payload) }),
  updatePhoneNumber: (id: number, payload: Partial<PhoneNumber>) =>
    request<PhoneNumber>(`/api/phone-numbers/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deletePhoneNumber: (id: number) => request<{ deleted: boolean }>(`/api/phone-numbers/${id}`, { method: 'DELETE' }),
  keepAlivePhone: (id: number) => request<PhoneNumber>(`/api/phone-numbers/${id}/keep-alive`, { method: 'POST' }),
  recordPhoneCost: (
    id: number,
    payload: {
      kind: 'topup' | 'monthly_fee' | 'extra_cost';
      amount: number;
      description: string;
      sync_to_finance: boolean;
      account_id: number | null;
      mark_keep_alive?: boolean;
    },
  ) =>
    request<{ phone: PhoneNumber; transaction: Transaction | null }>(`/api/phone-numbers/${id}/record-cost`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  syncPhoneReminders: (withinDays?: number) =>
    request<{ created_count: number; reminders: Reminder[] }>(
      `/api/phone-numbers/sync-reminders${withinDays ? `?within_days=${withinDays}` : ''}`,
      { method: 'POST' },
    ),

  // Settings
  getSettings: () => request<AppSettings>('/api/settings'),
  updateSettings: (updates: Partial<AppSettings>) =>
    request<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(updates) }),
  testAi: () => request<{ ok: boolean; provider: string; message: string }>('/api/settings/test-ai', { method: 'POST' }),
  testBark: () => request<{ ok: boolean; message: string }>('/api/settings/test-bark', { method: 'POST' }),
  resetData: (reseed = false) =>
    request<{ ok: boolean; reseeded: boolean }>(`/api/settings/reset-data?reseed=${reseed}`, { method: 'POST' }),
};
