import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from database.models import Base, User, APIKey, Setting
from database.database_utils import get_or_create_user, set_setting
from states.form_states import WalletStates, DealCreation
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import handlers.commands as cmd_handlers


class TestBotCommands(unittest.IsolatedAsyncioTestCase):
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

        # Mock FSM storage
        self.storage = MemoryStorage()
        
        # Mock Bot
        self.bot = AsyncMock()
        
        # Mock Chat
        self.chat = AsyncMock()
        self.chat.id = 123456
        self.chat.type = "group"
        self.chat.title = "Test Group"
        self.chat.export_invite_link = AsyncMock(return_value="https://t.me/joinlink")

        # Mock From User
        self.from_user = MagicMock()
        self.from_user.id = 9999
        self.from_user.username = "test_user"

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_start_command_suspended(self):
        # Create user and suspend them
        async with self.Session() as db_session:
            user = await get_or_create_user(db_session, self.from_user.id, self.from_user.username)
            user.is_suspended = True
            await db_session.commit()

        # Create message mock
        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.text = "/start"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.start(message, state)
        
        # Assert user received suspension message
        message.answer.assert_called_once_with("⛔ Your account has been suspended by the administrator.")

    async def test_start_command_active(self):
        # Create active user
        async with self.Session() as db_session:
            await get_or_create_user(db_session, self.from_user.id, self.from_user.username)

        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.chat.type = "private"
        message.text = "/start"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.start(message, state)
        
        # Check welcome messages
        message.answer.assert_any_call("I'm designed to facilitate transactions within a group chat. Please use me in the designated group chat for initiating and managing deals.")

    async def test_help_command(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.answer = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.help_command(message)

        message.answer.assert_called_once()
        self.assertIn("Help & Commands", message.answer.call_args[0][0])

    async def test_sent_wallet_validation(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.text = "/sent_wallet"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_seller_wallet(message, state)

        message.answer.assert_called_once_with("⚠️ Please provide your wallet address. Usage: `/sent_wallet [wallet_address]`")

    async def test_menu_command(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.menu_command(message, state)

        state.clear.assert_called_once()
        message.answer.assert_called_once()
        self.assertIn("Main Menu", message.answer.call_args[0][0])

    async def test_callback_menu_profile_with_api_key(self):
        # Create user and API Key
        async with self.Session() as db_session:
            user = await get_or_create_user(db_session, self.from_user.id, self.from_user.username)
            user.wallet_balance = Decimal("123.456")
            api_key_obj = APIKey(user_id=user.id, api_key="my-test-api-key", is_active=True)
            db_session.add(api_key_obj)
            await db_session.commit()

        callback = AsyncMock()
        callback.from_user = self.from_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.show_profile_menu(callback, state)

        callback.message.edit_text.assert_called_once()
        response_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("123.45600000", response_text)
        self.assertIn("my-test-api-key", response_text)

    async def test_callback_menu_wallet(self):
        async with self.Session() as db_session:
            user = await get_or_create_user(session=db_session, telegram_id=self.from_user.id, username=self.from_user.username)
            user.wallet_balance = Decimal("50.0")
            await db_session.commit()

        callback = AsyncMock()
        callback.from_user = self.from_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.show_wallet_menu(callback, state)

        callback.message.edit_text.assert_called_once()
        self.assertIn("50.00000000", callback.message.edit_text.call_args[0][0])

    async def test_topup_gateway_and_manual_prompts(self):
        callback = AsyncMock()
        callback.from_user = self.from_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_wallet_topup(callback, state)
            await cmd_handlers.topup_gateway_handler(callback, state)

        state.set_state.assert_called_once_with(WalletStates.awaiting_topup_amount_gateway)

        # Test manual prompt
        state.reset_mock()
        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.topup_manual_handler(callback, state)
        state.set_state.assert_called_once_with(WalletStates.awaiting_topup_amount_manual)

    async def test_process_topup_amount_manual(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.text = "100.0"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_topup_amount_manual(message, state)

        state.clear.assert_called_once()
        message.answer.assert_called_once()
        self.assertIn("100.0", message.answer.call_args[0][0])

    async def test_withdrawal_flow_success(self):
        async with self.Session() as db_session:
            user = await get_or_create_user(db_session, self.from_user.id, self.from_user.username)
            user.wallet_balance = Decimal("100.0")
            await set_setting(db_session, "min_withdrawal", "15.0")
            await db_session.commit()

        # Step 1: Request withdrawal starts
        callback = AsyncMock()
        callback.from_user = self.from_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.wallet_withdraw_handler(callback, state)

        state.set_state.assert_called_once_with(WalletStates.awaiting_withdrawal_address)

        # Step 2: User provides address
        message_addr = AsyncMock()
        message_addr.from_user = self.from_user
        message_addr.text = "TXYZ123456789012345678901234567890"  # 34 chars TRC20
        message_addr.answer = AsyncMock()
        
        state.reset_mock()
        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_withdrawal_address(message_addr, state)

        state.update_data.assert_called_once_with(withdraw_address="TXYZ123456789012345678901234567890")
        state.set_state.assert_called_once_with(WalletStates.awaiting_withdrawal_amount)

        # Step 3: User provides amount (success case)
        message_amt = AsyncMock()
        message_amt.from_user = self.from_user
        message_amt.text = "50.0"
        message_amt.answer = AsyncMock()
        
        state.reset_mock()
        state.get_data.return_value = {"withdraw_address": "TXYZ123456789012345678901234567890"}
        
        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_withdrawal_amount(message_amt, state)

        state.clear.assert_called_once()
        message_amt.answer.assert_called_once()
        self.assertIn("Withdrawal Successful", message_amt.answer.call_args[0][0])

        # Check balance in DB
        async with self.Session() as db_session:
            user_db = await get_or_create_user(db_session, self.from_user.id, self.from_user.username)
            self.assertEqual(user_db.wallet_balance, Decimal("50.0"))

    async def test_withdrawal_flow_below_min(self):
        async with self.Session() as db_session:
            user = await get_or_create_user(db_session, self.from_user.id, self.from_user.username)
            user.wallet_balance = Decimal("100.0")
            await set_setting(db_session, "min_withdrawal", "15.0")
            await db_session.commit()

        message_amt = AsyncMock()
        message_amt.from_user = self.from_user
        message_amt.text = "10.0"  # Less than 15.0 limit
        message_amt.reply = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_withdrawal_amount(message_amt, state)

        message_amt.reply.assert_called_once()
        self.assertIn("minimum withdrawal amount is 15.00", message_amt.reply.call_args[0][0])

    async def test_withdrawal_flow_insufficient_balance(self):
        async with self.Session() as db_session:
            user = await get_or_create_user(db_session, self.from_user.id, self.from_user.username)
            user.wallet_balance = Decimal("10.0")
            await set_setting(db_session, "min_withdrawal", "5.0")
            await db_session.commit()

        message_amt = AsyncMock()
        message_amt.from_user = self.from_user
        message_amt.text = "20.0"  # Exceeds user balance
        message_amt.reply = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_withdrawal_amount(message_amt, state)

        message_amt.reply.assert_called_once()
        self.assertIn("Insufficient balance", message_amt.reply.call_args[0][0])

    async def test_dispute_solver_dynamic_moderator(self):
        # We need Mongo collection mocked for collection_lobby
        mock_deal = {
            "unique_id": "123",
            "Seller's Telegram User": "seller_user",
            "Buyer's Telegram User": "test_user",
            "Status": "Active"
        }

        # Mock collection_lobby.find_one
        find_mock = AsyncMock(return_value=mock_deal)
        update_mock = AsyncMock()

        message = AsyncMock()
        message.from_user = self.from_user
        message.text = "/dispute 123"
        message.chat = self.chat
        message.reply = AsyncMock()
        message.bot = self.bot
        state = AsyncMock()

        async with self.Session() as db_session:
            await set_setting(db_session, "moderator_user_id", "777777")
            await db_session.commit()

        with patch("handlers.commands.collection_lobby.find_one", find_mock), \
             patch("handlers.commands.collection_lobby.update_one", update_mock), \
             patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.dispute_solver(message, state)

        # Verify chat invite link export is awaited
        message.chat.export_invite_link.assert_called_once()
        
        # Verify moderator notified with dynamic ID
        message.bot.send_message.assert_called_once_with(
            chat_id="777777",
            text="A dispute has been raised for Deal ID 123 in the group 'Test Group'. Please join using this invite link to assist: https://t.me/joinlink"
        )

    async def test_new_deal_command_group_chat(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.chat.type = "group"
        message.answer = AsyncMock()
        message.bot = self.bot
        self.bot.get_me = AsyncMock(return_value=MagicMock(username="my_test_bot"))
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.create_a_lobby(message, state)

        # Assert correct message text and button markup with deep link
        message.answer.assert_called_once()
        call_args = message.answer.call_args[1]
        self.assertIn("Create a New Deal", call_args.get("text") or message.answer.call_args[0][0])
        reply_markup = call_args.get("reply_markup")
        self.assertIsNotNone(reply_markup)
        # Check inline keyboard button
        inline_kb = reply_markup.inline_keyboard
        self.assertEqual(len(inline_kb), 1)
        self.assertEqual(inline_kb[0][0].text, "Configure Deal")
        self.assertEqual(inline_kb[0][0].url, f"https://t.me/my_test_bot?start=new_deal_{self.chat.id}")

    async def test_new_deal_command_private_chat(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.chat.type = "private"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.create_a_lobby(message, state)

        message.answer.assert_called_once()
        self.assertIn("Deals must be initiated inside a group chat", message.answer.call_args[0][0])

    async def test_start_command_with_new_deal_parameter(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.chat = self.chat
        message.chat.type = "private"
        message.text = f"/start new_deal_{self.chat.id}"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.start(message, state)

        state.update_data.assert_called_once_with(group_id=self.chat.id, buyer_id=self.from_user.id)
        state.set_state.assert_called_once_with(DealCreation.awaiting_seller_username)
        message.answer.assert_called_once_with("Please enter the Seller's Telegram Username (including @):")

    async def test_process_seller_username_valid(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.text = "@seller_user"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_seller_username(message, state)

        state.update_data.assert_called_once_with(seller_username="seller_user")
        state.set_state.assert_called_once_with(DealCreation.awaiting_amount)
        message.answer.assert_called_once_with("Please enter the transaction amount in USDT:")

        # Check DB created a dummy user for seller
        async with self.Session() as db_session:
            user = await get_or_create_user(db_session, -1, "seller_user")
            self.assertEqual(user.username, "seller_user")

    async def test_process_seller_username_own_username(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.text = f"@{self.from_user.username}"
        message.reply = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_seller_username(message, state)

        message.reply.assert_called_once_with("⚠️ You cannot enter your own username as the Seller. Please enter a different Seller's username:")
        state.update_data.assert_not_called()

    async def test_process_deal_amount_valid(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.text = "150.5"
        message.answer = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_deal_amount(message, state)

        state.update_data.assert_called_once_with(amount=150.5)
        state.set_state.assert_called_once_with(DealCreation.awaiting_payment_method)
        message.answer.assert_called_once()
        self.assertIn("Please select a payment method for the deal:", message.answer.call_args[0][0])

    async def test_process_deal_amount_invalid(self):
        message = AsyncMock()
        message.from_user = self.from_user
        message.text = "-10.0"
        message.reply = AsyncMock()
        state = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.process_deal_amount(message, state)

        message.reply.assert_called_once_with("⚠️ Invalid amount. Please enter a positive number:")
        state.update_data.assert_not_called()

    async def test_inline_process_create_deal_wallet(self):
        import handlers.inline_process as inline_handlers

        # Create buyer in DB
        async with self.Session() as db_session:
            await get_or_create_user(db_session, self.from_user.id, self.from_user.username)

        callback = AsyncMock()
        callback.from_user = self.from_user
        callback.data = "create_method_wallet"
        callback.message = AsyncMock()
        callback.bot = self.bot
        self.bot.send_message = AsyncMock()

        state = AsyncMock()
        state.get_data.return_value = {
            "buyer_id": self.from_user.id,
            "seller_username": "seller_user",
            "amount": 100.0,
            "group_id": self.chat.id
        }

        with patch("handlers.inline_process.async_session", self.Session):
            await inline_handlers.process_create_deal_method(callback, state)

        state.clear.assert_called_once()
        callback.message.answer.assert_called_once_with("✅ Deal created successfully! The confirmation link has been sent to the group chat.")
        
        # Verify group chat message
        self.bot.send_message.assert_called_once()
        group_call_args = self.bot.send_message.call_args
        self.assertEqual(group_call_args[1]["chat_id"], self.chat.id)
        self.assertIn("Deal Confirmation", group_call_args[1]["text"])
        self.assertIn("Seller: @seller_user", group_call_args[1]["text"])
        self.assertIn("Payment Method: Internal Wallet", group_call_args[1]["text"])

        # Check DB to ensure the deal was actually saved
        from database.models import Deal
        async with self.Session() as db_session:
            from sqlalchemy.future import select
            res = await db_session.execute(select(Deal))
            deals = res.scalars().all()
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0].amount, Decimal("100.0"))
            self.assertEqual(deals[0].payment_method, "WALLET")
            self.assertEqual(deals[0].status, "Active")

    async def test_inline_process_create_deal_gateway(self):
        import handlers.inline_process as inline_handlers

        async with self.Session() as db_session:
            await get_or_create_user(db_session, self.from_user.id, self.from_user.username)
            await set_setting(db_session, "gateway_fee_percent", "2.0")
            await db_session.commit()

        callback = AsyncMock()
        callback.from_user = self.from_user
        callback.data = "create_method_gateway"
        callback.message = AsyncMock()
        callback.bot = self.bot
        self.bot.send_message = AsyncMock()

        state = AsyncMock()
        state.get_data.return_value = {
            "buyer_id": self.from_user.id,
            "seller_username": "seller_user",
            "amount": 200.0,
            "group_id": self.chat.id
        }

        with patch("handlers.inline_process.async_session", self.Session):
            await inline_handlers.process_create_deal_method(callback, state)

        state.clear.assert_called_once()
        callback.message.answer.assert_called_once_with("✅ Deal created successfully! The confirmation link has been sent to the group chat.")
        
        # Verify group chat message
        self.bot.send_message.assert_called_once()
        group_call_args = self.bot.send_message.call_args
        self.assertEqual(group_call_args[1]["chat_id"], self.chat.id)
        self.assertIn("Deal Confirmation", group_call_args[1]["text"])
        self.assertIn("Payment Method: Payment Gateway", group_call_args[1]["text"])

        # Check DB
        from database.models import Deal
        async with self.Session() as db_session:
            from sqlalchemy.future import select
            res = await db_session.execute(select(Deal))
            deals = res.scalars().all()
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0].amount, Decimal("200.0"))
            self.assertEqual(deals[0].gateway_fee, Decimal("4.0"))  # 2% of 200
            self.assertEqual(deals[0].payment_method, "GATEWAY")

