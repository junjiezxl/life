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

    def test_14_ctrader_live_market_ticks_and_external_apis(self):
        from app.services.ctrader_market_service import _CTraderSpotClient, market_service
        from ctrader_open_api.messages import OpenApiMessages_pb2 as msg_pb

        # Reset in-memory market state for deterministic assertions
        market_service.market_cache.clear()
        market_service.tick_counts.clear()
        market_service.recent_ticks.clear()
        self.client.put("/api/settings", json={"ctrader_market_api_token": ""})

        # 1. Decode ProtoOASpotEvent with partial bid/ask updates
        received = []
        spot_client = _CTraderSpotClient("id", "sec", "tok", "123", "demo", on_tick=received.append)
        spot_client._id_to_symbol[1] = "XAUUSD.PRO"
        spot_client._handle_spot_event(msg_pb.ProtoOASpotEvent(symbolId=1, bid=265055000))
        self.assertEqual(len(received), 0)  # Ask not known yet
        spot_client._handle_spot_event(msg_pb.ProtoOASpotEvent(symbolId=1, bid=265055000, ask=265085000, timestamp=1740000000123))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["symbol"], "XAUUSD.PRO")
        self.assertEqual(received[0]["bid"], 2650.55)
        self.assertEqual(received[0]["ask"], 2650.85)
        self.assertEqual(received[0]["spread"], 0.3)

        # Feed ticks into market_service
        market_service.on_tick_received(received[0])
        market_service.on_tick_received(
            {
                "source": "ctrader",
                "symbol": "EURUSD",
                "bid": 1.0851,
                "ask": 1.0853,
                "spread": 0.0002,
                "timestamp": 1740000000500,
                "datetime_utc": "2026-11-21 12:00:00.500",
            }
        )

        # 2. External public endpoints (/status, /quotes, /quote/{symbol}) work without login
        anon = TestClient(app)
        st = anon.get("/status")
        self.assertEqual(st.status_code, 200)
        self.assertEqual(st.json()["tick_counts"]["XAUUSD.PRO"], 1)

        quotes = anon.get("/quotes")
        self.assertEqual(quotes.status_code, 200)
        self.assertEqual(quotes.json()["count"], 2)
        self.assertEqual(quotes.json()["data"]["XAUUSD.PRO"]["bid"], 2650.55)

        # Prefix match XAUUSD -> XAUUSD.PRO
        single = anon.get("/quote/XAUUSD")
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()["data"]["symbol"], "XAUUSD.PRO")
        self.assertEqual(anon.get("/quote/UNKNOWN").status_code, 404)

        # 3. Authenticated LifeHub UI endpoints
        mkt_st = self.client.get("/api/ctrader/market/status")
        self.assertEqual(mkt_st.status_code, 200)
        self.assertEqual(mkt_st.json()["total_ticks"], 2)

        recent = self.client.get("/api/ctrader/market/ticks?symbol=XAUUSD&limit=10")
        self.assertEqual(recent.status_code, 200)
        self.assertEqual(len(recent.json()["ticks"]), 1)
        self.assertEqual(recent.json()["ticks"][0]["symbol"], "XAUUSD.PRO")

        # 4. Token protection when ctrader_market_api_token is configured
        self.client.put("/api/settings", json={"ctrader_market_api_token": "secret-mkt-token"})
        self.assertEqual(anon.get("/quotes").status_code, 401)
        self.assertEqual(anon.get("/status").status_code, 401)
        self.assertEqual(anon.get("/quote/XAUUSD").status_code, 401)
        self.assertEqual(
            anon.get("/quotes", headers={"Authorization": "Bearer secret-mkt-token"}).status_code,
            200,
        )
        self.assertEqual(anon.get("/quote/XAUUSD?token=secret-mkt-token").status_code, 200)
        # Authenticated LifeHub session user still allowed
        self.assertEqual(self.client.get("/quotes").status_code, 200)

        # 5. WebSocket /ws/{symbol} receives live ticks
        with anon.websocket_connect("/ws/XAUUSD?token=secret-mkt-token") as ws:
            market_service.on_tick_received(
                {
                    "source": "ctrader",
                    "symbol": "XAUUSD.PRO",
                    "bid": 2651.1,
                    "ask": 2651.4,
                    "spread": 0.3,
                    "timestamp": 1740000001000,
                    "datetime_utc": "2026-11-21 12:00:01.000",
                }
            )
            msg = ws.receive_json()
            self.assertEqual(msg["symbol"], "XAUUSD.PRO")
            self.assertEqual(msg["bid"], 2651.1)

        # Clear token setting cleanly
        self.client.put("/api/settings", json={"ctrader_market_api_token": ""})

    def test_15_ctrader_incremental_and_chunked_history_sync(self):
        from unittest.mock import patch
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model_pb
        from app.db import get_conn
        from app.services import ctrader_service

        # Configure dummy cTrader credentials
        self.client.put(
            "/api/settings",
            json={
                "ctrader_client_id": "cid",
                "ctrader_client_secret": "csec",
                "ctrader_access_token": "tok",
                "ctrader_account_id": "999001",
                "ctrader_environment": "demo",
            },
        )
        with get_conn() as conn:
            conn.execute("DELETE FROM ctrader_deals")

        from_ms, to_ms = ctrader_service.parse_date_bounds_ms("2026-10-01", "2026-11-21")
        self.assertGreater(to_ms - from_ms, 50 * 86400 * 1000)

        requested_windows = []
        label_calls = []

        orig_client_cls = ctrader_service._CTraderAsyncClient

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.ctid_trader_account_id = 999001
                self.environment = "demo"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def fetch_symbols(self):
                return {1: "XAUUSD"}

            async def fetch_symbol_lot_sizes(self, symbol_ids):
                return {1: 10000}

            async def fetch_raw_deals_chunked(self, start_ms, end_ms, progress_cb=None):
                requested_windows.append((start_ms, end_ms))
                # Verify 7-day chunk splitting would produce >= 8 chunks for Oct 1 -> Nov 21
                d1 = model_pb.ProtoOADeal(
                    dealId=101,
                    orderId=201,
                    positionId=301,
                    symbolId=1,
                    tradeSide=model_pb.BUY,
                    filledVolume=100,  # 1 oz = 0.01 lots for XAUUSD (lotSize=10000)
                    executionPrice=3500.0,
                    executionTimestamp=1760000000000,
                    dealStatus=model_pb.FILLED,
                    moneyDigits=2,
                    commission=-8,
                )
                # Breakeven close deal (grossProfit=0, swap=-50 -> -$0.50, commission=-8 -> -$0.08)
                d2 = model_pb.ProtoOADeal(
                    dealId=102,
                    orderId=202,
                    positionId=301,
                    symbolId=1,
                    tradeSide=model_pb.SELL,
                    filledVolume=100,
                    executionPrice=3500.0,
                    executionTimestamp=1760000050000,
                    dealStatus=model_pb.FILLED,
                    moneyDigits=2,
                    closePositionDetail=model_pb.ProtoOAClosePositionDetail(
                        entryPrice=3500.0,
                        grossProfit=0,
                        swap=-50,
                        commission=-8,
                        balance=1000000,
                        moneyDigits=2,
                    ),
                )
                if progress_cb:
                    progress_cb(1, 1, 2)
                if len(requested_windows) == 1:
                    return [d1, d2]
                # Second call (incremental): return d2 again (same-timestamp overlap) + new deal d3
                d3 = model_pb.ProtoOADeal(
                    dealId=103,
                    orderId=203,
                    positionId=302,
                    symbolId=1,
                    tradeSide=model_pb.BUY,
                    filledVolume=1000,
                    executionPrice=3510.0,
                    executionTimestamp=1760000055000,
                    dealStatus=model_pb.FILLED,
                    moneyDigits=2,
                )
                return [d2, d3]

            async def fetch_order_labels(self, order_ids, progress_cb=None):
                label_calls.append(list(order_ids))
                res = {}
                for oid in order_ids:
                    if oid == 201:
                        res["201"] = ("GS2.1117.abc", "")
                    elif oid == 203:
                        res["203"] = ("GS2.1122.xyz", "")
                    else:
                        res[str(oid)] = ("", "")
                if progress_cb:
                    progress_cb(len(order_ids), len(order_ids))
                return res

            def decode_raw_deals(self, raw_deals, symbol_map, order_meta, lot_sizes=None):
                return orig_client_cls.decode_raw_deals(self, raw_deals, symbol_map, order_meta, lot_sizes=lot_sizes)

        with patch.object(ctrader_service, "_CTraderAsyncClient", FakeAsyncClient):
            # 1. Range historical backfill from 2026-10-01 to 2026-11-21
            r1 = self.client.post(
                "/api/ctrader/sync",
                json={
                    "mode": "range",
                    "start_date": "2026-10-01",
                    "end_date": "2026-11-21",
                    "background": False,
                },
            )
            self.assertEqual(r1.status_code, 200, r1.text)
            body1 = r1.json()
            self.assertEqual(body1["mode"], "range")
            self.assertEqual(body1["fetched"], 2)
            self.assertEqual(body1["inserted"], 2)
            self.assertEqual(body1["pushed"], 0)  # silent backfill by default

            # Verify breakeven close deal kept swap+commission (-0.58) & inherited position label
            resp1 = self.client.get("/api/ctrader/deals").json()
            deals1 = resp1["deals"]
            trades1 = resp1["trades"]
            self.assertEqual(len(deals1), 2)
            self.assertEqual(len(trades1), 1)
            self.assertEqual(trades1[0]["status"], "closed")
            self.assertEqual(trades1[0]["side"], "BUY")
            self.assertEqual(trades1[0]["volume"], 0.01)
            self.assertEqual(trades1[0]["open_price"], 3500.0)
            self.assertEqual(trades1[0]["close_price"], 3500.0)
            self.assertEqual(trades1[0]["net_profit"], -0.58)  # not double-charging opening commission
            self.assertEqual(trades1[0]["deal_count"], 2)
            close_deal = next(d for d in deals1 if d["deal_id"] == "102")
            self.assertEqual(close_deal["net_profit"], -0.58)
            self.assertEqual(close_deal["label"], "GS2.1117.abc")

            # 2. Default incremental sync starts from latest deal (1760000050000) - 3s = 1760000047000
            r2 = self.client.post("/api/ctrader/sync", json={"mode": "incremental"})
            self.assertEqual(r2.status_code, 200)
            body2 = r2.json()
            self.assertEqual(body2["sync_strategy"], "incremental_from_last")
            self.assertEqual(requested_windows[1][0], 1760000050000 - 3000)
            self.assertEqual(body2["inserted"], 1)
            self.assertEqual(body2["updated"], 1)  # overlapping deal 102 updated without duplicating

            # Cached order 202/201 were not re-queried if labeled; only new 203 queried
            self.assertNotIn(201, label_calls[1])
            self.assertIn(203, label_calls[1])

            deals2 = self.client.get("/api/ctrader/deals").json()
            self.assertEqual(deals2["stats"]["total_deals"], 3)
            self.assertEqual(deals2["stats"]["total_trades"], 2)
            self.assertEqual(deals2["stats"]["closed_trades"], 1)
            self.assertEqual(deals2["stats"]["open_trades"], 1)
            self.assertEqual(deals2["stats"]["total_net_profit"], -0.58)

        st_resp = self.client.get("/api/ctrader/sync/status")
        self.assertEqual(st_resp.status_code, 200)
        self.assertEqual(st_resp.json()["phase"], "completed")

    def test_16_startup_version_update_bark_notification(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app import main as main_mod

        self.client.put(
            "/api/settings",
            json={
                "bark_enabled": "1",
                "bark_server_url": "https://api.day.app",
                "bark_device_key": "test_bark_key_upd",
                "bark_group": "LifeHub",
                "bark_push_on_update": "1",
            },
        )

        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"code": 200, "message": "success"}

        with patch.dict(os.environ, {"LIFEHUB_BUILD_SHA": "dev"}):
            res_dev = asyncio.run(main_mod.check_and_notify_version_update())
            self.assertFalse(res_dev["notified"])
            self.assertEqual(res_dev["reason"], "dev_build")

        with (
            patch.dict(
                os.environ,
                {
                    "LIFEHUB_VERSION": "v1.0.0",
                    "LIFEHUB_BUILD_SHA": "a1b2c3d",
                    "LIFEHUB_BUILD_TIME": "2026-11-22 10:40:00",
                    "LIFEHUB_BUILD_MSG": "fix: 修复黄金手数换算问题",
                },
            ),
            patch("app.services.bark_service.httpx.AsyncClient.post", new=AsyncMock(return_value=FakeResp())) as mock_post,
        ):
            res1 = asyncio.run(main_mod.check_and_notify_version_update())
            self.assertTrue(res1["notified"])
            self.assertEqual(mock_post.call_count, 1)
            payload = mock_post.call_args.kwargs["json"]
            self.assertIn("v1.0.0", payload["title"])
            self.assertIn("修复：修复黄金手数换算问题", payload["body"])

            # Same version restart must not duplicate notification
            res2 = asyncio.run(main_mod.check_and_notify_version_update())
            self.assertFalse(res2["notified"])
            self.assertEqual(res2["reason"], "already_notified")
            self.assertEqual(mock_post.call_count, 1)

            health = self.client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["build"]["version"], "v1.0.0")
            self.assertEqual(health.json()["build"]["sha"], "a1b2c3d")

    def test_17_legacy_zero_volume_trade_prices_and_child_flags(self):
        from app.services.ctrader_service import group_deals_into_trades

        legacy_deals = [
            {
                "deal_id": "349451814",
                "order_id": "1",
                "position_id": "9001",
                "account_id": "100",
                "symbol_name": "XAUUSD",
                "side": "SELL",
                "volume": 0.0,
                "price": 3591.6,
                "commission": -0.07,
                "swap": 0.0,
                "gross_profit": 0.0,
                "net_profit": -0.07,
                "label": "",
                "comment": "",
                "executed_at": "2026-11-20T19:01:52+08:00",
                "execution_ms": 1795172512000,
                "is_closing": 0,
                "entry_price": 0.0,
                "pushed_at": "",
            },
            {
                "deal_id": "349465138",
                "order_id": "2",
                "position_id": "9001",
                "account_id": "100",
                "symbol_name": "XAUUSD",
                "side": "BUY",
                "volume": 0.0,
                "price": 3577.44,
                "commission": -0.14,
                "swap": 0.0,
                "gross_profit": 28.32,
                "net_profit": 28.18,
                "label": "",
                "comment": "",
                "executed_at": "2026-11-20T20:20:39+08:00",
                "execution_ms": 1795177239000,
                "is_closing": 0,
                "entry_price": 0.0,
                "pushed_at": "",
            },
        ]
        trades = group_deals_into_trades(legacy_deals)
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["status"], "closed")
        self.assertEqual(t["side"], "SELL")
        self.assertEqual(t["open_price"], 3591.6)
        self.assertEqual(t["close_price"], 3577.44)
        self.assertEqual(t["net_profit"], 28.18)
        self.assertEqual(t["deals"][0]["is_closing"], 0)
        self.assertEqual(t["deals"][1]["is_closing"], 1)

    def test_18_ctrader_oauth_exchange_and_auto_refresh(self):
        import asyncio
        import datetime
        from unittest.mock import patch
        from app.db import get_all_settings, update_settings
        from app.services import ctrader_oauth_service, ctrader_service

        self.assertEqual(
            ctrader_oauth_service.parse_oauth_code("https://life.example.com/api/ctrader/oauth/callback?code=AUTH_CODE_123&state=x"),
            "AUTH_CODE_123",
        )
        self.assertEqual(ctrader_oauth_service.parse_oauth_code("RAW_CODE_999"), "RAW_CODE_999")
        self.assertTrue(ctrader_oauth_service.is_token_expired_error("CH_ACCESS_TOKEN_INVALID: expired"))

        update_settings(
            {
                "ctrader_client_id": "cid_test_1",
                "ctrader_client_secret": "csec_test_1",
                "ctrader_account_id": "1396743",
                "ctrader_environment": "demo",
                "ctrader_auto_refresh_enabled": "1",
            }
        )

        auth_url_resp = self.client.get(
            "/api/ctrader/oauth/authorize-url",
            params={"redirect_uri": "https://life.example.com/api/ctrader/oauth/callback", "scope": "accounts"},
        )
        self.assertEqual(auth_url_resp.status_code, 200)
        auth_url = auth_url_resp.json()["url"]
        self.assertIn("https://openapi.ctrader.com/apps/auth?", auth_url)
        self.assertIn("client_id=cid_test_1", auth_url)

        calls = []

        class _MockResp:
            def __init__(self, payload, status_code=200):
                self._payload = payload
                self.status_code = status_code

            def json(self):
                return self._payload

        class _MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, params=None):
                calls.append((url, dict(params or {})))
                gt = (params or {}).get("grant_type")
                if gt == "authorization_code":
                    return _MockResp(
                        {
                            "accessToken": "AT_INITIAL_1",
                            "refreshToken": "RT_INITIAL_1",
                            "expiresIn": 2628000,
                            "tokenType": "bearer",
                        }
                    )
                if gt == "refresh_token":
                    n = len(calls)
                    return _MockResp(
                        {
                            "accessToken": f"AT_ROTATED_{n}",
                            "refreshToken": f"RT_ROTATED_{n}",
                            "expiresIn": 2628000,
                            "tokenType": "bearer",
                        }
                    )
                return _MockResp({"errorCode": "INVALID_GRANT"}, status_code=400)

        with patch("app.services.ctrader_oauth_service.httpx.AsyncClient", _MockAsyncClient):
            ex_resp = self.client.post(
                "/api/ctrader/oauth/exchange",
                json={
                    "code_or_url": "https://life.example.com/api/ctrader/oauth/callback?code=CODE_ABC",
                    "redirect_uri": "https://life.example.com/api/ctrader/oauth/callback",
                },
            )
            self.assertEqual(ex_resp.status_code, 200)
            self.assertTrue(ex_resp.json()["ok"])
            self.assertTrue(ex_resp.json()["has_refresh_token"])

            cfg1 = get_all_settings()
            self.assertEqual(cfg1["ctrader_access_token"], "AT_INITIAL_1")
            self.assertEqual(cfg1["ctrader_refresh_token"], "RT_INITIAL_1")
            self.assertEqual(cfg1["ctrader_oauth_redirect_uri"], "https://life.example.com/api/ctrader/oauth/callback")

            # Still valid (> 5 days remaining) -> skip proactive refresh
            check_valid = asyncio.run(ctrader_oauth_service.ensure_fresh_ctrader_token())
            self.assertFalse(check_valid["refreshed"])
            self.assertEqual(check_valid["reason"], "still_valid")

            # Expire within 2 days -> proactive auto-refresh rotates token
            soon_exp = (datetime.datetime.now().astimezone() + datetime.timedelta(days=2)).isoformat(timespec="seconds")
            update_settings({"ctrader_token_expires_at": soon_exp})
            check_renew = asyncio.run(ctrader_oauth_service.ensure_fresh_ctrader_token())
            self.assertTrue(check_renew["refreshed"])
            cfg2 = get_all_settings()
            self.assertEqual(cfg2["ctrader_access_token"], "AT_ROTATED_2")
            self.assertEqual(cfg2["ctrader_refresh_token"], "RT_ROTATED_2")

            # Reactive recovery inside _connect_with_auto_refresh on CH_ACCESS_TOKEN_INVALID
            seen_tokens = []

            class _FakeCTraderClient:
                def __init__(self, **creds):
                    self.access_token = creds["access_token"]

                async def __aenter__(self):
                    seen_tokens.append(self.access_token)
                    if len(seen_tokens) == 1:
                        raise RuntimeError("cTrader error CH_ACCESS_TOKEN_INVALID: Access token expired")
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            with patch.object(ctrader_service, "_CTraderAsyncClient", _FakeCTraderClient):
                client, new_cfg = asyncio.run(ctrader_service._connect_with_auto_refresh(get_all_settings()))
                self.assertEqual(seen_tokens, ["AT_ROTATED_2", "AT_ROTATED_3"])
                self.assertEqual(client.access_token, "AT_ROTATED_3")
                self.assertEqual(new_cfg["ctrader_refresh_token"], "RT_ROTATED_3")

        # Verify proxy parameter forwarding and descriptive network timeout error
        import httpx as _httpx

        proxy_seen = []

        class _MockTimeoutClient:
            def __init__(self, *args, **kwargs):
                proxy_seen.append(kwargs.get("proxy"))

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, params=None):
                raise _httpx.ConnectTimeout("")

        update_settings({"ctrader_oauth_proxy": "http://10.0.0.88:7890"})
        with patch("app.services.ctrader_oauth_service.httpx.AsyncClient", _MockTimeoutClient):
            err_resp = self.client.post("/api/ctrader/oauth/refresh")
            self.assertEqual(err_resp.status_code, 400)
            self.assertIn("ConnectTimeout", err_resp.json()["detail"])
            self.assertIn("全局网络代理配置", err_resp.json()["detail"])
        self.assertEqual(proxy_seen, ["http://10.0.0.88:7890"])
        update_settings({"ctrader_oauth_proxy": ""})

    def test_19_ctrader_bark_open_and_close_push_format(self):
        import asyncio
        import datetime
        from unittest.mock import patch
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model_pb
        from app.db import get_conn, update_settings
        from app.services import ctrader_service

        cst = datetime.timezone(datetime.timedelta(hours=8))
        open_dt = datetime.datetime(2026, 11, 20, 23, 35, 0, tzinfo=cst)
        close_dt = datetime.datetime(2026, 11, 21, 1, 20, 0, tzinfo=cst)
        open_ms = int(open_dt.timestamp() * 1000)
        close_ms = int(close_dt.timestamp() * 1000)

        # 1. Direct formatter checks (open + close loss + close profit)
        t_open, b_open = ctrader_service.format_ctrader_deal_bark_message(
            {
                "deal_id": "9001",
                "order_id": "403576173",
                "position_id": "663450252",
                "symbol_name": "XAUUSD",
                "side": "BUY",
                "volume": 0.02,
                "price": 3547.20,
                "stop_loss": 3523.21,
                "take_profit": 3583.18,
                "label": "GL1119.237cadc2-deadbeef",
                "executed_at": open_dt.isoformat(),
                "execution_ms": open_ms,
                "is_closing": 0,
            }
        )
        self.assertEqual(t_open, "🟢【开仓提醒】Gold1119 BUY XAUUSD")
        self.assertEqual(
            b_open,
            "\n".join(
                [
                    "• 策略模型：Gold1119",
                    "• 交易方向：做多 (BUY)",
                    "• 开仓价格：$3,547.20",
                    "• 止损价格：$3,523.21 (距开仓 $23.99)",
                    "• 止盈价格：$3,583.18 (距开仓 $35.98)",
                    "• 成交手数：0.02 手 (XAUUSD)",
                    "• 订单/持仓：#403576173 / #663450252",
                    "• 北京时间：2026-11-20 23:35:00",
                ]
            ),
        )

        t_loss, b_loss = ctrader_service.format_ctrader_deal_bark_message(
            {
                "deal_id": "9002",
                "order_id": "403580000",
                "position_id": "663450252",
                "symbol_name": "XAUUSD",
                "side": "SELL",
                "volume": 0.02,
                "price": 3531.25,
                "gross_profit": -31.90,
                "net_profit": -32.04,
                "account_balance": 506.23,
                "label": "Gold1119",
                "executed_at": close_dt.isoformat(),
                "execution_ms": close_ms,
                "is_closing": 1,
            }
        )
        self.assertEqual(t_loss, "🔻【平仓亏损】Gold1119 -$32.04")
        self.assertEqual(
            b_loss,
            "\n".join(
                [
                    "• 策略模型：Gold1119",
                    "• 结算净利：$-32.04 (毛利：$-31.90，费用：$-0.14)",
                    "• 账户余额：$506.23",
                    "• 平仓动作：平多 (SELL)",
                    "• 平仓价格：$3,531.25",
                    "• 成交手数：0.02 手 (XAUUSD)",
                    "• 持仓编号：#663450252",
                    "• 北京时间：2026-11-21 01:20:00",
                ]
            ),
        )

        t_win, b_win = ctrader_service.format_ctrader_deal_bark_message(
            {
                "deal_id": "9003",
                "position_id": "663450999",
                "symbol_name": "XAUUSD",
                "side": "BUY",
                "volume": 0.02,
                "price": 3520.10,
                "gross_profit": 2.16,
                "net_profit": 2.02,
                "account_balance": 538.27,
                "label": "Gold1119",
                "executed_at": close_dt.isoformat(),
                "execution_ms": close_ms,
                "is_closing": 1,
            }
        )
        self.assertEqual(t_win, "💰【平仓盈利】Gold1119 +$2.02")
        self.assertIn("• 结算净利：+$2.02 (毛利：$2.16，费用：$-0.14)", b_win)
        self.assertIn("• 平仓动作：平空 (BUY)", b_win)

        # 2. End-to-end sync_ctrader_deals enrichment + Bark push
        with get_conn() as conn:
            conn.execute("DELETE FROM ctrader_deals")
        update_settings(
            {
                "ctrader_client_id": "cid",
                "ctrader_client_secret": "csec",
                "ctrader_access_token": "tok",
                "ctrader_account_id": "999001",
                "ctrader_environment": "demo",
                "ctrader_bark_push_enabled": "1",
                "bark_enabled": "1",
                "bark_device_key": "dummy_bark_key",
            }
        )

        orig_client_cls = ctrader_service._CTraderAsyncClient

        class _RichFakeClient:
            def __init__(self, **kwargs):
                self.ctid_trader_account_id = 999001
                self.environment = "demo"
                self._order_risk_cache = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def fetch_symbols(self):
                return {1: "XAUUSD"}

            async def fetch_symbol_lot_sizes(self, symbol_ids):
                return {1: 10000}

            async def fetch_raw_deals_chunked(self, start_ms, end_ms, progress_cb=None):
                d_open = model_pb.ProtoOADeal(
                    dealId=9101,
                    orderId=403576173,
                    positionId=663450252,
                    symbolId=1,
                    tradeSide=model_pb.BUY,
                    filledVolume=200,
                    executionPrice=3547.20,
                    executionTimestamp=open_ms,
                    dealStatus=model_pb.FILLED,
                    moneyDigits=2,
                )
                d_close = model_pb.ProtoOADeal(
                    dealId=9102,
                    orderId=403580000,
                    positionId=663450252,
                    symbolId=1,
                    tradeSide=model_pb.SELL,
                    filledVolume=200,
                    executionPrice=3531.25,
                    executionTimestamp=close_ms,
                    dealStatus=model_pb.FILLED,
                    moneyDigits=2,
                    closePositionDetail=model_pb.ProtoOAClosePositionDetail(
                        entryPrice=3547.20,
                        grossProfit=-3190,
                        swap=0,
                        commission=-14,
                        balance=50623,
                        moneyDigits=2,
                    ),
                )
                return [d_open, d_close]

            async def fetch_order_labels(self, order_ids, progress_cb=None):
                out = {}
                for oid in order_ids:
                    if oid == 403576173:
                        out[str(oid)] = ("GL1119.237cadc2-test", "")
                        self._order_risk_cache[str(oid)] = (3523.21, 3583.18)
                    else:
                        out[str(oid)] = ("", "")
                return out

            async def fetch_reconcile_state(self):
                return {"positions": {}, "orders": {}}

            async def fetch_trader_balance(self):
                return 506.23

            def decode_raw_deals(self, raw_deals, symbol_map, order_meta, lot_sizes=None):
                return orig_client_cls.decode_raw_deals(self, raw_deals, symbol_map, order_meta, lot_sizes=lot_sizes)

        sent_pushes = []

        async def _fake_send_bark(**kwargs):
            sent_pushes.append(kwargs)
            return {"ok": True}

        with (
            patch.object(ctrader_service, "_CTraderAsyncClient", _RichFakeClient),
            patch.object(ctrader_service, "send_bark_push", side_effect=_fake_send_bark),
        ):
            res = asyncio.run(ctrader_service.sync_ctrader_deals(days=7, push_new=True, mode="incremental"))

        self.assertEqual(res["pushed"], 2)
        self.assertEqual(len(sent_pushes), 2)
        self.assertEqual(sent_pushes[0]["title"], "🟢【开仓提醒】Gold1119 BUY XAUUSD")
        self.assertIn("• 止损价格：$3,523.21 (距开仓 $23.99)", sent_pushes[0]["body"])
        self.assertIn("• 止盈价格：$3,583.18 (距开仓 $35.98)", sent_pushes[0]["body"])
        self.assertEqual(sent_pushes[1]["title"], "🔻【平仓亏损】Gold1119 -$32.04")
        self.assertIn("• 账户余额：$506.23", sent_pushes[1]["body"])
        self.assertIn("• 结算净利：$-32.04 (毛利：$-31.90，费用：$-0.14)", sent_pushes[1]["body"])

        update_settings({"bark_enabled": "0", "bark_device_key": ""})

    def test_20_ibkr_flex_sync_and_daily_overview(self):
        from unittest.mock import patch
        from app.db import get_conn, update_settings

        with get_conn() as conn:
            conn.execute("DELETE FROM ibkr_equity_snapshots")
            conn.execute("DELETE FROM ibkr_positions")
            conn.execute("DELETE FROM ibkr_trades")
            conn.execute("DELETE FROM ibkr_cash_transactions")

        update_settings(
            {
                "ibkr_flex_token": "flex_tok_123",
                "ibkr_flex_query_id": "998877",
                "ibkr_bark_daily_summary": "0",
            }
        )

        sample_flex_xml = """<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="DailyActivity" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20261121" toDate="20261123" whenGenerated="20261123;220000">
      <EquitySummaryInBase>
        <EquitySummaryByReportDateInBase accountId="U1234567" currency="USD" reportDate="20261122" totalCash="10000.00" stock="20000.00" options="500.00" />
        <EquitySummaryByReportDateInBase accountId="U1234567" currency="USD" reportDate="20261123" totalCash="10500.00" stock="20800.00" options="700.00" />
      </EquitySummaryInBase>
      <OpenPositions>
        <OpenPosition accountId="U1234567" levelOfDetail="SUMMARY" symbol="NVDA" conid="4815747" assetCategory="STK" description="NVIDIA CORP" currency="USD" fxRateToBase="1" position="100" markPrice="148.00" positionValue="14800.00" openPrice="140.00" costBasis="14000.00" fifoPnlUnrealized="800.00" side="Long" reportDate="20261123" />
        <OpenPosition accountId="U1234567" levelOfDetail="LOT" symbol="NVDA" conid="4815747" assetCategory="STK" currency="USD" fxRateToBase="1" position="50" markPrice="148.00" positionValue="7400.00" openPrice="138.00" costBasis="6900.00" fifoPnlUnrealized="500.00" side="Long" reportDate="20261123" />
      </OpenPositions>
      <Trades>
        <Trade accountId="U1234567" levelOfDetail="ORDER" symbol="AAPL" conid="265598" assetCategory="STK" currency="USD" fxRateToBase="1" buySell="SELL" quantity="-10" tradePrice="230.00" proceeds="2300.00" ibCommission="-1.00" fifoPnlRealized="150.00" tradeDate="20261123" tradeTime="100500" orderID="55501" />
        <Trade accountId="U1234567" levelOfDetail="EXECUTION" symbol="AAPL" conid="265598" assetCategory="STK" currency="USD" fxRateToBase="1" buySell="SELL" openCloseIndicator="C" quantity="-10" tradePrice="230.00" proceeds="2300.00" ibCommission="-1.00" fifoPnlRealized="150.00" tradeDate="20261123" tradeTime="100501" tradeID="88801" ibOrderID="55501" />
        <Trade accountId="U1234567" levelOfDetail="EXECUTION" symbol="MSFT" conid="272093" assetCategory="STK" currency="USD" fxRateToBase="1" buySell="BUY" openCloseIndicator="O" quantity="5" tradePrice="420.00" proceeds="-2100.00" ibCommission="-1.05" fifoPnlRealized="0" tradeDate="20261122" tradeTime="151000" tradeID="88802" ibOrderID="55502" />
      </Trades>
      <CashTransactions>
        <CashTransaction accountId="U1234567" transactionID="CT901" type="Dividends" symbol="AAPL" description="AAPL CASH DIVIDEND" currency="USD" fxRateToBase="1" amount="25.50" reportDate="20261123" />
      </CashTransactions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""

        req_urls = []
        get_stmt_calls = 0

        class _MockFlexResp:
            def __init__(self, text):
                self.text = text
                self.status_code = 200

            def raise_for_status(self):
                return None

        class _MockFlexClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, params=None):
                nonlocal get_stmt_calls
                req_urls.append((url, dict(params or {})))
                if "SendRequest" in url:
                    return _MockFlexResp(
                        "<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF777</ReferenceCode>"
                        "<Url>https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement</Url>"
                        "</FlexStatementResponse>"
                    )
                get_stmt_calls += 1
                if get_stmt_calls == 1:
                    return _MockFlexResp(
                        "<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>"
                        "<ErrorMessage>Statement generation in progress.</ErrorMessage></FlexStatementResponse>"
                    )
                return _MockFlexResp(sample_flex_xml)

        with (
            patch("app.services.ibkr_service.httpx.AsyncClient", _MockFlexClient),
            patch("app.services.ibkr_service.asyncio.sleep", return_value=None),
        ):
            sync_resp = self.client.post("/api/ibkr/sync", json={})

        self.assertEqual(sync_resp.status_code, 200, sync_resp.text)
        sr = sync_resp.json()
        self.assertTrue(sr["ok"])
        self.assertEqual(sr["accounts"], ["U1234567"])
        self.assertEqual(sr["equity_snapshots"], 2)
        self.assertEqual(sr["positions"], 1)  # SUMMARY only, LOT skipped
        self.assertEqual(sr["trades_inserted"], 2)  # EXECUTION only, ORDER skipped
        self.assertEqual(sr["cash_transactions"], 1)

        ov = self.client.get("/api/ibkr/overview")
        self.assertEqual(ov.status_code, 200)
        body = ov.json()
        s = body["summary"]
        self.assertEqual(s["latest_report_date"], "2026-11-23")
        self.assertEqual(s["total_nav"], 32000.0)
        self.assertEqual(s["day_change"], 1500.0)
        self.assertAlmostEqual(s["day_change_pct"], 4.92, places=2)
        self.assertEqual(s["unrealized_pnl"], 800.0)
        self.assertEqual(s["total_realized_pnl"], 150.0)
        self.assertEqual(s["total_commission"], -2.05)
        self.assertEqual(s["total_net_pnl"], 147.95)
        self.assertEqual(s["total_dividends"], 25.5)
        self.assertEqual(len(body["daily_equity"]), 2)
        self.assertEqual(len(body["daily_trades"]), 2)
        self.assertEqual(body["daily_trades"][0]["trade_date"], "2026-11-23")
        self.assertEqual(body["daily_trades"][0]["net_pnl"], 149.0)

    def test_21_global_proxy_switches_and_proxied_imap_gmail(self):
        import socket
        import threading
        from unittest.mock import patch
        from app.db import update_settings
        from app.services import email_service, proxy_service

        update_settings(
            {
                "global_proxy_url": "http://10.0.0.99:7890",
                "global_proxy_enabled": "1",
                "email_use_proxy": "1",
                "ai_use_proxy": "0",
                "ibkr_use_proxy": "1",
                "bark_use_proxy": "0",
                "ctrader_oauth_use_proxy": "1",
            }
        )
        self.assertEqual(proxy_service.resolve_proxy_url("email_use_proxy"), "http://10.0.0.99:7890")
        self.assertEqual(proxy_service.resolve_proxy_url("ai_use_proxy"), "")
        self.assertEqual(proxy_service.resolve_proxy_url("ibkr_use_proxy"), "http://10.0.0.99:7890")
        self.assertEqual(proxy_service.resolve_proxy_url("bark_use_proxy"), "")
        self.assertEqual(proxy_service.resolve_proxy_url("ctrader_oauth_use_proxy"), "http://10.0.0.99:7890")

        # Master switch off overrides all module switches
        update_settings({"global_proxy_enabled": "0"})
        self.assertEqual(proxy_service.resolve_proxy_url("email_use_proxy"), "")
        self.assertEqual(proxy_service.resolve_proxy_url("ctrader_oauth_use_proxy"), "")

        # Verify pure-stdlib HTTP CONNECT tunnel handshake with loopback server
        captured_req = []
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _serve_connect():
            conn, _ = srv.accept()
            data = conn.recv(4096).decode("ascii", errors="ignore")
            captured_req.append(data)
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            conn.close()

        t = threading.Thread(target=_serve_connect, daemon=True)
        t.start()
        try:
            sock = proxy_service.create_proxied_socket(
                "imap.gmail.com",
                993,
                f"http://user1:pass1@127.0.0.1:{port}",
                timeout=5.0,
            )
            sock.close()
        finally:
            t.join(timeout=2)
            srv.close()

        self.assertEqual(len(captured_req), 1)
        self.assertIn("CONNECT imap.gmail.com:993 HTTP/1.1", captured_req[0])
        self.assertIn("Proxy-Authorization: Basic ", captured_req[0])

        # Verify Gmail-specific auth hint & test-proxy endpoint
        with patch.object(email_service, "_connect_imap", side_effect=Exception("[AUTHENTICATIONFAILED] Invalid credentials")):
            res = email_service.test_imap_connection(
                {"email_imap_host": "imap.gmail.com", "email_username": "u@gmail.com", "email_password": "bad"}
            )
            self.assertFalse(res["ok"])
            self.assertIn("应用专用密码", res["message"])

        update_settings({"global_proxy_url": "http://127.0.0.1:7890", "global_proxy_enabled": "1"})

        class _ProxyMockResp:
            def __init__(self, code):
                self.status_code = code

        class _ProxyMockClient:
            def __init__(self, *args, **kwargs):
                self.proxy = kwargs.get("proxy")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url):
                return _ProxyMockResp(204 if "google" in url else 200)

        with patch("app.services.proxy_service.httpx.AsyncClient", _ProxyMockClient):
            tp = self.client.post("/api/settings/test-proxy")
            self.assertEqual(tp.status_code, 200)
            self.assertTrue(tp.json()["ok"])
            self.assertIn("Google: HTTP 204", tp.json()["message"])

        update_settings({"global_proxy_url": "", "email_use_proxy": "0", "ibkr_use_proxy": "0"})

    def test_22_ai_chat_analyze_gmail_za_bank_hkd_and_bookkeep(self):
        from unittest.mock import patch
        from app.db import update_settings
        from app.services import email_service

        update_settings(
            {
                "ai_provider": "local_fallback",
                "email_imap_host": "imap.gmail.com",
                "email_imap_port": "993",
                "email_username": "june@gmail.com",
                "email_password": "app_password_16ch",
            }
        )

        za_email_body = (
            "众安银行\n"
            "你有一次「最股励」机会\n\n"
            "你好 June,\n"
            "你于 2026-11-24 12:14:11 使用 ZA Card (0720) 完成一笔以下消费\n\n"
            "消费金额:\n"
            "HKD 11.87\n"
            "消费商户: WECHAT PAY HONG KONG L\n\n"
            "你于此笔消费得到一次 4%「最股励」回赠机会，回赠机会将于 48 小时后失效。\n"
        )

        fake_raw = [
            {
                "uid": "<za-bank-20261124-001@za.group>",
                "imap_uid": "501",
                "subject": "你有一次「最股励」机会",
                "sender": "ZA Bank <no-reply@bank.za.group>",
                "received_at": "2026-11-24 12:15",
                "body": za_email_body,
                "attachments": [],
            }
        ]

        with patch.object(email_service, "_fetch_raw_emails_sync", return_value=fake_raw):
            r1 = self.client.post("/api/chat", json={"message": "分析邮件中的数据把消费的内容计入个人财务"})
            self.assertEqual(r1.status_code, 200, r1.text)
            b1 = r1.json()
            self.assertEqual(len(b1["created_transactions"]), 1)
            tx = b1["created_transactions"][0]
            self.assertEqual(tx["type"], "expense")
            self.assertAlmostEqual(tx["amount"], round(11.87 * 0.92, 2), places=2)
            self.assertEqual(tx["occurred_at"], "2026-11-24 12:14")
            self.assertIn("WECHAT PAY HONG KONG L", tx["description"])
            self.assertIn("ZA Card (0720)", tx["description"])
            self.assertIn("HKD 11.87", tx["description"])
            self.assertIn("已自动计入个人财务 1 笔", b1["message"]["content"])

            # Second identical chat request must deduplicate and not double-charge
            r2 = self.client.post("/api/chat", json={"message": "分析邮件中的数据把消费的内容计入个人财务"})
            self.assertEqual(r2.status_code, 200, r2.text)
            b2 = r2.json()
            self.assertEqual(len(b2["created_transactions"]), 0)
            self.assertIn("跳过重复入账 1 笔", b2["message"]["content"])

        update_settings({"email_imap_host": "", "email_username": "", "email_password": ""})

    def test_23_multi_email_accounts_crud_and_chat_aggregation(self):
        from unittest.mock import patch
        from app.db import get_conn, update_settings
        from app.services import email_service

        with get_conn() as conn:
            conn.execute("DELETE FROM email_accounts")
        update_settings({"ai_provider": "local_fallback", "global_proxy_url": "http://10.0.0.8:7890", "global_proxy_enabled": "1"})

        a1 = self.client.post(
            "/api/email-accounts",
            json={
                "name": "主Gmail",
                "imap_host": "imap.gmail.com",
                "imap_port": 993,
                "username": "june@gmail.com",
                "password": "gmail_app_pwd",
                "use_ssl": True,
                "use_proxy": True,
                "enabled": True,
            },
        )
        self.assertEqual(a1.status_code, 200, a1.text)
        id1 = a1.json()["id"]
        self.assertEqual(a1.json()["password"], "")
        self.assertTrue(a1.json()["has_password"])

        a2 = self.client.post(
            "/api/email-accounts",
            json={
                "name": "QQ账单",
                "imap_host": "imap.qq.com",
                "imap_port": 993,
                "username": "76870@qq.com",
                "password": "qq_auth_code",
                "use_ssl": True,
                "use_proxy": False,
                "enabled": True,
            },
        )
        self.assertEqual(a2.status_code, 200, a2.text)
        id2 = a2.json()["id"]

        # Update name with blank password keeps existing password
        upd = self.client.put(f"/api/email-accounts/{id2}", json={"name": "QQ主力账单", "password": ""})
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.json()["name"], "QQ主力账单")
        self.assertTrue(upd.json()["has_password"])

        seen_calls = []

        def _fake_fetch_raw(settings, days=14, limit=25, unread_only=False):
            user = settings["email_username"]
            seen_calls.append((user, settings.get("email_use_proxy")))
            if user == "june@gmail.com":
                return [
                    {
                        "uid": "<multi-gmail-001@gmail.com>",
                        "imap_uid": "10",
                        "subject": "众安银行消费通知",
                        "sender": "ZA Bank <no-reply@za.group>",
                        "received_at": "2026-11-24 10:00",
                        "body": "你于 2026-11-24 10:00:00 使用 ZA Card (0720) 完成一笔以下消费\n消费金额:\nHKD 100.00\n消费商户: APPLE STORE HK",
                        "attachments": [],
                    }
                ]
            return [
                {
                    "uid": "<multi-qq-001@qq.com>",
                    "imap_uid": "20",
                    "subject": "付款成功通知",
                    "sender": "Alipay <service@alipay.com>",
                    "received_at": "2026-11-24 11:00",
                    "body": "您于 2026-11-24 11:00 付款成功，实付金额：¥35.50，商户：瑞幸咖啡",
                    "attachments": [],
                }
            ]

        with patch.object(email_service, "_fetch_raw_emails_sync", side_effect=_fake_fetch_raw):
            # 1. Fetch all enabled mailboxes
            f_all = self.client.post("/api/emails/fetch-parse", json={"days": 7, "limit": 20})
            self.assertEqual(f_all.status_code, 200, f_all.text)
            body_all = f_all.json()
            self.assertEqual(len(body_all["emails"]), 2)
            self.assertEqual(len(body_all["transactions"]), 2)
            self.assertEqual(seen_calls, [("june@gmail.com", "1"), ("76870@qq.com", "0")])

            # 2. Fetch single specific mailbox
            seen_calls.clear()
            f_one = self.client.post("/api/emails/fetch-parse", json={"days": 7, "limit": 20, "account_id": id2})
            self.assertEqual(f_one.status_code, 200)
            self.assertEqual(len(f_one.json()["emails"]), 1)
            self.assertEqual(seen_calls, [("76870@qq.com", "0")])

            # 3. AI Chat aggregates & bookkeeps both mailboxes
            chat_res = self.client.post("/api/chat", json={"message": "分析邮件中的数据把消费的内容计入个人财务"})
            self.assertEqual(chat_res.status_code, 200, chat_res.text)
            cb = chat_res.json()
            self.assertEqual(len(cb["created_transactions"]), 2)
            self.assertIn("主Gmail", cb["message"]["content"])
            self.assertIn("QQ主力账单", cb["message"]["content"])

        # Cleanup
        self.assertEqual(self.client.delete(f"/api/email-accounts/{id1}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/email-accounts/{id2}").status_code, 200)
        self.assertEqual(len(self.client.get("/api/email-accounts").json()), 0)
        update_settings({"global_proxy_url": ""})

    def test_24_multi_channel_bill_reconciliation_and_auto_cards(self):
        # Clean DB to test end-to-end Alipay + WeChat + Bank Email + Cash reconciliation cleanly
        from app.db import reset_all_data

        reset_all_data(reseed=False)

        # 1. Upload Alipay CSV with real combined payment methods & installment formats
        alipay_csv = (
            "支付宝交易记录明细查询\n"
            "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
            "2026-11-20 12:30:00,餐饮美食,瑞幸咖啡,luckin,生椰拿铁,支出,28.50,广发银行信用卡(3955)&红包,交易成功,ALI20261120001,,\n"
            "2026-11-21 18:10:00,数码电器,京东商城,jd,显示器,支出,1549.00,招商银行信用卡分期(9613) 12期&招商银行满减,交易成功,ALI20261121002,,\n"
            "2026-11-21 19:00:00,日用百货,便利店,store,矿泉水,支出,5.00,花呗,交易成功,ALI20261121003,,\n"
            "2026-11-21 20:00:00,信用借还,广发银行,cgb,还款,不计收支,2000.00,工商银行储蓄卡(1797),还款成功,ALI20261121004,,\n"
            "2026-11-21 21:00:00,家居家装,某店,shop,关闭订单,支出,99.00,广发银行信用卡(3955),交易关闭,ALI20261121005,,\n"
        ).encode("gb18030")

        p_ali = self.client.post(
            "/api/bills/parse-upload",
            files={"file": ("支付宝交易明细.csv", alipay_csv, "text/csv")},
        )
        self.assertEqual(p_ali.status_code, 200, p_ali.text)
        ali_body = p_ali.json()
        self.assertEqual(len(ali_body["transactions"]), 3)
        self.assertEqual(ali_body["transactions"][0]["card_last4"], "3955")
        self.assertEqual(ali_body["transactions"][0]["bank_name"], "广发银行")
        self.assertEqual(ali_body["transactions"][1]["card_last4"], "9613")
        self.assertEqual(ali_body["transactions"][1]["bank_name"], "招商银行")
        self.assertEqual(ali_body["transactions"][2]["account_display_name"], "支付宝花呗")
        self.assertGreaterEqual(len(ali_body["detected_cards"]), 3)

        # 2. Upload WeChat CSV sharing card (3955) + debit card (1797) + 零钱 + skipped full refund
        wechat_csv = (
            "微信支付账单明细\n"
            "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n"
            "2026-11-22 19:15:00,商户消费,海底捞火锅,聚餐,支出,¥286.00,广发银行信用卡(3955),支付成功,WX20261122001,,\n"
            "2026-11-22 20:00:00,商户消费,山姆会员店,日用品,支出,¥199.00,工商银行储蓄卡(1797),支付成功,WX20261122002,,\n"
            "2026-11-22 21:00:00,商户消费,退单商户,退货,支出,¥88.00,广发银行信用卡(3955),已全额退款,WX20261122003,,\n"
            "2026-11-23 09:00:00,商户消费,早餐店,豆浆油条,支出,¥12.00,零钱,支付成功,WX20261123004,,\n"
        ).encode("utf-8-sig")

        p_wx = self.client.post(
            "/api/bills/parse-upload",
            files={"file": ("微信支付账单.csv", wechat_csv, "text/csv")},
        )
        self.assertEqual(p_wx.status_code, 200, p_wx.text)
        wx_body = p_wx.json()
        self.assertEqual(len(wx_body["transactions"]), 3)  # full refund skipped

        # 3. Parse bank credit card monthly statement email containing BOTH a WeChat-via-3955 duplicate AND a direct POS swipe!
        stmt_res = self.client.post(
            "/api/emails/parse-text",
            json={
                "subject": "广发银行信用卡11月电子账单（尾号3955）",
                "sender": "cgb@cgbchina.com.cn",
                "body": (
                    "本期应还金额：人民币 1,500.00 元，最后还款日：2026-12-10。\n"
                    "2026-11-22 财付通-海底捞火锅 3955 286.00\n"
                    "2026-11-23 中石化加油站POS 3955 320.00\n"
                    "2026-11-24 自动还款 3955 -1000.00\n"
                ),
            },
        )
        self.assertEqual(stmt_res.status_code, 200, stmt_res.text)
        stmt_body = stmt_res.json()
        self.assertEqual(len(stmt_body["reminders"]), 1)
        self.assertEqual(len(stmt_body["transactions"]), 2)  # auto repayment skipped, 2 swipes kept

        # 4. Parse ZA Card HKD direct swipe email (0720)
        za_res = self.client.post(
            "/api/emails/parse-text",
            json={
                "subject": "众安银行消费通知",
                "sender": "ZA Bank <no-reply@za.group>",
                "body": "你于 2026-11-24 12:14:11 使用 ZA Card (0720) 完成一笔以下消费\n消费金额:\nHKD 100.00\n消费商户: APPLE STORE HK",
            },
        )
        self.assertEqual(za_res.status_code, 200)
        za_txs = za_res.json()["transactions"]
        self.assertEqual(len(za_txs), 1)
        self.assertEqual(za_txs[0]["card_last4"], "0720")

        # 5. Batch import Alipay + WeChat + Bank Email transactions with auto_create_accounts & auto_reconcile
        all_import_txs = ali_body["transactions"] + wx_body["transactions"] + stmt_body["transactions"] + za_txs
        imp = self.client.post(
            "/api/emails/import",
            json={
                "transactions": all_import_txs,
                "reminders": stmt_body["reminders"],
                "account_id": None,
                "skip_duplicates": True,
                "auto_create_accounts": True,
                "auto_reconcile": True,
                "email_logs": [],
            },
        )
        self.assertEqual(imp.status_code, 200, imp.text)
        imp_body = imp.json()
        self.assertEqual(imp_body["imported_tx_count"], 9)
        self.assertEqual(imp_body["reconciliation"]["newly_matched_count"], 1)  # 海底捞 ¥286 matched across WeChat & CGB 3955!

        # 6. Verify auto-created credit_cards & accounts (no duplicates for card 3955 shared across Alipay, WeChat & Email!)
        debt_ov = self.client.get("/api/debt/overview").json()
        cc_last4s = [c["last4"] for c in debt_ov["cards"]]
        self.assertIn("3955", cc_last4s)
        self.assertIn("9613", cc_last4s)
        self.assertEqual(cc_last4s.count("3955"), 1)  # deduplicated across 3 sources!

        accs = self.client.get("/api/accounts").json()["accounts"]
        acc_names = [a["name"] for a in accs]
        self.assertTrue(any("1797" in n for n in acc_names))
        self.assertTrue(any("0720" in n for n in acc_names))
        self.assertTrue(any("微信零钱" in n for n in acc_names))

        # 7. Add a Cash transaction and verify auto-created 现金钱包
        cash_res = self.client.post(
            "/api/transactions",
            json={
                "type": "expense",
                "amount": 45.0,
                "category": "餐饮",
                "description": "菜市场买菜",
                "source_type": "cash",
                "payment_channel": "cash",
                "payment_method": "现金",
            },
        )
        self.assertEqual(cash_res.status_code, 200, cash_res.text)
        self.assertEqual(cash_res.json()["reconcile_status"], "cash")
        self.assertIn("现金", cash_res.json()["account_name"])

        # 8. Check unified reconciliation overview: zero double-counting of ¥286, direct swipes (¥320 + ¥92) & cash (¥45) included!
        ov = self.client.get("/api/bills/reconcile/overview").json()
        # Effective = 3 Alipay + 3 WeChat + 2 Direct Cards (¥320 + ¥92) + 1 Cash (¥45) = 9 (duplicate ¥286 excluded!)
        self.assertEqual(ov["summary"]["total_effective_count"], 9)
        self.assertEqual(ov["summary"]["matched_count"], 1)
        self.assertEqual(ov["summary"]["deduped_count"], 1)
        self.assertAlmostEqual(ov["summary"]["deduped_expense_saved"], 286.0, places=2)
        self.assertEqual(ov["summary"]["direct_card_count"], 2)
        self.assertEqual(ov["summary"]["cash_count"], 1)
        expected_total_exp = round(28.50 + 1549.00 + 5.00 + 286.00 + 199.00 + 12.00 + 320.00 + 92.00 + 45.00, 2)
        self.assertAlmostEqual(ov["summary"]["total_expense"], expected_total_exp, places=2)

        # Verify /api/transactions default unsettled_summary also excludes the duplicate ¥286!
        tx_list = self.client.get("/api/transactions").json()
        self.assertAlmostEqual(tx_list["unsettled_summary"]["expense"], expected_total_exp, places=2)


if __name__ == "__main__":
    unittest.main()
