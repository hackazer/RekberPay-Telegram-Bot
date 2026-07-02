# RekberPay Telegram Bot

RekberPay is an automated peer-to-peer (P2P) transaction escrow bot and web administrator panel designed for secure Telegram group commerce.

---

## Key Features

* **Dual Payment Workflows:** Transact natively using internal user wallets or process automated payments through the NOWPayments API gateway.
* **FastAPI Admin Dashboard:** Manage settings, users, dispute release/refund actions, and monitor live transactions from a secure web portal.
* **External REST API:** Allows external developers to generate API keys, create deals, and monitor transactions programmatically.
* **Security Audited:** Strict SHA512 HMAC signature checks on payment webhooks and role-based access validation on FSM callbacks.
* **MySQL Storage:** Powered by SQLAlchemy Async Engine for robust query processing and connection pool scaling.

---

## Project Structure

```
├── Dockerfile                  # Application Docker setup
├── README.md                   # Project documentation
├── config.py                   # Environment and database configuration
├── deploy                      # Deployment guides and documentation
│   └── README.md
├── docker-compose.yml          # Container configuration
├── main.py                     # Entry point (FastAPI web server & Telegram bot task runner)
├── database
│   ├── database_utils.py       # Async SQL query wrappers
│   └── models.py               # Declarative SQLAlchemy models
├── handlers
│   ├── commands.py             # User message and command routing
│   └── inline_process.py       # Inline keyboard callbacks and FSM state flows
├── states
│   └── form_states.py          # State definitions
├── templates
│   └── dashboard.html          # Web dashboard layout
└── tests                       # Suite of 43 unit and integration tests
```

---

## Quick Start (Local Testing)

1. Clone the repository and navigate to the project directory:
   ```bash
   git clone https://github.com/hackazer/RekberPay-Telegram-Bot.git
   cd RekberPay-Telegram-Bot
   ```

2. Initialize a Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Configure your local configuration inside `.env`.

4. Run the automated test suite to verify the setup:
   ```bash
   PYTHONPATH=. .venv/bin/pytest tests/ -v
   ```

5. Start the bot and web server:
   ```bash
   python main.py
   ```
   The API will listen on `http://localhost:8050/` and the admin portal will be available at `http://localhost:8050/admin`.

---

## Production Deployment

Detailed deployment configuration guides for Nginx, SSL certificates, systemd background daemons, and Docker environments can be found in the [deploy/README.md](deploy/README.md) file.

---

## Copyright & Credits

Copyright (c) 2026 [RekberPay](https://rekberpay.com).

This project is led by [Rizaldy Primanta Putra](https://riz.my.id) and is a part of the KSATRIA Indonesia Group.

---

## License

This project is licensed under the [MIT License](LICENSE).
