from fastapi import APIRouter
from app.api.v1.endpoints import (
    auth,
    users,
    mfa,
    oauth,
    wallets,
    backups,
    address_book,
    admin_wallets,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(mfa.router, prefix="/mfa", tags=["mfa"])
api_router.include_router(oauth.router, prefix="/oauth", tags=["oauth"])
api_router.include_router(wallets.router, prefix="/wallets", tags=["wallets"])
api_router.include_router(backups.router, prefix="/backups", tags=["wallet-backups"])
api_router.include_router(address_book.router, prefix="/address-book", tags=["address-book"])
api_router.include_router(admin_wallets.router, prefix="/admin", tags=["admin-wallets"])
