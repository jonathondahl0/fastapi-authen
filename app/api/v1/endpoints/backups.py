from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User as UserModel
from app.schemas.wallet import (WalletBackupCreate, WalletBackupMeta,
                                WalletBackupResponse, WalletBackupUpdate,
                                WalletRestoreRequest, WalletRestoreResponse)
from app.services.wallet import (WalletBackupError, WalletBackupLimitError,
                                 WalletBackupNotFound, WalletBackupService,
                                 WalletPassphraseError)
from app.services.wallet_registry import WalletNotFound, WalletService

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> WalletBackupService:
    return WalletBackupService(db)


def _wallet_service(db: Session = Depends(get_db)) -> WalletService:
    return WalletService(db)


@router.post("/", response_model=WalletBackupMeta, status_code=status.HTTP_201_CREATED)
async def create_wallet_backup(
    payload: WalletBackupCreate,
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_service),
    wallet_service: WalletService = Depends(_wallet_service),
):
    """Create an encrypted wallet backup, optionally linked to a wallet."""
    if payload.wallet_id is not None:
        try:
            wallet_service.get_wallet(current_user, payload.wallet_id)
        except WalletNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    try:
        backup = service.create_backup(
            user=current_user,
            label=payload.label,
            encrypted_data=payload.encrypted_data,
            passphrase=payload.passphrase,
            salt=payload.salt,
            kdf_iterations=payload.kdf_iterations,
            wallet_id=payload.wallet_id,
            wallet_address=payload.wallet_address,
        )
    except WalletBackupLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except WalletBackupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return backup


@router.get("/", response_model=List[WalletBackupMeta])
async def list_wallet_backups(
    wallet_id: Optional[int] = Query(None, description="Filter by wallet"),
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_service),
):
    """List all active wallet backups for the authenticated user."""
    return service.list_backups(current_user, wallet_id=wallet_id)


@router.get("/{backup_id}", response_model=WalletBackupResponse)
async def get_wallet_backup(
    backup_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_service),
):
    """Fetch a single backup including ciphertext (metadata + encrypted data)."""
    try:
        return service.get_backup(current_user, backup_id)
    except WalletBackupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{backup_id}/restore", response_model=WalletRestoreResponse)
async def restore_wallet_backup(
    backup_id: int,
    payload: WalletRestoreRequest,
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_service),
):
    """Verify the passphrase and return the encrypted payload to restore.

    The passphrase is checked against a stored verifier (never the passphrase
    itself). The client decrypts the returned ciphertext locally.
    """
    try:
        backup = service.restore_backup(current_user, backup_id, payload.passphrase)
    except WalletBackupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except WalletPassphraseError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    return WalletRestoreResponse(
        id=backup.id,
        label=backup.label,
        ciphertext=backup.ciphertext,
        salt=backup.salt,
        kdf_iterations=backup.kdf_iterations,
        restored_at=backup.last_restored_at,
    )


@router.put("/{backup_id}", response_model=WalletBackupMeta)
async def update_wallet_backup(
    backup_id: int,
    payload: WalletBackupUpdate,
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_service),
):
    """Update backup metadata (label and/or public address)."""
    try:
        return service.update_backup(
            current_user,
            backup_id,
            label=payload.label,
            wallet_address=payload.wallet_address,
        )
    except WalletBackupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.delete("/{backup_id}")
async def delete_wallet_backup(
    backup_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_service),
):
    """Soft-delete a wallet backup."""
    try:
        service.delete_backup(current_user, backup_id)
    except WalletBackupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": "Wallet backup deleted successfully"}
