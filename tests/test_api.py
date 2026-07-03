import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from decimal import Decimal
import base64
import os
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from database.models import Base, User, Deal, Transaction, APIKey, Setting
from main import app

class TestAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Create an in-memory async SQLite engine for testing
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        self.Session = sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        # Create all tables
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        # Patch main's session maker to use our sqlite session maker
        self.session_patcher = patch("main.async_session", self.Session)
        self.session_patcher.start()
        
        # Mock the Bot object to prevent real Telegram notifications and make methods awaitable
        self.bot_patcher = patch("main.bot")
        self.mock_bot = self.bot_patcher.start()
        self.mock_bot.send_message = AsyncMock()
        
        # Mock payout API triggers
        self.create_payout_patcher = patch("main.create_payout")
        self.mock_create_payout = self.create_payout_patcher.start()
        
        # Set up httpx client
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.create_payout_patcher.stop()
        self.bot_patcher.stop()
        self.session_patcher.stop()
        await self.engine.dispose()

    async def create_user_and_key(self, telegram_id, username, is_suspended=False, key_str="RP-testkey", key_active=True):
        async with self.Session() as session:
            user = User(telegram_id=telegram_id, username=username, is_suspended=is_suspended)
            session.add(user)
            await session.commit()
            
            api_key = APIKey(user_id=user.id, api_key=key_str, is_active=key_active)
            session.add(api_key)
            await session.commit()
            
            return user, api_key

    # --- Task 1: Key Checks / Auth Tests ---

    async def test_auth_missing_key(self):
        response = await self.client.get("/api/v1/deals/0190772b1a237190b4ad624f114c0000")
        self.assertEqual(response.status_code, 401)
        self.assertIn("API Key missing", response.json()["detail"])

    async def test_auth_invalid_key(self):
        response = await self.client.get("/api/v1/deals/0190772b1a237190b4ad624f114c0000", headers={"X-API-KEY": "RP-invalid"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid API Key", response.json()["detail"])

    async def test_auth_suspended_user(self):
        await self.create_user_and_key(111, "suspended_user", is_suspended=True, key_str="RP-suspended")
        response = await self.client.get("/api/v1/deals/0190772b1a237190b4ad624f114c0000", headers={"X-API-KEY": "RP-suspended"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("User is suspended", response.json()["detail"])

    async def test_auth_user_not_found(self):
        async with self.Session() as session:
            # Add API key with a non-existent user_id to simulate user not found
            api_key = APIKey(user_id=99999, api_key="RP-ghost", is_active=True)
            session.add(api_key)
            await session.commit()
            
        response = await self.client.get("/api/v1/deals/0190772b1a237190b4ad624f114c0000", headers={"X-API-KEY": "RP-ghost"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("User not found", response.json()["detail"])

    async def test_auth_revoked_key(self):
        await self.create_user_and_key(222, "revoked_user", is_suspended=False, key_str="RP-revoked", key_active=False)
        response = await self.client.get("/api/v1/deals/0190772b1a237190b4ad624f114c0000", headers={"X-API-KEY": "RP-revoked"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid API Key", response.json()["detail"])

    async def test_key_revocation_via_admin(self):
        user, api_key = await self.create_user_and_key(333, "revokable_user", key_str="RP-torevoke")
        
        # Verify it works initially (404 Deal not found instead of 401/403)
        response = await self.client.get("/api/v1/deals/0190772b1a237190b4ad624f114c0000", headers={"X-API-KEY": "RP-torevoke"})
        self.assertEqual(response.status_code, 404)
        
        # Use basic auth credentials from config
        import config
        auth_str = f"{config.ADMIN_USERNAME}:{config.ADMIN_PASSWORD}"
        auth_bytes = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        
        # Call admin revoke key
        revoke_res = await self.client.post(
            f"/admin/api-keys/revoke/{api_key.id}", 
            headers={"Authorization": f"Basic {auth_bytes}"}
        )
        self.assertEqual(revoke_res.status_code, 200)
        self.assertTrue(revoke_res.json()["success"])
        
        # Verify it is now invalid
        response2 = await self.client.get("/api/v1/deals/0190772b1a237190b4ad624f114c0000", headers={"X-API-KEY": "RP-torevoke"})
        self.assertEqual(response2.status_code, 401)

    # --- Task 2: POST /api/v1/deals Tests ---

    async def test_create_deal_success(self):
        user, api_key = await self.create_user_and_key(444, "caller_user", key_str="RP-valid")
        
        # Set custom gateway fee percent
        async with self.Session() as session:
            setting = Setting(key_name="gateway_fee_percent", value="2.5")
            session.add(setting)
            await session.commit()
            
        payload = {
            "seller_username": "@seller_test",
            "buyer_username": "buyer_test",
            "amount": 200.0,
            "transfer_method": "USDT_TRON"
        }
        
        response = await self.client.post(
            "/api/v1/deals",
            json=payload,
            headers={"X-API-KEY": "RP-valid"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertIn("unique_id", data)
        self.assertEqual(data["buyer_username"], "buyer_test")
        self.assertEqual(data["seller_username"], "seller_test")
        self.assertEqual(data["amount"], 200.0)
        self.assertEqual(data["status"], "Active")
        self.assertIsNotNone(data["created_at"])
        
        # Check DB
        async with self.Session() as session:
            db_res = await session.execute(
                select(Deal).where(Deal.unique_id == bytes.fromhex(data["unique_id"]))
            )
            deal = db_res.scalars().first()
            self.assertIsNotNone(deal)
            self.assertEqual(deal.amount, Decimal("200.0"))
            self.assertEqual(deal.gateway_fee, Decimal("5.0"))  # 200 * 2.5%
            self.assertEqual(deal.payment_method, "GATEWAY")

    # --- Task 2: GET /api/v1/deals/{unique_id} Tests ---

    async def test_get_deal_success(self):
        user, api_key = await self.create_user_and_key(555, "retriever_user", key_str="RP-valid2")
        
        async with self.Session() as session:
            buyer = User(telegram_id=123, username="buyer_one")
            seller = User(telegram_id=456, username="seller_one")
            session.add_all([buyer, seller])
            await session.commit()
            
            uid = bytes.fromhex("0190772b1a237190b4ad624f114c0006")
            deal = Deal(
                unique_id=uid,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("150.0"),
                escrow_fee=Decimal("0.0"),
                gateway_fee=Decimal("1.5"),
                total_amount=Decimal("151.5"),
                payment_method="GATEWAY",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
        response = await self.client.get(
            "/api/v1/deals/0190772b1a237190b4ad624f114c0006",
            headers={"X-API-KEY": "RP-valid2"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["unique_id"], "0190772b1a237190b4ad624f114c0006")
        self.assertEqual(data["buyer_username"], "buyer_one")
        self.assertEqual(data["seller_username"], "seller_one")
        self.assertEqual(data["amount"], 150.0)
        self.assertEqual(data["status"], "Active")

    # --- Task 2: POST /api/v1/deals/{unique_id}/release Tests ---

    async def test_release_deal_wallet(self):
        user, api_key = await self.create_user_and_key(666, "releaser_user", key_str="RP-valid3")
        
        async with self.Session() as session:
            buyer = User(telegram_id=1111, username="buyer_w")
            seller = User(telegram_id=2222, username="seller_w", wallet_balance=Decimal("10.0"))
            session.add_all([buyer, seller])
            await session.commit()
            
            uid = bytes.fromhex("0190772b1a237190b4ad624f114c0007")
            deal = Deal(
                unique_id=uid,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("50.0"),
                escrow_fee=Decimal("0.0"),
                gateway_fee=Decimal("0.0"),
                total_amount=Decimal("50.0"),
                payment_method="WALLET",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
        response = await self.client.post(
            "/api/v1/deals/0190772b1a237190b4ad624f114c0007/release",
            headers={"X-API-KEY": "RP-valid3"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        
        # Check database
        async with self.Session() as session:
            s_res = await session.execute(select(User).where(User.telegram_id == 2222))
            seller_db = s_res.scalars().first()
            self.assertEqual(seller_db.wallet_balance, Decimal("60.0"))
            
            d_res = await session.execute(select(Deal).where(Deal.unique_id == uid))
            deal_db = d_res.scalars().first()
            self.assertEqual(deal_db.status, "Completed")
            
            t_res = await session.execute(select(Transaction).where(Transaction.deal_id == deal_db.id))
            tx_db = t_res.scalars().first()
            self.assertIsNotNone(tx_db)
            self.assertEqual(tx_db.type, "ESCROW_RELEASE")
            self.assertTrue(tx_db.completed)

    async def test_release_deal_gateway_success(self):
        user, api_key = await self.create_user_and_key(777, "releaser_user_g", key_str="RP-valid4")
        
        self.mock_create_payout.return_value = {"success": True, "raw": {"id": "payout-123"}}
        
        async with self.Session() as session:
            buyer = User(telegram_id=3333, username="buyer_g")
            seller = User(telegram_id=4444, username="seller_g", wallet_balance=Decimal("0.0"))
            session.add_all([buyer, seller])
            await session.commit()
            
            uid = bytes.fromhex("0190772b1a237190b4ad624f114c0008")
            deal = Deal(
                unique_id=uid,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("100.0"),
                escrow_fee=Decimal("0.0"),
                gateway_fee=Decimal("1.0"),
                total_amount=Decimal("101.0"),
                payment_method="GATEWAY",
                seller_wallet="TCdRk63AC8gXH1qPrsesdFXeKLERh9m2ni",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
        response = await self.client.post(
            "/api/v1/deals/0190772b1a237190b4ad624f114c0008/release",
            headers={"X-API-KEY": "RP-valid4"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        
        async with self.Session() as session:
            s_res = await session.execute(select(User).where(User.telegram_id == 4444))
            seller_db = s_res.scalars().first()
            self.assertEqual(seller_db.wallet_balance, Decimal("0.0"))
            
            d_res = await session.execute(select(Deal).where(Deal.unique_id == uid))
            deal_db = d_res.scalars().first()
            self.assertEqual(deal_db.status, "Completed")
            
            self.mock_create_payout.assert_called_once()
            
            t_res = await session.execute(select(Transaction).where(Transaction.deal_id == deal_db.id))
            tx_db = t_res.scalars().first()
            self.assertIsNotNone(tx_db)
            self.assertEqual(tx_db.type, "ESCROW_RELEASE")
            self.assertTrue(tx_db.completed)

    async def test_release_deal_gateway_missing_wallet(self):
        user, api_key = await self.create_user_and_key(888, "releaser_user_gw_fail", key_str="RP-valid5")
        
        async with self.Session() as session:
            buyer = User(telegram_id=5555, username="buyer_gw_fail")
            seller = User(telegram_id=6666, username="seller_gw_fail")
            session.add_all([buyer, seller])
            await session.commit()
            
            uid = bytes.fromhex("0190772b1a237190b4ad624f114c0009")
            deal = Deal(
                unique_id=uid,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("100.0"),
                escrow_fee=Decimal("0.0"),
                gateway_fee=Decimal("1.0"),
                total_amount=Decimal("101.0"),
                payment_method="GATEWAY",
                seller_wallet=None,
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
        response = await self.client.post(
            "/api/v1/deals/0190772b1a237190b4ad624f114c0009/release",
            headers={"X-API-KEY": "RP-valid5"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertIn("wallet address is missing", response.json()["error"])

    async def test_release_deal_gateway_payout_fail(self):
        user, api_key = await self.create_user_and_key(889, "releaser_user_gw_payout_fail", key_str="RP-valid6")
        
        self.mock_create_payout.return_value = {"success": False, "error": "Insufficient funds in NOWPayments balance"}
        
        async with self.Session() as session:
            buyer = User(telegram_id=7777, username="buyer_gw_payout_fail")
            seller = User(telegram_id=8888, username="seller_gw_payout_fail")
            session.add_all([buyer, seller])
            await session.commit()
            
            uid = bytes.fromhex("0190772b1a237190b4ad624f114c000a")
            deal = Deal(
                unique_id=uid,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("100.0"),
                escrow_fee=Decimal("0.0"),
                gateway_fee=Decimal("1.0"),
                total_amount=Decimal("101.0"),
                payment_method="GATEWAY",
                seller_wallet="TCdRk63AC8gXH1qPrsesdFXeKLERh9m2ni",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
        response = await self.client.post(
            "/api/v1/deals/0190772b1a237190b4ad624f114c000a/release",
            headers={"X-API-KEY": "RP-valid6"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertIn("Insufficient funds in NOWPayments balance", response.json()["error"])
