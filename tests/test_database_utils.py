import unittest
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from database.models import Base, User, Deal, Transaction, Setting
from database.database_utils import (
    get_user_by_telegram_id,
    get_user_by_username,
    get_or_create_user,
    get_active_deal_by_user,
    get_deal_by_unique_id,
    create_deal,
    save_transaction,
    get_transaction_by_payment_id,
    update_transaction_status,
    get_setting,
    set_setting,
    suspend_user,
    generate_unique_id,
)

class TestDatabaseUtils(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Create an in-memory async SQLite engine
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        self.Session = sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        # Create all tables
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_get_user_by_telegram_id(self):
        async with self.Session() as session:
            # Setup: Create a user
            user = User(telegram_id=12345, username="testuser")
            session.add(user)
            await session.commit()
            
            # Action & Assertion
            fetched = await get_user_by_telegram_id(session, 12345)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.username, "testuser")
            
            # Action & Assertion for non-existent user
            non_existent = await get_user_by_telegram_id(session, 99999)
            self.assertIsNone(non_existent)

    async def test_get_user_by_username(self):
        async with self.Session() as session:
            # Setup: Create a user
            user = User(telegram_id=12345, username="testuser")
            session.add(user)
            await session.commit()
            
            # Action & Assertion: plain username
            fetched = await get_user_by_username(session, "testuser")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.telegram_id, 12345)
            
            # Action & Assertion: username with '@'
            fetched_at = await get_user_by_username(session, "@testuser")
            self.assertIsNotNone(fetched_at)
            self.assertEqual(fetched_at.telegram_id, 12345)
            
            # Action & Assertion: non-existent username
            non_existent = await get_user_by_username(session, "other")
            self.assertIsNone(non_existent)

    async def test_get_or_create_user(self):
        async with self.Session() as session:
            # 1. User does not exist (Create new)
            user1 = await get_or_create_user(session, 111, "newuser")
            self.assertIsNotNone(user1)
            self.assertEqual(user1.telegram_id, 111)
            self.assertEqual(user1.username, "newuser")
            
            # Verify in DB
            db_user = await get_user_by_telegram_id(session, 111)
            self.assertEqual(db_user.id, user1.id)
            
            # 2. Get existing user by telegram_id
            user2 = await get_or_create_user(session, 111, "newuser_changed")
            self.assertEqual(user2.id, user1.id)
            
            # 3. User pre-exists with dummy/negative telegram_id and same username
            # In create_deal, we pre-create users with dummy/negative telegram_id
            dummy_user = User(telegram_id=-10, username="pre_created")
            session.add(dummy_user)
            await session.commit()
            
            # Now they start the bot with telegram_id 222
            linked_user = await get_or_create_user(session, 222, "pre_created")
            self.assertEqual(linked_user.id, dummy_user.id)
            self.assertEqual(linked_user.telegram_id, 222)

    async def test_get_active_deal_by_user(self):
        async with self.Session() as session:
            buyer = User(telegram_id=111, username="buyer")
            seller = User(telegram_id=222, username="seller")
            session.add_all([buyer, seller])
            await session.commit()
            
            # Create active deal
            deal = Deal(
                unique_id=1001,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("100.00"),
                escrow_fee=Decimal("5.00"),
                total_amount=Decimal("105.00"),
                payment_method="GATEWAY",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
            # Fetch for buyer
            buyer_deal = await get_active_deal_by_user(session, 111)
            self.assertIsNotNone(buyer_deal)
            self.assertEqual(buyer_deal.unique_id, 1001)
            
            # Fetch for seller
            seller_deal = await get_active_deal_by_user(session, 222)
            self.assertIsNotNone(seller_deal)
            self.assertEqual(seller_deal.unique_id, 1001)
            
            # Fetch for unrelated user
            unrelated_deal = await get_active_deal_by_user(session, 999)
            self.assertIsNone(unrelated_deal)
            
            # Complete the deal
            deal.status = "Completed"
            await session.commit()
            
            # Deal should no longer be fetched as active
            no_active_deal = await get_active_deal_by_user(session, 111)
            self.assertIsNone(no_active_deal)

    async def test_get_deal_by_unique_id(self):
        async with self.Session() as session:
            buyer = User(telegram_id=111, username="buyer")
            seller = User(telegram_id=222, username="seller")
            session.add_all([buyer, seller])
            await session.commit()
            
            deal = Deal(
                unique_id=9876,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("50.0"),
                escrow_fee=Decimal("2.5"),
                total_amount=Decimal("52.5"),
                payment_method="GATEWAY",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
            fetched = await get_deal_by_unique_id(session, 9876)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.unique_id, 9876)
            self.assertEqual(fetched.amount, Decimal("50.0"))

    async def test_create_deal(self):
        async with self.Session() as session:
            # 1. Create deal with existing users
            buyer = User(telegram_id=111, username="buyer_user")
            seller = User(telegram_id=222, username="seller_user")
            session.add_all([buyer, seller])
            await session.commit()
            
            deal = await create_deal(
                session=session,
                unique_id=1234,
                buyer_username="buyer_user",
                seller_username="seller_user",
                amount=200.0,
                transfer_method="TRX",
                gateway_fee=1.5,
                payment_method="GATEWAY"
            )
            self.assertIsNotNone(deal)
            self.assertEqual(deal.unique_id, 1234)
            self.assertEqual(deal.buyer_id, buyer.id)
            self.assertEqual(deal.seller_id, seller.id)
            self.assertEqual(deal.amount, Decimal("200.0"))
            self.assertEqual(deal.gateway_fee, Decimal("1.5"))
            
            # 2. Create deal with non-existent users (should create dummy users)
            deal2 = await create_deal(
                session=session,
                unique_id=5678,
                buyer_username="ghost_buyer",
                seller_username="ghost_seller",
                amount=150.0,
                transfer_method="USDT",
                gateway_fee=0.0,
                payment_method="GATEWAY"
            )
            self.assertIsNotNone(deal2)
            
            # Verify dummy users exist
            ghost_b = await get_user_by_username(session, "ghost_buyer")
            ghost_s = await get_user_by_username(session, "ghost_seller")
            self.assertIsNotNone(ghost_b)
            self.assertIsNotNone(ghost_s)
            self.assertEqual(deal2.buyer_id, ghost_b.id)
            self.assertEqual(deal2.seller_id, ghost_s.id)
            # Dummy users must have unique negative or distinct dummy telegram_id
            self.assertNotEqual(ghost_b.telegram_id, ghost_s.telegram_id)

    async def test_save_transaction(self):
        async with self.Session() as session:
            buyer = User(telegram_id=111, username="buyer")
            seller = User(telegram_id=222, username="seller")
            session.add_all([buyer, seller])
            await session.commit()
            
            deal = Deal(
                unique_id=1001,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("100.00"),
                escrow_fee=Decimal("5.00"),
                total_amount=Decimal("105.00"),
                payment_method="GATEWAY",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
            tx = await save_transaction(
                session=session,
                user_id=buyer.id,
                deal_id=deal.id,
                tx_type="ESCROW_PAY",
                payment_id="PAY-12345",
                amount=105.0,
                currency="USDT"
            )
            self.assertIsNotNone(tx)
            self.assertEqual(tx.payment_id, "PAY-12345")
            self.assertEqual(tx.status, "waiting")
            self.assertFalse(tx.completed)

    async def test_get_transaction_by_payment_id(self):
        async with self.Session() as session:
            user = User(telegram_id=111, username="buyer")
            session.add(user)
            await session.commit()
            
            tx = Transaction(
                user_id=user.id,
                type="TOPUP",
                payment_id="PAY-999",
                amount=Decimal("10.0"),
                currency="USDT",
                status="waiting"
            )
            session.add(tx)
            await session.commit()
            
            fetched = await get_transaction_by_payment_id(session, "PAY-999")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.payment_id, "PAY-999")
            
            non_existent = await get_transaction_by_payment_id(session, "PAY-NONE")
            self.assertIsNone(non_existent)

    async def test_update_transaction_status(self):
        async with self.Session() as session:
            user = User(telegram_id=111, username="buyer")
            session.add(user)
            await session.commit()
            
            tx = Transaction(
                user_id=user.id,
                type="TOPUP",
                payment_id="PAY-777",
                amount=Decimal("10.0"),
                currency="USDT",
                status="waiting",
                completed=False
            )
            session.add(tx)
            await session.commit()
            
            updated = await update_transaction_status(session, "PAY-777", "success", completed=True)
            self.assertIsNotNone(updated)
            self.assertEqual(updated.status, "success")
            self.assertTrue(updated.completed)
            
            # Check in DB
            db_tx = await get_transaction_by_payment_id(session, "PAY-777")
            self.assertEqual(db_tx.status, "success")
            self.assertTrue(db_tx.completed)

    async def test_settings(self):
        async with self.Session() as session:
            # 1. get_setting default
            val = await get_setting(session, "non_existent", "default_val")
            self.assertEqual(val, "default_val")
            
            # 2. set_setting
            await set_setting(session, "some_key", "some_value")
            
            # 3. get_setting
            val2 = await get_setting(session, "some_key")
            self.assertEqual(val2, "some_value")
            
            # Update setting
            await set_setting(session, "some_key", "new_value")
            val3 = await get_setting(session, "some_key")
            self.assertEqual(val3, "new_value")

    async def test_suspend_user(self):
        async with self.Session() as session:
            user = User(telegram_id=555, username="suspended_user")
            session.add(user)
            await session.commit()
            
            # Suspend
            await suspend_user(session, 555, True)
            db_user = await get_user_by_telegram_id(session, 555)
            self.assertTrue(db_user.is_suspended)
            
            # Unsuspend
            await suspend_user(session, 555, False)
            db_user2 = await get_user_by_telegram_id(session, 555)
            self.assertFalse(db_user2.is_suspended)

    async def test_generate_unique_id(self):
        async with self.Session() as session:
            # Generate multiple times, make sure they are within 1000 and 9999
            for _ in range(10):
                uid = await generate_unique_id(session)
                self.assertTrue(1000 <= uid <= 9999)
            
            # Add an active deal using unique ID 1234
            buyer = User(telegram_id=111, username="buyer")
            seller = User(telegram_id=222, username="seller")
            session.add_all([buyer, seller])
            await session.commit()
            
            deal = Deal(
                unique_id=1234,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount=Decimal("10.0"),
                escrow_fee=Decimal("0.5"),
                total_amount=Decimal("10.5"),
                payment_method="GATEWAY",
                status="Active"
            )
            session.add(deal)
            await session.commit()
            
            # Now mock random.randint to return 1234 first, then 5678.
            # We want to verify generate_unique_id skips 1234 because it is active.
            from unittest import mock
            with mock.patch("random.randint", side_effect=[1234, 5678]):
                uid = await generate_unique_id(session)
                self.assertEqual(uid, 5678)
                
            # If the deal is completed, 1234 is no longer active, so it should be allowed again.
            deal.status = "Completed"
            await session.commit()
            
            with mock.patch("random.randint", side_effect=[1234, 5678]):
                uid = await generate_unique_id(session)
                self.assertEqual(uid, 1234)
