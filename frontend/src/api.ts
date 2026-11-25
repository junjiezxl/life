import type {
  Account,
  AppSettings,
  AuthStatus,
  ChatMessage,
  CreditCard,
  DashboardData,
  DebtOperation,
  DebtOverviewResponse,
  DebtPlanResponse,
  DetectedCardSummary,
  EmailFetchResponse,
  EmailSyncLog,
  Loan,
  NoteCandidate,
  NoteSnippet,
  ParsedBillTransaction,
  PhoneNumber,
  PhoneNumbersResponse,
  ReconcileOverviewResponse,
  ReconcileRunResult,
  RecurringBill,
  Reminder,
  Settlement,
  SettlementCategoryStat,
  Transaction,
} from './types';

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  });
  if (!resp.ok) {
    if (resp.status === 401 && !url.startsWith('/api/auth/')) {
      unauthorizedHandler?.();
    }
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
  // Auth
  getAuthStatus: () => request<AuthStatus>('/api/auth/status'),
  setupAuth: (username: string, password: string) =>
    request<{ ok: boolean; username: string; expires_at: string }>('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  login: (username: string, password: string, rememberMe = true) =>
    request<{ ok: boolean; username: string; expires_at: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, remember_me: rememberMe }),
    }),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  logoutAll: (includeCurrent = false) =>
    request<{ ok: boolean; revoked_count: number }>(`/api/auth/logout-all?include_current=${includeCurrent}`, {
      method: 'POST',
    }),
  changePassword: (payload: { current_password: string; new_password?: string; new_username?: string }) =>
    request<{ ok: boolean; username: string; revoked_other_sessions: number }>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

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

  listTransactions: (params?: {
    settled?: 'unsettled' | 'settled';
    settlement_id?: number;
    category?: string;
    tx_type?: string;
    q?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.settled) qs.set('settled', params.settled);
    if (params?.settlement_id !== undefined) qs.set('settlement_id', String(params.settlement_id));
    if (params?.category) qs.set('category', params.category);
    if (params?.tx_type) qs.set('tx_type', params.tx_type);
    if (params?.q) qs.set('q', params.q);
    if (params?.limit) qs.set('limit', String(params.limit));
    const queryStr = qs.toString();
    return request<{
      transactions: Transaction[];
      unsettled_summary: {
        count: number;
        income: number;
        expense: number;
        net_cashflow: number;
        category_breakdown: Record<string, number>;
      };
    }>(`/api/transactions${queryStr ? `?${queryStr}` : ''}`);
  },
  createTransaction: (payload: Partial<Transaction>) =>
    request<Transaction>('/api/transactions', { method: 'POST', body: JSON.stringify(payload) }),
  updateTransaction: (id: number, payload: { category?: string; description?: string }) =>
    request<Transaction>(`/api/transactions/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
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
  getSettlementTransactions: (
    settlementId: number,
    params?: { category?: string; tx_type?: string; q?: string },
  ) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set('category', params.category);
    if (params?.tx_type) qs.set('tx_type', params.tx_type);
    if (params?.q) qs.set('q', params.q);
    const queryStr = qs.toString();
    return request<{
      settlement: Settlement;
      category_stats: SettlementCategoryStat[];
      transactions: Transaction[];
    }>(`/api/settlements/${settlementId}/transactions${queryStr ? `?${queryStr}` : ''}`);
  },
  regenerateSettlement: (settlementId: number, periodLabel?: string) =>
    request<Settlement>(`/api/settlements/${settlementId}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(periodLabel !== undefined ? { period_label: periodLabel } : {}),
    }),
  deleteSettlement: (settlementId: number) =>
    request<{ deleted: boolean; released_tx_count: number }>(`/api/settlements/${settlementId}`, {
      method: 'DELETE',
    }),

  // Bills & Emails
  parseBillUpload: async (file: File, zipPassword = '') => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('zip_password', zipPassword);
    const resp = await fetch('/api/bills/parse-upload', {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (!resp.ok) {
      if (resp.status === 401) {
        unauthorizedHandler?.();
      }
      throw new Error(`上传解析失败 (${resp.status})`);
    }
    return resp.json() as Promise<{
      transactions: ParsedBillTransaction[];
      platforms: string[];
      detected_cards?: DetectedCardSummary[];
      errors: string[];
    }>;
  },
  getReconcileOverview: (params?: {
    month?: string;
    channel?: string;
    card_last4?: string;
    reconcile_status?: string;
    q?: string;
    include_duplicates?: boolean;
  }) => {
    const qs = new URLSearchParams();
    if (params?.month) qs.set('month', params.month);
    if (params?.channel && params.channel !== 'all') qs.set('channel', params.channel);
    if (params?.card_last4) qs.set('card_last4', params.card_last4);
    if (params?.reconcile_status && params.reconcile_status !== 'all') qs.set('reconcile_status', params.reconcile_status);
    if (params?.q) qs.set('q', params.q);
    if (params?.include_duplicates) qs.set('include_duplicates', 'true');
    const qstr = qs.toString();
    return request<ReconcileOverviewResponse>(`/api/bills/reconcile/overview${qstr ? `?${qstr}` : ''}`);
  },
  runReconcile: (payload?: { backfill_accounts?: boolean; auto_create_accounts?: boolean }) =>
    request<{ reconciliation: ReconcileRunResult; overview: ReconcileOverviewResponse }>('/api/bills/reconcile', {
      method: 'POST',
      body: JSON.stringify(payload || { backfill_accounts: true, auto_create_accounts: true }),
    }),
  unmatchReconciledPair: (txId: number) =>
    request<{ unmatched: boolean; tx_id: number; peer_id: number }>(`/api/bills/reconcile/unmatch/${txId}`, {
      method: 'POST',
    }),
  batchImportTransactions: (
    transactions: ParsedBillTransaction[],
    accountId: number | null,
    skipDuplicates = true,
    autoCreateAccounts = true,
    autoReconcile = true,
  ) =>
    request<{
      imported_tx_count: number;
      skipped_duplicate_count: number;
      auto_created_accounts?: Account[];
      auto_created_cards?: CreditCard[];
      reconciliation?: ReconcileRunResult;
    }>('/api/transactions/batch-import', {
      method: 'POST',
      body: JSON.stringify({
        transactions,
        account_id: accountId,
        skip_duplicates: skipDuplicates,
        auto_create_accounts: autoCreateAccounts,
        auto_reconcile: autoReconcile,
      }),
    }),
  listEmailAccounts: () => request<import('./types').EmailAccount[]>('/api/email-accounts'),
  createEmailAccount: (payload: {
    name?: string;
    imap_host: string;
    imap_port: number;
    username: string;
    password: string;
    use_ssl?: boolean;
    use_proxy?: boolean;
    bill_zip_password?: string;
    enabled?: boolean;
  }) =>
    request<import('./types').EmailAccount>('/api/email-accounts', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateEmailAccount: (
    id: number,
    payload: Partial<{
      name: string;
      imap_host: string;
      imap_port: number;
      username: string;
      password: string;
      use_ssl: boolean;
      use_proxy: boolean;
      bill_zip_password: string;
      enabled: boolean;
    }>,
  ) =>
    request<import('./types').EmailAccount>(`/api/email-accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteEmailAccount: (id: number) =>
    request<{ deleted: boolean }>(`/api/email-accounts/${id}`, { method: 'DELETE' }),
  testEmailAccount: (id: number) =>
    request<{ ok: boolean; message: string; inbox_count: number }>(`/api/email-accounts/${id}/test`, {
      method: 'POST',
    }),
  testEmail: () => request<{ ok: boolean; message: string; inbox_count: number }>('/api/emails/test', { method: 'POST' }),
  fetchParseEmails: (params: {
    days: number;
    limit: number;
    keyword: string;
    zip_password: string;
    include_body_ai: boolean;
    unread_only: boolean;
    account_id?: number;
  }) => request<EmailFetchResponse>('/api/emails/fetch-parse', { method: 'POST', body: JSON.stringify(params) }),
  parseEmailText: (params: { subject: string; sender: string; body: string }) =>
    request<{
      transactions: ParsedBillTransaction[];
      reminders: NoteCandidate[];
      detected_cards?: DetectedCardSummary[];
      engine: string;
      warning?: string;
    }>('/api/emails/parse-text', { method: 'POST', body: JSON.stringify(params) }),
  importEmailItems: (params: {
    transactions: ParsedBillTransaction[];
    reminders: NoteCandidate[];
    account_id: number | null;
    skip_duplicates: boolean;
    auto_create_accounts?: boolean;
    auto_reconcile?: boolean;
    email_logs: Array<{
      message_uid: string;
      subject: string;
      sender: string;
      received_at: string;
      tx_imported: number;
      rem_imported: number;
    }>;
  }) =>
    request<{
      imported_tx_count: number;
      skipped_duplicate_count: number;
      imported_rem_count: number;
      auto_created_accounts?: Account[];
      auto_created_cards?: CreditCard[];
      reconciliation?: ReconcileRunResult;
    }>('/api/emails/import', { method: 'POST', body: JSON.stringify(params) }),
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

  // Credit Cards, Loans & Debt Repayment Planner
  getDebtOverview: () => request<DebtOverviewResponse>('/api/debt/overview'),
  createCreditCard: (payload: Partial<CreditCard>) =>
    request<CreditCard>('/api/debt/cards', { method: 'POST', body: JSON.stringify(payload) }),
  updateCreditCard: (id: number, payload: Partial<CreditCard>) =>
    request<CreditCard>(`/api/debt/cards/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteCreditCard: (id: number) => request<{ deleted: boolean }>(`/api/debt/cards/${id}`, { method: 'DELETE' }),

  createLoan: (payload: Partial<Loan>) =>
    request<Loan>('/api/debt/loans', { method: 'POST', body: JSON.stringify(payload) }),
  updateLoan: (id: number, payload: Partial<Loan>) =>
    request<Loan>(`/api/debt/loans/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteLoan: (id: number) => request<{ deleted: boolean }>(`/api/debt/loans/${id}`, { method: 'DELETE' }),

  planDebtRepayment: (payload: {
    available_cash?: number;
    horizon_days?: number;
    reserve_ratio?: number;
    strategy?: 'balanced' | 'max_free_days' | 'min_fee';
    allow_relay?: boolean;
  }) => request<DebtPlanResponse>('/api/debt/plan', { method: 'POST', body: JSON.stringify(payload) }),

  executeDebtOperation: (payload: {
    op_type: 'cash_repay_card' | 'swipe_repay_card' | 'repay_loan' | 'swipe_cash_out';
    source_card_id?: number | null;
    source_account_id?: number | null;
    target_card_id?: number | null;
    target_loan_id?: number | null;
    amount: number;
    fee_amount?: number;
    note?: string;
    sync_to_finance?: boolean;
  }) => request<DebtOperation>('/api/debt/operations', { method: 'POST', body: JSON.stringify(payload) }),
  undoDebtOperation: (opId: number) =>
    request<{ deleted: boolean }>(`/api/debt/operations/${opId}`, { method: 'DELETE' }),
  syncDebtReminders: (withinDays = 5) =>
    request<{ created_count: number; reminders: Reminder[] }>(`/api/debt/sync-reminders?within_days=${withinDays}`, {
      method: 'POST',
    }),

  // Settings
  getSettings: () => request<AppSettings>('/api/settings'),
  updateSettings: (updates: Partial<AppSettings>) =>
    request<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(updates) }),
  testAi: () => request<{ ok: boolean; provider: string; message: string }>('/api/settings/test-ai', { method: 'POST' }),
  testProxy: () => request<{ ok: boolean; message: string }>('/api/settings/test-proxy', { method: 'POST' }),
  testBark: () => request<{ ok: boolean; message: string }>('/api/settings/test-bark', { method: 'POST' }),
  getCTraderDeals: (limit = 5000) => request<import('./types').CTraderDealsResponse>(`/api/ctrader/deals?limit=${limit}`),
  getCTraderSyncStatus: () => request<import('./types').CTraderSyncStatus>('/api/ctrader/sync/status'),
  syncCTraderDeals: (options?: {
    mode?: 'incremental' | 'range';
    days?: number;
    start_date?: string;
    end_date?: string;
    overlap_seconds?: number;
    push_new?: boolean;
    background?: boolean;
  }) =>
    request<import('./types').CTraderSyncResponse>('/api/ctrader/sync', {
      method: 'POST',
      body: JSON.stringify({
        mode: options?.mode ?? 'incremental',
        days: options?.days ?? 7,
        start_date: options?.start_date || undefined,
        end_date: options?.end_date || undefined,
        overlap_seconds: options?.overlap_seconds ?? 3,
        push_new: options?.push_new,
        background: options?.background ?? false,
      }),
    }),
  testCTrader: () =>
    request<{ ok: boolean; account_id: string; environment: string; message: string }>('/api/ctrader/test', {
      method: 'POST',
    }),
  getCTraderAuthorizeUrl: (redirectUri: string, scope: 'accounts' | 'trading' = 'accounts') =>
    request<{ ok: boolean; url: string }>(
      `/api/ctrader/oauth/authorize-url?redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(scope)}`,
    ),
  exchangeCTraderCode: (codeOrUrl: string, redirectUri?: string) =>
    request<{ ok: boolean; expires_in: number; expires_at: string; has_refresh_token: boolean; message: string }>(
      '/api/ctrader/oauth/exchange',
      {
        method: 'POST',
        body: JSON.stringify({ code_or_url: codeOrUrl, redirect_uri: redirectUri || '' }),
      },
    ),
  refreshCTraderToken: (refreshToken?: string) =>
    request<{ ok: boolean; already_rotated?: boolean; expires_at?: string; message: string }>(
      '/api/ctrader/oauth/refresh',
      {
        method: 'POST',
        body: JSON.stringify(refreshToken ? { refresh_token: refreshToken } : {}),
      },
    ),
  getCTraderMarketStatus: () =>
    request<import('./types').CTraderMarketStatus>('/api/ctrader/market/status'),
  getCTraderRecentTicks: (symbol = '', limit = 50) =>
    request<import('./types').CTraderRecentTicksResponse>(
      `/api/ctrader/market/ticks?symbol=${encodeURIComponent(symbol)}&limit=${limit}`,
    ),
  startCTraderMarket: (symbols?: string) =>
    request<{ ok: boolean; status: import('./types').CTraderMarketStatus }>('/api/ctrader/market/start', {
      method: 'POST',
      body: JSON.stringify(symbols !== undefined ? { symbols } : {}),
    }),
  stopCTraderMarket: () =>
    request<{ ok: boolean; status: import('./types').CTraderMarketStatus }>('/api/ctrader/market/stop', {
      method: 'POST',
    }),
  restartCTraderMarket: () =>
    request<{ ok: boolean; status: import('./types').CTraderMarketStatus }>('/api/ctrader/market/restart', {
      method: 'POST',
    }),
  getIbkrOverview: (accountId = '') =>
    request<import('./types').IbkrOverviewResponse>(
      `/api/ibkr/overview${accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''}`,
    ),
  syncIbkrFlex: (options?: { query_id?: string; push_summary?: boolean }) =>
    request<{
      ok: boolean;
      accounts: string[];
      equity_snapshots: number;
      positions: number;
      trades_inserted: number;
      trades_updated: number;
      cash_transactions: number;
      synced_at: string;
    }>('/api/ibkr/sync', {
      method: 'POST',
      body: JSON.stringify(options || {}),
    }),
  testIbkr: () => request<{ ok: boolean; accounts: string[]; message: string }>('/api/ibkr/test', { method: 'POST' }),

  // Stock Grid Trading Ledger
  listGridStrategies: () => request<import('./types').GridListResponse>('/api/grids'),
  createGridStrategy: (payload: Partial<import('./types').GridStrategy>) =>
    request<import('./types').GridDetailResponse>('/api/grids', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getGridStrategy: (strategyId: number) =>
    request<import('./types').GridDetailResponse>(`/api/grids/${strategyId}`),
  updateGridStrategy: (strategyId: number, payload: Partial<import('./types').GridStrategy>) =>
    request<import('./types').GridDetailResponse>(`/api/grids/${strategyId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteGridStrategy: (strategyId: number) =>
    request<{ deleted: boolean }>(`/api/grids/${strategyId}`, { method: 'DELETE' }),
  createGridTrade: (
    strategyId: number,
    payload: {
      side: 'buy' | 'sell';
      price: number;
      quantity: number;
      fee?: number;
      tax?: number;
      traded_at?: string;
      grid_level?: number;
      note?: string;
    },
  ) =>
    request<{ trade: import('./types').StockTrade; detail: import('./types').GridDetailResponse }>(
      `/api/grids/${strategyId}/trades`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    ),
  deleteGridTrade: (tradeId: number) =>
    request<{ deleted: boolean; strategy_id: number; detail: import('./types').GridDetailResponse }>(
      `/api/grids/trades/${tradeId}`,
      { method: 'DELETE' },
    ),
  refreshGridQuotes: (strategyId?: number) =>
    request<{ updated_count: number; quotes: Record<string, unknown>; errors: string[] }>(
      `/api/grids/refresh-quotes${strategyId ? `?strategy_id=${strategyId}` : ''}`,
      { method: 'POST' },
    ),

  resetData: (reseed = false) =>
    request<{ ok: boolean; reseeded: boolean }>(`/api/settings/reset-data?reseed=${reseed}`, { method: 'POST' }),
};
