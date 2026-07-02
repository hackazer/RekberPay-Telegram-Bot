# Progress - Component 5: API Key Authentication & REST API Endpoints

## Objective
Implement Component 5:
1. `X-API-KEY` authentication header dependency verifying API key status and owner suspension status.
2. `POST /api/v1/deals` endpoint for automated/API-driven deal creation with dynamic gateway fee calculations.
3. `GET /api/v1/deals/{unique_id}` endpoint to retrieve deal details.
4. `POST /api/v1/deals/{unique_id}/release` endpoint to trigger escrow release (via user balance or NOWPayments payout depending on the payment method).
5. Comprehensive test suite under `tests/test_api.py`.

## Implementation Details

### 1. X-API-KEY Authentication Dependency
- Created `get_api_key` dependency reading the `X-API-KEY` header using `APIKeyHeader`.
- Verifies key status from the `api_keys` table (returns `401 Unauthorized` if invalid/missing).
- Verifies the owner user is not suspended (returns `403 Forbidden` if suspended or not found).

### 2. REST Endpoints
- `POST /api/v1/deals`: Validates the payload using a Pydantic model (`DealCreatePayload`), generates a 4-digit ID via `generate_unique_id`, calculates the gateway fee dynamically based on settings, and inserts the `GATEWAY` deal via `create_deal`.
- `GET /api/v1/deals/{unique_id}`: Retrieves deal details along with buyer and seller usernames.
- `POST /api/v1/deals/{unique_id}/release`: Releases funds to the seller (credits WALLET balance or initiates a NOWPayments payout for GATEWAY deals).

### 3. Verification & Test Suite
- Implemented in `tests/test_api.py` covering:
  - Auth checks (missing, invalid, revoked keys; suspended, non-existent users).
  - API key revocation via the admin endpoint.
  - Deal creation via the REST endpoint (asserting correct dynamic gateway fee calculations).
  - Deal retrieval via `GET`.
  - Escrow release triggers for both `WALLET` and `GATEWAY` payment methods, asserting database and wallet state transitions, as well as handling of payout failure scenarios.

## Verification
- Verified by running all unit tests in the project.
- **Result**: All 43 tests passed successfully.
