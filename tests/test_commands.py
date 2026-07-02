import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from database.models import Base, User, APIKey, Setting
from database.database_utils import get_or_create_user, set_setting
from states.form_states import WalletStates
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
        message.answer = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.start(message)
        
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
        message.answer = AsyncMock()

        with patch("handlers.commands.async_session", self.Session):
            await cmd_handlers.start(message)
        
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
