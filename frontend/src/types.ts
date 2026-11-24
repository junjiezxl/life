export type TabKey = 'dashboard' | 'reminders' | 'notes' | 'phones' | 'finance' | 'ctrader' | 'ibkr' | 'settings';

export interface IbkrEquitySnapshot {
  report_date: string;
  currency: string;
  total_nav: number;
  total_cash: number;
  stock_value: number;
  options_value: number;
  futures_value: number;
  other_value: number;
  accrued_interest: number;
  dividend_accruals: number;
  day_change: number;
  day_change_pct: number;
}

export interface IbkrDailyTradeSummary {
  trade_date: string;
  trade_count: number;
  buy_count: number;
  sell_count: number;
  realized_pnl: number;
  commission: number;
  net_pnl: number;
  turnover: number;
  symbols: string[];
}

export interface IbkrPosition {
  account_id: string;
  report_date: string;
  conid: string;
  symbol: string;
  asset_category: string;
  description: string;
  currency: string;
  fx_rate_to_base: number;
  position: number;
  mark_price: number;
  position_value: number;
  position_value_base: number;
  open_price: number;
  cost_basis: number;
  unrealized_pnl: number;
  unrealized_pnl_base: number;
  side: string;
  expiry: string;
  strike: string;
  put_call: string;
  multiplier: number;
}

export interface IbkrTrade {
  trade_key: string;
  account_id: string;
  trade_id: string;
  order_id: string;
  symbol: string;
  asset_category: string;
  description: string;
  currency: string;
  side: string;
  open_close: string;
  quantity: number;
  trade_price: number;
  proceeds_base: number;
  commission_base: number;
  realized_pnl_base: number;
  trade_date: string;
  trade_time: string;
  executed_at: string;
}

export interface IbkrCashTransaction {
  tx_key: string;
  account_id: string;
  tx_type: string;
  symbol: string;
  description: string;
  currency: string;
  amount: number;
  amount_base: number;
  report_date: string;
  date_time: string;
}

export interface IbkrOverviewResponse {
  summary: {
    configured: boolean;
    base_currency: string;
    latest_report_date: string;
    total_nav: number;
    total_cash: number;
    stock_value: number;
    options_value: number;
    futures_value: number;
    other_value: number;
    day_change: number;
    day_change_pct: number;
    unrealized_pnl: number;
    total_realized_pnl: number;
    total_commission: number;
    total_net_pnl: number;
    total_dividends: number;
    latest_day_trade_count: number;
    latest_day_net_pnl: number;
    account_count: number;
    position_count: number;
    trade_count: number;
  };
  accounts: string[];
  daily_equity: IbkrEquitySnapshot[];
  daily_trades: IbkrDailyTradeSummary[];
  positions: IbkrPosition[];
  trades: IbkrTrade[];
  cash_transactions: IbkrCashTransaction[];
  sync_status: {
    running: boolean;
    phase: string;
    last_sync_at: string;
    last_error: string | null;
  };
}

export interface CTraderDeal {
  deal_id: string;
  order_id: string;
  position_id: string;
  account_id: string;
  symbol_id: number;
  symbol_name: string;
  side: string;
  volume: number;
  price: number;
  commission: number;
  swap: number;
  gross_profit: number;
  net_profit: number;
  status: string;
  label?: string;
  comment?: string;
  executed_at: string;
  execution_ms?: number;
  is_closing?: number;
  entry_price?: number;
  pushed_at: string;
}

export interface CTraderTrade {
  trade_key: string;
  position_id: string;
  order_ids: string[];
  deal_ids: string[];
  account_id: string;
  symbol_name: string;
  side: string;
  status: 'closed' | 'partial' | 'open';
  volume: number;
  open_volume: number;
  close_volume: number;
  open_price: number | null;
  close_price: number | null;
  gross_profit: number;
  swap: number;
  commission: number;
  net_profit: number;
  label?: string;
  comment?: string;
  opened_at: string;
  closed_at: string;
  executed_at: string;
  execution_ms: number;
  pushed_at: string;
  deal_count: number;
  deals: CTraderDeal[];
}

export interface CTraderStats {
  total_deals: number;
  total_trades?: number;
  closed_trades?: number;
  open_trades?: number;
  total_net_profit: number;
  total_volume: number;
  win_rate: number;
  latest_executed_at?: string;
  earliest_executed_at?: string;
}

export interface CTraderDealsResponse {
  trades?: CTraderTrade[];
  deals: CTraderDeal[];
  stats: CTraderStats;
}

export interface CTraderSyncStatus {
  running: boolean;
  mode: string;
  phase: string;
  from_time: string;
  to_time: string;
  chunks_done: number;
  chunks_total: number;
  deals_fetched: number;
  labels_done: number;
  labels_total: number;
  inserted: number;
  updated: number;
  pushed: number;
  started_at: string;
  finished_at: string;
  last_error: string | null;
  last_result: CTraderSyncResponse | null;
}

export interface CTraderSyncResponse {
  ok?: boolean;
  mode?: string;
  sync_strategy?: string;
  account_id?: string;
  environment?: string;
  from_time?: string;
  to_time?: string;
  fetched?: number;
  inserted?: number;
  updated?: number;
  pushed?: number;
  synced_at?: string;
  started?: boolean;
  already_running?: boolean;
  status?: CTraderSyncStatus;
}

export interface CTraderQuote {
  source: string;
  symbol: string;
  bid: number;
  ask: number;
  spread: number;
  timestamp: number;
  datetime_utc: string;
}

export interface CTraderMarketStatus {
  running: boolean;
  connected: boolean;
  authenticated: boolean;
  enabled: boolean;
  configured: boolean;
  status: 'stopped' | 'unconfigured' | 'connecting' | 'streaming' | 'reconnecting';
  mode: string;
  environment: string;
  account_id: string;
  symbols: string[];
  subscribed_symbols: string[];
  has_api_token: boolean;
  uptime_sec: number;
  ticks_per_sec: number;
  tick_counts: Record<string, number>;
  total_ticks: number;
  active_ws_clients: number;
  last_error: string | null;
  quotes: Record<string, CTraderQuote>;
}

export interface CTraderRecentTicksResponse {
  symbol: string;
  ticks: CTraderQuote[];
  status: CTraderMarketStatus;
}

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
    build?: {
      version?: string;
      sha: string;
      build_time: string;
      commit_msg: string;
    };
  };
}

export interface EmailAccount {
  id: number;
  name: string;
  imap_host: string;
  imap_port: number;
  username: string;
  password?: string;
  has_password?: boolean;
  use_ssl: boolean;
  use_proxy: boolean;
  bill_zip_password: string;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
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
  email_account_name?: string;
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
  email_account_name?: string;
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

export interface PhoneSubCardSummary {
  id: number;
  label: string;
  phone_number: string;
  status: 'active' | 'standby' | 'paused' | 'cancelled';
  monthly_fee_cny: number;
  sim_type: 'physical' | 'esim' | 'virtual';
  device_slot: string;
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
  parent_phone_id: number | null;
  parent_label?: string;
  parent_number?: string;
  sub_cards_count?: number;
  sub_cards?: PhoneSubCardSummary[];
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface PhoneNumbersResponse {
  items: PhoneNumber[];
  summary: {
    total_count: number;
    active_count: number;
    sub_card_count?: number;
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

export interface CreditCard {
  id: number;
  name: string;
  bank_name: string;
  last4: string;
  account_id: number | null;
  credit_limit: number;
  current_debt: number;
  available_credit: number;
  utilization_pct: number;
  statement_bill_amount: number;
  paid_amount_this_cycle: number;
  remaining_bill: number;
  min_payment: number;
  statement_day: number;
  due_day: number;
  grace_days: number;
  swipe_fee_rate: number;
  status: 'active' | 'frozen' | 'closed';
  notes: string;
  next_due_date: string;
  days_until_due: number;
  grace_deadline_date: string;
  swipe_bill_date: string;
  swipe_repay_date: string;
  swipe_free_days: number;
  days_to_next_statement: number;
  is_statement_day: boolean;
  next_cycle_start_date: string;
  next_cycle_free_days: number;
  created_at: string;
  updated_at: string;
}

export interface Loan {
  id: number;
  name: string;
  lender: string;
  total_principal: number;
  remaining_principal: number;
  monthly_payment: number;
  paid_this_month: number;
  remaining_month_payment: number;
  due_day: number;
  apr: number;
  remaining_periods: number;
  account_id: number | null;
  status: 'active' | 'paid_off';
  notes: string;
  next_due_date: string;
  days_until_due: number;
  created_at: string;
  updated_at: string;
}

export interface DebtOperation {
  id: number;
  op_type: 'cash_repay_card' | 'swipe_repay_card' | 'repay_loan' | 'swipe_cash_out';
  source_card_id: number | null;
  source_account_id: number | null;
  target_card_id: number | null;
  target_loan_id: number | null;
  source_card_name?: string;
  target_card_name?: string;
  target_loan_name?: string;
  source_account_name?: string;
  amount: number;
  fee_amount: number;
  op_date: string;
  note: string;
  tx_id: number | null;
  created_at: string;
}

export interface DebtOverviewResponse {
  cards: CreditCard[];
  loans: Loan[];
  operations: DebtOperation[];
  suggested_cash: number;
  summary: {
    active_card_count: number;
    active_loan_count: number;
    total_credit_limit: number;
    total_card_debt: number;
    total_available_credit: number;
    overall_utilization_pct: number;
    total_remaining_bill: number;
    total_loan_principal: number;
    total_loan_month_remaining: number;
    total_debt: number;
    total_fee_spent: number;
  };
}

export interface DebtPlanStep {
  step_no: number;
  op_type: 'cash_repay_card' | 'swipe_repay_card' | 'repay_loan' | 'swipe_cash_out';
  title: string;
  source_type: 'cash' | 'credit_card';
  source_id: number | null;
  source_name: string;
  target_type: 'credit_card' | 'loan';
  target_id: number;
  target_name: string;
  amount: number;
  fee_amount: number;
  due_date: string;
  days_until_due: number;
  free_days_gained: number;
  repay_date: string;
  reason: string;
  risk_tip: string;
}

export interface DebtPlanResponse {
  strategy: 'balanced' | 'max_free_days' | 'min_fee';
  horizon_days: number;
  available_cash_input: number;
  remaining_cash_after: number;
  summary: {
    obligation_count: number;
    total_due: number;
    total_loan_due: number;
    total_card_due: number;
    cash_used: number;
    swipe_used: number;
    total_fee: number;
    avg_free_days: number;
    total_shortfall: number;
    fully_covered: boolean;
  };
  steps: DebtPlanStep[];
  uncovered_obligations: Array<{
    kind: string;
    id: number;
    name: string;
    shortfall: number;
    due_date: string;
  }>;
  warnings: string[];
  swipe_ranking: Array<{
    id: number;
    name: string;
    usable_available: number;
    swipe_free_days: number;
    swipe_repay_date: string;
    swipe_fee_rate: number;
    utilization_pct: number;
    days_to_next_statement: number;
    next_cycle_free_days: number;
  }>;
}
