export type TabKey = 'dashboard' | 'reminders' | 'notes' | 'emails' | 'phones' | 'finance' | 'settings';

export interface Reminder {
  id: number;
  title: string;
  description: string;
  due_at: string | null;
  priority: 'high' | 'medium' | 'low';
  category: 'work' | 'life' | 'finance' | 'health' | 'study';
  status: 'pending' | 'done' | 'snoozed';
  source_type: 'manual' | 'ai_chat' | 'siyuan_note' | 'recurring_bill' | 'email' | 'phone_sim';
  source_ref: string;
  linked_amount: number | null;
  linked_tx_type: 'expense' | 'income' | null;
  created_at: string;
  updated_at: string;
}

export interface Account {
  id: number;
  name: string;
  type: 'cash' | 'bank' | 'investment' | 'credit_card' | 'loan';
  balance: number;
  credit_limit: number;
  currency: string;
  updated_at: string;
}

export interface Transaction {
  id: number;
  type: 'expense' | 'income' | 'transfer';
  amount: number;
  category: string;
  account_id: number | null;
  account_name?: string;
  description: string;
  occurred_at: string;
  settlement_id: number | null;
  source_type: string;
  created_at: string;
}

export interface RecurringBill {
  id: number;
  name: string;
  amount: number;
  category: string;
  account_id: number | null;
  account_name?: string;
  billing_day: number;
  cycle: 'monthly' | 'yearly' | 'weekly';
  next_due_date: string;
  auto_remind_days: number;
  active: number;
  last_generated_at: string | null;
}

export interface Settlement {
  id: number;
  period_label: string;
  start_date: string;
  end_date: string;
  total_income: number;
  total_expense: number;
  net_cashflow: number;
  net_worth_snapshot: number;
  tx_count: number;
  category_breakdown: Record<string, number>;
  account_snapshot?: Array<{ name: string; type: string; balance: number }>;
  ai_summary: string;
  created_at: string;
}

export interface SettlementCategoryStat {
  type: 'expense' | 'income' | 'transfer';
  category: string;
  count: number;
  total: number;
}

export interface ChatAction {
  kind: 'reminder' | 'transaction';
  id: number;
  title?: string;
  due_at?: string | null;
  type?: string;
  amount?: number;
  category?: string;
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  actions: ChatAction[];
  created_at: string;
}

export interface NoteSnippet {
  id: string;
  title: string;
  path: string;
  content: string;
  updated?: string;
}

export interface NoteCandidate {
  title: string;
  description: string;
  due_at: string | null;
  priority: 'high' | 'medium' | 'low';
  category: 'work' | 'life' | 'finance' | 'health' | 'study';
  source_type: string;
  source_ref: string;
  linked_amount: number | null;
  linked_tx_type: 'expense' | 'income' | null;
  reason?: string;
}

export interface DashboardData {
  reminders_summary: {
    pending_count: number;
    done_count: number;
    due_today_count: number;
    overdue_count: number;
    upcoming: Reminder[];
  };
  finance_summary: {
    total_assets: number;
    total_liabilities: number;
    net_worth: number;
    unsettled_count: number;
    unsettled_income: number;
    unsettled_expense: number;
    unsettled_net_cashflow: number;
    category_breakdown: Record<string, number>;
    accounts: Account[];
    recent_unsettled: Transaction[];
  };
  recurring_bills: RecurringBill[];
  latest_settlement: Settlement | null;
  config_status: {
    ai_provider: string;
    siyuan_mode: string;
  };
}

export interface ParsedBillTransaction {
  type: 'expense' | 'income';
  amount: number;
  category: string;
  description: string;
  occurred_at: string;
  source_type: string;
  external_ref: string;
  already_imported?: boolean;
  email_subject?: string;
  email_uid?: string;
}

export interface EmailScanSummary {
  uid: string;
  subject: string;
  sender: string;
  received_at: string;
  attachments: string[];
  already_synced: boolean;
  parsed_tx_count: number;
  parsed_rem_count: number;
}

export interface EmailFetchResponse {
  emails: EmailScanSummary[];
  transactions: ParsedBillTransaction[];
  reminders: NoteCandidate[];
  warnings: string[];
}

export interface EmailSyncLog {
  id: number;
  message_uid: string;
  subject: string;
  sender: string;
  received_at: string;
  tx_imported: number;
  rem_imported: number;
  synced_at: string;
}

export interface PhoneNumber {
  id: number;
  label: string;
  phone_number: string;
  country_region: string;
  carrier: string;
  sim_type: 'physical' | 'esim' | 'virtual';
  device_slot: string;
  monthly_fee: number;
  currency: string;
  cny_rate: number;
  monthly_fee_cny: number;
  balance: number;
  extra_comm_cost: number;
  total_comm_spent: number;
  billing_day: number;
  keep_alive_rule: string;
  keep_alive_cycle_days: number;
  remind_before_days: number;
  last_keep_alive_date: string;
  next_keep_alive_date: string;
  keep_alive_days_left: number | null;
  expiry_date: string;
  bound_services: string;
  status: 'active' | 'standby' | 'paused' | 'cancelled';
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface PhoneNumbersResponse {
  items: PhoneNumber[];
  summary: {
    total_count: number;
    active_count: number;
    monthly_cny_total: number;
    annual_cny_total: number;
    extra_cny_total: number;
    urgent_keep_alive_count: number;
  };
}

export type AppSettings = Record<string, string>;

export interface AuthStatus {
  authenticated: boolean;
  setup_required: boolean;
  username: string | null;
  active_sessions: number;
}
