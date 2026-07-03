from sqlalchemy import Column, Integer, BigInteger, String, DECIMAL as Decimal, Boolean, Text, ForeignKey, TIMESTAMP, JSON, func, BINARY
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255), nullable=True)
    wallet_balance = Column(Decimal(16, 8), default=0.00000000)
    is_suspended = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    
    deals_as_seller = relationship("Deal", foreign_keys="[Deal.seller_id]", back_populates="seller")
    deals_as_buyer = relationship("Deal", foreign_keys="[Deal.buyer_id]", back_populates="buyer")
    transactions = relationship("Transaction", back_populates="user")
    api_keys = relationship("APIKey", back_populates="user")

class Deal(Base):
    __tablename__ = 'deals'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    unique_id = Column(BINARY(16), unique=True, nullable=False)
    group_id = Column(String(100), nullable=True)
    seller_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    buyer_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    amount = Column(Decimal(16, 8), nullable=False)
    escrow_fee = Column(Decimal(16, 8), nullable=False)
    gateway_fee = Column(Decimal(16, 8), default=0.00000000)
    total_amount = Column(Decimal(16, 8), nullable=False)
    payment_method = Column(String(50), nullable=False) # 'WALLET' or 'GATEWAY'
    seller_wallet = Column(String(255), nullable=True)
    status = Column(String(50), default='Active') # 'Active', 'Completed', 'Canceled', 'Disputed', 'Awaiting_Payout', 'Awaiting_Verification'
    continue_deal_count = Column(Integer, default=0)
    users_interacted = Column(JSON, default=list)
    nowpayments_payment_id = Column(String(255), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    
    seller = relationship("User", foreign_keys=[seller_id], back_populates="deals_as_seller")
    buyer = relationship("User", foreign_keys=[buyer_id], back_populates="deals_as_buyer")
    transactions = relationship("Transaction", back_populates="deal")

class Transaction(Base):
    __tablename__ = 'transactions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    deal_id = Column(Integer, ForeignKey('deals.id'), nullable=True)
    type = Column(String(50), nullable=False) # 'TOPUP', 'WITHDRAWAL', 'ESCROW_PAY', 'ESCROW_RELEASE'
    payment_id = Column(String(255), unique=True, nullable=True)
    amount = Column(Decimal(16, 8), nullable=False)
    currency = Column(String(10), nullable=False)
    status = Column(String(50), default='waiting')
    completed = Column(Boolean, default=False)
    raw_response = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    
    user = relationship("User", back_populates="transactions")
    deal = relationship("Deal", back_populates="transactions")

class APIKey(Base):
    __tablename__ = 'api_keys'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    api_key = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    
    user = relationship("User", back_populates="api_keys")

class Setting(Base):
    __tablename__ = 'settings'
    
    key_name = Column(String(255), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
