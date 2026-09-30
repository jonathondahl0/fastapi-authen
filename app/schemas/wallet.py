import json
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, validator

WALLET_TYPES = ("hd", "imported", "watch_only")


def validate_evm_address(value: Optional[str]) -> Optional[str]:
    """Validate an EVM-style address (0x + 40 hex chars) when it looks EVM-ish.

    Non-EVM identifiers are accepted as-is so other chains can be registered.
    """
    if value is not None:
        value = value.strip()
        if value == "":
            return None
        if value.lower().startswith("0x"):
            if len(value) != 42:
                raise ValueError("EVM address must be 0x followed by 40 hex characters")
            if not all(c in "0123456789abcdefABCDEF" for c in value[2:]):
                raise ValueError(
                    "EVM address must contain only hex characters after 0x"
                )
    return value


def validate_hex_salt(value: str) -> str:
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        raise ValueError("salt must be a hex-encoded string")
    if len(raw) < 8:
        raise ValueError("salt must be at least 8 bytes")
    return value


# ---------------------------------------------------------------------- #
# Wallet registry
# ---------------------------------------------------------------------- #
class WalletBackupInline(BaseModel):
    """Encrypted backup supplied together with a new wallet registration."""

    encrypted_data: str = Field(
        ..., min_length=1, description="Fernet-encrypted wallet payload (client-side)"
    )
    salt: str = Field(
        ..., min_length=16, max_length=128, description="Hex-encoded KDF salt"
    )
    kdf_iterations: int = Field(200_000, ge=100_000, le=10_000_000)
    passphrase: str = Field(..., min_length=8, max_length=256)
    label: Optional[str] = Field(None, min_length=1, max_length=100)

    @validator("salt")
    def check_salt(cls, v):
        return validate_hex_salt(v)


class WalletCreate(BaseModel):
    """Register a wallet. Optionally attach its encrypted backup atomically."""

    label: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1, max_length=128)
    chain: str = Field("huanchain", min_length=1, max_length=50)
    wallet_type: str = Field("hd", description="One of: hd, imported, watch_only")
    description: Optional[str] = Field(None, max_length=500)
    make_primary: bool = False
    backup: Optional[WalletBackupInline] = None

    @validator("wallet_type")
    def check_wallet_type(cls, v):
        if v not in WALLET_TYPES:
            raise ValueError(f"wallet_type must be one of: {', '.join(WALLET_TYPES)}")
        return v

    @validator("address")
    def check_address(cls, v):
        v = validate_evm_address(v)
        if not v:
            raise ValueError("address is required")
        return v

    @validator("chain")
    def check_chain(cls, v):
        v = v.strip().lower()
        if not v:
            raise ValueError("chain is required")
        return v


class WalletUpdate(BaseModel):
    """Update wallet metadata (address and chain are immutable)."""

    label: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class WalletMeta(BaseModel):
    """Wallet registry entry with computed backup aggregates."""

    id: int
    label: str
    address: str
    chain: str
    wallet_type: str
    description: Optional[str] = None
    is_primary: bool
    backup_count: int = 0
    last_backup_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------- #
# Wallet backups
# ---------------------------------------------------------------------- #
class WalletBackupCreate(BaseModel):
    """Create a standalone encrypted backup, optionally linked to a wallet.

    The client encrypts the wallet payload (private key / mnemonic) locally
    with a passphrase-derived Fernet key and submits the ciphertext together
    with the KDF parameters it used. The server never sees the plaintext and
    never stores the passphrase — only a verifier derived from it.
    """

    label: str = Field(..., min_length=1, max_length=100, description="Backup label")
    wallet_id: Optional[int] = Field(
        None, description="Link the backup to one of the user's registered wallets"
    )
    wallet_address: Optional[str] = Field(
        None, max_length=128, description="Public wallet address (optional)"
    )
    encrypted_data: str = Field(
        ..., min_length=1, description="Fernet-encrypted wallet payload (base64)"
    )
    salt: str = Field(
        ..., min_length=16, max_length=128, description="Hex-encoded KDF salt"
    )
    kdf_iterations: int = Field(200_000, ge=100_000, le=10_000_000)
    passphrase: str = Field(..., min_length=8, max_length=256)

    @validator("wallet_address")
    def check_address(cls, v):
        return validate_evm_address(v)

    @validator("salt")
    def check_salt(cls, v):
        return validate_hex_salt(v)


class WalletBackupUpdate(BaseModel):
    """Update backup metadata (not the encrypted data)."""

    label: Optional[str] = Field(None, min_length=1, max_length=100)
    wallet_address: Optional[str] = Field(None, max_length=128)


class WalletBackupMeta(BaseModel):
    """Backup metadata returned in listings (never exposes ciphertext)."""

    id: int
    wallet_id: Optional[int] = None
    label: str
    wallet_address: Optional[str] = None
    kdf_iterations: int
    salt: str
    is_active: bool
    last_restored_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WalletBackupResponse(WalletBackupMeta):
    """Full backup record including ciphertext (for restore flows)."""

    ciphertext: str


class WalletCreateResponse(BaseModel):
    """Result of registering a wallet (with its backup, if provided)."""

    wallet: WalletMeta
    backup: Optional[WalletBackupMeta] = None


class WalletRestoreRequest(BaseModel):
    """Request to restore a wallet backup.

    The client decrypts the returned ciphertext locally with the
    passphrase-derived key. The verifier is used server-side only to reject
    wrong passphrases without exposing the wallet data.
    """

    passphrase: str = Field(..., min_length=8, max_length=256)


class WalletRestoreResponse(BaseModel):
    """Restore response with everything the client needs to decrypt locally."""

    id: int
    label: str
    ciphertext: str
    salt: str
    kdf_iterations: int
    restored_at: datetime


# ---------------------------------------------------------------------- #
# Activity & stats
# ---------------------------------------------------------------------- #
class WalletActivityResponse(BaseModel):
    """One entry from the wallet audit trail."""

    id: int
    wallet_id: Optional[int] = None
    action: str
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


class WalletStatsResponse(BaseModel):
    """Aggregate wallet statistics for dashboards."""

    total_wallets: int
    total_backups: int
    primary_wallet_id: Optional[int] = None
    by_chain: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
