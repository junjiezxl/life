from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ReminderCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = ""
    due_at: Optional[str] = None
    priority: str = "medium"  # high | medium | low
    category: str = "life"  # work | life | finance | health | study
    status: str = "pending"  # pending | done | snoozed
    source_type: str = "manual"
    source_ref: str = ""
    linked_amount: Optional[float] = None
    linked_tx_type: Optional[str] = None


class ReminderUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_at: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    linked_amount: Optional[float] = None
    linked_tx_type: Optional[str] = None


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    type: str = "bank"  # cash | bank | investment | credit_card | loan
    balance: float = 0.0
    credit_limit: float = 0.0
    currency: str = "CNY"
    bank_name: str = ""
    card_last4: str = Field("", max_length=8)


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    balance: Optional[float] = None
    credit_limit: Optional[float] = None
    currency: Optional[str] = None
    bank_name: Optional[str] = None
    card_last4: Optional[str] = None


class TransactionCreate(BaseModel):
    type: str = "expense"  # expense | income | transfer
    amount: float = Field(..., gt=0)
    category: str = "其他"
    account_id: Optional[int] = None
    description: str = ""
    occurred_at: Optional[str] = None
    source_type: str = "manual"
    external_ref: str = ""
    payment_channel: str = ""  # alipay | wechat | card_direct | cash | bank | email | manual
    payment_method: str = ""
    bank_name: str = ""
    card_last4: str = ""
    merchant: str = ""
    original_currency: str = "CNY"
    original_amount: Optional[float] = None
    auto_match_account: bool = True


class TransactionUpdate(BaseModel):
    category: Optional[str] = Field(None, min_length=1, max_length=40)
    description: Optional[str] = Field(None, max_length=300)
    payment_channel: Optional[str] = None
    payment_method: Optional[str] = None
    card_last4: Optional[str] = None
    account_id: Optional[int] = None


class RecurringBillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    amount: float = Field(..., gt=0)
    category: str = "账单"
    account_id: Optional[int] = None
    billing_day: int = Field(1, ge=1, le=31)
    cycle: str = "monthly"  # monthly | yearly | weekly
    next_due_date: str
    auto_remind_days: int = Field(3, ge=0, le=30)
    active: bool = True


class RecurringBillUpdate(BaseModel):
    name: Optional[str] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    account_id: Optional[int] = None
    billing_day: Optional[int] = None
    cycle: Optional[str] = None
    next_due_date: Optional[str] = None
    auto_remind_days: Optional[int] = None
    active: Optional[bool] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class NoteSnippet(BaseModel):
    id: str = ""
    title: str = "未命名笔记"
    path: str = ""
    content: str = ""
    updated: str = ""


class NoteExtractRequest(BaseModel):
    notes: List[NoteSnippet] = Field(default_factory=list)
    raw_text: str = ""


class SiyuanFetchRequest(BaseModel):
    keyword: str = ""
    sql: str = ""
    limit: int = 20


class BatchImportRemindersRequest(BaseModel):
    reminders: List[ReminderCreate]


class SettlementCreateRequest(BaseModel):
    period_label: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    transaction_ids: Optional[List[int]] = None


class SettlementRegenerateRequest(BaseModel):
    period_label: Optional[str] = None


class BatchImportTransactionsRequest(BaseModel):
    transactions: List[TransactionCreate]
    account_id: Optional[int] = None
    skip_duplicates: bool = True
    auto_create_accounts: bool = True
    auto_reconcile: bool = True


class BillReconcileRequest(BaseModel):
    backfill_accounts: bool = True
    auto_create_accounts: bool = True


class EmailFetchRequest(BaseModel):
    days: int = Field(14, ge=1, le=1825)
    limit: int = Field(25, ge=1, le=5000)
    keyword: str = ""
    zip_password: str = ""
    include_body_ai: bool = True
    unread_only: bool = False
    account_id: Optional[int] = None
    source: Literal["auto", "cache", "imap"] = "auto"


class EmailCacheSyncRequest(BaseModel):
    days: int = Field(30, ge=1, le=1825)
    limit: int = Field(100, ge=1, le=5000)
    account_id: Optional[int] = None
    force_refresh: bool = False
    zip_password: str = ""
    background: bool = False


class EmailAccountCreate(BaseModel):
    name: str = ""
    imap_host: str = Field(..., min_length=1, max_length=200)
    imap_port: int = Field(993, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=500)
    use_ssl: bool = True
    use_proxy: bool = False
    bill_zip_password: str = ""
    enabled: bool = True


class EmailAccountUpdate(BaseModel):
    name: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    use_ssl: Optional[bool] = None
    use_proxy: Optional[bool] = None
    bill_zip_password: Optional[str] = None
    enabled: Optional[bool] = None


class EmailParseTextRequest(BaseModel):
    subject: str = "手动粘贴邮件"
    sender: str = ""
    received_at: str = ""
    body: str = Field(..., min_length=1)


class EmailSyncLogInput(BaseModel):
    message_uid: str
    subject: str = ""
    sender: str = ""
    received_at: str = ""
    tx_imported: int = 0
    rem_imported: int = 0


class EmailStockTradeItem(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=24)
    name: str = ""
    market: str = ""  # CN | HK | US (auto-inferred if blank)
    currency: str = ""
    side: Literal["buy", "sell"]
    price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    fee: float = Field(0.0, ge=0)
    tax: float = Field(0.0, ge=0)
    broker: str = "HSBC"
    traded_at: str = ""
    external_ref: str = ""
    note: str = ""


class EmailImportRequest(BaseModel):
    transactions: List[TransactionCreate] = Field(default_factory=list)
    reminders: List[ReminderCreate] = Field(default_factory=list)
    stock_trades: List[EmailStockTradeItem] = Field(default_factory=list)
    account_id: Optional[int] = None
    skip_duplicates: bool = True
    auto_create_accounts: bool = True
    auto_reconcile: bool = True
    auto_create_grid_strategies: bool = False
    email_logs: List[EmailSyncLogInput] = Field(default_factory=list)


class SettingsUpdateRequest(BaseModel):
    ai_provider: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_model: Optional[str] = None
    siyuan_mode: Optional[str] = None
    mcp_command: Optional[str] = None
    mcp_args_json: Optional[str] = None
    mcp_env_json: Optional[str] = None
    mcp_url: Optional[str] = None
    siyuan_api_url: Optional[str] = None
    siyuan_token: Optional[str] = None
    siyuan_sql_filter: Optional[str] = None
    email_imap_host: Optional[str] = None
    email_imap_port: Optional[str] = None
    email_username: Optional[str] = None
    email_password: Optional[str] = None
    email_use_ssl: Optional[str] = None
    email_bill_zip_password: Optional[str] = None
    email_auto_sync_enabled: Optional[str] = None
    email_sync_interval_min: Optional[str] = None
    email_sync_days: Optional[str] = None
    email_backfill_730d_done: Optional[str] = None
    bark_enabled: Optional[str] = None
    bark_server_url: Optional[str] = None
    bark_device_key: Optional[str] = None
    bark_group: Optional[str] = None
    bark_sound: Optional[str] = None
    bark_push_on_create: Optional[str] = None
    bark_push_on_update: Optional[str] = None
    bark_due_advance_min: Optional[str] = None
    extra_push_enabled: Optional[str] = None
    extra_push_urls: Optional[str] = None
    global_proxy_url: Optional[str] = None
    global_proxy_enabled: Optional[str] = None
    ai_use_proxy: Optional[str] = None
    email_use_proxy: Optional[str] = None
    ctrader_oauth_use_proxy: Optional[str] = None
    ibkr_use_proxy: Optional[str] = None
    bark_use_proxy: Optional[str] = None
    ctrader_client_id: Optional[str] = None
    ctrader_client_secret: Optional[str] = None
    ctrader_access_token: Optional[str] = None
    ctrader_refresh_token: Optional[str] = None
    ctrader_token_expires_at: Optional[str] = None
    ctrader_auto_refresh_enabled: Optional[str] = None
    ctrader_oauth_redirect_uri: Optional[str] = None
    ctrader_oauth_proxy: Optional[str] = None
    ctrader_account_id: Optional[str] = None
    ctrader_environment: Optional[str] = None
    ctrader_auto_sync_enabled: Optional[str] = None
    ctrader_sync_interval_sec: Optional[str] = None
    ctrader_bark_push_enabled: Optional[str] = None
    ctrader_market_enabled: Optional[str] = None
    ctrader_market_symbols: Optional[str] = None
    ctrader_market_api_token: Optional[str] = None
    ibkr_flex_token: Optional[str] = None
    ibkr_flex_query_id: Optional[str] = None
    ibkr_auto_sync_enabled: Optional[str] = None
    ibkr_sync_interval_min: Optional[str] = None
    ibkr_bark_daily_summary: Optional[str] = None
    ibkr_proxy_url: Optional[str] = None
    ibgw_enabled: Optional[str] = None
    ibgw_host: Optional[str] = None
    ibgw_port: Optional[str] = None
    ibgw_client_id: Optional[str] = None
    ibgw_trading_mode: Optional[str] = None
    ibgw_paper_only_guard: Optional[str] = None
    ibgw_auto_reconnect: Optional[str] = None
    ibgw_auto_grid_enabled: Optional[str] = None
    ibgw_sync_interval_sec: Optional[str] = None
    ibgw_notify_fills: Optional[str] = None
    ibgw_notify_disconnect: Optional[str] = None
    ibgw_account: Optional[str] = None


class IBKRSyncRequest(BaseModel):
    query_id: Optional[str] = None
    push_summary: Optional[bool] = None
    sync_grids: bool = True
    reconcile_orders: bool = False


class IbGatewaySyncRequest(BaseModel):
    sync_grids: bool = True
    reconcile_orders: bool = False
    push_summary: bool = False


class IbGatewayTestOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    market: Literal["CN", "HK", "US"] = "US"
    side: Literal["buy", "sell"] = "buy"
    quantity: float = Field(1.0, gt=0)
    lmt_price: float = Field(..., gt=0)
    currency: str = ""
    exchange: str = ""
    strategy_id: Optional[int] = None
    dry_run: bool = False


class GridAutoSyncOrdersRequest(BaseModel):
    cancel_only: bool = False


class CTraderSyncRequest(BaseModel):
    mode: Literal["incremental", "range"] = "incremental"
    days: int = Field(default=7, ge=1, le=3650)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    overlap_seconds: int = Field(default=3, ge=0, le=300)
    push_new: Optional[bool] = None
    background: bool = False


class CTraderMarketActionRequest(BaseModel):
    symbols: Optional[str] = None


class CTraderOAuthExchangeRequest(BaseModel):
    code_or_url: str = Field(..., min_length=1, max_length=2000)
    redirect_uri: str = ""


class CTraderOAuthRefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class PhoneNumberCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    phone_number: str = Field(..., min_length=3, max_length=60)
    country_region: str = "中国大陆 (+86)"
    carrier: str = ""
    sim_type: str = "physical"  # physical | esim | virtual
    device_slot: str = ""
    monthly_fee: float = Field(0.0, ge=0)
    currency: str = "CNY"
    cny_rate: float = Field(1.0, gt=0)
    balance: float = 0.0
    extra_comm_cost: float = Field(0.0, ge=0)
    total_comm_spent: float = Field(0.0, ge=0)
    billing_day: int = Field(1, ge=1, le=31)
    keep_alive_rule: str = ""
    keep_alive_cycle_days: int = Field(0, ge=0, le=3650)
    remind_before_days: int = Field(7, ge=0, le=180)
    last_keep_alive_date: str = ""
    next_keep_alive_date: str = ""
    expiry_date: str = ""
    bound_services: str = ""
    status: str = "active"  # active | standby | paused | cancelled
    parent_phone_id: Optional[int] = None
    notes: str = ""


class PhoneNumberUpdate(BaseModel):
    label: Optional[str] = None
    phone_number: Optional[str] = None
    country_region: Optional[str] = None
    carrier: Optional[str] = None
    sim_type: Optional[str] = None
    device_slot: Optional[str] = None
    monthly_fee: Optional[float] = None
    currency: Optional[str] = None
    cny_rate: Optional[float] = None
    balance: Optional[float] = None
    extra_comm_cost: Optional[float] = None
    total_comm_spent: Optional[float] = None
    billing_day: Optional[int] = None
    keep_alive_rule: Optional[str] = None
    keep_alive_cycle_days: Optional[int] = None
    remind_before_days: Optional[int] = None
    last_keep_alive_date: Optional[str] = None
    next_keep_alive_date: Optional[str] = None
    expiry_date: Optional[str] = None
    bound_services: Optional[str] = None
    status: Optional[str] = None
    parent_phone_id: Optional[int] = None
    notes: Optional[str] = None


class PhoneCommRecordRequest(BaseModel):
    kind: str = "topup"  # topup | monthly_fee | extra_cost
    amount: float = Field(..., gt=0)
    description: str = ""
    sync_to_finance: bool = True
    account_id: Optional[int] = None
    mark_keep_alive: bool = False


class AuthSetupRequest(BaseModel):
    username: str = Field("admin", min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)


class AuthLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    remember_me: bool = True


class AuthChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: Optional[str] = Field(None, max_length=128)
    new_username: Optional[str] = Field(None, max_length=64)


class CreditCardCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    bank_name: str = ""
    last4: str = Field("", max_length=8)
    account_id: Optional[int] = None
    credit_limit: float = Field(..., ge=0)
    current_debt: float = Field(0.0, ge=0)
    statement_bill_amount: float = Field(0.0, ge=0)
    paid_amount_this_cycle: float = Field(0.0, ge=0)
    min_payment: float = Field(0.0, ge=0)
    statement_day: int = Field(5, ge=1, le=31)
    due_day: int = Field(23, ge=1, le=31)
    grace_days: int = Field(3, ge=0, le=10)
    swipe_fee_rate: float = Field(0.006, ge=0, le=0.1)
    status: str = "active"  # active | frozen | closed
    notes: str = ""


class CreditCardUpdate(BaseModel):
    name: Optional[str] = None
    bank_name: Optional[str] = None
    last4: Optional[str] = None
    account_id: Optional[int] = None
    credit_limit: Optional[float] = None
    current_debt: Optional[float] = None
    statement_bill_amount: Optional[float] = None
    paid_amount_this_cycle: Optional[float] = None
    min_payment: Optional[float] = None
    statement_day: Optional[int] = None
    due_day: Optional[int] = None
    grace_days: Optional[int] = None
    swipe_fee_rate: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class LoanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    lender: str = ""
    total_principal: float = Field(0.0, ge=0)
    remaining_principal: float = Field(0.0, ge=0)
    monthly_payment: float = Field(..., ge=0)
    paid_this_month: float = Field(0.0, ge=0)
    due_day: int = Field(15, ge=1, le=31)
    apr: float = Field(0.0, ge=0, le=100)
    remaining_periods: int = Field(12, ge=0, le=600)
    account_id: Optional[int] = None
    status: str = "active"  # active | paid_off
    notes: str = ""


class LoanUpdate(BaseModel):
    name: Optional[str] = None
    lender: Optional[str] = None
    total_principal: Optional[float] = None
    remaining_principal: Optional[float] = None
    monthly_payment: Optional[float] = None
    paid_this_month: Optional[float] = None
    due_day: Optional[int] = None
    apr: Optional[float] = None
    remaining_periods: Optional[int] = None
    account_id: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class DebtPlanRequest(BaseModel):
    available_cash: Optional[float] = Field(None, ge=0)
    horizon_days: int = Field(30, ge=1, le=90)
    reserve_ratio: float = Field(0.05, ge=0.0, le=0.5)
    strategy: str = "balanced"  # balanced | max_free_days | min_fee
    allow_relay: bool = True


class DebtOperationCreate(BaseModel):
    op_type: str  # cash_repay_card | swipe_repay_card | repay_loan | swipe_cash_out
    source_card_id: Optional[int] = None
    source_account_id: Optional[int] = None
    target_card_id: Optional[int] = None
    target_loan_id: Optional[int] = None
    amount: float = Field(..., gt=0)
    fee_amount: float = Field(0.0, ge=0)
    op_date: Optional[str] = None
    note: str = ""
    sync_to_finance: bool = False


class GridStrategyCreate(BaseModel):
    name: str = Field("", max_length=60)
    symbol: str = Field(..., min_length=1, max_length=20)
    market: Literal["CN", "HK", "US"] = "CN"
    currency: str = ""
    grid_type: Literal["arithmetic", "geometric"] = "arithmetic"
    lower_price: float = Field(0.0, ge=0)
    upper_price: float = Field(0.0, ge=0)
    step_value: float = Field(0.0, ge=0)
    shares_per_grid: float = Field(100.0, ge=0)
    base_price: float = Field(0.0, ge=0)
    fee_rate: float = Field(0.00025, ge=0, le=0.05)
    min_fee: float = Field(5.0, ge=0)
    stamp_tax_rate: float = Field(0.0, ge=0, le=0.05)
    stamp_tax_side: Literal["sell", "both"] = "sell"
    current_price: float = Field(0.0, ge=0)
    ibkr_auto_sync: bool = True
    broker: str = Field("", max_length=40)
    auto_trade_enabled: bool = False
    auto_allow_live: bool = False
    max_holding_qty: float = Field(0.0, ge=0)
    stop_buy_below_lower: bool = True
    lot_size: float = Field(0.0, ge=0)
    tick_size: float = Field(0.0, ge=0)
    conid: str = Field("", max_length=32)
    exchange: str = Field("", max_length=32)
    status: Literal["active", "paused", "closed"] = "active"
    notes: str = ""


class GridStrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=60)
    symbol: Optional[str] = Field(None, min_length=1, max_length=20)
    market: Optional[Literal["CN", "HK", "US"]] = None
    currency: Optional[str] = None
    grid_type: Optional[Literal["arithmetic", "geometric"]] = None
    lower_price: Optional[float] = Field(None, ge=0)
    upper_price: Optional[float] = Field(None, ge=0)
    step_value: Optional[float] = Field(None, ge=0)
    shares_per_grid: Optional[float] = Field(None, ge=0)
    base_price: Optional[float] = Field(None, ge=0)
    fee_rate: Optional[float] = Field(None, ge=0, le=0.05)
    min_fee: Optional[float] = Field(None, ge=0)
    stamp_tax_rate: Optional[float] = Field(None, ge=0, le=0.05)
    stamp_tax_side: Optional[Literal["sell", "both"]] = None
    current_price: Optional[float] = Field(None, ge=0)
    ibkr_auto_sync: Optional[bool] = None
    broker: Optional[str] = Field(None, max_length=40)
    auto_trade_enabled: Optional[bool] = None
    auto_allow_live: Optional[bool] = None
    max_holding_qty: Optional[float] = Field(None, ge=0)
    stop_buy_below_lower: Optional[bool] = None
    lot_size: Optional[float] = Field(None, ge=0)
    tick_size: Optional[float] = Field(None, ge=0)
    conid: Optional[str] = Field(None, max_length=32)
    exchange: Optional[str] = Field(None, max_length=32)
    status: Optional[Literal["active", "paused", "closed"]] = None
    notes: Optional[str] = None


class GridIbkrSyncRequest(BaseModel):
    strategy_id: Optional[int] = None
    create_symbols: List[str] = Field(default_factory=list)
    refresh_flex_first: bool = False
    refresh_gateway_first: bool = False
    default_step_pct: float = Field(3.0, gt=0.1, le=50.0)


class GridEmailFetchRequest(BaseModel):
    days: int = Field(30, ge=1, le=1825)
    limit: int = Field(50, ge=1, le=5000)
    keyword: str = ""
    pdf_password: str = ""
    account_id: Optional[int] = None
    include_body_ai: bool = True
    source: Literal["auto", "cache", "imap"] = "auto"


class GridEmailImportRequest(BaseModel):
    trades: List[EmailStockTradeItem] = Field(default_factory=list)
    strategy_id: Optional[int] = None
    auto_create_missing: bool = True
    default_step_pct: float = Field(3.0, gt=0.1, le=50.0)
    broker_tag: str = ""


class StockTradeCreate(BaseModel):
    side: Literal["buy", "sell"]
    price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    fee: Optional[float] = Field(None, ge=0)
    tax: Optional[float] = Field(None, ge=0)
    traded_at: Optional[str] = None
    grid_level: Optional[int] = Field(None, ge=0)
    note: str = ""
