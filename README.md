# FastAPI Authentication API

A comprehensive authentication API built with FastAPI, featuring user registration, login, MFA, OAuth2 integration, and more.

<img width="1554" height="826" alt="image" src="https://github.com/user-attachments/assets/59cc0a5b-ba87-47e9-a234-948a939c366b" />


## Features

### Core Authentication
- ✅ User Registration (Sign Up)
- ✅ User Login (Sign In)
- ✅ Logout
- ✅ Password Management (Change/Reset)

### Token & Session Management
- ✅ JWT Access Tokens
- ✅ Refresh Tokens
- ✅ Session Management
- ✅ Token Revocation

### Account Management
- ✅ Email/Phone Verification
- ✅ Profile Management
- ✅ API Key Management

### Security Features
- ✅ Multi-Factor Authentication (MFA) with TOTP
- ✅ Rate Limiting & Brute-Force Protection
- ✅ Password Hashing with bcrypt
- ✅ Secure Session Management

### OAuth2 & Third-Party Integration
- ✅ OAuth2/OpenID Connect Support
- ✅ Google OAuth2 Integration
- ✅ GitHub OAuth2 Integration
- ✅ API Key Authentication

### Developer-Friendly Features
- ✅ Token Introspection
- ✅ Health Check Endpoint
- ✅ Comprehensive API Documentation
- ✅ Database Migrations with Alembic

### Wallet System (Non-Custodial)
- ✅ Multi-Wallet Registry (HD / imported / watch-only, multi-chain)
- ✅ Primary Wallet Management
- ✅ Encrypted Wallet Backups (PBKDF2 + Fernet, client-side encryption)
- ✅ Passphrase-Verified Restore (server never stores passphrase or keys)
- ✅ Address Book for Saved Recipients
- ✅ Per-Wallet Activity Audit Trail
- ✅ Wallet & Backup Statistics

## Quick Start

### 1. Clone the Repository
```bash
git clone <repository-url>
cd fastapi-authen
```

### 2. Create Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Set Up Environment Variables
Create a `.env` file in the root directory:
```env
# Database Configuration
DATABASE_URL=postgresql://username:password@localhost:5432/fastapi_auth
REDIS_URL=redis://localhost:6379/0

# JWT Configuration
SECRET_KEY=your-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# Email Configuration
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_TLS=true

# OAuth2 Configuration
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret

# Security Configuration
BCRYPT_ROUNDS=12
RATE_LIMIT_PER_MINUTE=60
MAX_LOGIN_ATTEMPTS=5
LOCKOUT_DURATION_MINUTES=15

# API Configuration
API_V1_STR=/api/v1
PROJECT_NAME=FastAPI Authentication API
PROJECT_VERSION=1.0.0
```

### 5. Set Up Database
```bash
# Create database
createdb fastapi_auth

# Run migrations
alembic upgrade head
```

### 6. Run the Application
```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once the server is running, you can access:
- Interactive API docs: `http://localhost:8000/docs`
- Alternative API docs: `http://localhost:8000/redoc`

## API Endpoints

### Authentication
- `POST /api/v1/auth/register` - User registration
- `POST /api/v1/auth/login` - User login
- `POST /api/v1/auth/logout` - User logout
- `POST /api/v1/auth/change-password` - Change password
- `POST /api/v1/auth/reset-password` - Request password reset
- `POST /api/v1/auth/reset-password/confirm` - Confirm password reset

### Users
- `GET /api/v1/users/me` - Get current user profile
- `PUT /api/v1/users/me` - Update user profile
- `POST /api/v1/users/api-keys` - Create API key
- `GET /api/v1/users/api-keys` - List API keys
- `DELETE /api/v1/users/api-keys/{key_id}` - Revoke API key

### MFA
- `POST /api/v1/mfa/setup` - Setup MFA
- `POST /api/v1/mfa/verify` - Verify MFA code
- `POST /api/v1/mfa/disable` - Disable MFA
- `GET /api/v1/mfa/status` - Get MFA status

### Wallet Management (Non-Custodial)
- `POST /api/v1/wallets/` - Register a wallet (optionally with its encrypted backup)
- `GET /api/v1/wallets/` - List wallets (filter by `chain`, `wallet_type`)
- `GET /api/v1/wallets/stats` - Wallet & backup statistics
- `GET /api/v1/wallets/{wallet_id}` - Wallet details with backup aggregates
- `PUT /api/v1/wallets/{wallet_id}` - Update wallet label/description
- `POST /api/v1/wallets/{wallet_id}/primary` - Set primary wallet
- `DELETE /api/v1/wallets/{wallet_id}` - Soft-delete wallet (auto-promotes next primary)
- `GET /api/v1/wallets/{wallet_id}/activity` - Wallet audit trail (paginated)

### Wallet Backups
- `POST /api/v1/backups/` - Create encrypted backup (optional `wallet_id` link)
- `GET /api/v1/backups/` - List backups (metadata only, filter by `wallet_id`)
- `GET /api/v1/backups/{backup_id}` - Get backup with ciphertext
- `POST /api/v1/backups/{backup_id}/restore` - Verify passphrase & fetch encrypted payload
- `PUT /api/v1/backups/{backup_id}` - Update backup metadata
- `DELETE /api/v1/backups/{backup_id}` - Soft-delete backup

### Address Book
- `POST /api/v1/address-book/` - Save a recipient address
- `GET /api/v1/address-book/` - List saved addresses (filter by `chain`, `search`)
- `GET /api/v1/address-book/{entry_id}` - Get one entry
- `PUT /api/v1/address-book/{entry_id}` - Update an entry
- `DELETE /api/v1/address-book/{entry_id}` - Delete an entry

### OAuth2
- `GET /api/v1/oauth/google` - Google OAuth2 login
- `GET /api/v1/oauth/google/callback` - Google OAuth2 callback
- `GET /api/v1/oauth/github` - GitHub OAuth2 login
- `GET /api/v1/oauth/github/callback` - GitHub OAuth2 callback

### Wallet System Usage (Non-Custodial)

The server **never** sees your wallet keys or passphrase. The client encrypts locally (PBKDF2-SHA256 + Fernet) and submits only ciphertext. The registry stores public metadata only.

**Client-side encryption** (before calling the API):
```python
import os, base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

passphrase = "your-secret-passphrase"
salt = os.urandom(16).hex()
iterations = 200_000
wallet_json = '{"mnemonic": "...", "private_key": "..."}'

kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=bytes.fromhex(salt), iterations=iterations)
key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
encrypted = Fernet(key).encrypt(wallet_json.encode()).decode()
```

**Register a wallet with its encrypted backup (atomic):**
```bash
curl -X POST "http://localhost:8000/api/v1/wallets/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "label": "Main Wallet",
    "address": "0x1234567890abcdef1234567890abcdef12345678",
    "wallet_type": "hd",
    "make_primary": true,
    "backup": {
      "encrypted_data": "<Fernet token from client-side encryption>",
      "salt": "<hex salt used>",
      "kdf_iterations": 200000,
      "passphrase": "your-secret-passphrase"
    }
  }'
```
An existing wallet can also get additional encrypted backups via `POST /api/v1/backups/` with its `wallet_id`.

**Restore (verify passphrase, then decrypt locally):**
```bash
curl -X POST "http://localhost:8000/api/v1/backups/1/restore" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "your-secret-passphrase"}'
```
The response returns `ciphertext`, `salt`, and `kdf_iterations`; decrypt locally with the same PBKDF2+Fernet parameters to recover the wallet JSON.

**Safety behaviors built into the system:**
- Registering a wallet whose backup does not decrypt with the supplied passphrase is rejected atomically (no half-created wallet).
- Wrong passphrases are rejected via a stored derived-key verifier — the server can never decrypt backups itself.
- Deleting a wallet keeps its encrypted backups and auto-promotes the oldest remaining wallet to primary.
- All wallet operations are recorded in a per-wallet audit trail.

## Usage Examples

### Register a New User
```bash
curl -X POST "http://localhost:8000/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "username": "testuser",
    "password": "securepassword123"
  }'
```

### Login
```bash
curl -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "securepassword123"
  }'
```

### Access Protected Endpoint
```bash
curl -X GET "http://localhost:8000/api/v1/users/me" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

## Development

### Running Tests
```bash
pytest
```

### Database Migrations
```bash
# Create a new migration
alembic revision --autogenerate -m "Description of changes"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1
```

### Code Formatting
```bash
black .
isort .
```

## Security Considerations

1. **Environment Variables**: Never commit `.env` files to version control
2. **Secret Keys**: Use strong, randomly generated secret keys in production
3. **Database**: Use connection pooling and SSL in production
4. **Rate Limiting**: Configure appropriate rate limits for your use case
5. **HTTPS**: Always use HTTPS in production
6. **CORS**: Configure CORS properly for your frontend domains

## Production Deployment

1. Set up a production database (PostgreSQL recommended)
2. Configure Redis for session storage
3. Set up a reverse proxy (Nginx)
4. Use a process manager (PM2, systemd)
5. Set up monitoring and logging
6. Configure SSL certificates
7. Set up backup strategies

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

This project is licensed under the MIT License.
