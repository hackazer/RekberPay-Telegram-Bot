import httpx
import asyncio
import logging
import random
from decimal import Decimal
import os
from aiogram import Router, types, F
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardRemove

from utils import global_command_filter, RoleFilter, button_builder, send_divider
from wallet_processing import create_payment, create_payout, get_payment_status
from config import async_session
from states.user_registration import UserRegistration
from database.database_utils import (
    get_or_create_user,
    get_user_by_telegram_id,
    get_user_by_username,
    get_setting,
    save_transaction,
    update_transaction_status
)
from database.models import User, Deal, Transaction
from sqlalchemy.future import select

logger = logging.getLogger(__name__)
form_router = Router()
form_router.message.filter(global_command_filter)


@form_router.callback_query(F.data == "submitted_form")
async def submitted_form_handler(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    
    # Check user suspension state at the beginning
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.message.answer("⛔ Your account has been suspended by the administrator.")
            return

    username = callback.from_user.username
    start_time = asyncio.get_event_loop().time()
    found = False
    await callback.message.edit_reply_markup()
    
    try:
        while (asyncio.get_event_loop().time() - start_time) < (15 * 60):  # 15 minutes
            async with async_session() as session:
                result = await session.execute(
                    select(Deal).where(Deal.status == 'Active')
                )
                deals = result.scalars().all()
                
                document = None
                buyer_username = None
                seller_username = None
                
                for d in deals:
                    buyer_res = await session.execute(select(User).where(User.id == d.buyer_id))
                    buyer = buyer_res.scalars().first()
                    seller_res = await session.execute(select(User).where(User.id == d.seller_id))
                    seller = seller_res.scalars().first()
                    
                    if (buyer and buyer.username == username.lstrip('@')) or (seller and seller.username == username.lstrip('@')):
                        document = d
                        buyer_username = buyer.username if buyer else "Not Provided"
                        seller_username = seller.username if seller else "Not Provided"
                        break
                        
                if document:
                    try:
                        await state.update_data(unique_id=document.unique_id, seller=seller_username)
                    except Exception as e:
                        logger.error(f"Error updating state data: {e}")
                        await callback.message.answer("There was an error processing your submission. Please try again.")
                        break

                    found = True
                    await callback.message.answer("Your submission has been confirmed!", reply_markup=ReplyKeyboardRemove())

                    deal_data = (
                        "🔍 <b>Deal Confirmation</b>\n"
                        "Please review the details of your transaction carefully:\n\n"
                        f"👤 <b>Seller:</b> @{seller_username}\n"
                        f"👥 <b>Buyer:</b> @{buyer_username}\n"
                        f"💳 <b>Payment Method:</b> {document.payment_method}\n"
                        f"💰 <b>Amount:</b> {document.amount} USDT\n"
                        f"🆔 <b>Deal ID:</b> {document.unique_id}\n\n"
                        "This ID is essential for dispute resolution or any further assistance.\n\n"
                        "If all details are correct, please <b>confirm</b> by clicking 'Continue with the deal' below.\n"
                        "To modify any information, use the 'Data is incorrect' button to submit again."
                    )

                    reply_keyboard = await button_builder(
                        ["The data is incorrect. Submit again", 'Continue with the deal'],
                        ["resubmit_form", "continue_deal"])
                    reply_keyboard.adjust(1, 1)
                    await send_divider(callback)
                    await callback.message.answer(deal_data,
                                                  reply_markup=reply_keyboard.as_markup(),
                                                  parse_mode=ParseMode.HTML)
                    break
            await asyncio.sleep(15)

        if not found:
            reply_keyboard = await button_builder(["Retry submission"], ["submitted_form"])
            await callback.message.answer("We could not find your submission. Please submit the form again.",
                                          reply_markup=reply_keyboard.as_markup())

        await state.set_state(UserRegistration.awaiting_deal_continuation)

    except Exception as e:
        logger.exception(f"An error occurred during form submission handling: {e}")
        await callback.message.answer("An unexpected error occurred. Please contact support.")


@form_router.callback_query(F.data == "resubmit_form")
async def handle_incorrect_data_submission(callback: types.CallbackQuery) -> None:
    await callback.answer()
    
    # Check user suspension state at the beginning
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.message.answer("⛔ Your account has been suspended by the administrator.")
            return

        username = callback.from_user.username
        
        result = await session.execute(
            select(Deal).where(Deal.status == 'Active')
        )
        deals = result.scalars().all()
        deal_to_delete = None
        for d in deals:
            buyer_res = await session.execute(select(User).where(User.id == d.buyer_id))
            buyer = buyer_res.scalars().first()
            seller_res = await session.execute(select(User).where(User.id == d.seller_id))
            seller = seller_res.scalars().first()
            
            if (buyer and buyer.username == username.lstrip('@')) or (seller and seller.username == username.lstrip('@')):
                deal_to_delete = d
                break
                
        if deal_to_delete:
            await session.delete(deal_to_delete)
            await session.commit()

    message_text = (
        "🙇‍♂️ <b>Oops! Looks like we need a bit more info.</b>\n\n"
        "No worries at all! It happens to the best of us. Here’s a quick way to get everything sorted:\n\n"
        "1️⃣ <a href='https://docs.google.com/forms/d/e/1FAIpQLSejcvHsgFE2fhfewtlss6ZpMQphPHYw6-l7k6gmdrjh-9gslw/"
        "viewform?usp=sf_link'>"
        "Click here to revisit the form</a> and fill in the missing or incorrect details.\n\n"
        "2️⃣ Once you’ve made the updates, hit the 'I've submitted the form' button below to let me know. 📬\n\n"
        "Take your time, and if you have any questions or need help along the way, just give me a shout!"
    )

    reply_keyboard = await button_builder(["Confirm form resubmission"], ["submitted_form"])
    await send_divider(callback)
    await callback.message.answer(
        message_text,
        reply_markup=reply_keyboard.as_markup(),
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML
    )


@form_router.callback_query(F.data == "continue_deal")
async def continue_deal(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    
    # Check user suspension state at the beginning
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.message.answer("⛔ Your account has been suspended by the administrator.")
            return

        username = callback.from_user.username
        user_id = callback.from_user.id

        result = await session.execute(
            select(Deal).where(Deal.status == 'Active')
        )
        deals = result.scalars().all()
        deal = None
        buyer_user = None
        seller_user = None
        
        for d in deals:
            buyer_res = await session.execute(select(User).where(User.id == d.buyer_id))
            buyer_user = buyer_res.scalars().first()
            seller_res = await session.execute(select(User).where(User.id == d.seller_id))
            seller_user = seller_res.scalars().first()
            
            if (buyer_user and buyer_user.username == username.lstrip('@')) or (seller_user and seller_user.username == username.lstrip('@')):
                deal = d
                break
                
        if not deal:
            await callback.message.answer("No active deal found associated with your account. Please start a new deal.")
            return

        users_interacted = deal.users_interacted or []
        if user_id in users_interacted:
            await callback.message.answer("You have already continued this deal.")
            return

        new_count = (deal.continue_deal_count or 0) + 1
        deal.continue_deal_count = new_count
        
        new_interacted = list(users_interacted)
        new_interacted.append(user_id)
        deal.users_interacted = new_interacted
        
        session.add(deal)
        await session.commit()

        if new_count < 2:
            await callback.message.answer("Waiting for the other party to continue the deal.")
        elif new_count == 2:
            escrow_pct_str = await get_setting(session, "escrow_fee_percent", "2.0")
            gateway_pct_str = await get_setting(session, "gateway_fee_percent", "1.0")
            try:
                escrow_pct = Decimal(escrow_pct_str)
            except Exception:
                escrow_pct = Decimal("2.0")
            try:
                gateway_pct = Decimal(gateway_pct_str)
            except Exception:
                gateway_pct = Decimal("1.0")
                
            escrow_fee = deal.amount * (escrow_pct / Decimal("100.0"))
            if deal.payment_method == "GATEWAY":
                gateway_fee = deal.amount * (gateway_pct / Decimal("100.0"))
            else:
                gateway_fee = Decimal("0.0")
                
            total_amount = deal.amount + escrow_fee + gateway_fee
            
            deal.escrow_fee = escrow_fee
            deal.gateway_fee = gateway_fee
            deal.total_amount = total_amount
            session.add(deal)
            await session.commit()
            
            if deal.payment_method == "WALLET":
                if buyer_user.wallet_balance < total_amount:
                    await callback.message.answer(
                        f"❌ <b>Insufficient Wallet Balance</b>\n\n"
                        f"Your current balance: <code>{buyer_user.wallet_balance:.8f} USDT</code>\n"
                        f"Required amount: <code>{total_amount:.8f} USDT</code> (including fees)\n\n"
                        "Please top up your wallet or submit data again to choose another method.",
                        parse_mode=ParseMode.HTML
                    )
                    return
                
                buyer_user.wallet_balance -= total_amount
                session.add(buyer_user)
                
                tx_id = f"WT-{deal.unique_id}-{random.randint(100000, 999999)}"
                tx = await save_transaction(
                    session=session,
                    user_id=buyer_user.id,
                    deal_id=deal.id,
                    tx_type="ESCROW_PAY",
                    payment_id=tx_id,
                    amount=float(total_amount),
                    currency="USDT",
                    status="completed"
                )
                tx.completed = True
                await session.commit()
                
                builder = await button_builder(["Release funds"], ["release_funds"])
                message_text = (
                    "✅ <b>Funds Secured in Escrow!</b>\n\n"
                    f"We've successfully debited <b>{total_amount:.8f} USDT</b> from the buyer's balance.\n\n"
                    "What's next?\n"
                    "👉 <b>Seller:</b> Please proceed with delivering the items to the buyer.\n"
                    "👉 <b>Buyer:</b> Once you've received the items and are happy with them, please release the funds to the seller by clicking the button below.\n"
                )
                await send_divider(callback)
                await callback.message.answer(
                    message_text,
                    reply_markup=builder.as_markup(),
                    parse_mode=ParseMode.HTML
                )
                await state.set_state(UserRegistration.awaiting_funds_release)
            
            else:  # GATEWAY payment
                try:
                    api_key = await get_setting(session, "nowpayments_api_key") or os.getenv("NOWPAYMENTS_API_KEY")
                    api_url = await get_setting(session, "nowpayments_api_url") or os.getenv("NOWPAYMENTS_API_URL", "https://api.nowpayments.io")
                    ipn_url = await get_setting(session, "nowpayments_ipn_url") or os.getenv("NOWPAYMENTS_IPN_URL")

                    if not api_key:
                        raise ValueError("NOWPayments API Key not configured.")

                    order_id = f"DEAL-{deal.unique_id}-{random.randint(100000, 999999)}"
                    payment_data = await create_payment(
                        price_amount=float(total_amount),
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

                    tx = await save_transaction(
                        session=session,
                        user_id=buyer_user.id,
                        deal_id=deal.id,
                        tx_type="ESCROW_PAY",
                        payment_id=str(payment_id),
                        amount=float(pay_amount),
                        currency="USDT",
                        status="waiting"
                    )
                    tx.raw_response = payment_data
                    deal.nowpayments_payment_id = str(payment_id)
                    await session.commit()
                    
                    payment_instructions = (
                        "✅ <b>Ready to Transfer Funds</b>\n\n"
                        "Here’s how to complete your transaction:\n\n"
                        "1️⃣ <b>Transfer the Amount:</b>\n"
                        f"   Send exactly <b>{pay_amount} USDT</b> to the wallet address below.\n"
                        f"   <code>{pay_address}</code>\n\n"
                        "2️⃣ <b>Use the Right Network:</b>\n"
                        f"   Ensure you are using the <i>TRC20</i> network for the transfer.\n\n"
                        "3️⃣ <b>Confirmation:</b>\n"
                        "   After you've sent the funds, tap the 'Funds Sent' button so we can proceed with the deal.\n\n"
                        f"🆔 <b>Payment ID:</b> <code>{payment_id}</code>\n\n"
                        "🔔 Need help or have questions? Don't hesitate to reach out!"
                    )
                except Exception as e:
                    logger.warning(f"NOWPayments create_payment failed: {e}. Falling back to simulation.")
                    payment_id = f"GW-{deal.unique_id}-{random.randint(100000, 999999)}"
                    mock_address = "TXYZ1234567890MOCKGATEWAYADDRESS"
                    pay_amount = total_amount

                    tx = await save_transaction(
                        session=session,
                        user_id=buyer_user.id,
                        deal_id=deal.id,
                        tx_type="ESCROW_PAY",
                        payment_id=payment_id,
                        amount=float(pay_amount),
                        currency="USDT",
                        status="waiting"
                    )
                    deal.nowpayments_payment_id = payment_id
                    await session.commit()

                    payment_instructions = (
                        "✅ <b>Ready to Transfer Funds (Simulation Mode)</b>\n\n"
                        "Here’s how to complete your transaction:\n\n"
                        "1️⃣ <b>Transfer the Amount:</b>\n"
                        f"   Send exactly <b>{pay_amount} USDT</b> to the wallet address below.\n"
                        f"   <code>{mock_address}</code>\n\n"
                        "2️⃣ <b>Use the Right Network:</b>\n"
                        f"   Ensure you are using the <i>TRC20</i> network for the transfer.\n\n"
                        "3️⃣ <b>Confirmation:</b>\n"
                        "   After you've sent the funds, tap the 'Funds Sent' button so we can proceed with the deal.\n\n"
                        f"🆔 <b>Payment ID:</b> <code>{payment_id}</code>\n\n"
                        "🔔 Need help or have questions? Don't hesitate to reach out!"
                    )

                reply_keyboard = await button_builder(["Sent funds"], ["sent_funds"])
                await send_divider(callback)
                await callback.message.answer(payment_instructions,
                                              reply_markup=reply_keyboard.as_markup(),
                                              parse_mode=ParseMode.HTML)


@form_router.callback_query(F.data == "sent_funds",
                            RoleFilter("Buyer"))
async def sent_funds_handler(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    
    # Check user suspension state at the beginning
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.message.answer("⛔ Your account has been suspended by the administrator.")
            return

        result = await session.execute(
            select(Deal).where((Deal.buyer_id == user.id) & (Deal.status == 'Active'))
        )
        deal = result.scalars().first()
        if not deal:
            await callback.message.answer("No active deal found.")
            return

        tx_result = await session.execute(
            select(Transaction).where((Transaction.deal_id == deal.id) & (Transaction.type == "ESCROW_PAY"))
        )
        tx = tx_result.scalars().first()
        if not tx:
            await callback.message.answer("Payment transaction not found.")
            return

        is_mock = tx.payment_id.startswith("GW-") if tx.payment_id else True
        if not tx.completed and not is_mock:
            try:
                api_key = await get_setting(session, "nowpayments_api_key") or os.getenv("NOWPAYMENTS_API_KEY")
                api_url = await get_setting(session, "nowpayments_api_url") or os.getenv("NOWPAYMENTS_API_URL", "https://api.nowpayments.io")
                status_data = await get_payment_status(tx.payment_id, api_key, api_url)
                pay_status = status_data.get("payment_status")
                if pay_status in ("confirmed", "finished"):
                    tx.completed = True
                    tx.status = pay_status
                    await session.commit()
            except Exception as e:
                logger.error(f"Error checking payment status: {e}")

        # Reload status
        if tx.completed:
            builder = await button_builder(["Release funds"], ["release_funds"])
            message_text = (
                "✅ <b>Transaction Confirmed!</b>\n\n"
                "We've successfully located and confirmed your payment.\n\n"
                "What's next?\n"
                "👉 <b>Seller:</b> You're good to go! Please proceed with delivering the items to the buyer.\n"
                "👉 <b>Buyer:</b> Once you've received the items and are happy with them, please release the "
                "funds to the seller by clicking 'Release funds' below.\n"
            )
            await send_divider(callback)
            await callback.message.answer(
                message_text,
                reply_markup=builder.as_markup(),
                parse_mode=ParseMode.HTML
            )
            await state.set_state(UserRegistration.awaiting_funds_release)
        else:
            await callback.message.answer("⏳ Payment not yet confirmed. Please wait or check back in a moment.")


@form_router.callback_query(F.data == "release_funds",
                            RoleFilter("Buyer"))
async def send_random_value(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    
    # Check user suspension state at the beginning
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        if user.is_suspended:
            await callback.message.answer("⛔ Your account has been suspended by the administrator.")
            return

        result = await session.execute(
            select(Deal).where((Deal.buyer_id == user.id) & (Deal.status == 'Active'))
        )
        deal = result.scalars().first()
        if not deal:
            await callback.message.answer("No active deal found.")
            return

        seller_res = await session.execute(select(User).where(User.id == deal.seller_id))
        seller = seller_res.scalars().first()

        if deal.payment_method == "WALLET":
            seller.wallet_balance += deal.amount
            session.add(seller)
            deal.status = "Completed"
            
            tx = await save_transaction(
                session=session,
                user_id=seller.id,
                deal_id=deal.id,
                tx_type="ESCROW_RELEASE",
                payment_id=f"REL-{deal.unique_id}-{random.randint(100000, 999999)}",
                amount=float(deal.amount),
                currency="USDT",
                status="completed"
            )
            tx.completed = True
            await session.commit()
            
            await callback.message.answer(
                "✅ <b>Escrow Funds Released!</b>\n\n"
                f"The amount of <b>{deal.amount} USDT</b> has been successfully credited to the seller's wallet balance.",
                parse_mode=ParseMode.HTML
            )
            await state.clear()
            
        else:  # GATEWAY payment
            if not deal.seller_wallet:
                await send_divider(callback)
                await callback.message.answer(
                    "🚫 <b>Wallet Not Found</b> 🚫\n\n"
                    "It seems we don't have a wallet address on file for the seller. No problem, though! Here’s how to fix it:\n\n"
                    "👤 <b>Seller:</b> Please submit your wallet address using the command below. Just replace <i>&lt;your wallet&gt;</i> with your actual wallet address.\n\n"
                    "💼 <code>/sent_wallet &lt;your wallet&gt;</code>\n\n"
                    "For example:\n"
                    "<code>/sent_wallet TCdRk63AC8gXH1qPrsesdFXeKLERh9m2ni</code>\n\n"
                    "This will ensure we have the correct wallet address for transactions.",
                    parse_mode=ParseMode.HTML
                )
                return

            email = await get_setting(session, "nowpayments_payout_email") or os.getenv("NOWPAYMENTS_EMAIL")
            password = await get_setting(session, "nowpayments_payout_password") or os.getenv("NOWPAYMENTS_PASSWORD")
            api_key = await get_setting(session, "nowpayments_api_key") or os.getenv("NOWPAYMENTS_API_KEY")
            api_url = await get_setting(session, "nowpayments_api_url") or os.getenv("NOWPAYMENTS_API_URL", "https://api.nowpayments.io")

            payout_res = await create_payout(
                email=email,
                password=password,
                address=deal.seller_wallet,
                amount=float(deal.amount),
                currency="usdttrc20",
                api_key=api_key,
                api_url=api_url
            )

            # Fix logic bypass: verify that create_payout returns success
            if payout_res.get("success") is True:
                deal.status = "Completed"
                
                tx = await save_transaction(
                    session=session,
                    user_id=seller.id,
                    deal_id=deal.id,
                    tx_type="ESCROW_RELEASE",
                    payment_id=f"REL-{deal.unique_id}-{random.randint(100000, 999999)}",
                    amount=float(deal.amount),
                    currency="USDT",
                    status="completed"
                )
                tx.completed = True
                await session.commit()
                
                await callback.message.answer(
                    "✅ <b>Escrow Funds Released!</b>\n\n"
                    f"Payout of <b>{deal.amount} USDT</b> has been successfully initiated to the seller's wallet:\n"
                    f"<code>{deal.seller_wallet}</code>\n\n"
                    "Thank you for using RekberPay!",
                    parse_mode=ParseMode.HTML
                )
                await state.clear()
            else:
                error_msg = payout_res.get("error", "Payout API error")
                await callback.message.answer(
                    f"❌ <b>Payout Failed</b>\n\n"
                    f"Error details: <code>{error_msg}</code>\n\n"
                    "The funds remain safely in escrow. Please contact the administrator.",
                    parse_mode=ParseMode.HTML
                )
