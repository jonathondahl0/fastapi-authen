import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, validator

from app.schemas.wallet import validate_evm_address


def _check_evm(value: str) -> str:
    checked = validate_evm_address(value)
    if not checked:
        raise ValueError("address is required")
    return checked


# ---------------------------------------------------------------------- #
# System wallet registry
# ---------------------------------------------------------------------- #
class SystemWalletCreate(BaseModel):
    """Register a server-operated wallet (keys stay in external custody)."""

    label: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1, max_length=128)
    chain: str = Field("huanchain", min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=500)
    signer_reference: Optional[str] = Field(
        None,
        max_length=255,
        description="Where signing happens (KMS key id, HSM slot, custody reference)",
    )

    @validator("address")
    def check_address(cls, v):
        return _check_evm(v)

    @validator("chain")
    def check_chain(cls, v):
        v = v.strip().lower()
        if not v:
            raise ValueError("chain is required")
        return v


class SystemWalletUpdate(BaseModel):
    """Update system wallet metadata (address and chain are immutable)."""

    label: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    signer_reference: Optional[str] = Field(None, max_length=255)


class SystemWalletResponse(BaseModel):
    """System wallet returned by the admin API."""

    id: int
    label: str
    address: str
    chain: str
    description: Optional[str] = None
    signer_reference: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------- #
# Withdrawals
# ---------------------------------------------------------------------- #
class WithdrawalCreate(BaseModel):
    """Request a withdrawal from a system wallet."""

    to_address: str = Field(..., min_length=1, max_length=128)
    amount: Decimal = Field(
        ...,
        gt=0,
        max_digits=78,
        decimal_places=18,
        description="Positive decimal amount",
    )
    asset: str = Field(
        "native",
        max_length=128,
        description='"native" for the chain coin, or a token contract address',
    )
    note: Optional[str] = Field(None, max_length=500)
    idempotency_key: Optional[str] = Field(
        None,
        min_length=8,
        max_length=128,
        description="Replays return the original request",
    )

    @validator("to_address")
    def check_to_address(cls, v):
        return _check_evm(v)

    @validator("asset")
    def check_asset(cls, v):
        v = v.strip()
        if v.lower() == "native":
            return "native"
        return _check_evm(v)


class WithdrawalApproveRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=500)


class WithdrawalSubmitRequest(BaseModel):
    """Submit for execution.

    In manual mode (production default), ``tx_hash`` is required: the operator
    signs externally and records the transaction hash here.
    """

    tx_hash: Optional[str] = Field(None, min_length=4, max_length=128)


class WithdrawalConfirmRequest(BaseModel):
    tx_hash: Optional[str] = Field(None, min_length=4, max_length=128)


class WithdrawalFailRequest(BaseModel):
    error: str = Field(..., min_length=1, max_length=1000)


class WithdrawalCancelRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class WithdrawalResponse(BaseModel):
    """Withdrawal request with full lifecycle state."""

    id: int
    system_wallet_id: int
    chain: str
    asset: str
    to_address: str
    amount: str
    status: str
    requested_by_user_id: int
    approved_by_user_id: Optional[int] = None
    tx_hash: Optional[str] = None
    error: Optional[str] = None
    note: Optional[str] = None
    simulated: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WithdrawalEventResponse(BaseModel):
    """One audit event in a withdrawal's lifecycle."""

    id: int
    withdrawal_id: int
    action: str
    actor_user_id: Optional[int] = None
    detail: Optional[Dict[str, Any]] = None
    created_at: datetime

    @field_validator("detail", mode="before")
    @classmethod
    def parse_detail(cls, v):
        if isinstance(v, str) and v:
            try:
                return json.loads(v)
            except ValueError:
                return {"raw": v}
        return v

    class Config:
        from_attributes = True
