import httpx
import asyncio
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Legacy stubs to prevent import errors in handlers/inline_process.py
collection_trans = None

async def get_incoming_transactions(client) -> None:
    logger.warning("get_incoming_transactions is deprecated. Use NOWPayments get_payment_status instead.")
    return None

async def send_trc20_tokens(client, currency, amount, recipient_address, memo=None) -> dict:
    logger.warning("send_trc20_tokens is deprecated. Use NOWPayments create_payout instead.")
    return {}

# NOWPayments Gateway Client Implementation

def get_nowpayments_headers(api_key: str, jwt_token: str = None) -> dict:
    """
    Constructs request headers containing x-api-key.
    If jwt_token is provided, includes Authorization: Bearer <jwt_token>.
    """
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    return headers

async def get_jwt_token(email: str, password: str, api_key: str, api_url: str) -> str:
    """
    Asynchronously retrieves a JWT token by posting to {api_url}/v1/auth.
    Returns the JWT token string, or None if authentication fails.
    """
    headers = get_nowpayments_headers(api_key)
    payload = {
        "email": email,
        "password": password
    }
    base_url = api_url.rstrip("/")
    url = f"{base_url}/v1/auth"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                data = response.json()
                return data.get("token")
            else:
                logger.error(f"Failed to retrieve JWT token: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        logger.exception(f"Exception during JWT token retrieval: {e}")
    return None

async def create_payment(
    price_amount: float,
    price_currency: str,
    pay_currency: str,
    order_id: str,
    ipn_url: str,
    api_key: str,
    api_url: str
) -> dict:
    """
    Asynchronously POSTs to {api_url}/v1/payment with the required payload fields.
    Returns the JSON response containing the generated pay_address, pay_amount, and payment_id,
    or raises an exception.
    """
    headers = get_nowpayments_headers(api_key)
    payload = {
        "price_amount": price_amount,
        "price_currency": price_currency,
        "pay_currency": pay_currency,
        "order_id": order_id,
        "ipn_callback_url": ipn_url
    }
    base_url = api_url.rstrip("/")
    url = f"{base_url}/v1/payment"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.exception(f"Exception during create_payment: {e}")
        raise

async def get_payment_status(payment_id: str, api_key: str, api_url: str) -> dict:
    """
    Asynchronously GETs {api_url}/v1/payment/{payment_id}.
    Returns the JSON response containing status details.
    """
    headers = get_nowpayments_headers(api_key)
    base_url = api_url.rstrip("/")
    url = f"{base_url}/v1/payment/{payment_id}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.exception(f"Exception during get_payment_status: {e}")
        raise

async def create_payout(
    email: str,
    password: str,
    address: str,
    amount: float,
    currency: str,
    api_key: str,
    api_url: str
) -> dict:
    """
    Asynchronously:
    - Obtains a JWT token using get_jwt_token.
    - POSTs to {api_url}/v1/payout with headers including BOTH x-api-key and Authorization: Bearer <jwt_token>.
    - Payload: {"payouts": [{"address": address, "amount": amount, "currency": currency}]}.
    - Validates that the response is successful and contains a successful payout status (or code 200/201).
    - Returns {"success": True, "raw": response_json} on success, or {"success": False, "error": "Reason"} on failure.
    """
    try:
        jwt_token = await get_jwt_token(email, password, api_key, api_url)
        if not jwt_token:
            return {"success": False, "error": "Authentication failed"}

        headers = get_nowpayments_headers(api_key, jwt_token)
        payload = {
            "payouts": [
                {
                    "address": address,
                    "amount": amount,
                    "currency": currency
                }
            ]
        }
        base_url = api_url.rstrip("/")
        url = f"{base_url}/v1/payout"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return {"success": True, "raw": response.json()}
            else:
                return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
    except Exception as e:
        logger.exception(f"Exception during create_payout: {e}")
        return {"success": False, "error": str(e)}
