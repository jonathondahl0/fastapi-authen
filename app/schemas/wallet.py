from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator


class WalletBackupCreate(BaseModel):
    """Payload for creating an encrypted wallet backup.

    The client encrypts the wallet payload (private key / mnemonic) locally
    with a passphrase-derived Fernet key and submits the ciphertext together
    with the KDF parameters it used. The server never sees the plaintext and
    never stores the passphrase — only a verifier derived from it.
    """

    label: str = Field(..., min_length=1, max_length=100, description="Wallet label")
    wallet_address: Optional[str] = Field(
        None, max_length=128, description="Public wallet address (optional)"
    )
    encrypted_data: str = Field(
        ...,
        min_length=1,
        description="Fernet-encrypted wallet payload (base64), encrypted client-side",
    )
    salt: str = Field(
        ...,
        min_length=16,
        max_length=128,
        description="Hex-encoded salt used for PBKDF2 (client-generated)",
    )
    kdf_iterations: int = Field(
        200_000,
        ge=100_000,
        le=10_000_000,
        description="PBKDF2 iteration count used by the client",
    )
    passphrase: str = Field(
        ...,
        min_length=8,
        max_length=256,
        description="Passphrase used to derive the encryption key (never stored)",
    )

    @validator("wallet_address")
    def validate_wallet_address(cls, v):
        if v is not None:
            v = v.strip()
            if v == "":
                return None
            # EVM-style addresses: 0x + 40 hex chars
            if v.lower().startswith("0x"):
                if len(v) != 42:
                    raise ValueError(
                        "EVM address must be 0x followed by 40 hex characters"
                    )
                if not all(c in "0123456789abcdefABCDEF" for c in v[2:]):
                    raise ValueError(
                        "EVM address must contain only hex characters after 0x"
                    )
        return v

    @validator("salt")
    def validate_salt(cls, v):
        try:
            raw = bytes.fromhex(v)
        except ValueError:
            raise ValueError("salt must be a hex-encoded string")
        if len(raw) < 8:
            raise ValueError("salt must be at least 8 bytes")
        return v


class WalletBackupUpdate(BaseModel):
    """Payload for updating wallet backup metadata (not the encrypted data)."""

    label: Optional[str] = Field(None, min_length=1, max_length=100)
    wallet_address: Optional[str] = Field(None, max_length=128)


class WalletBackupMeta(BaseModel):
    """Wallet backup metadata returned in listings (never exposes ciphertext)."""

    id: int
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


class WalletRestoreRequest(BaseModel):
    """Request to restore a wallet backup.

    The client downloads the ciphertext, then decrypts locally with the
    passphrase-derived key. The verifier is used server-side only to reject
    obviously wrong passphrases without exposing the wallet data.
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
