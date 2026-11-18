from typing import Any, Dict, List, Optional
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


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    balance: Optional[float] = None
    credit_limit: Optional[float] = None
    currency: Optional[str] = None


class TransactionCreate(BaseModel):
    type: str = "expense"  # expense | income | transfer
    amount: float = Field(..., gt=0)
    category: str = "其他"
    account_id: Optional[int] = None
    description: str = ""
    occurred_at: Optional[str] = None
    source_type: str = "manual"
    external_ref: str = ""


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


class BatchImportTransactionsRequest(BaseModel):
    transactions: List[TransactionCreate]
    account_id: Optional[int] = None
    skip_duplicates: bool = True


class EmailFetchRequest(BaseModel):
    days: int = Field(14, ge=1, le=365)
    limit: int = Field(25, ge=1, le=100)
    keyword: str = ""
    zip_password: str = ""
    include_body_ai: bool = True
    unread_only: bool = False


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


class EmailImportRequest(BaseModel):
    transactions: List[TransactionCreate] = Field(default_factory=list)
    reminders: List[ReminderCreate] = Field(default_factory=list)
    account_id: Optional[int] = None
    skip_duplicates: bool = True
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
    bark_enabled: Optional[str] = None
    bark_server_url: Optional[str] = None
    bark_device_key: Optional[str] = None
    bark_group: Optional[str] = None
    bark_sound: Optional[str] = None
    bark_push_on_create: Optional[str] = None
    bark_due_advance_min: Optional[str] = None


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
    notes: Optional[str] = None


class PhoneCommRecordRequest(BaseModel):
    kind: str = "topup"  # topup | monthly_fee | extra_cost
    amount: float = Field(..., gt=0)
    description: str = ""
    sync_to_finance: bool = True
    account_id: Optional[int] = None
    mark_keep_alive: bool = False
