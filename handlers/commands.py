from aiogram import Bot, Dispatcher, Router, types, F, html
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardRemove,
)
from utils import RoleFilter, get_data_from_db, button_builder, global_command_filter, GroupChatFilter, send_divider
from config import collection_lobby, MODERATOR_USER_ID, async_session
from states.user_registration import UserRegistration
from states.form_states import WalletStates, DealCreation
from database.database_utils import (
    get_or_create_user,
    get_setting,
    save_transaction,
    update_transaction_status,
)
from database.models import APIKey
from sqlalchemy.future import select
from aiogram.utils.keyboard import InlineKeyboardBuilder
from decimal import Decimal
import os
import random
import logging

logger = logging.getLogger(__name__)

deal_commands_router = Router()
deal_commands_router.message.filter(global_command_filter)


@deal_commands_router.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    # Check for deep link arguments
    if isinstance(message.text, str):
        command_args = message.text.split(maxsplit=1)
        if len(command_args) > 1:
            param = command_args[1].strip()
            if param.startswith("new_deal_"):
                group_id_str = param.replace("new_deal_", "")
                try:
                    group_id = int(group_id_str)
                except ValueError:
                    group_id = group_id_str
                
                await state.update_data(group_id=group_id, buyer_id=message.from_user.id)
                await state.set_state(DealCreation.awaiting_seller_username)
                await message.answer("Please enter the Seller's Telegram Username (including @):")
                return

    if message.chat.type == "private":
        group_message = (
            "I'm designed to facilitate transactions within a group chat. "
            "Please use me in the designated group chat for initiating and managing deals."
        )
        # If you have a static or known link to the group, you could include it here
        # group_message += "\n\nJoin our group chat here: [Group Link]"
        await message.answer(group_message)
    welcome_message = (
        "Hello! I'm the Escrow Bot, designed to facilitate secure, fast, and automated "
        "peer-to-peer transactions within this group. Here’s how you can get started:\n\n"
        "- Use /new_deal to initiate a new transaction.\n"
        "- Follow the prompts to configure your transaction details.\n\n"
        "Please ensure you understand how the escrow process works before initiating a deal. "
        "Type /help for more commands and information on how to use me."
    )
    await message.answer(welcome_message, parse_mode=ParseMode.HTML)


@deal_commands_router.message(Command("help"))
async def help_command(message: types.Message):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    help_text = """
<b>Help & Commands</b>
Here's a list of commands and how to use them:

/start - Get started with the bot and see introductory information.

/help - Display this help message with detailed instructions and commands.

/menu - Open the main menu to view your profile and manage your wallet.

/new_deal - Start a new transaction. Use this command in a group chat where both parties are present. Follow the prompts to set up the deal.

/cancel_trade - Cancel an ongoing transaction. This command can be used if you need to abort the current deal for any reason.

/sent_wallet [wallet_address] - Submit your wallet address as part of the transaction process. Replace [wallet_address] with your actual wallet address.

/release_funds - Release funds from escrow once the transaction conditions are met. This ensures the seller receives the payment securely.

<b>Using the Bot</b>
1. To initiate a deal, use the /new_deal command in the group chat.
2. Follow the instructions provided by the bot to set up the deal.
3. Both parties will need to confirm the deal details for the transaction to proceed.
4. Use /sent_wallet to provide your cryptocurrency wallet address when prompted.
5. Once the deal is set, and conditions are met, use /release_funds to complete the transaction.

For support or more information, please reach out to the bot administrator or visit our FAQ section.

Remember: Always exercise caution when conducting transactions online. Verify the identity of the other party and ensure you are comfortable with the deal before proceeding.
"""
    await message.answer(help_text, parse_mode=ParseMode.HTML)


@deal_commands_router.message(Command("new_deal"))
async def create_a_lobby(message: Message, state: FSMContext) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    if message.chat.type in ['group', 'supergroup']:
        bot_info = await message.bot.get_me()
        bot_username = bot_info.username
        group_id = message.chat.id
        await state.update_data(group_id=group_id)
        deep_link = f"https://t.me/{bot_username}?start=new_deal_{group_id}"
        
        text = (
            "📝 <b>Create a New Deal</b>\n\n"
            "To configure the deal details securely, click the button below to message me in private."
        )
        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(text="Configure Deal", url=deep_link))
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode=ParseMode.HTML)
    else:
        await message.answer(
            "⚠️ Deals must be initiated inside a group chat so both parties can participate. "
            "Please run /new_deal in your transaction group chat."
        )


@deal_commands_router.message(Command("sent_wallet"), RoleFilter("Seller", collection_lobby), GroupChatFilter())
async def process_seller_wallet(message: Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Please provide your wallet address. Usage: `/sent_wallet [wallet_address]`")
        return
    wallet_address = parts[1].strip()

    deal = await collection_lobby.find_one({"Seller's Telegram User": message.from_user.username, "Status": "Active"})
    if deal:
        deal_id = deal.get('unique_id')

        if 'Seller\'s Wallet Address' in deal and deal['Seller\'s Wallet Address']:
            await message.answer("The wallet address has already been stored.")
        else:
            await collection_lobby.update_one(
                {"unique_id": deal_id},  # Filter to match the specific document
                {"$set": {"Seller's Wallet Address": wallet_address}}  # Update
            )
            confirmation_message = (
                "✅ <b>Your wallet address has been saved successfully!</b>\n\n"
                "Please double-check to ensure accuracy:\n"
                f"🔐 Wallet: <code>{wallet_address}</code>\n\n"
                "It's crucial that this wallet is on the <i>same network</i> used for the fund transfer. "
                "If this is incorrect or you need to update the information, please let me know."
            )

            await message.answer(
                confirmation_message,
                parse_mode=ParseMode.HTML
            )

    else:
        await message.answer("No active deal found. Please start a new deal.")


@deal_commands_router.message(Command("cancel_trade"), GroupChatFilter())
async def cancel_trade(message: Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    username = message.from_user.username
    # Query the database to find an active deal with the user's username as either buyer or seller
    deal = await collection_lobby.find_one(
        {
            "$or": [
                {"Buyer's Telegram User": username},
                {"Seller's Telegram User": username}
            ],
            "Status": "Active"
        }
    )

    if deal:
        deal_id = deal.get('unique_id')
        if deal_id:
            # Mark the deal as canceled instead of deleting, for record-keeping
            await collection_lobby.update_one({"unique_id": deal_id}, {"$set": {"Status": "Canceled"}})
            await message.answer("Your deal has been canceled.", reply_markup=ReplyKeyboardRemove())
    else:
        await message.answer("No active deal found to cancel.")

    # Now, reset the user's state, if any
    if await state.get_state():
        await state.clear()
        await message.answer("Your current action has been reset.")


@deal_commands_router.message(Command("dispute"), GroupChatFilter())
async def dispute_solver(message: types.Message, state: FSMContext) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

        db_mod_id = await get_setting(session, "moderator_user_id")
        moderator_id = db_mod_id if db_mod_id else MODERATOR_USER_ID

    # Parse args manually to make it robust
    parts = message.text.split(maxsplit=1)
    
    unique_id = None
    if len(parts) >= 2:
        unique_id_str = parts[1].strip()
        try:
            unique_id = bytes.fromhex(unique_id_str)
        except ValueError:
            unique_id = unique_id_str
    else:
        if message.chat.type in ['group', 'supergroup']:
            async with async_session() as session:
                from database.models import Deal
                stmt = select(Deal).where(
                    (Deal.group_id == str(message.chat.id)) & 
                    (Deal.status == 'Active')
                )
                res = await session.execute(stmt)
                db_deal = res.scalars().first()
                if db_deal:
                    unique_id = db_deal.unique_id
                else:
                    await message.reply("No active deal found for this group chat.")
                    return
        else:
            await message.reply("Please provide the unique ID of the deal you wish to dispute. Usage: /dispute <unique_id>")
            return

    mongo_uid = unique_id.hex() if isinstance(unique_id, bytes) else unique_id

    deal = await collection_lobby.find_one({"unique_id": mongo_uid, "Status": "Active"})

    if not deal:
        await message.reply("No active deal found with the provided unique ID.")
        return

    username = message.from_user.username
    if username.startswith('@'):
        username = username[1:]

    if deal["Seller's Telegram User"].lstrip('@') != username and deal["Buyer's Telegram User"].lstrip('@') != username:
        await message.reply("You are not a part of this deal.")
        return

    if deal["Status"] == "Disputed":
        await message.reply("This deal is already under dispute.")
        return

    await collection_lobby.update_one({"unique_id": mongo_uid}, {"$set": {"Status": "Disputed"}})

    # Also update the MySQL Deal status to 'Disputed' if it exists there
    async with async_session() as session:
        from database.models import Deal
        db_unique_id = unique_id
        if isinstance(db_unique_id, str):
            try:
                if len(db_unique_id) == 32:
                    db_unique_id = bytes.fromhex(db_unique_id)
                else:
                    db_unique_id = db_unique_id.encode('utf-8')[:16].ljust(16, b'\x00')
            except ValueError:
                db_unique_id = db_unique_id.encode('utf-8')[:16].ljust(16, b'\x00')
        elif isinstance(db_unique_id, int):
            db_unique_id = db_unique_id.to_bytes(16, byteorder='big')
        stmt = select(Deal).where(Deal.unique_id == db_unique_id)
        res = await session.execute(stmt)
        sql_deal = res.scalars().first()
        if sql_deal:
            sql_deal.status = 'Disputed'
            await session.commit()

    # Generate the invite link for the group chat
    invite_link = await message.chat.export_invite_link()

    # Notify the moderator by sending them the invite link directly
    notify_message = (
        f"A dispute has been raised for Deal ID {mongo_uid} in the group '{message.chat.title}'. "
        f"Please join using this invite link to assist: {invite_link}"
    )
    await message.bot.send_message(chat_id=moderator_id, text=notify_message)

    # Notify all parties involved in the group chat.
    await message.reply(
        "The dispute has been registered. A moderator has been notified and will join shortly to assist.")


# --- PROFILE & WALLET MENU SYSTEM IMPLEMENTATION ---

@deal_commands_router.message(Command("menu"))
async def menu_command(message: types.Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    await state.clear()

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="👤 Profile", callback_data="menu_profile"))
    builder.add(types.InlineKeyboardButton(text="💼 Wallet", callback_data="menu_wallet"))
    builder.adjust(1)

    await message.answer(
        "📱 <b>Main Menu</b>\n\nChoose an option below:",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


@deal_commands_router.callback_query(F.data == "menu_main")
async def show_main_menu(callback: types.CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.answer("⛔ Your account has been suspended by the administrator.", show_alert=True)
            return

    await state.clear()

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="👤 Profile", callback_data="menu_profile"))
    builder.add(types.InlineKeyboardButton(text="💼 Wallet", callback_data="menu_wallet"))
    builder.adjust(1)

    await callback.message.edit_text(
        "📱 <b>Main Menu</b>\n\nChoose an option below:",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


@deal_commands_router.callback_query(F.data == "menu_profile")
async def show_profile_menu(callback: types.CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.answer("⛔ Your account has been suspended by the administrator.", show_alert=True)
            return

        result = await session.execute(
            select(APIKey).where((APIKey.user_id == user.id) & (APIKey.is_active == True))
        )
        api_key_obj = result.scalars().first()
        api_key_str = api_key_obj.api_key if api_key_obj else "None"

    profile_text = (
        "👤 <b>Your Profile</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>\n"
        f"💰 <b>Wallet Balance:</b> <code>{user.wallet_balance:.8f} USDT</code>\n"
        f"🔑 <b>API Key:</b> <code>{api_key_str}</code>"
    )

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="⬅️ Back", callback_data="menu_main"))

    await callback.message.edit_text(
        profile_text,
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


@deal_commands_router.callback_query(F.data == "menu_wallet")
async def show_wallet_menu(callback: types.CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.answer("⛔ Your account has been suspended by the administrator.", show_alert=True)
            return

    wallet_text = (
        "💼 <b>Wallet Sub-menu</b>\n\n"
        f"💰 <b>Your Balance:</b> <code>{user.wallet_balance:.8f} USDT</code>\n\n"
        "Please select an option:"
    )

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="💵 Top-up", callback_data="wallet_topup"))
    builder.add(types.InlineKeyboardButton(text="💸 Withdrawal", callback_data="wallet_withdraw"))
    builder.add(types.InlineKeyboardButton(text="⬅️ Back", callback_data="menu_main"))
    builder.adjust(2, 1)

    await callback.message.edit_text(
        wallet_text,
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


@deal_commands_router.callback_query(F.data == "wallet_topup")
async def process_wallet_topup(callback: types.CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.answer("⛔ Your account has been suspended by the administrator.", show_alert=True)
            return

    topup_text = (
        "💳 <b>Choose Top-up Method</b>\n\n"
        "Please choose how you would like to top up your wallet:\n\n"
        "1. <b>Payment Gateway (NOWPayments):</b> Automated, quick, credit to balance on confirmation.\n"
        "2. <b>Manual:</b> Admin transfer verification."
    )

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🌐 Payment Gateway", callback_data="topup_gateway"))
    builder.add(types.InlineKeyboardButton(text="✍️ Manual", callback_data="topup_manual"))
    builder.add(types.InlineKeyboardButton(text="⬅️ Back", callback_data="menu_wallet"))
    builder.adjust(2, 1)

    await callback.message.edit_text(
        topup_text,
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


@deal_commands_router.callback_query(F.data == "topup_gateway")
async def topup_gateway_handler(callback: types.CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.answer("⛔ Your account has been suspended by the administrator.", show_alert=True)
            return

    await state.set_state(WalletStates.awaiting_topup_amount_gateway)
    await callback.message.edit_text(
        "🌐 <b>Gateway Top-up</b>\n\nPlease enter the amount you wish to top up in USDT:",
        parse_mode=ParseMode.HTML
    )


@deal_commands_router.message(WalletStates.awaiting_topup_amount_gateway)
async def process_topup_amount_gateway(message: types.Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

        try:
            amount = float(message.text.strip())
            if amount <= 0:
                raise ValueError()
        except ValueError:
            await message.reply("⚠️ Invalid amount. Please enter a positive number:")
            return

        await state.clear()

        # Try to call NOWPayments or fallback
        from wallet_processing import create_payment

        try:
            api_key = await get_setting(session, "nowpayments_api_key") or os.getenv("NOWPAYMENTS_API_KEY")
            api_url = await get_setting(session, "nowpayments_api_url") or os.getenv("NOWPAYMENTS_API_URL", "https://api.nowpayments.io")
            ipn_url = await get_setting(session, "nowpayments_ipn_url") or os.getenv("NOWPAYMENTS_IPN_URL")

            if not api_key:
                raise ValueError("NOWPayments API Key not configured.")

            order_id = f"TOPUP-{user.id}-{random.randint(100000, 999999)}"
            payment_data = await create_payment(
                price_amount=amount,
                price_currency="usd",
                pay_currency="usdttrc20",
                order_id=order_id,
                ipn_url=ipn_url or "http://localhost",
                api_key=api_key,
                api_url=api_url
            )
            pay_address = payment_data.get("pay_address")
            pay_amount = payment_data.get("pay_amount")
            payment_id = payment_data.get("payment_id")

            await save_transaction(
                session=session,
                user_id=user.id,
                deal_id=None,
                tx_type="TOPUP",
                payment_id=str(payment_id),
                amount=amount,
                currency="USDT",
                status="waiting"
            )

            response_text = (
                "🌐 <b>Gateway Top-up Initiated</b>\n\n"
                f"💰 <b>Amount:</b> <code>{amount} USDT</code>\n"
                f"🔐 <b>Deposit Address (TRC20):</b> <code>{pay_address}</code>\n"
                f"💵 <b>Pay Amount:</b> <code>{pay_amount} USDT</code>\n"
                f"🆔 <b>Payment ID:</b> <code>{payment_id}</code>\n\n"
                "Please send the exact amount to the address above. Your balance will be updated automatically upon payment confirmation."
            )
        except Exception as e:
            logger.warning(f"NOWPayments create_payment failed: {e}. Falling back to simulation.")
            payment_id = f"GW-{random.randint(100000, 999999)}"
            mock_address = "TXYZ1234567890MOCKGATEWAYADDRESS"

            await save_transaction(
                session=session,
                user_id=user.id,
                deal_id=None,
                tx_type="TOPUP",
                payment_id=payment_id,
                amount=amount,
                currency="USDT",
                status="waiting"
            )

            response_text = (
                "🌐 <b>Gateway Top-up (Simulation Mode)</b>\n\n"
                f"💰 <b>Amount:</b> <code>{amount} USDT</code>\n"
                f"🔐 <b>Deposit Address (TRC20):</b> <code>{mock_address}</code>\n"
                f"🆔 <b>Payment ID:</b> <code>{payment_id}</code>\n\n"
                "Please send the exact amount to the address above (simulated). Since this is a simulation, contact the administrator to manually credit your balance."
            )

    await message.answer(response_text, parse_mode=ParseMode.HTML)


@deal_commands_router.callback_query(F.data == "topup_manual")
async def topup_manual_handler(callback: types.CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.answer("⛔ Your account has been suspended by the administrator.", show_alert=True)
            return

    await state.set_state(WalletStates.awaiting_topup_amount_manual)
    await callback.message.edit_text(
        "✍️ <b>Manual Top-up</b>\n\nPlease enter the amount you wish to top up in USDT:",
        parse_mode=ParseMode.HTML
    )


@deal_commands_router.message(WalletStates.awaiting_topup_amount_manual)
async def process_topup_amount_manual(message: types.Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

        try:
            amount = float(message.text.strip())
            if amount <= 0:
                raise ValueError()
        except ValueError:
            await message.reply("⚠️ Invalid amount. Please enter a positive number:")
            return

        await state.clear()

        payment_id = f"MN-{random.randint(100000, 999999)}"
        mock_address = "TXYZ1234567890MANUALADDRESS"

        await save_transaction(
            session=session,
            user_id=user.id,
            deal_id=None,
            tx_type="TOPUP",
            payment_id=payment_id,
            amount=amount,
            currency="USDT",
            status="waiting"
        )

    response_text = (
        "✍️ <b>Manual Top-up Request</b>\n\n"
        f"💰 <b>Amount:</b> <code>{amount} USDT</code>\n"
        f"🔐 <b>Official TRC20 Wallet:</b> <code>{mock_address}</code>\n"
        f"🆔 <b>Request ID:</b> <code>{payment_id}</code>\n\n"
        "Please transfer the amount to our official TRC20 wallet address. "
        "After transferring, contact the administrator with your Request ID and receipt for verification."
    )

    await message.answer(response_text, parse_mode=ParseMode.HTML)


@deal_commands_router.callback_query(F.data == "wallet_withdraw")
async def wallet_withdraw_handler(callback: types.CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.answer("⛔ Your account has been suspended by the administrator.", show_alert=True)
            return

    await state.set_state(WalletStates.awaiting_withdrawal_address)
    await callback.message.edit_text(
        "💸 <b>Request Withdrawal</b>\n\nPlease enter the recipient TRC20 wallet address:",
        parse_mode=ParseMode.HTML
    )


@deal_commands_router.message(WalletStates.awaiting_withdrawal_address)
async def process_withdrawal_address(message: types.Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    address = message.text.strip()
    # Basic TRC20 validation: starts with 'T', length 34
    if not (address.startswith("T") and len(address) == 34):
        await message.reply("⚠️ Invalid TRC20 address. Please make sure it starts with 'T' and is 34 characters long. Re-enter address:")
        return

    await state.update_data(withdraw_address=address)
    await state.set_state(WalletStates.awaiting_withdrawal_amount)
    await message.answer("💰 <b>Enter Amount</b>\n\nPlease enter the amount you wish to withdraw in USDT:")


@deal_commands_router.message(WalletStates.awaiting_withdrawal_amount)
async def process_withdrawal_amount(message: types.Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

        try:
            amount_val = Decimal(message.text.strip())
            if amount_val <= 0:
                raise ValueError()
        except ValueError:
            await message.reply("⚠️ Invalid amount. Please enter a positive number:")
            return

        # Check min_withdrawal setting
        db_min_withdraw = await get_setting(session, "min_withdrawal", "10.0")
        try:
            min_withdraw = Decimal(str(db_min_withdraw))
        except Exception:
            min_withdraw = Decimal("10.0")

        if amount_val < min_withdraw:
            await message.reply(f"❌ The minimum withdrawal amount is {min_withdraw:.2f} USDT. Please enter a higher amount:")
            return

        if user.wallet_balance < amount_val:
            await message.reply(f"❌ Insufficient balance. Your current balance is {user.wallet_balance:.8f} USDT. Please enter a valid amount:")
            return

        # Fetch address from state
        data = await state.get_data()
        address = data.get("withdraw_address")

        await state.clear()

        # Deduct balance
        user.wallet_balance -= amount_val
        session.add(user)
        await session.commit()

        # Save transaction
        payment_id = f"WD-{random.randint(100000, 999999)}"
        tx = await save_transaction(
            session=session,
            user_id=user.id,
            deal_id=None,
            tx_type="WITHDRAWAL",
            payment_id=payment_id,
            amount=float(amount_val),
            currency="USDT",
            status="waiting"
        )
        tx.raw_response = {"withdraw_address": address}
        await session.commit()

    response_text = (
        "✅ <b>Withdrawal Successful & Pending Admin Approval</b>\n\n"
        f"💰 <b>Amount:</b> <code>{amount_val:.8f} USDT</code>\n"
        f"🔐 <b>Address:</b> <code>{address}</code>\n"
        f"🆔 <b>Transaction ID:</b> <code>{payment_id}</code>\n\n"
        "Your withdrawal has been queued for manual admin approval."
    )
    await message.answer(response_text, parse_mode=ParseMode.HTML)


@deal_commands_router.message(DealCreation.awaiting_seller_username)
async def process_seller_username(message: Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    username = message.text.strip()
    if not username.startswith('@'):
        username = '@' + username
    
    buyer_username = message.from_user.username
    if buyer_username:
        buyer_username = buyer_username.lstrip('@')
    
    stripped = username.lstrip('@')
    
    if buyer_username and stripped.lower() == buyer_username.lower():
        await message.reply("⚠️ You cannot enter your own username as the Seller. Please enter a different Seller's username:")
        return

    # Query the database to get/create the user (using telegram_id=-1)
    async with async_session() as session:
        await get_or_create_user(session, telegram_id=-1, username=stripped)

    await state.update_data(seller_username=stripped)
    await state.set_state(DealCreation.awaiting_amount)
    await message.answer("Please enter the transaction amount in USDT:")


@deal_commands_router.message(DealCreation.awaiting_amount)
async def process_deal_amount(message: Message, state: FSMContext):
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        if user.is_suspended:
            await message.answer("⛔ Your account has been suspended by the administrator.")
            return

    try:
        amount = Decimal(message.text.strip())
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.reply("⚠️ Invalid amount. Please enter a positive number:")
        return

    await state.update_data(amount=float(amount))
    await state.set_state(DealCreation.awaiting_payment_method)
    
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Internal Wallet", callback_data="create_method_wallet"))
    builder.add(types.InlineKeyboardButton(text="Payment Gateway", callback_data="create_method_gateway"))
    builder.adjust(2)
    
    await message.answer(
        "Please select a payment method for the deal:",
        reply_markup=builder.as_markup()
    )
