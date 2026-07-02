import random
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database.models import User, Deal, Transaction, Setting

async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalars().first()

async def get_user_by_username(session: AsyncSession, username: str) -> User:
    if not username:
        return None
    stripped_username = username.lstrip('@')
    result = await session.execute(
        select(User).where(User.username == stripped_username)
    )
    return result.scalars().first()

async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str = None) -> User:
    # 1. Query by telegram_id
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalars().first()
    
    stripped_username = username.lstrip('@') if username else None
    
    if user:
        if stripped_username and user.username != stripped_username:
            user.username = stripped_username
            await session.commit()
        return user
        
    # 2. If not found by telegram_id, check if they exist by username (e.g. pre-created via create_deal)
    if stripped_username:
        result = await session.execute(
            select(User).where(User.username == stripped_username)
        )
        user = result.scalars().first()
        if user:
            user.telegram_id = telegram_id
            await session.commit()
            return user
            
    # 3. Create new user
    user = User(telegram_id=telegram_id, username=stripped_username)
    session.add(user)
    await session.commit()
    return user

async def get_active_deal_by_user(session: AsyncSession, telegram_id: int) -> Deal:
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        return None
        
    result = await session.execute(
        select(Deal).where(
            ((Deal.buyer_id == user.id) | (Deal.seller_id == user.id)) & 
            (Deal.status == 'Active')
        )
    )
    return result.scalars().first()

async def get_deal_by_unique_id(session: AsyncSession, unique_id: int) -> Deal:
    result = await session.execute(
        select(Deal).where(Deal.unique_id == unique_id)
    )
    return result.scalars().first()

async def _get_unique_dummy_telegram_id(session: AsyncSession) -> int:
    while True:
        dummy_id = -random.randint(1, 1000000000)
        existing = await get_user_by_telegram_id(session, dummy_id)
        if not existing:
            return dummy_id

async def _get_or_create_dummy_user(session: AsyncSession, username: str) -> User:
    user = await get_user_by_username(session, username)
    if user:
        return user
        
    dummy_id = await _get_unique_dummy_telegram_id(session)
    user = User(telegram_id=dummy_id, username=username.lstrip('@'))
    session.add(user)
    await session.commit()
    return user

async def create_deal(
    session: AsyncSession,
    unique_id: int,
    buyer_username: str,
    seller_username: str,
    amount: float,
    transfer_method: str,
    gateway_fee: float = 0.0,
    payment_method: str = "GATEWAY"
) -> Deal:
    buyer = await _get_or_create_dummy_user(session, buyer_username)
    seller = await _get_or_create_dummy_user(session, seller_username)
    
    dec_amount = Decimal(str(amount))
    dec_gateway_fee = Decimal(str(gateway_fee))
    escrow_fee = Decimal("0.0")
    total_amount = dec_amount + escrow_fee + dec_gateway_fee
    
    deal = Deal(
        unique_id=unique_id,
        buyer_id=buyer.id,
        seller_id=seller.id,
        amount=dec_amount,
        escrow_fee=escrow_fee,
        gateway_fee=dec_gateway_fee,
        total_amount=total_amount,
        payment_method=payment_method,
        status="Active",
        users_interacted=[]
    )
    session.add(deal)
    await session.commit()
    return deal

async def save_transaction(
    session: AsyncSession,
    user_id: int,
    deal_id: int,
    tx_type: str,
    payment_id: str,
    amount: float,
    currency: str,
    status: str = "waiting"
) -> Transaction:
    tx = Transaction(
        user_id=user_id,
        deal_id=deal_id,
        type=tx_type,
        payment_id=payment_id,
        amount=Decimal(str(amount)),
        currency=currency,
        status=status,
        completed=False
    )
    session.add(tx)
    await session.commit()
    return tx

async def get_transaction_by_payment_id(session: AsyncSession, payment_id: str) -> Transaction:
    result = await session.execute(
        select(Transaction).where(Transaction.payment_id == payment_id)
    )
    return result.scalars().first()

async def update_transaction_status(
    session: AsyncSession,
    payment_id: str,
    status: str,
    completed: bool = False
) -> Transaction:
    result = await session.execute(
        select(Transaction).where(Transaction.payment_id == payment_id)
    )
    tx = result.scalars().first()
    if tx:
        tx.status = status
        tx.completed = completed
        await session.commit()
    return tx

async def get_setting(session: AsyncSession, key_name: str, default: str = None) -> str:
    result = await session.execute(
        select(Setting).where(Setting.key_name == key_name)
    )
    setting = result.scalars().first()
    if setting:
        return setting.value
    return default

async def set_setting(session: AsyncSession, key_name: str, value: str):
    result = await session.execute(
        select(Setting).where(Setting.key_name == key_name)
    )
    setting = result.scalars().first()
    if setting:
        setting.value = value
    else:
        setting = Setting(key_name=key_name, value=value)
        session.add(setting)
    await session.commit()

async def suspend_user(session: AsyncSession, telegram_id: int, suspend: bool = True):
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalars().first()
    if user:
        user.is_suspended = suspend
        await session.commit()

async def generate_unique_id(session: AsyncSession) -> int:
    while True:
        uid = random.randint(1000, 9999)
        # Check if this unique_id is used in any active deal
        result = await session.execute(
            select(Deal).where((Deal.unique_id == uid) & (Deal.status == 'Active'))
        )
        if not result.scalars().first():
            return uid
