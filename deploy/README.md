# Deployment Manual: RekberPay Telegram Bot

This document outlines the step-by-step production deployment instructions for the RekberPay Telegram Bot.

All internal services run on port **8050** as configured.

---

## 1. Server Environment Prerequisites

Make sure the following system packages are installed on your Linux server:
* Nginx (Web Server and Reverse Proxy)
* Git
* curl / snap (for SSL and Docker setup)

---

## 2. Option A: Containerized Deployment (Recommended)

This runs only the RekberPay application inside Docker, while connecting directly to your host's existing production MySQL server. This prevents any port conflicts or risk of breaking existing services.

### 2.1. Install Docker and Docker Compose
If Docker is not yet installed, install it using the official script:
```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
```

### 2.2. Configure Host MySQL Access
To allow the container to securely access the host's MySQL:
1. Ensure your host's MySQL configuration (`/etc/mysql/mysql.conf.d/mysqld.cnf` or equivalent) allows connections from the Docker bridge gateway interface (typically `127.0.0.1` and `172.17.0.1`, or `0.0.0.0`).
   * For example, ensure your `bind-address` is set to `0.0.0.0` or includes the Docker gateway IP, and configure your firewall to block public port `3306` access.
2. In MySQL, create a database and grant access:
   ```sql
   CREATE DATABASE rekberpay_db;
   -- Grant access specifically to the Docker bridge subnet (typically 172.16.0.0/12 or 172.17.0.0/16)
   CREATE USER 'rekberpay_user'@'%' IDENTIFIED BY 'user_secure_password';
   GRANT ALL PRIVILEGES ON rekberpay_db.* TO 'rekberpay_user'@'%';
   FLUSH PRIVILEGES;
   ```

### 2.3. Setup docker-compose.yml
1. Copy project files to your server directory (e.g., `/var/www/rekberpay`).
2. Open `docker-compose.yml` and modify the environment settings:
   * `MYSQL_HOST`: Set to `host.docker.internal` (Docker will resolve this to the host machine gateway IP).
   * `MYSQL_USER`: `rekberpay_user`
   * `MYSQL_PASSWORD`: Your password
   * `MYSQL_DB`: `rekberpay_db`
   * `TOKEN`: Telegram Bot HTTP API token.
   * `MODERATOR_USER_ID`: Telegram numeric ID of the dispute resolver.
   * `ADMIN_USERNAME` / `ADMIN_PASSWORD`: Your credentials for dashboard login.
   * `NOWPAYMENTS_*`: API keys and details.

### 2.4. Run the Container
Start the container in detached mode:
```bash
docker compose up -d --build
```
This launches both the bot and web API listener on port `8050` and safely links to your host's MySQL database.

---

## 3. Option B: Native Host Deployment (Alternative)

If you prefer to run services directly on your host machine without Docker.

### 3.1. Install System Dependencies
Install Python 3.10+, pip, and MySQL Server:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv mysql-server build-essential -y
```

### 3.2. Setup Database
1. Log into MySQL:
   ```bash
   sudo mysql
   ```
2. Create database and user:
   ```sql
   CREATE DATABASE rekberpay_db;
   CREATE USER 'rekberpay_user'@'localhost' IDENTIFIED BY 'user_secure_password';
   GRANT ALL PRIVILEGES ON rekberpay_db.* TO 'rekberpay_user'@'localhost';
   FLUSH PRIVILEGES;
   EXIT;
   ```

### 3.3. Install Project Requirements
1. Navigate to the project root directory.
2. Initialize virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

### 3.4. Configure Environment Variables
Create a `.env` file in the project root:
```ini
TOKEN=your_telegram_bot_token
MODERATOR_USER_ID=your_telegram_id
MYSQL_HOST=localhost
MYSQL_USER=rekberpay_user
MYSQL_PASSWORD=user_secure_password
MYSQL_DB=rekberpay_db
MYSQL_PORT=3306
ADMIN_USERNAME=admin
ADMIN_PASSWORD=secure_admin_password
```

### 3.5. Configure Background Systemd Service
1. Create a service file:
   ```bash
   sudo nano /etc/systemd/system/rekberpay.service
   ```
2. Add the configuration:
   ```ini
   [Unit]
   Description=RekberPay Telegram Bot & Dashboard Service
   After=network.target mysql.service

   [Service]
   Type=simple
   User=www-data
   WorkingDirectory=/var/www/rekberpay
   ExecStart=/var/www/rekberpay/.venv/bin/python main.py
   Restart=always
   EnvironmentFile=/var/www/rekberpay/.env

   [Install]
   WantedBy=multi-user.target
   ```
3. Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable rekberpay
   sudo systemctl start rekberpay
   ```

---

## 4. Nginx Reverse Proxy Configuration

Configure Nginx to route external requests from `tg.rekberpay.com` securely to port `8050`.

1. Create Nginx site configuration:
   ```bash
   sudo nano /etc/nginx/sites-available/rekberpay
   ```
2. Paste the server block configuration:
   ```nginx
   server {
       listen 80;
       server_name tg.rekberpay.com;

       location / {
           proxy_pass http://127.0.0.1:8050;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```
3. Enable configuration and reload Nginx:
   ```bash
   sudo ln -s /etc/nginx/sites-available/rekberpay /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

---

## 5. Enable SSL via Certbot (HTTPS)

Secure the endpoint using Let's Encrypt certificates:
```bash
sudo snap install --classic certbot
sudo ln -s /snap/bin/certbot /usr/bin/certbot
sudo certbot --nginx -d tg.rekberpay.com
```
Follow the interactive prompts to enable SSL redirection. Nginx will automatically handle SSL handshake and route traffic safely to the bot ecosystem.
