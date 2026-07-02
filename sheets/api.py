import asyncio
import logging
import aiohttp
from decimal import Decimal
from config import sheety_api_url, sheety_token, async_session
from database.database_utils import get_deal_by_unique_id, create_deal
from database.models import User, Deal
from sqlalchemy.future import select

logger = logging.getLogger(__name__)


async def fetch_data_from_sheet():
    headers = {}
    if sheety_token:
        headers["Authorization"] = f"Bearer {sheety_token}"
        
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.get(sheety_api_url, headers=headers, ssl=False) as response:
            if response.status == 200:
                data = await response.json()
                return data.get('escrowSheetLink', [])  # Ensure we get the list or an empty list if not found
            else:
                response_text = await response.text()
                logger.error(f"Error fetching data: {response.status}")
                logger.error(f"Response body: {response_text}")
                return []


async def delete_row_from_sheet(row_id):
    sheety_delete_url = f"{sheety_api_url}/{row_id}"
    headers = {}
    if sheety_token:
        headers["Authorization"] = f"Bearer {sheety_token}"
        
    async with aiohttp.ClientSession() as session:
        response = await session.delete(sheety_delete_url, headers=headers, ssl=False)
        if response.status == 204:
            logger.info(f"Row {row_id} deleted successfully.")
        else:
            logger.error(f"Failed to delete row {row_id}. Status code: {response.status}")


async def has_active_deal(session, username: str) -> bool:
    if not username:
        return False
    stripped = username.lstrip('@')
    user_res = await session.execute(select(User).where(User.username == stripped))
    user = user_res.scalars().first()
    if not user:
        return False
        
    deal_res = await session.execute(
        select(Deal).where(
            ((Deal.buyer_id == user.id) | (Deal.seller_id == user.id)) &
            (Deal.status == 'Active')
        )
    )
    return deal_res.scalars().first() is not None


async def job_function():
    logger.info("Fetching and updating database...")
    data = await fetch_data_from_sheet()
    
    for row in data:
        seller_username = row.get("seller'sTelegramUsername")
        buyer_username = row.get("buyer'sTelegramUsername")
        row_id = row.get("id")

        if not seller_username or not buyer_username or row_id is None:
            continue

        async with async_session() as session:
            existed_deal = await get_deal_by_unique_id(session, row_id)
            seller_active = await has_active_deal(session, seller_username)
            buyer_active = await has_active_deal(session, buyer_username)

            if existed_deal:
                logger.info(f"Deal {row_id} already exists in database. Skipping.")
                continue
            elif seller_active or buyer_active:
                logger.info(f"Active deal already exists for user in row {row_id}. Deleting row from sheet.")
                await delete_row_from_sheet(row_id)
            else:
                # If no active deal exists, create the deal in MySQL database
                amount_val = float(row.get("amountOfMoneyWithoutCommision", 0.0))
                pref_method = str(row.get("preferredTransferMethod", "GATEWAY")).upper()
                if pref_method not in ("WALLET", "GATEWAY"):
                    pref_method = "GATEWAY"
                
                await create_deal(
                    session=session,
                    unique_id=row_id,
                    buyer_username=buyer_username,
                    seller_username=seller_username,
                    amount=amount_val,
                    transfer_method="USDT",
                    gateway_fee=0.0,
                    payment_method=pref_method
                )
                logger.info(f"Synchronized new deal {row_id} from Google Sheets.")


async def scheduler(interval, func):
    while True:
        try:
            await func()
        except Exception as e:
            logger.exception(f"Exception during scheduled sync: {e}")
        await asyncio.sleep(interval)


async def main():
    # Run the job function every 120 seconds
    asyncio.create_task(scheduler(120, job_function))

    while True:
        await asyncio.sleep(3600)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
