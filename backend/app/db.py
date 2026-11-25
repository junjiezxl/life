import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("LIFEHUB_DB_PATH", os.path.join(DATA_DIR, "life_hub.db"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_db_dir() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


DEFAULT_SETTINGS: Dict[str, str] = {
    "ai_provider": "local_fallback",  # gemini | openai_compatible | local_fallback
    "ai_api_key": "",
    "ai_base_url": "https://api.openai.com/v1",
    "ai_model": "gemini-2.5-flash",
    "siyuan_mode": "mcp_stdio",  # mcp_stdio | mcp_http | siyuan_api
    "mcp_command": "npx",
    "mcp_args_json": json.dumps(["-y", "siyuan-mcp@latest"]),
    "mcp_env_json": json.dumps({"SIYUAN_HOST": "127.0.0.1", "SIYUAN_PORT": "6806", "SIYUAN_TOKEN": ""}),
    "mcp_url": "http://127.0.0.1:3000/mcp",
    "siyuan_api_url": "http://127.0.0.1:6806",
    "siyuan_token": "",
    "siyuan_sql_filter": "SELECT id, box, hpath, content, markdown, updated, type, subtype FROM blocks WHERE (markdown LIKE '%[ ]%' OR content LIKE '%提醒%' OR content LIKE '%TODO%' OR content LIKE '%待办%') ORDER BY updated DESC LIMIT 20",
    "email_imap_host": "imap.qq.com",
    "email_imap_port": "993",
    "email_username": "",
    "email_password": "",
    "email_use_ssl": "1",
    "email_bill_zip_password": "",
    "email_auto_sync_enabled": "0",
    "email_sync_interval_min": "30",
    "bark_enabled": "0",
    "bark_server_url": "https://api.day.app",
    "bark_device_key": "",
    "bark_group": "LifeHub",
    "bark_sound": "bell",
    "bark_push_on_create": "1",
    "bark_push_on_update": "1",
    "bark_due_advance_min": "15",
    "global_proxy_url": "",
    "global_proxy_enabled": "1",
    "ai_use_proxy": "0",
    "email_use_proxy": "0",
    "ctrader_oauth_use_proxy": "1",
    "ibkr_use_proxy": "0",
    "bark_use_proxy": "0",
    "ctrader_client_id": "",
    "ctrader_client_secret": "",
    "ctrader_access_token": "",
    "ctrader_refresh_token": "",
    "ctrader_token_expires_at": "",
    "ctrader_auto_refresh_enabled": "1",
    "ctrader_oauth_redirect_uri": "",
    "ctrader_oauth_proxy": "",
    "ctrader_account_id": "",
    "ctrader_environment": "demo",
    "ctrader_auto_sync_enabled": "0",
    "ctrader_sync_interval_sec": "60",
    "ctrader_bark_push_enabled": "1",
    "ctrader_market_enabled": "1",
    "ctrader_market_symbols": "XAUUSD,EURUSD",
    "ctrader_market_api_token": "",
    "ibkr_flex_token": "",
    "ibkr_flex_query_id": "",
    "ibkr_auto_sync_enabled": "0",
    "ibkr_sync_interval_min": "360",
    "ibkr_bark_daily_summary": "0",
    "ibkr_proxy_url": "",
    "ibkr_last_notified_date": "",
}


def init_db(seed_demo: bool = True) -> None:
    ensure_db_dir()
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                due_at TEXT,
                priority TEXT NOT NULL DEFAULT 'medium',
                category TEXT NOT NULL DEFAULT 'life',
                status TEXT NOT NULL DEFAULT 'pending',
                source_type TEXT NOT NULL DEFAULT 'manual',
                source_ref TEXT DEFAULT '',
                linked_amount REAL,
                linked_tx_type TEXT,
                bark_notified_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                credit_limit REAL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'CNY',
                bank_name TEXT DEFAULT '',
                card_last4 TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_label TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                total_income REAL NOT NULL,
                total_expense REAL NOT NULL,
                net_cashflow REAL NOT NULL,
                net_worth_snapshot REAL NOT NULL,
                tx_count INTEGER NOT NULL,
                category_breakdown_json TEXT NOT NULL,
                account_snapshot_json TEXT NOT NULL,
                ai_summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                description TEXT DEFAULT '',
                occurred_at TEXT NOT NULL,
                settlement_id INTEGER REFERENCES settlements(id) ON DELETE SET NULL,
                source_type TEXT NOT NULL DEFAULT 'manual',
                external_ref TEXT DEFAULT '',
                payment_channel TEXT DEFAULT 'manual',
                payment_method TEXT DEFAULT '',
                bank_name TEXT DEFAULT '',
                card_last4 TEXT DEFAULT '',
                credit_card_id INTEGER REFERENCES credit_cards(id) ON DELETE SET NULL,
                reconcile_status TEXT DEFAULT 'unmatched',
                matched_tx_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
                merchant TEXT DEFAULT '',
                original_currency TEXT DEFAULT 'CNY',
                original_amount REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_uid TEXT UNIQUE,
                subject TEXT DEFAULT '',
                sender TEXT DEFAULT '',
                received_at TEXT DEFAULT '',
                tx_imported INTEGER NOT NULL DEFAULT 0,
                rem_imported INTEGER NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                imap_host TEXT NOT NULL,
                imap_port INTEGER NOT NULL DEFAULT 993,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                use_ssl INTEGER NOT NULL DEFAULT 1,
                use_proxy INTEGER NOT NULL DEFAULT 0,
                bill_zip_password TEXT DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recurring_bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                billing_day INTEGER NOT NULL DEFAULT 1,
                cycle TEXT NOT NULL DEFAULT 'monthly',
                next_due_date TEXT NOT NULL,
                auto_remind_days INTEGER NOT NULL DEFAULT 3,
                active INTEGER NOT NULL DEFAULT 1,
                last_generated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                actions_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ctrader_deals (
                deal_id TEXT PRIMARY KEY,
                order_id TEXT DEFAULT '',
                position_id TEXT DEFAULT '',
                account_id TEXT NOT NULL DEFAULT '',
                symbol_id INTEGER DEFAULT 0,
                symbol_name TEXT DEFAULT '',
                side TEXT DEFAULT '',
                volume REAL NOT NULL DEFAULT 0,
                price REAL NOT NULL DEFAULT 0,
                commission REAL NOT NULL DEFAULT 0,
                swap REAL NOT NULL DEFAULT 0,
                gross_profit REAL NOT NULL DEFAULT 0,
                net_profit REAL NOT NULL DEFAULT 0,
                status TEXT DEFAULT '',
                label TEXT DEFAULT '',
                comment TEXT DEFAULT '',
                executed_at TEXT NOT NULL,
                execution_ms INTEGER NOT NULL DEFAULT 0,
                is_closing INTEGER NOT NULL DEFAULT 0,
                entry_price REAL NOT NULL DEFAULT 0,
                stop_loss REAL NOT NULL DEFAULT 0,
                take_profit REAL NOT NULL DEFAULT 0,
                account_balance REAL NOT NULL DEFAULT 0,
                pushed_at TEXT DEFAULT '',
                raw_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ibkr_equity_snapshots (
                account_id TEXT NOT NULL,
                report_date TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                total_nav REAL NOT NULL DEFAULT 0,
                total_cash REAL NOT NULL DEFAULT 0,
                stock_value REAL NOT NULL DEFAULT 0,
                options_value REAL NOT NULL DEFAULT 0,
                futures_value REAL NOT NULL DEFAULT 0,
                other_value REAL NOT NULL DEFAULT 0,
                starting_cash REAL NOT NULL DEFAULT 0,
                accrued_interest REAL NOT NULL DEFAULT 0,
                dividend_accruals REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, report_date)
            );

            CREATE TABLE IF NOT EXISTS ibkr_positions (
                account_id TEXT NOT NULL,
                report_date TEXT NOT NULL DEFAULT '',
                conid TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '',
                asset_category TEXT NOT NULL DEFAULT 'STK',
                description TEXT DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'USD',
                fx_rate_to_base REAL NOT NULL DEFAULT 1,
                position REAL NOT NULL DEFAULT 0,
                mark_price REAL NOT NULL DEFAULT 0,
                position_value REAL NOT NULL DEFAULT 0,
                position_value_base REAL NOT NULL DEFAULT 0,
                open_price REAL NOT NULL DEFAULT 0,
                cost_basis REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                unrealized_pnl_base REAL NOT NULL DEFAULT 0,
                side TEXT DEFAULT '',
                expiry TEXT DEFAULT '',
                strike TEXT DEFAULT '',
                put_call TEXT DEFAULT '',
                multiplier REAL NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, conid)
            );

            CREATE TABLE IF NOT EXISTS ibkr_trades (
                trade_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                trade_id TEXT DEFAULT '',
                order_id TEXT DEFAULT '',
                conid TEXT DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                asset_category TEXT NOT NULL DEFAULT 'STK',
                description TEXT DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'USD',
                fx_rate_to_base REAL NOT NULL DEFAULT 1,
                side TEXT DEFAULT '',
                open_close TEXT DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                trade_price REAL NOT NULL DEFAULT 0,
                proceeds REAL NOT NULL DEFAULT 0,
                proceeds_base REAL NOT NULL DEFAULT 0,
                commission REAL NOT NULL DEFAULT 0,
                commission_base REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                realized_pnl_base REAL NOT NULL DEFAULT 0,
                trade_date TEXT NOT NULL DEFAULT '',
                trade_time TEXT DEFAULT '',
                report_date TEXT NOT NULL DEFAULT '',
                executed_at TEXT NOT NULL DEFAULT '',
                expiry TEXT DEFAULT '',
                strike TEXT DEFAULT '',
                put_call TEXT DEFAULT '',
                multiplier REAL NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ibkr_cash_transactions (
                tx_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                transaction_id TEXT DEFAULT '',
                tx_type TEXT DEFAULT '',
                symbol TEXT DEFAULT '',
                description TEXT DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'USD',
                fx_rate_to_base REAL NOT NULL DEFAULT 1,
                amount REAL NOT NULL DEFAULT 0,
                amount_base REAL NOT NULL DEFAULT 0,
                report_date TEXT NOT NULL DEFAULT '',
                date_time TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS phone_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                country_region TEXT NOT NULL DEFAULT '中国大陆 (+86)',
                carrier TEXT DEFAULT '',
                sim_type TEXT NOT NULL DEFAULT 'physical',
                device_slot TEXT DEFAULT '',
                monthly_fee REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'CNY',
                cny_rate REAL NOT NULL DEFAULT 1.0,
                balance REAL NOT NULL DEFAULT 0,
                extra_comm_cost REAL NOT NULL DEFAULT 0,
                total_comm_spent REAL NOT NULL DEFAULT 0,
                billing_day INTEGER NOT NULL DEFAULT 1,
                keep_alive_rule TEXT DEFAULT '',
                keep_alive_cycle_days INTEGER NOT NULL DEFAULT 0,
                remind_before_days INTEGER NOT NULL DEFAULT 7,
                last_keep_alive_date TEXT DEFAULT '',
                next_keep_alive_date TEXT DEFAULT '',
                expiry_date TEXT DEFAULT '',
                bound_services TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                parent_phone_id INTEGER REFERENCES phone_numbers(id) ON DELETE SET NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                remember_me INTEGER NOT NULL DEFAULT 1,
                user_agent TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS credit_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                bank_name TEXT DEFAULT '',
                last4 TEXT DEFAULT '',
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                credit_limit REAL NOT NULL DEFAULT 0,
                current_debt REAL NOT NULL DEFAULT 0,
                statement_bill_amount REAL NOT NULL DEFAULT 0,
                paid_amount_this_cycle REAL NOT NULL DEFAULT 0,
                min_payment REAL NOT NULL DEFAULT 0,
                statement_day INTEGER NOT NULL DEFAULT 5,
                due_day INTEGER NOT NULL DEFAULT 23,
                grace_days INTEGER NOT NULL DEFAULT 3,
                swipe_fee_rate REAL NOT NULL DEFAULT 0.006,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                lender TEXT DEFAULT '',
                total_principal REAL NOT NULL DEFAULT 0,
                remaining_principal REAL NOT NULL DEFAULT 0,
                monthly_payment REAL NOT NULL DEFAULT 0,
                paid_this_month REAL NOT NULL DEFAULT 0,
                due_day INTEGER NOT NULL DEFAULT 15,
                apr REAL NOT NULL DEFAULT 0,
                remaining_periods INTEGER NOT NULL DEFAULT 12,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS debt_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                op_type TEXT NOT NULL,
                source_card_id INTEGER REFERENCES credit_cards(id) ON DELETE SET NULL,
                source_account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                target_card_id INTEGER REFERENCES credit_cards(id) ON DELETE SET NULL,
                target_loan_id INTEGER REFERENCES loans(id) ON DELETE SET NULL,
                amount REAL NOT NULL,
                fee_amount REAL NOT NULL DEFAULT 0,
                op_date TEXT NOT NULL,
                note TEXT DEFAULT '',
                tx_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        from .services.auth_service import ensure_env_auth_user

        ensure_env_auth_user(conn)

        # Safe column migration for existing databases
        acc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        if "bank_name" not in acc_cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN bank_name TEXT DEFAULT ''")
        if "card_last4" not in acc_cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN card_last4 TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acc_card_last4 ON accounts(card_last4)")

        tx_cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        if "external_ref" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN external_ref TEXT DEFAULT ''")
        if "payment_channel" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN payment_channel TEXT DEFAULT 'manual'")
        if "payment_method" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN payment_method TEXT DEFAULT ''")
        if "bank_name" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN bank_name TEXT DEFAULT ''")
        if "card_last4" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN card_last4 TEXT DEFAULT ''")
        if "credit_card_id" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN credit_card_id INTEGER REFERENCES credit_cards(id) ON DELETE SET NULL")
        if "reconcile_status" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN reconcile_status TEXT DEFAULT 'unmatched'")
        if "matched_tx_id" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN matched_tx_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL")
        if "merchant" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN merchant TEXT DEFAULT ''")
        if "original_currency" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN original_currency TEXT DEFAULT 'CNY'")
        if "original_amount" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN original_amount REAL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_external_ref ON transactions(external_ref)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_card_last4 ON transactions(card_last4)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_reconcile_status ON transactions(reconcile_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_payment_channel ON transactions(payment_channel)")

        phone_cols = {r["name"] for r in conn.execute("PRAGMA table_info(phone_numbers)").fetchall()}
        if "remind_before_days" not in phone_cols:
            conn.execute("ALTER TABLE phone_numbers ADD COLUMN remind_before_days INTEGER NOT NULL DEFAULT 7")
        if "parent_phone_id" not in phone_cols:
            conn.execute("ALTER TABLE phone_numbers ADD COLUMN parent_phone_id INTEGER REFERENCES phone_numbers(id) ON DELETE SET NULL")

        rem_cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminders)").fetchall()}
        ctrader_cols = {r["name"] for r in conn.execute("PRAGMA table_info(ctrader_deals)").fetchall()}
        if "label" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN label TEXT DEFAULT ''")
        if "comment" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN comment TEXT DEFAULT ''")
        if "execution_ms" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN execution_ms INTEGER NOT NULL DEFAULT 0")
        if "is_closing" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN is_closing INTEGER NOT NULL DEFAULT 0")
        if "entry_price" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN entry_price REAL NOT NULL DEFAULT 0")
        if "stop_loss" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN stop_loss REAL NOT NULL DEFAULT 0")
        if "take_profit" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN take_profit REAL NOT NULL DEFAULT 0")
        if "account_balance" not in ctrader_cols:
            conn.execute("ALTER TABLE ctrader_deals ADD COLUMN account_balance REAL NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ctrader_deals_exec_ms ON ctrader_deals(account_id, execution_ms DESC)")
        if "bark_notified_at" not in rem_cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN bark_notified_at TEXT DEFAULT ''")

        ts = now_iso()
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, ts),
            )

        # Migrate legacy per-module proxy URLs into global_proxy_url if not yet populated
        gp_row = conn.execute("SELECT value FROM settings WHERE key = 'global_proxy_url'").fetchone()
        if gp_row and not (gp_row["value"] or "").strip():
            for legacy_k in ("ctrader_oauth_proxy", "ibkr_proxy_url"):
                leg_row = conn.execute("SELECT value FROM settings WHERE key = ?", (legacy_k,)).fetchone()
                leg_val = (leg_row["value"] or "").strip() if leg_row else ""
                if leg_val:
                    conn.execute(
                        "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'global_proxy_url'",
                        (leg_val, ts),
                    )
                    break

        # Migrate legacy single email_* settings into email_accounts table once
        ea_count = conn.execute("SELECT COUNT(*) FROM email_accounts").fetchone()[0]
        if ea_count == 0:
            s_rows = {
                r["key"]: r["value"]
                for r in conn.execute(
                    "SELECT key, value FROM settings WHERE key LIKE 'email_%'"
                ).fetchall()
            }
            leg_host = (s_rows.get("email_imap_host") or "").strip()
            leg_user = (s_rows.get("email_username") or "").strip()
            leg_pwd = (s_rows.get("email_password") or "").strip()
            if leg_host and leg_user and leg_pwd:
                try:
                    leg_port = int(s_rows.get("email_imap_port") or 993)
                except ValueError:
                    leg_port = 993
                conn.execute(
                    """
                    INSERT INTO email_accounts
                    (name, imap_host, imap_port, username, password, use_ssl, use_proxy, bill_zip_password, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        leg_user,
                        leg_host,
                        leg_port,
                        leg_user,
                        leg_pwd,
                        0 if s_rows.get("email_use_ssl") == "0" else 1,
                        1 if s_rows.get("email_use_proxy") == "1" else 0,
                        (s_rows.get("email_bill_zip_password") or "").strip(),
                        ts,
                        ts,
                    ),
                )

        if seed_demo:
            seeded_flag = conn.execute("SELECT value FROM settings WHERE key = 'demo_seeded'").fetchone()
            if not seeded_flag:
                count = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
                acc_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
                if count == 0 and acc_count == 0:
                    _seed_demo_data(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('demo_seeded', '1', ?)",
                    (ts,),
                )


def _seed_demo_data(conn: sqlite3.Connection) -> None:
    now = datetime.now().astimezone()
    ts = now.isoformat(timespec="seconds")

    # Accounts
    accounts_data = [
        ("招商银行储蓄卡", "bank", 38450.0, 0.0, "CNY", ts),
        ("支付宝余额宝", "cash", 12680.5, 0.0, "CNY", ts),
        ("指数基金定投", "investment", 45200.0, 0.0, "CNY", ts),
        ("招商银行信用卡", "credit_card", -3420.0, 30000.0, "CNY", ts),
    ]
    conn.executemany(
        "INSERT INTO accounts (name, type, balance, credit_limit, currency, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        accounts_data,
    )

    bank_id = conn.execute("SELECT id FROM accounts WHERE name='招商银行储蓄卡'").fetchone()[0]
    alipay_id = conn.execute("SELECT id FROM accounts WHERE name='支付宝余额宝'").fetchone()[0]
    cc_id = conn.execute("SELECT id FROM accounts WHERE name='招商银行信用卡'").fetchone()[0]

    # Reminders
    reminders_data = [
        (
            "提交季度项目复盘文档",
            "整理 Q4 关键指标与下阶段排期",
            (now + timedelta(hours=3)).isoformat(timespec="minutes"),
            "high",
            "work",
            "pending",
            "siyuan_note",
            "思源笔记: /工作/周报与计划",
            None,
            None,
            ts,
            ts,
        ),
        (
            "缴纳本月房租",
            "联系房东转账并索要电子收据",
            (now + timedelta(days=2)).replace(hour=10, minute=0).isoformat(timespec="minutes"),
            "high",
            "finance",
            "pending",
            "recurring_bill",
            "周期账单 #1",
            3200.0,
            "expense",
            ts,
            ts,
        ),
        (
            "牙科常规洁牙预约",
            "提前 15 分钟到达门诊",
            (now + timedelta(days=4)).replace(hour=14, minute=30).isoformat(timespec="minutes"),
            "medium",
            "health",
            "pending",
            "ai_chat",
            "AI 对话提取",
            None,
            None,
            ts,
            ts,
        ),
        (
            "整理思源笔记年度读书摘录",
            "归档已读完的 3 本技术书籍卡片",
            (now + timedelta(days=6)).replace(hour=20, minute=0).isoformat(timespec="minutes"),
            "low",
            "study",
            "pending",
            "manual",
            "",
            None,
            None,
            ts,
            ts,
        ),
    ]
    conn.executemany(
        """INSERT INTO reminders
        (title, description, due_at, priority, category, status, source_type, source_ref, linked_amount, linked_tx_type, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        reminders_data,
    )

    # Historical settlement
    last_month_start = (now.replace(day=1) - timedelta(days=30)).strftime("%Y-%m-%d")
    last_month_end = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")
    conn.execute(
        """INSERT INTO settlements
        (period_label, start_date, end_date, total_income, total_expense, net_cashflow, net_worth_snapshot, tx_count, category_breakdown_json, account_snapshot_json, ai_summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "上月财务总结（示例）",
            last_month_start,
            last_month_end,
            16500.0,
            7840.0,
            8660.0,
            84250.5,
            18,
            json.dumps({"住房": 3200, "餐饮": 2150, "交通": 640, "购物": 1250, "订阅服务": 600}, ensure_ascii=False),
            json.dumps(
                [
                    {"name": "招商银行储蓄卡", "type": "bank", "balance": 32000.0},
                    {"name": "支付宝余额宝", "type": "cash", "balance": 11050.5},
                    {"name": "指数基金定投", "type": "investment", "balance": 43800.0},
                    {"name": "招商银行信用卡", "type": "credit_card", "balance": -2600.0},
                ],
                ensure_ascii=False,
            ),
            "上期总收入 ¥16,500，总支出 ¥7,840，结余 ¥8,660（储蓄率 52.5%）。住房与餐饮占主要支出，整体现金流健康。",
            ts,
        ),
    )

    # Current unsettled transactions
    tx_data = [
        ("income", 16500.0, "工资收入", bank_id, "本月薪资到账", (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M"), None, "manual", ts),
        ("expense", 68.5, "餐饮", alipay_id, "精品超市采购水果牛奶", (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M"), None, "ai_chat", ts),
        ("expense", 42.0, "餐饮", alipay_id, "工作日午餐套餐", (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"), None, "manual", ts),
        ("expense", 38.0, "交通", alipay_id, "加班打车回家", (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), None, "ai_chat", ts),
        ("expense", 299.0, "购物", cc_id, "购买人体工学鼠标垫与键盘腕托", now.strftime("%Y-%m-%d %H:%M"), None, "manual", ts),
        ("expense", 198.0, "订阅服务", cc_id, "云服务与 AI 工具月度会员", now.strftime("%Y-%m-%d %H:%M"), None, "recurring_bill", ts),
    ]
    conn.executemany(
        """INSERT INTO transactions
        (type, amount, category, account_id, description, occurred_at, settlement_id, source_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        tx_data,
    )

    # Recurring bills
    bills_data = [
        ("公寓房租", 3200.0, "住房", bank_id, 20, "monthly", (now + timedelta(days=2)).strftime("%Y-%m-%d"), 3, 1, None),
        ("宽带与手机话费", 159.0, "通讯", alipay_id, 25, "monthly", (now + timedelta(days=7)).strftime("%Y-%m-%d"), 3, 1, None),
        ("云盘与效率软件订阅", 198.0, "订阅服务", cc_id, 1, "monthly", (now + timedelta(days=13)).strftime("%Y-%m-%d"), 2, 1, None),
    ]
    conn.executemany(
        """INSERT INTO recurring_bills
        (name, amount, category, account_id, billing_day, cycle, next_due_date, auto_remind_days, active, last_generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        bills_data,
    )

    # Welcome chat message
    conn.execute(
        "INSERT INTO chat_messages (role, content, actions_json, created_at) VALUES (?, ?, ?, ?)",
        (
            "assistant",
            "你好！我是你的个人生活与财务助手。你可以直接对我说：\n• 「明天下午3点提醒我复盘周报」\n• 「今天买咖啡花了35元，顺便周五提醒我交水电费180元」\n• 「帮我总结一下目前的资产和待结账支出」",
            "[]",
            ts,
        ),
    )

    # Demo phone numbers
    phones_data = [
        (
            "大陆主力号",
            "13800138000",
            "中国大陆 (+86)",
            "中国移动",
            "physical",
            "主力 iPhone 卡槽1",
            39.0,
            "CNY",
            1.0,
            128.5,
            12.0,
            240.0,
            1,
            "正常月租扣费即可，无需额外保号动作",
            0,
            now.strftime("%Y-%m-%d"),
            "",
            "",
            "微信、支付宝、招商银行、12306",
            "active",
            "已开通国际漫游接收短信",
            ts,
            ts,
        ),
        (
            "港卡保号副卡",
            "+852 6123 4567",
            "中国香港 (+852)",
            "ClubSIM",
            "esim",
            "主力 iPhone eSIM",
            0.0,
            "HKD",
            0.93,
            56.0,
            6.0,
            56.0,
            1,
            "每 365 天购买一次 HK$6 短信包即可续期保号",
            365,
            (now - timedelta(days=355)).strftime("%Y-%m-%d"),
            (now + timedelta(days=10)).strftime("%Y-%m-%d"),
            (now + timedelta(days=10)).strftime("%Y-%m-%d"),
            "Apple ID、港股券商、海外银行验证码",
            "standby",
            "平时关闭数据漫游，仅收验证码",
            ts,
            ts,
        ),
    ]
    conn.executemany(
        """INSERT INTO phone_numbers
        (label, phone_number, country_region, carrier, sim_type, device_slot, monthly_fee, currency, cny_rate, balance, extra_comm_cost, total_comm_spent, billing_day, keep_alive_rule, keep_alive_cycle_days, last_keep_alive_date, next_keep_alive_date, expiry_date, bound_services, status, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        phones_data,
    )


def get_all_settings() -> Dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for row in rows:
            result[row["key"]] = row["value"]
        return result


def update_settings(updates: Dict[str, Any]) -> Dict[str, str]:
    ts = now_iso()
    with get_conn() as conn:
        for k, v in updates.items():
            if v is None:
                continue
            val_str = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            conn.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (k, val_str, ts),
            )
    return get_all_settings()


def reset_all_data(reseed: bool = False) -> None:
    ts = now_iso()
    with get_conn() as conn:
        conn.executescript(
            """
            DELETE FROM chat_messages;
            DELETE FROM transactions;
            DELETE FROM recurring_bills;
            DELETE FROM reminders;
            DELETE FROM settlements;
            DELETE FROM accounts;
            DELETE FROM email_sync_logs;
            DELETE FROM phone_numbers;
            DELETE FROM debt_operations;
            DELETE FROM credit_cards;
            DELETE FROM loans;
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('demo_seeded', '1', ?)",
            (ts,),
        )
        if reseed:
            _seed_demo_data(conn)
