import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import httpx
from wallet_processing import (
    get_nowpayments_headers,
    get_jwt_token,
    create_payment,
    get_payment_status,
    create_payout
)

def test_get_nowpayments_headers():
    headers = get_nowpayments_headers("test-key")
    assert headers["x-api-key"] == "test-key"
    assert "Authorization" not in headers

    headers_jwt = get_nowpayments_headers("test-key", "jwt-token")
    assert headers_jwt["x-api-key"] == "test-key"
    assert headers_jwt["Authorization"] == "Bearer jwt-token"

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_get_jwt_token_success(mock_post):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"token": "mock-jwt-token"}
    mock_post.return_value = mock_response

    token = await get_jwt_token("email@test.com", "password", "api-key", "https://api.nowpayments.io")
    assert token == "mock-jwt-token"
    mock_post.assert_called_once()

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_get_jwt_token_fail(mock_post):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 401
    mock_post.return_value = mock_response

    token = await get_jwt_token("email@test.com", "password", "api-key", "https://api.nowpayments.io")
    assert token is None

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_create_payment_success(mock_post):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "pay_address": "0x123",
        "pay_amount": 10.0,
        "payment_id": "pay-id"
    }
    mock_post.return_value = mock_response

    res = await create_payment(10.0, "USD", "USDT", "order-123", "https://ipn.url", "api-key", "https://api.nowpayments.io")
    assert res["payment_id"] == "pay-id"
    assert res["pay_address"] == "0x123"

@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_get_payment_status_success(mock_get):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"payment_id": "pay-id", "payment_status": "waiting"}
    mock_get.return_value = mock_response

    res = await get_payment_status("pay-id", "api-key", "https://api.nowpayments.io")
    assert res["payment_status"] == "waiting"

@pytest.mark.asyncio
@patch("wallet_processing.get_jwt_token")
@patch("httpx.AsyncClient.post")
async def test_create_payout_success(mock_post, mock_get_jwt):
    mock_get_jwt.return_value = "mock-jwt-token"
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_response.json.return_value = {"id": "payout-id", "payouts": []}
    mock_post.return_value = mock_response

    res = await create_payout("email@test.com", "password", "0xabc", 50.0, "USDT", "api-key", "https://api.nowpayments.io")
    assert res["success"] is True
    assert res["raw"]["id"] == "payout-id"
