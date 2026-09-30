from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_superuser
from app.models.user import User as UserModel
from app.schemas.system_wallet import (SystemWalletCreate,
                                       SystemWalletResponse,
                                       SystemWalletUpdate,
                                       WithdrawalApproveRequest,
                                       WithdrawalCancelRequest,
                                       WithdrawalConfirmRequest,
                                       WithdrawalCreate,
                                       WithdrawalEventResponse,
                                       WithdrawalFailRequest,
                                       WithdrawalResponse,
                                       WithdrawalSubmitRequest)
from app.services.system_wallet import (SystemWalletAlreadyExists,
                                        SystemWalletConfigError,
                                        SystemWalletError,
                                        SystemWalletNotFound,
                                        SystemWalletService,
                                        WithdrawalLimitError,
                                        WithdrawalService,
                                        WithdrawalStateError)

router = APIRouter()


def _wallet_service(db: Session = Depends(get_db)) -> SystemWalletService:
    return SystemWalletService(db)


def _withdrawal_service(db: Session = Depends(get_db)) -> WithdrawalService:
    return WithdrawalService(db)


def _raise_http(exc: SystemWalletError):
    """Map service errors to HTTP responses."""
    if isinstance(exc, SystemWalletNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, SystemWalletAlreadyExists):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, WithdrawalLimitError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, WithdrawalStateError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, SystemWalletConfigError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ---------------------------------------------------------------------- #
# System wallet registry (admin only)
# ---------------------------------------------------------------------- #
@router.post(
    "/system-wallets",
    response_model=SystemWalletResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_system_wallet(
    payload: SystemWalletCreate,
    current_admin: UserModel = Depends(get_current_superuser),
    service: SystemWalletService = Depends(_wallet_service),
):
    """Register a server-operated wallet (private keys stay in external custody)."""
    try:
        return service.create_system_wallet(
            label=payload.label,
            address=payload.address,
            chain=payload.chain,
            description=payload.description,
            signer_reference=payload.signer_reference,
        )
    except SystemWalletError as exc:
        _raise_http(exc)


@router.get("/system-wallets", response_model=List[SystemWalletResponse])
async def list_system_wallets(
    include_inactive: bool = Query(False),
    current_admin: UserModel = Depends(get_current_superuser),
    service: SystemWalletService = Depends(_wallet_service),
):
    """List system wallets."""
    return service.list_system_wallets(include_inactive=include_inactive)


@router.get("/system-wallets/{wallet_id}", response_model=SystemWalletResponse)
async def get_system_wallet(
    wallet_id: int,
    current_admin: UserModel = Depends(get_current_superuser),
    service: SystemWalletService = Depends(_wallet_service),
):
    """Fetch one system wallet."""
    try:
        return service.get_system_wallet(wallet_id)
    except SystemWalletError as exc:
        _raise_http(exc)


@router.put("/system-wallets/{wallet_id}", response_model=SystemWalletResponse)
async def update_system_wallet(
    wallet_id: int,
    payload: SystemWalletUpdate,
    current_admin: UserModel = Depends(get_current_superuser),
    service: SystemWalletService = Depends(_wallet_service),
):
    """Update system wallet metadata."""
    try:
        return service.update_system_wallet(
            wallet_id,
            label=payload.label,
            description=payload.description,
            signer_reference=payload.signer_reference,
        )
    except SystemWalletError as exc:
        _raise_http(exc)


@router.delete("/system-wallets/{wallet_id}")
async def deactivate_system_wallet(
    wallet_id: int,
    current_admin: UserModel = Depends(get_current_superuser),
    service: SystemWalletService = Depends(_wallet_service),
):
    """Deactivate a system wallet (soft delete). Existing withdrawals are kept."""
    try:
        service.deactivate_system_wallet(wallet_id)
    except SystemWalletError as exc:
        _raise_http(exc)
    return {"message": "System wallet deactivated successfully"}


# ---------------------------------------------------------------------- #
# Withdrawals (admin only)
# ---------------------------------------------------------------------- #
@router.post(
    "/system-wallets/{wallet_id}/withdrawals",
    response_model=WithdrawalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_withdrawal(
    wallet_id: int,
    payload: WithdrawalCreate,
    response: Response,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Request a withdrawal from a system wallet (starts in ``pending``).

    Replays with the same ``idempotency_key`` return the original request (200).
    """
    try:
        withdrawal, created = service.create_withdrawal(
            actor=current_admin,
            system_wallet_id=wallet_id,
            to_address=payload.to_address,
            amount=str(payload.amount),
            asset=payload.asset,
            note=payload.note,
            idempotency_key=payload.idempotency_key,
        )
    except SystemWalletError as exc:
        _raise_http(exc)
    if not created:
        response.status_code = status.HTTP_200_OK
    return withdrawal


@router.get("/withdrawals", response_model=List[WithdrawalResponse])
async def list_withdrawals(
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by lifecycle status"
    ),
    system_wallet_id: Optional[int] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """List withdrawal requests (newest first)."""
    return service.list_withdrawals(
        status=status_filter,
        system_wallet_id=system_wallet_id,
        limit=limit,
        offset=offset,
    )


@router.get("/withdrawals/{withdrawal_id}", response_model=WithdrawalResponse)
async def get_withdrawal(
    withdrawal_id: int,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Fetch one withdrawal request."""
    try:
        return service.get_withdrawal(withdrawal_id)
    except SystemWalletError as exc:
        _raise_http(exc)


@router.post("/withdrawals/{withdrawal_id}/approve", response_model=WithdrawalResponse)
async def approve_withdrawal(
    withdrawal_id: int,
    payload: Optional[WithdrawalApproveRequest] = None,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Approve a pending withdrawal (dual approval may require a second admin)."""
    try:
        return service.approve_withdrawal(
            current_admin, withdrawal_id, note=payload.note if payload else None
        )
    except SystemWalletError as exc:
        _raise_http(exc)


@router.post("/withdrawals/{withdrawal_id}/cancel", response_model=WithdrawalResponse)
async def cancel_withdrawal(
    withdrawal_id: int,
    payload: Optional[WithdrawalCancelRequest] = None,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Cancel a pending or approved withdrawal."""
    try:
        return service.cancel_withdrawal(
            current_admin, withdrawal_id, reason=payload.reason if payload else None
        )
    except SystemWalletError as exc:
        _raise_http(exc)


@router.post("/withdrawals/{withdrawal_id}/submit", response_model=WithdrawalResponse)
async def submit_withdrawal(
    withdrawal_id: int,
    payload: Optional[WithdrawalSubmitRequest] = None,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Execute an approved withdrawal.

    Simulation mode auto-confirms with a simulated tx hash. Manual mode
    requires ``tx_hash`` from an externally-signed transaction.
    """
    try:
        return service.submit_withdrawal(
            current_admin, withdrawal_id, tx_hash=payload.tx_hash if payload else None
        )
    except SystemWalletError as exc:
        _raise_http(exc)


@router.post("/withdrawals/{withdrawal_id}/confirm", response_model=WithdrawalResponse)
async def confirm_withdrawal(
    withdrawal_id: int,
    payload: Optional[WithdrawalConfirmRequest] = None,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Record on-chain confirmation of a submitted withdrawal."""
    try:
        return service.confirm_withdrawal(
            current_admin, withdrawal_id, tx_hash=payload.tx_hash if payload else None
        )
    except SystemWalletError as exc:
        _raise_http(exc)


@router.post("/withdrawals/{withdrawal_id}/fail", response_model=WithdrawalResponse)
async def fail_withdrawal(
    withdrawal_id: int,
    payload: WithdrawalFailRequest,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Mark an approved or submitted withdrawal as failed."""
    try:
        return service.fail_withdrawal(
            current_admin, withdrawal_id, error=payload.error
        )
    except SystemWalletError as exc:
        _raise_http(exc)


@router.get(
    "/withdrawals/{withdrawal_id}/events", response_model=List[WithdrawalEventResponse]
)
async def withdrawal_events(
    withdrawal_id: int,
    current_admin: UserModel = Depends(get_current_superuser),
    service: WithdrawalService = Depends(_withdrawal_service),
):
    """Audit trail for a withdrawal request."""
    try:
        return service.get_events(withdrawal_id)
    except SystemWalletError as exc:
        _raise_http(exc)
