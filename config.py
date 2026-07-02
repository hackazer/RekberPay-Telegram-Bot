from dotenv import load_dotenv
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()

# MySQL Database Configuration
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "rekberpay")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")

# Build async connection URL
DATABASE_URL = f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

# Create async engine and sessionmaker
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Admin Authentication Configuration
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin_secure_password_default")

# Bot & Other Configurations
TOKEN = os.getenv('TOKEN')

sheety_api_url = os.getenv("SHEETY_URL")
sheety_token = os.getenv("SHEETY_ID")

MODERATOR_USER_ID = os.getenv("MODERATOR_USER_ID")

kucoin_api_key = os.getenv("KUCOIN_API_KEY")
kucoin_api_secret = os.getenv("KUCOIN_API_SECRET")
kucoin_api_passphrase = os.getenv("KUCOIN_API_PASSPHRASE")