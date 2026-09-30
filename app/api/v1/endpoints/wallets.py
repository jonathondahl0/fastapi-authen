from fastapi import APIRouter, Depends, HTTPException, status
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

router = APIRouter()


def _get_service(db: Session = Depends(get_db)) -> WalletBackupService:
    return WalletBackupService(db)


@router.post("/", response_model=WalletBackupMeta, status_code=status.HTTP_201_CREATED)
async def create_wallet_backup(
    payload: WalletBackupCreate,
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_get_service),
):
    """Create an encrypted wallet backup for the authenticated user."""
    try:
        backup = service.create_backup(
            user=current_user,
            label=payload.label,
            encrypted_data=payload.encrypted_data,
            passphrase=payload.passphrase,
            salt=payload.salt,
            kdf_iterations=payload.kdf_iterations,
            wallet_address=payload.wallet_address,
        )
    except WalletBackupLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except WalletBackupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return backup


@router.get("/", response_model=list[WalletBackupMeta])
async def list_wallet_backups(
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_get_service),
):
    """List all active wallet backups for the authenticated user."""
    return service.list_backups(current_user)


@router.get("/{backup_id}", response_model=WalletBackupResponse)
async def get_wallet_backup(
    backup_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: WalletBackupService = Depends(_get_service),
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
    service: WalletBackupService = Depends(_get_service),
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
    service: WalletBackupService = Depends(_get_service),
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
    service: WalletBackupService = Depends(_get_service),
):
    """Soft-delete a wallet backup."""
    try:
        service.delete_backup(current_user, backup_id)
    except WalletBackupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": "Wallet backup deleted successfully"}
