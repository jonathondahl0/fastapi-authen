from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User as UserModel
from app.schemas.wallet import (WalletActivityResponse, WalletBackupMeta,
                                WalletCreate, WalletCreateResponse, WalletMeta,
                                WalletStatsResponse, WalletUpdate)
from app.services.wallet_registry import (WalletAlreadyExists,
                                          WalletLimitError, WalletNotFound,
                                          WalletRegistryError, WalletService)

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> WalletService:
    return WalletService(db)


def _wallet_meta(
    wallet,
    backup_count: int = 0,
    last_backup_at: Optional[datetime] = None,
) -> WalletMeta:
    meta = WalletMeta.model_validate(wallet)
    meta.backup_count = backup_count
    meta.last_backup_at = last_backup_at
    return meta


@router.post(
    "/", response_model=WalletCreateResponse, status_code=status.HTTP_201_CREATED
)
async def register_wallet(
    payload: WalletCreate,
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """Register a wallet, optionally creating its encrypted backup atomically."""
    try:
        wallet, backup = service.create_wallet(
            user=current_user,
            label=payload.label,
            address=payload.address,
            chain=payload.chain,
            wallet_type=payload.wallet_type,
            description=payload.description,
            make_primary=payload.make_primary,
            backup=payload.backup.model_dump() if payload.backup else None,
        )
    except WalletAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except WalletLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except WalletRegistryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return WalletCreateResponse(
        wallet=_wallet_meta(
            wallet,
            backup_count=1 if backup else 0,
            last_backup_at=backup.created_at if backup else None,
        ),
        backup=WalletBackupMeta.model_validate(backup) if backup else None,
    )


@router.get("/", response_model=List[WalletMeta])
async def list_wallets(
    chain: Optional[str] = Query(None, description="Filter by chain"),
    wallet_type: Optional[str] = Query(None, description="Filter by wallet type"),
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """List the user's wallets, primary first."""
    wallets = service.list_wallets(current_user, chain=chain, wallet_type=wallet_type)
    counts = service.get_backup_counts(current_user, [w.id for w in wallets])
    return [_wallet_meta(w, *counts.get(w.id, (0, None))) for w in wallets]


@router.get("/stats", response_model=WalletStatsResponse)
async def wallet_stats(
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """Aggregate wallet and backup statistics for the user."""
    return service.get_stats(current_user)


@router.get("/{wallet_id}", response_model=WalletMeta)
async def get_wallet(
    wallet_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """Fetch a single wallet with its backup aggregates."""
    try:
        wallet = service.get_wallet(current_user, wallet_id)
    except WalletNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    counts = service.get_backup_counts(current_user, [wallet.id])
    return _wallet_meta(wallet, *counts.get(wallet.id, (0, None)))


@router.put("/{wallet_id}", response_model=WalletMeta)
async def update_wallet(
    wallet_id: int,
    payload: WalletUpdate,
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """Update wallet metadata (label and/or description)."""
    try:
        wallet = service.update_wallet(
            current_user,
            wallet_id,
            label=payload.label,
            description=payload.description,
        )
    except WalletNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    counts = service.get_backup_counts(current_user, [wallet.id])
    return _wallet_meta(wallet, *counts.get(wallet.id, (0, None)))


@router.post("/{wallet_id}/primary", response_model=WalletMeta)
async def set_primary_wallet(
    wallet_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """Mark a wallet as the user's primary wallet."""
    try:
        wallet = service.set_primary(current_user, wallet_id)
    except WalletNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    counts = service.get_backup_counts(current_user, [wallet.id])
    return _wallet_meta(wallet, *counts.get(wallet.id, (0, None)))


@router.delete("/{wallet_id}")
async def delete_wallet(
    wallet_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """Soft-delete a wallet.

    Encrypted backups are kept so recovery data is never lost; if the deleted
    wallet was primary, the oldest remaining wallet is promoted automatically.
    """
    try:
        service.delete_wallet(current_user, wallet_id)
    except WalletNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": "Wallet deleted successfully"}


@router.get("/{wallet_id}/activity", response_model=List[WalletActivityResponse])
async def wallet_activity(
    wallet_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserModel = Depends(get_current_user),
    service: WalletService = Depends(_service),
):
    """Audit trail for a wallet (newest first)."""
    try:
        return service.get_activity(
            current_user, wallet_id=wallet_id, limit=limit, offset=offset
        )
    except WalletNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
