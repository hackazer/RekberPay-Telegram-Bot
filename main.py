import sys
import logging
import asyncio
import os
import secrets
import random
from decimal import Decimal
import hmac
import hashlib

from fastapi import FastAPI, Request, Depends, HTTPException, status, Security
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials, APIKeyHeader
from pydantic import BaseModel, Field

import uvicorn
from sqlalchemy import func
from sqlalchemy.future import select

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from utils import global_command_filter
from handlers.inline_process import form_router
from handlers.commands import deal_commands_router

from config import TOKEN, engine, async_session, ADMIN_USERNAME, ADMIN_PASSWORD
from database.models import Base, User, Deal, Transaction, APIKey
from database.database_utils import (
    get_or_create_user,
    get_user_by_telegram_id,
    get_setting,
    set_setting,
    suspend_user,
    save_transaction,
    get_transaction_by_payment_id,
    generate_unique_id,
    create_deal,
    get_deal_by_unique_id
)
from wallet_processing import create_payout

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# Initialize FastAPI App
app = FastAPI(title="RekberPay Admin API")
templates = Jinja2Templates(directory="templates")

# Initialize Telegram Bot
bot = Bot(token=TOKEN or "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ", default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Dynamic Basic Auth
security = HTTPBasic()

def authenticate_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# --- Admin Dashboard Routes ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/admin/logout")
def admin_logout():
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Logged out",
        headers={"WWW-Authenticate": "Basic"},
    )

@app.get("/admin/stats")
async def get_stats(username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        vol_res = await session.execute(select(func.sum(Deal.amount)).where(Deal.status == 'Completed'))
        total_volume = float(vol_res.scalar() or 0)
        
        deals_res = await session.execute(select(func.count(Deal.id)))
        total_deals = deals_res.scalar() or 0
        
        disputes_res = await session.execute(select(func.count(Deal.id)).where(Deal.status == 'Disputed'))
        active_disputes = disputes_res.scalar() or 0
        
        users_res = await session.execute(select(func.count(User.id)))
        total_users = users_res.scalar() or 0
        
        tx_res = await session.execute(
            select(Transaction, User.username)
            .join(User, Transaction.user_id == User.id)
            .order_by(Transaction.created_at.desc())
            .limit(10)
        )
        recent_txs = []
        for tx, username_val in tx_res.all():
            recent_txs.append({
                "payment_id": tx.payment_id,
                "username": username_val or "user",
                "type": tx.type,
                "amount": float(tx.amount),
                "status": tx.status
            })
            
        return {
            "total_volume": total_volume,
            "total_deals": total_deals,
            "active_disputes": active_disputes,
            "total_users": total_users,
            "recent_transactions": recent_txs
        }

@app.get("/admin/settings")
async def get_all_settings(username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        keys = [
            "moderator_user_id", "min_withdrawal", "nowpayments_api_key",
            "nowpayments_api_url", "nowpayments_ipn_url", "nowpayments_ipn_secret",
            "escrow_fee_percent", "gateway_fee_percent", "nowpayments_payout_email",
            "nowpayments_payout_password", "group_chat_id"
        ]
        settings_dict = {}
        for key in keys:
            val = await get_setting(session, key)
            if val is None:
                if key == "moderator_user_id":
                    val = os.getenv("MODERATOR_USER_ID", "")
                elif key == "min_withdrawal":
                    val = "10.0"
                elif key == "nowpayments_api_key":
                    val = os.getenv("NOWPAYMENTS_API_KEY", "")
                elif key == "nowpayments_api_url":
                    val = os.getenv("NOWPAYMENTS_API_URL", "https://api.nowpayments.io")
                elif key == "nowpayments_ipn_url":
                    val = os.getenv("NOWPAYMENTS_IPN_URL", "")
                elif key == "nowpayments_ipn_secret":
                    val = os.getenv("NOWPAYMENTS_IPN_SECRET", "")
                elif key == "escrow_fee_percent":
                    val = "2.0"
                elif key == "gateway_fee_percent":
                    val = "1.0"
                elif key == "nowpayments_payout_email":
                    val = os.getenv("NOWPAYMENTS_EMAIL", "")
                elif key == "nowpayments_payout_password":
                    val = os.getenv("NOWPAYMENTS_PASSWORD", "")
                else:
                    val = ""
            settings_dict[key] = val
        return settings_dict

@app.post("/admin/settings")
async def save_all_settings(data: dict, username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        for k, v in data.items():
            await set_setting(session, k, str(v))
        return {"success": True}

@app.get("/admin/users")
async def list_users(username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        res = await session.execute(select(User).order_by(User.id.asc()))
        users = res.scalars().all()
        return [{
            "id": u.id,
            "telegram_id": u.telegram_id,
            "username": u.username,
            "wallet_balance": float(u.wallet_balance),
            "is_suspended": u.is_suspended
        } for u in users]

@app.post("/admin/users/suspend")
async def update_user_suspension(data: dict, username: str = Depends(authenticate_admin)):
    tg_id = data.get("telegram_id")
    suspend = data.get("suspend", True)
    async with async_session() as session:
        await suspend_user(session, tg_id, suspend)
        return {"success": True}

@app.post("/admin/users/balance")
async def update_user_balance(data: dict, username: str = Depends(authenticate_admin)):
    user_id = data.get("user_id")
    balance = data.get("balance", 0.0)
    async with async_session() as session:
        res = await session.execute(select(User).where(User.id == user_id))
        user = res.scalars().first()
        if user:
            user.wallet_balance = Decimal(str(balance))
            await session.commit()
            return {"success": True}
        return {"success": False, "error": "User not found"}

@app.get("/admin/deals")
async def list_deals(username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        res = await session.execute(select(Deal).order_by(Deal.id.desc()))
        deals = res.scalars().all()
        result = []
        for d in deals:
            buyer_res = await session.execute(select(User.username).where(User.id == d.buyer_id))
            buyer_username = buyer_res.scalar() or "N/A"
            seller_res = await session.execute(select(User.username).where(User.id == d.seller_id))
            seller_username = seller_res.scalar() or "N/A"
            result.append({
                "id": d.id,
                "unique_id": d.unique_id.hex() if isinstance(d.unique_id, bytes) else d.unique_id,
                "buyer_username": buyer_username,
                "seller_username": seller_username,
                "amount": float(d.amount),
                "escrow_fee": float(d.escrow_fee),
                "gateway_fee": float(d.gateway_fee),
                "total_amount": float(d.total_amount),
                "status": d.status
            })
        return result

@app.post("/admin/deals/dispute-resolve")
async def resolve_dispute(data: dict, username: str = Depends(authenticate_admin)):
    uid = data.get("unique_id")
    action = data.get("action")
    
    async with async_session() as session:
        deal = await get_deal_by_unique_id(session, uid)
        if not deal:
            return {"success": False, "error": "Deal not found"}
            
        buyer_res = await session.execute(select(User).where(User.id == deal.buyer_id))
        buyer = buyer_res.scalars().first()
        seller_res = await session.execute(select(User).where(User.id == deal.seller_id))
        seller = seller_res.scalars().first()
        
        if action == "release":
            if deal.payment_method == "WALLET":
                seller.wallet_balance += deal.amount
                session.add(seller)
                deal.status = "Completed"
                
                tx = await save_transaction(
                    session=session,
                    user_id=seller.id,
                    deal_id=deal.id,
                    tx_type="ESCROW_RELEASE",
                    payment_id=f"DISP-REL-{deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id}-{random.randint(100000, 999999)}",
                    amount=float(deal.amount),
                    currency="USDT",
                    status="completed"
                )
                tx.completed = True
                await session.commit()
                
                try:
                    await bot.send_message(
                        chat_id=seller.telegram_id,
                        text=f"✅ Admin has resolved a dispute on Deal ID {deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id} in your favor! {deal.amount} USDT has been credited to your balance."
                    )
                    await bot.send_message(
                        chat_id=buyer.telegram_id,
                        text=f"ℹ️ Admin has resolved a dispute on Deal ID {deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id} and released the funds to the seller."
                    )
                except Exception as e:
                    logger.error(f"Failed to send bot notification: {e}")
                return {"success": True}
                
            else:  # GATEWAY
                if not deal.seller_wallet:
                    return {"success": False, "error": "Seller's wallet address is missing. Payout cannot be created."}
                    
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
                
                if payout_res.get("success") is True:
                    deal.status = "Completed"
                    tx = await save_transaction(
                        session=session,
                        user_id=seller.id,
                        deal_id=deal.id,
                        tx_type="ESCROW_RELEASE",
                        payment_id=f"DISP-REL-{deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id}-{random.randint(100000, 999999)}",
                        amount=float(deal.amount),
                        currency="USDT",
                        status="completed"
                    )
                    tx.completed = True
                    await session.commit()
                    return {"success": True}
                else:
                    return {"success": False, "error": payout_res.get("error", "Payout failed")}
                    
        elif action == "refund":
            buyer.wallet_balance += deal.amount
            session.add(buyer)
            deal.status = "Canceled"
            
            tx = await save_transaction(
                session=session,
                user_id=buyer.id,
                deal_id=deal.id,
                tx_type="ESCROW_RELEASE",
                payment_id=f"DISP-REF-{deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id}-{random.randint(100000, 999999)}",
                amount=float(deal.amount),
                currency="USDT",
                status="completed"
            )
            tx.completed = True
            await session.commit()
            
            try:
                await bot.send_message(
                    chat_id=buyer.telegram_id,
                    text=f"✅ Dispute on Deal ID {deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id} resolved: escrow amount of {deal.amount} USDT has been refunded to your wallet balance."
                )
                await bot.send_message(
                    chat_id=seller.telegram_id,
                    text=f"ℹ️ Dispute on Deal ID {deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id} resolved: escrow amount refunded to buyer."
                )
            except Exception as e:
                logger.error(f"Failed to send bot notification: {e}")
                
            return {"success": True}
        return {"success": False, "error": "Invalid action"}

@app.get("/admin/api-keys")
async def list_api_keys(username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        res = await session.execute(select(APIKey).order_by(APIKey.id.desc()))
        keys = res.scalars().all()
        result = []
        for k in keys:
            u_res = await session.execute(select(User).where(User.id == k.user_id))
            u = u_res.scalars().first()
            result.append({
                "id": k.id,
                "user_id": k.user_id,
                "telegram_id": u.telegram_id if u else "N/A",
                "api_key": k.api_key,
                "is_active": k.is_active
            })
        return result

@app.post("/admin/api-keys/generate")
async def generate_new_api_key(data: dict, username: str = Depends(authenticate_admin)):
    tg_id = data.get("telegram_id")
    async with async_session() as session:
        user = await get_user_by_telegram_id(session, tg_id)
        if not user:
            return {"success": False, "error": "User not found"}
        
        key_str = f"RP-{secrets.token_hex(24)}"
        
        existing_res = await session.execute(
            select(APIKey).where((APIKey.user_id == user.id) & (APIKey.is_active == True))
        )
        for old_key in existing_res.scalars().all():
            old_key.is_active = False
            
        new_key = APIKey(
            user_id=user.id,
            api_key=key_str,
            is_active=True
        )
        session.add(new_key)
        await session.commit()
        return {"success": True, "api_key": key_str}

@app.post("/admin/api-keys/revoke/{id}")
async def revoke_api_key(id: int, username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        res = await session.execute(select(APIKey).where(APIKey.id == id))
        key = res.scalars().first()
        if key:
            key.is_active = False
            await session.commit()
            return {"success": True}
        return {"success": False, "error": "Key not found"}

@app.get("/admin/payouts")
async def list_payouts(username: str = Depends(authenticate_admin)):
    async with async_session() as session:
        res = await session.execute(
            select(Transaction).where(
                (Transaction.type == "WITHDRAWAL") &
                (Transaction.completed == False)
            ).order_by(Transaction.created_at.desc())
        )
        payouts = res.scalars().all()
        return [{
            "id": p.id,
            "payment_id": p.payment_id,
            "user_id": p.user_id,
            "amount": float(p.amount),
            "currency": p.currency,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None
        } for p in payouts]

@app.post("/admin/payouts/approve")
async def approve_payout(data: dict, username: str = Depends(authenticate_admin)):
    payment_id = data.get("payment_id")
    action = data.get("action")
    
    async with async_session() as session:
        res = await session.execute(
            select(Transaction).where(Transaction.payment_id == payment_id)
        )
        tx = res.scalars().first()
        if not tx:
            return {"success": False, "error": "Transaction not found"}
            
        user_res = await session.execute(select(User).where(User.id == tx.user_id))
        user = user_res.scalars().first()
        if not user:
            return {"success": False, "error": "User not found"}
            
        if action == "approve":
            withdraw_address = (tx.raw_response or {}).get("withdraw_address")
            if not withdraw_address:
                return {"success": False, "error": "Withdrawal address missing in transaction metadata."}
                
            email = await get_setting(session, "nowpayments_payout_email") or os.getenv("NOWPAYMENTS_EMAIL")
            password = await get_setting(session, "nowpayments_payout_password") or os.getenv("NOWPAYMENTS_PASSWORD")
            api_key = await get_setting(session, "nowpayments_api_key") or os.getenv("NOWPAYMENTS_API_KEY")
            api_url = await get_setting(session, "nowpayments_api_url") or os.getenv("NOWPAYMENTS_API_URL", "https://api.nowpayments.io")

            payout_res = await create_payout(
                email=email,
                password=password,
                address=withdraw_address,
                amount=float(tx.amount),
                currency="usdttrc20",
                api_key=api_key,
                api_url=api_url
            )
            
            if payout_res.get("success") is True:
                tx.completed = True
                tx.status = "completed"
                await session.commit()
                
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"✅ Your withdrawal of {tx.amount} USDT has been approved and processed to your address: {withdraw_address}."
                    )
                except Exception as e:
                    logger.error(f"Failed to send bot notification: {e}")
                return {"success": True}
            else:
                return {"success": False, "error": payout_res.get("error", "Payout API failed")}
                
        elif action == "reject":
            user.wallet_balance += tx.amount
            session.add(user)
            tx.completed = False
            tx.status = "rejected"
            await session.commit()
            
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=f"❌ Your withdrawal request of {tx.amount} USDT has been rejected by the administrator. The funds have been returned to your wallet balance."
                )
            except Exception as e:
                logger.error(f"Failed to send bot notification: {e}")
            return {"success": True}
        return {"success": False, "error": "Invalid action"}

# --- Webhooks Routing ---

async def verify_nowpayments_signature(request: Request, ipn_secret: str) -> bool:
    signature = request.headers.get("x-nowpayments-sig")
    if not signature:
        return False
    body = await request.body()
    mac = hmac.new(ipn_secret.encode('utf-8'), body, hashlib.sha512)
    expected_sig = mac.hexdigest()
    return secrets.compare_digest(signature, expected_sig)

@app.post("/webhooks/nowpayments")
async def nowpayments_webhook(request: Request):
    async with async_session() as session:
        ipn_secret = await get_setting(session, "nowpayments_ipn_secret") or os.getenv("NOWPAYMENTS_IPN_SECRET")
        if not ipn_secret:
            logger.error("NOWPayments IPN Secret is not configured. Webhook rejected.")
            raise HTTPException(status_code=500, detail="Webhook secret not configured")

        if not await verify_nowpayments_signature(request, ipn_secret):
            logger.warning("Invalid signature on NOWPayments webhook request")
            raise HTTPException(status_code=401, detail="Invalid signature")

        payload = await request.json()
        payment_id = str(payload.get("payment_id"))
        payment_status = payload.get("payment_status")

        if payment_status in ("confirmed", "finished"):
            tx = await get_transaction_by_payment_id(session, payment_id)
            if tx and not tx.completed:
                tx.completed = True
                tx.status = payment_status
                session.add(tx)
                await session.commit()

                if tx.type == "TOPUP":
                    user_res = await session.execute(select(User).where(User.id == tx.user_id))
                    user = user_res.scalars().first()
                    if user:
                        user.wallet_balance += Decimal(str(tx.amount))
                        session.add(user)
                        await session.commit()
                        try:
                            await bot.send_message(
                                chat_id=user.telegram_id,
                                text=f"✅ <b>Top-up Confirmed!</b>\n\nYour wallet balance has been credited with <b>{tx.amount:.8f} USDT</b>."
                            )
                        except Exception as e:
                            logger.error(f"Failed to send bot notification: {e}")

                elif tx.type == "ESCROW_PAY" and tx.deal_id:
                    res_deal = await session.execute(select(Deal).where(Deal.id == tx.deal_id))
                    deal = res_deal.scalars().first()
                    if deal:
                        buyer_res = await session.execute(select(User).where(User.id == deal.buyer_id))
                        buyer = buyer_res.scalars().first()
                        seller_res = await session.execute(select(User).where(User.id == deal.seller_id))
                        seller = seller_res.scalars().first()

                        deal.status = "Active"
                        session.add(deal)
                        await session.commit()

                        message_text = (
                            f"🔔 <b>Payment Confirmed for Deal ID {deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id}!</b>\n\n"
                            f"Buyer @{buyer.username if buyer else 'buyer'} has successfully deposited the funds.\n"
                            f"Seller @{seller.username if seller else 'seller'} can now proceed with delivering the items."
                        )
                        
                        group_chat_id = await get_setting(session, "group_chat_id")
                        if group_chat_id:
                            try:
                                await bot.send_message(chat_id=int(group_chat_id), text=message_text)
                            except Exception as e:
                                logger.error(f"Failed to notify group chat: {e}")
                                
                        for usr in (buyer, seller):
                            if usr and usr.telegram_id:
                                try:
                                    await bot.send_message(chat_id=usr.telegram_id, text=message_text)
                                except Exception as e:
                                    logger.error(f"Failed to notify user privately: {e}")

        return {"status": "ok"}

# --- API Authentication and Endpoints ---

api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)

async def get_api_key(
    api_key_header_val: str = Security(api_key_header)
) -> User:
    if not api_key_header_val:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key missing"
        )
    
    async with async_session() as session:
        query = select(APIKey).where(APIKey.api_key == api_key_header_val)
        result = await session.execute(query)
        db_key = result.scalars().first()
        
        if not db_key or not db_key.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API Key"
            )
            
        user_query = select(User).where(User.id == db_key.user_id)
        user_result = await session.execute(user_query)
        user = user_result.scalars().first()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not found"
            )
            
        if user.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is suspended"
            )
            
        return user

class DealCreatePayload(BaseModel):
    seller_username: str
    buyer_username: str
    amount: float = Field(..., gt=0)
    transfer_method: str

@app.post("/api/v1/deals")
async def api_create_deal(
    payload: DealCreatePayload,
    user: User = Depends(get_api_key)
):
    async with async_session() as session:
        gateway_pct_str = await get_setting(session, "gateway_fee_percent", "1.0")
        try:
            gateway_pct = Decimal(gateway_pct_str)
        except Exception:
            gateway_pct = Decimal("1.0")
        
        amount_dec = Decimal(str(payload.amount))
        gateway_fee = amount_dec * (gateway_pct / Decimal("100.0"))
        
        unique_id = await generate_unique_id(session)
        
        deal = await create_deal(
            session=session,
            unique_id=unique_id,
            buyer_username=payload.buyer_username,
            seller_username=payload.seller_username,
            amount=payload.amount,
            transfer_method=payload.transfer_method,
            gateway_fee=float(gateway_fee),
            payment_method="GATEWAY"
        )
        
        await session.refresh(deal)
        
        return {
            "unique_id": deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id,
            "buyer_username": payload.buyer_username.lstrip('@'),
            "seller_username": payload.seller_username.lstrip('@'),
            "amount": float(deal.amount),
            "status": deal.status,
            "created_at": deal.created_at.isoformat() if deal.created_at else None
        }

@app.get("/api/v1/deals/{unique_id}")
async def api_get_deal(
    unique_id: str,
    user: User = Depends(get_api_key)
):
    async with async_session() as session:
        deal = await get_deal_by_unique_id(session, unique_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        
        buyer_res = await session.execute(select(User.username).where(User.id == deal.buyer_id))
        buyer_username = buyer_res.scalar() or "N/A"
        seller_res = await session.execute(select(User.username).where(User.id == deal.seller_id))
        seller_username = seller_res.scalar() or "N/A"
        
        return {
            "unique_id": deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id,
            "buyer_username": buyer_username,
            "seller_username": seller_username,
            "amount": float(deal.amount),
            "status": deal.status,
            "created_at": deal.created_at.isoformat() if deal.created_at else None
        }

@app.post("/api/v1/deals/{unique_id}/release")
async def api_release_deal(
    unique_id: str,
    user: User = Depends(get_api_key)
):
    async with async_session() as session:
        deal = await get_deal_by_unique_id(session, unique_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
            
        if deal.status in ("Completed", "Canceled"):
            return {"success": False, "error": f"Deal is already {deal.status.lower()}"}
            
        seller_res = await session.execute(select(User).where(User.id == deal.seller_id))
        seller = seller_res.scalars().first()
        if not seller:
            return {"success": False, "error": "Seller user not found"}
            
        if deal.payment_method == "WALLET":
            seller.wallet_balance += deal.amount
            session.add(seller)
            deal.status = "Completed"
            
            tx = await save_transaction(
                session=session,
                user_id=seller.id,
                deal_id=deal.id,
                tx_type="ESCROW_RELEASE",
                payment_id=f"REL-{deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id}-{random.randint(100000, 999999)}",
                amount=float(deal.amount),
                currency="USDT",
                status="completed"
            )
            tx.completed = True
            await session.commit()
            
            try:
                buyer_res = await session.execute(select(User).where(User.id == deal.buyer_id))
                buyer = buyer_res.scalars().first()
                if seller.telegram_id:
                    await bot.send_message(
                        chat_id=seller.telegram_id,
                        text=f"✅ Escrow released! {deal.amount} USDT has been credited to your wallet balance."
                    )
                if buyer and buyer.telegram_id:
                    await bot.send_message(
                        chat_id=buyer.telegram_id,
                        text=f"ℹ️ Escrow funds for Deal ID {deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id} have been released to the seller."
                    )
            except Exception as e:
                logger.error(f"Failed to send bot notification: {e}")
                
            return {"success": True}
            
        else: # GATEWAY
            if not deal.seller_wallet:
                return {"success": False, "error": "Seller's wallet address is missing. Payout cannot be created."}
                
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
            
            if payout_res.get("success") is True:
                deal.status = "Completed"
                tx = await save_transaction(
                    session=session,
                    user_id=seller.id,
                    deal_id=deal.id,
                    tx_type="ESCROW_RELEASE",
                    payment_id=f"REL-{deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id}-{random.randint(100000, 999999)}",
                    amount=float(deal.amount),
                    currency="USDT",
                    status="completed"
                )
                tx.completed = True
                await session.commit()
                
                try:
                    buyer_res = await session.execute(select(User).where(User.id == deal.buyer_id))
                    buyer = buyer_res.scalars().first()
                    if seller.telegram_id:
                        await bot.send_message(
                            chat_id=seller.telegram_id,
                            text=f"✅ Escrow released! Payout of {deal.amount} USDT has been initiated to your wallet: {deal.seller_wallet}"
                        )
                    if buyer and buyer.telegram_id:
                        await bot.send_message(
                            chat_id=buyer.telegram_id,
                            text=f"ℹ️ Escrow funds for Deal ID {deal.unique_id.hex() if isinstance(deal.unique_id, bytes) else deal.unique_id} have been released to the seller."
                        )
                except Exception as e:
                    logger.error(f"Failed to send bot notification: {e}")
                    
                return {"success": True}
            else:
                return {"success": False, "error": payout_res.get("error", "Payout failed")}

# --- Startup and polling concurrent runner ---

async def run_fastapi_server(fastapi_app):
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8050, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main() -> None:
    # 1. Initialize base tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized.")

    # 3. Setup Bot Dispatcher and router configs
    dp = Dispatcher()
    dp.message.filter(global_command_filter)
    dp.include_router(form_router)
    dp.include_router(deal_commands_router)

    # 4. Start polling and FastAPI web server concurrently
    await asyncio.gather(
        dp.start_polling(bot),
        run_fastapi_server(app)
    )

if __name__ == "__main__":
    asyncio.run(main())
