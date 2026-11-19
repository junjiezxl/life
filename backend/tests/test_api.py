import os
import tempfile
import unittest

# Isolate DB for tests
tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp_db.close()
os.environ["LIFEHUB_DB_PATH"] = tmp_db.name

from fastapi.testclient import TestClient  # noqa: E402
from app.db import init_db, reset_all_data  # noqa: E402
from app.main import app  # noqa: E402


class TestLifeHubAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed_demo=True)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(tmp_db.name):
            os.remove(tmp_db.name)

    def test_00_auth_setup_login_and_protection(self):
        # Unauthenticated requests to protected APIs must be rejected
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        self.assertEqual(self.client.get("/api/settings").status_code, 401)

        st0 = self.client.get("/api/auth/status")
        self.assertEqual(st0.status_code, 200)
        self.assertFalse(st0.json()["authenticated"])
        self.assertTrue(st0.json()["setup_required"])

        # First-time setup
        setup_resp = self.client.post(
            "/api/auth/setup",
            json={"username": "admin", "password": "initial_password_123"},
        )
        self.assertEqual(setup_resp.status_code, 200)
        self.assertEqual(setup_resp.json()["username"], "admin")

        # Subsequent setup must be forbidden
        second_setup = self.client.post(
            "/api/auth/setup",
            json={"username": "hacker", "password": "hack_password_123"},
        )
        self.assertEqual(second_setup.status_code, 403)

        # Separate client tests brute-force rate limiting & login
        anon = TestClient(app)
        for _ in range(4):
            bad = anon.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong_password"},
                headers={"x-forwarded-for": "198.51.100.42"},
            )
            self.assertEqual(bad.status_code, 401)
        locked = anon.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong_password"},
            headers={"x-forwarded-for": "198.51.100.42"},
        )
        self.assertEqual(locked.status_code, 429)

        # Login from normal IP succeeds
        ok_login = anon.post(
            "/api/auth/login",
            json={"username": "admin", "password": "initial_password_123", "remember_me": True},
            headers={"x-forwarded-for": "198.51.100.99"},
        )
        self.assertEqual(ok_login.status_code, 200)
        self.assertEqual(anon.get("/api/dashboard").status_code, 200)

        # Change password on self.client revokes anon's session
        chg = self.client.post(
            "/api/auth/change-password",
            json={"current_password": "initial_password_123", "new_password": "new_password_456"},
        )
        self.assertEqual(chg.status_code, 200)
        self.assertGreaterEqual(chg.json()["revoked_other_sessions"], 1)
        self.assertEqual(anon.get("/api/dashboard").status_code, 401)

        # Logout and re-login with new password on self.client
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        relogin = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "new_password_456", "remember_me": True},
            headers={"x-forwarded-for": "198.51.100.99"},
        )
        self.assertEqual(relogin.status_code, 200)

    def test_01_dashboard_seeded(self):
        resp = self.client.get("/api/dashboard")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["reminders_summary"]["pending_count"], 1)
        self.assertIn("net_worth", data["finance_summary"])
        self.assertGreater(data["finance_summary"]["net_worth"], 0)

    def test_02_ai_chat_creates_reminder_and_transaction(self):
        resp = self.client.post(
            "/api/chat",
            json={"message": "明天下午3点提醒我交房租3200元，另外今天午饭花了42元"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["created_reminders"]), 1)
        self.assertEqual(len(data["created_transactions"]), 1)
        self.assertEqual(data["created_transactions"][0]["amount"], 42.0)
        self.assertEqual(data["created_transactions"][0]["type"], "expense")
        self.assertIsNotNone(data["created_reminders"][0]["due_at"])

    def test_03_note_extraction_and_batch_import(self):
        raw_text = """
        - [ ] 周五前提交项目报销单 680元
        - [x] 已完成买菜
        提醒：下周一去医院复查
        """
        resp = self.client.post("/api/notes/extract", json={"notes": [], "raw_text": raw_text})
        self.assertEqual(resp.status_code, 200)
        candidates = resp.json()["candidates"]
        self.assertEqual(len(candidates), 2)

        imp = self.client.post("/api/reminders/batch-import", json={"reminders": candidates})
        self.assertEqual(imp.status_code, 200)
        self.assertEqual(imp.json()["imported_count"], 2)

    def test_04_settlement_checkout_flow(self):
        before = self.client.get("/api/transactions?settled=unsettled").json()
        unsettled_count_before = before["unsettled_summary"]["count"]
        self.assertGreater(unsettled_count_before, 0)

        settle_resp = self.client.post(
            "/api/settlements/settle",
            json={"period_label": "测试期结账"},
        )
        self.assertEqual(settle_resp.status_code, 200)
        settlement = settle_resp.json()
        self.assertEqual(settlement["period_label"], "测试期结账")
        self.assertEqual(settlement["tx_count"], unsettled_count_before)
        self.assertTrue(len(settlement["ai_summary"]) > 10)

        after = self.client.get("/api/transactions?settled=unsettled").json()
        self.assertEqual(after["unsettled_summary"]["count"], 0)

        # Inspect category stats & transactions inside the settlement
        details = self.client.get(f"/api/settlements/{settlement['id']}/transactions").json()
        self.assertEqual(len(details["transactions"]), unsettled_count_before)
        self.assertGreater(len(details["category_stats"]), 0)

        # Update an expense transaction's category and regenerate summary
        first_expense = next((t for t in details["transactions"] if t["type"] == "expense"), None)
        self.assertIsNotNone(first_expense)
        upd = self.client.patch(f"/api/transactions/{first_expense['id']}", json={"category": "医疗"})
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.json()["category"], "医疗")

        regen = self.client.post(f"/api/settlements/{settlement['id']}/regenerate", json={"period_label": "重算测试结账"})
        self.assertEqual(regen.status_code, 200)
        self.assertEqual(regen.json()["period_label"], "重算测试结账")
        self.assertIn("医疗", regen.json()["category_breakdown"])

        # Undo settlement releases transactions back to unsettled, then re-settle
        undo = self.client.delete(f"/api/settlements/{settlement['id']}")
        self.assertEqual(undo.status_code, 200)
        self.assertEqual(undo.json()["released_tx_count"], unsettled_count_before)
        restored = self.client.get("/api/transactions?settled=unsettled").json()
        self.assertEqual(restored["unsettled_summary"]["count"], unsettled_count_before)

        re_settle = self.client.post("/api/settlements/settle", json={"period_label": "测试期结账"})
        self.assertEqual(re_settle.status_code, 200)

    def test_05_recurring_bill_sync_and_complete_reminder(self):
        sync_resp = self.client.post("/api/recurring-bills/sync-reminders?force_all=true")
        self.assertEqual(sync_resp.status_code, 200)
        self.assertGreaterEqual(sync_resp.json()["created_count"], 1)

        rem = self.client.post(
            "/api/reminders",
            json={
                "title": "缴纳电费",
                "category": "finance",
                "priority": "high",
                "linked_amount": 150.0,
                "linked_tx_type": "expense",
            },
        ).json()
        comp = self.client.post(f"/api/reminders/{rem['id']}/complete-and-record")
        self.assertEqual(comp.status_code, 200)
        self.assertEqual(comp.json()["reminder"]["status"], "done")
        self.assertEqual(comp.json()["transaction"]["amount"], 150.0)

    def test_06_frontend_static_served(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("LifeHub", resp.text)

    def test_07_alipay_csv_and_wechat_zip_bill_parsing(self):
        # 1. Alipay CSV (GB18030 encoded with preamble)
        alipay_csv = (
            "支付宝交易明细证明\n"
            "-------------------------\n"
            "交易时间,交易分类,交易对方,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
            "2026-11-17 12:30:00,餐饮美食,瑞幸咖啡,生椰拿铁,支出,18.50,余额宝,交易成功,ALI202611170001,,\n"
            "2026-11-17 13:00:00,投资理财,余额宝,转入,不计收支,500.00,银行卡,交易成功,ALI202611170002,,\n"
        ).encode("gb18030")
        resp = self.client.post(
            "/api/bills/parse-upload",
            files={"file": ("alipay_record.csv", alipay_csv, "text/csv")},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["transactions"][0]["source_type"], "alipay")
        self.assertEqual(data["transactions"][0]["amount"], 18.5)
        self.assertEqual(data["transactions"][0]["category"], "餐饮")

        # Import and verify dedup on re-import
        imp = self.client.post(
            "/api/transactions/batch-import",
            json={"transactions": data["transactions"], "account_id": None, "skip_duplicates": True},
        )
        self.assertEqual(imp.status_code, 200)
        self.assertEqual(imp.json()["imported_tx_count"], 1)

        imp_again = self.client.post(
            "/api/transactions/batch-import",
            json={"transactions": data["transactions"], "account_id": None, "skip_duplicates": True},
        )
        self.assertEqual(imp_again.json()["imported_tx_count"], 0)
        self.assertEqual(imp_again.json()["skipped_duplicate_count"], 1)

        # 2. WeChat XLSX inside AES-encrypted ZIP
        import io
        import openpyxl
        import pyzipper

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["微信支付账单明细"])
        ws.append(["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注"])
        ws.append(["2026-11-17 19:20:00", "商户消费", "滴滴出行", "快车车费", "支出", "¥36.00", "零钱", "支付成功", "WX202611170001", "/", "/"])
        ws.append(["2026-11-17 20:00:00", "零钱提现", "零钱提现", "/", "/", "¥100.00", "零钱", "提现已到账", "WX202611170002", "/", "/"])
        xlsx_buf = io.BytesIO()
        wb.save(xlsx_buf)

        zip_buf = io.BytesIO()
        with pyzipper.AESZipFile(zip_buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(b"123456")
            zf.writestr("wechat_bill.xlsx", xlsx_buf.getvalue())

        resp_wx = self.client.post(
            "/api/bills/parse-upload",
            files={"file": ("wechat.zip", zip_buf.getvalue(), "application/zip")},
            data={"zip_password": "123456"},
        )
        self.assertEqual(resp_wx.status_code, 200)
        wx_data = resp_wx.json()
        self.assertEqual(wx_data["errors"], [])
        self.assertEqual(len(wx_data["transactions"]), 1)
        self.assertEqual(wx_data["transactions"][0]["source_type"], "wechat")
        self.assertEqual(wx_data["transactions"][0]["category"], "交通")
        self.assertEqual(wx_data["transactions"][0]["amount"], 36.0)

    def test_08_email_text_parsing_and_import_dedup(self):
        # Credit card statement email -> reminder
        resp_bill = self.client.post(
            "/api/emails/parse-text",
            json={
                "subject": "招商银行信用卡11月电子账单",
                "sender": "cc@cmbchina.com",
                "body": "尊敬的持卡人，您本期应还金额人民币 3,420.00 元，最后还款日为 11月25日。",
            },
        )
        self.assertEqual(resp_bill.status_code, 200)
        bill_res = resp_bill.json()
        self.assertGreaterEqual(len(bill_res["reminders"]), 1)
        self.assertEqual(bill_res["reminders"][0]["linked_amount"], 3420.0)

        # Receipt email -> transaction
        resp_rcpt = self.client.post(
            "/api/emails/parse-text",
            json={
                "subject": "京东订单付款成功通知",
                "sender": "notice@jd.com",
                "body": "您的订单已完成付款成功，实付金额 299.00 元，商品：蓝牙键盘。",
            },
        )
        self.assertEqual(resp_rcpt.status_code, 200)
        rcpt_res = resp_rcpt.json()
        self.assertEqual(len(rcpt_res["transactions"]), 1)
        self.assertEqual(rcpt_res["transactions"][0]["amount"], 299.0)

        imp = self.client.post(
            "/api/emails/import",
            json={
                "transactions": rcpt_res["transactions"],
                "reminders": bill_res["reminders"],
                "account_id": None,
                "skip_duplicates": True,
                "email_logs": [
                    {
                        "message_uid": "msg-test-001",
                        "subject": "招商银行信用卡11月电子账单",
                        "sender": "cc@cmbchina.com",
                        "received_at": "2026-11-18 09:00",
                        "tx_imported": 1,
                        "rem_imported": 1,
                    }
                ],
            },
        )
        self.assertEqual(imp.status_code, 200)
        self.assertEqual(imp.json()["imported_tx_count"], 1)
        self.assertEqual(imp.json()["imported_rem_count"], 1)

        logs = self.client.get("/api/emails/logs").json()
        self.assertTrue(any(l["message_uid"] == "msg-test-001" for l in logs))

    def test_09_phone_number_management_and_keep_alive(self):
        from datetime import datetime, timedelta

        last_ka = (datetime.now() - timedelta(days=170)).strftime("%Y-%m-%d")
        created = self.client.post(
            "/api/phone-numbers",
            json={
                "label": "英国保号卡",
                "phone_number": "+44 7700 900123",
                "country_region": "英国 (+44)",
                "carrier": "giffgaff",
                "sim_type": "esim",
                "monthly_fee": 6.0,
                "currency": "GBP",
                "cny_rate": 9.15,
                "balance": 15.0,
                "keep_alive_rule": "每180天发一条短信保号",
                "keep_alive_cycle_days": 180,
                "remind_before_days": 15,
                "last_keep_alive_date": last_ka,
                "bound_services": "Wise, 银行验证码",
            },
        )
        self.assertEqual(created.status_code, 200)
        phone = created.json()
        self.assertLessEqual(phone["keep_alive_days_left"], 15)

        listing = self.client.get("/api/phone-numbers").json()
        self.assertGreaterEqual(listing["summary"]["urgent_keep_alive_count"], 1)

        sync_rem = self.client.post("/api/phone-numbers/sync-reminders?within_days=15").json()
        self.assertGreaterEqual(sync_rem["created_count"], 1)

        cost_res = self.client.post(
            f"/api/phone-numbers/{phone['id']}/record-cost",
            json={"kind": "extra_cost", "amount": 0.3, "description": "发送保号短信", "sync_to_finance": True},
        )
        self.assertEqual(cost_res.status_code, 200)
        self.assertEqual(cost_res.json()["phone"]["extra_comm_cost"], 0.3)
        self.assertIsNotNone(cost_res.json()["transaction"])

        ka_res = self.client.post(f"/api/phone-numbers/{phone['id']}/keep-alive")
        self.assertEqual(ka_res.status_code, 200)
        self.assertGreater(ka_res.json()["keep_alive_days_left"], 150)

    def test_10_philippines_auto_advance_reminder_and_topup_keep_alive(self):
        from datetime import datetime, timedelta

        last_ka = (datetime.now() - timedelta(days=350)).strftime("%Y-%m-%d")
        ph_res = self.client.post(
            "/api/phone-numbers",
            json={
                "label": "菲律宾 Globe 主号",
                "phone_number": "+63 917 123 4567",
                "country_region": "菲律宾 (+63)",
                "carrier": "Globe",
                "sim_type": "physical",
                "monthly_fee": 0.0,
                "currency": "PHP",
                "cny_rate": 0.125,
                "balance": 20.0,
                "keep_alive_rule": "每 365 天至少充值一次 ₱50 防止销号",
                "keep_alive_cycle_days": 365,
                "remind_before_days": 20,
                "last_keep_alive_date": last_ka,
            },
        )
        self.assertEqual(ph_res.status_code, 200)
        ph = ph_res.json()
        self.assertEqual(ph["remind_before_days"], 20)
        self.assertLessEqual(ph["keep_alive_days_left"], 20)

        # Auto-generated advance reminder should already exist
        rems = self.client.get("/api/reminders?status=pending&source_type=phone_sim").json()
        self.assertTrue(any("菲律宾 Globe 主号" in r["title"] for r in rems))

        # Top up with mark_keep_alive=True -> advances keep-alive and closes pending reminder
        topup = self.client.post(
            f"/api/phone-numbers/{ph['id']}/record-cost",
            json={
                "kind": "topup",
                "amount": 50.0,
                "description": "Globe 充值保号",
                "sync_to_finance": True,
                "mark_keep_alive": True,
            },
        )
        self.assertEqual(topup.status_code, 200)
        self.assertEqual(topup.json()["phone"]["balance"], 70.0)
        self.assertGreater(topup.json()["phone"]["keep_alive_days_left"], 350)

    def test_11_bark_push_and_due_scheduler(self):
        from datetime import datetime, timedelta
        from unittest.mock import AsyncMock, patch

        # Configure Bark
        self.client.put(
            "/api/settings",
            json={
                "bark_enabled": "1",
                "bark_server_url": "https://api.day.app",
                "bark_device_key": "test_bark_key_123",
                "bark_group": "LifeHub",
                "bark_sound": "bell",
                "bark_push_on_create": "1",
                "bark_due_advance_min": "15",
            },
        )

        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"code": 200, "message": "success"}

        due_soon = (datetime.now().astimezone() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")

        with patch("app.services.bark_service.httpx.AsyncClient.post", new=AsyncMock(return_value=FakeResp())) as mock_post:
            # 1. Creating reminder triggers immediate Bark confirmation push
            rem = self.client.post(
                "/api/reminders",
                json={
                    "title": "测试 Bark 提醒",
                    "description": "记得按时处理",
                    "due_at": due_soon,
                    "priority": "high",
                    "category": "life",
                },
            ).json()
            self.assertEqual(mock_post.call_count, 1)
            call_url = mock_post.call_args.args[0]
            self.assertEqual(call_url, "https://api.day.app/test_bark_key_123")
            payload = mock_post.call_args.kwargs["json"]
            self.assertEqual(payload["level"], "timeSensitive")
            self.assertEqual(payload["group"], "LifeHub")

            # 2. Due-check scheduler pushes due reminder ONCE, then skips on second run
            chk1 = self.client.post("/api/reminders/check-bark-due").json()
            self.assertGreaterEqual(chk1["pushed_count"], 1)
            calls_after_first = mock_post.call_count

            chk2 = self.client.post("/api/reminders/check-bark-due").json()
            self.assertEqual(chk2["pushed_count"], 0)
            self.assertEqual(mock_post.call_count, calls_after_first)

            # 3. Manual single reminder Bark push works
            manual = self.client.post(f"/api/reminders/{rem['id']}/push-bark").json()
            self.assertTrue(manual["ok"])
            self.assertEqual(mock_post.call_count, calls_after_first + 1)

    def test_12_phone_number_subcards(self):
        primary = self.client.post(
            "/api/phone-numbers",
            json={
                "label": "移动主卡",
                "phone_number": "+86 13800138000",
                "country_region": "中国大陆 (+86)",
                "carrier": "中国移动",
                "monthly_fee": 99.0,
            },
        ).json()
        sub = self.client.post(
            "/api/phone-numbers",
            json={
                "label": "移动副卡1",
                "phone_number": "+86 13800138001",
                "country_region": "中国大陆 (+86)",
                "carrier": "中国移动",
                "monthly_fee": 10.0,
                "parent_phone_id": primary["id"],
            },
        )
        self.assertEqual(sub.status_code, 200)
        sub_id = sub.json()["id"]

        # Reject nesting a sub-card under another sub-card
        nested = self.client.post(
            "/api/phone-numbers",
            json={
                "label": "套娃副卡",
                "phone_number": "+86 13800138002",
                "parent_phone_id": sub_id,
            },
        )
        self.assertEqual(nested.status_code, 400)

        # Listing enriches parent & sub_cards metadata
        listing = self.client.get("/api/phone-numbers").json()
        self.assertGreaterEqual(listing["summary"]["sub_card_count"], 1)
        p_item = next(p for p in listing["items"] if p["id"] == primary["id"])
        s_item = next(p for p in listing["items"] if p["id"] == sub_id)
        self.assertEqual(p_item["sub_cards_count"], 1)
        self.assertEqual(p_item["sub_cards"][0]["id"], sub_id)
        self.assertEqual(s_item["parent_phone_id"], primary["id"])
        self.assertEqual(s_item["parent_label"], "移动主卡")

        # Deleting primary detaches sub-card cleanly instead of failing or deleting sub-card
        self.assertEqual(self.client.delete(f"/api/phone-numbers/{primary['id']}").status_code, 200)
        listing_after = self.client.get("/api/phone-numbers").json()
        s_after = next(p for p in listing_after["items"] if p["id"] == sub_id)
        self.assertIsNone(s_after["parent_phone_id"])

    def test_13_credit_card_loan_and_optimal_repayment_planner(self):
        from datetime import datetime, timedelta

        today = datetime.now().astimezone().date()
        urgent_due_day = (today + timedelta(days=2)).day
        later_due_day = (today + timedelta(days=10)).day
        # Card B just passed statement day (e.g. yesterday) -> long interest-free period
        just_passed_stmt_day = (today - timedelta(days=2)).day
        # Card A statement day is upcoming so shorter free days
        upcoming_stmt_day = (today + timedelta(days=5)).day

        card_a = self.client.post(
            "/api/debt/cards",
            json={
                "name": "招行主力卡",
                "bank_name": "招商银行",
                "credit_limit": 30000,
                "current_debt": 22000,
                "statement_bill_amount": 8000,
                "statement_day": upcoming_stmt_day,
                "due_day": urgent_due_day,
                "swipe_fee_rate": 0.006,
            },
        ).json()
        card_b = self.client.post(
            "/api/debt/cards",
            json={
                "name": "广发周转卡",
                "bank_name": "广发银行",
                "credit_limit": 40000,
                "current_debt": 5000,
                "statement_bill_amount": 0,
                "statement_day": just_passed_stmt_day,
                "due_day": later_due_day,
                "swipe_fee_rate": 0.0055,
            },
        ).json()
        loan = self.client.post(
            "/api/debt/loans",
            json={
                "name": "消费信用贷",
                "lender": "微众银行",
                "total_principal": 50000,
                "remaining_principal": 30000,
                "monthly_payment": 2500,
                "due_day": urgent_due_day,
                "apr": 7.2,
            },
        ).json()

        ov = self.client.get("/api/debt/overview")
        self.assertEqual(ov.status_code, 200)
        self.assertEqual(ov.json()["summary"]["active_card_count"], 2)
        self.assertEqual(ov.json()["summary"]["active_loan_count"], 1)

        # Plan with 3000 cash -> 2500 covers rigid loan, 500 goes to Card A, remaining 7500 on Card A swiped from Card B
        plan_resp = self.client.post(
            "/api/debt/plan",
            json={
                "available_cash": 3000,
                "horizon_days": 30,
                "reserve_ratio": 0.05,
                "strategy": "balanced",
                "allow_relay": True,
            },
        )
        self.assertEqual(plan_resp.status_code, 200)
        plan = plan_resp.json()
        self.assertTrue(plan["summary"]["fully_covered"])
        self.assertEqual(plan["summary"]["cash_used"], 3000.0)
        self.assertEqual(plan["summary"]["swipe_used"], 7500.0)
        swipe_steps = [s for s in plan["steps"] if s["op_type"] == "swipe_repay_card"]
        self.assertEqual(len(swipe_steps), 1)
        self.assertEqual(swipe_steps[0]["source_id"], card_b["id"])
        self.assertEqual(swipe_steps[0]["target_id"], card_a["id"])
        self.assertEqual(swipe_steps[0]["amount"], 7500.0)

        # Execute swipe_repay_card step and verify card balances update
        step = swipe_steps[0]
        exec_resp = self.client.post(
            "/api/debt/operations",
            json={
                "op_type": step["op_type"],
                "source_card_id": step["source_id"],
                "target_card_id": step["target_id"],
                "amount": step["amount"],
                "fee_amount": step["fee_amount"],
                "sync_to_finance": True,
            },
        )
        self.assertEqual(exec_resp.status_code, 200)
        op_id = exec_resp.json()["id"]

        ov2 = self.client.get("/api/debt/overview").json()
        c_a2 = next(c for c in ov2["cards"] if c["id"] == card_a["id"])
        c_b2 = next(c for c in ov2["cards"] if c["id"] == card_b["id"])
        self.assertEqual(c_a2["remaining_bill"], 500.0)
        self.assertEqual(c_a2["current_debt"], 14500.0)
        self.assertEqual(c_b2["current_debt"], 12500.0)

        # Undo operation reverts balances cleanly
        self.assertEqual(self.client.delete(f"/api/debt/operations/{op_id}").status_code, 200)
        ov3 = self.client.get("/api/debt/overview").json()
        c_a3 = next(c for c in ov3["cards"] if c["id"] == card_a["id"])
        self.assertEqual(c_a3["remaining_bill"], 8000.0)
        self.assertEqual(c_a3["current_debt"], 22000.0)

        # Sync debt reminders creates reminders for upcoming card & loan
        sync_rem = self.client.post("/api/debt/sync-reminders?within_days=10")
        self.assertEqual(sync_rem.status_code, 200)
        self.assertGreaterEqual(sync_rem.json()["created_count"], 2)


if __name__ == "__main__":
    unittest.main()
