from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String,
                        Text)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base

# Supported wallet types:
# - "hd": HD wallet managed by the client (seed phrase)
# - "imported": wallet imported from a private key / keystore
# - "watch_only": public address only, no key material anywhere
WALLET_TYPES = ("hd", "imported", "watch_only")


class Wallet(Base):
    """Public registry entry for a user's wallet.

    Non-custodial by design: this table stores only public metadata (address,
    label, chain, type). Key material never lives here — it is kept
    exclusively in an encrypted WalletBackup the server cannot decrypt.
    """

    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    label = Column(String, nullable=False, default="Main Wallet")
    address = Column(String, nullable=False, index=True)
    chain = Column(String, nullable=False, default="huanchain", index=True)
    wallet_type = Column(String, nullable=False, default="hd")
    description = Column(Text)

    is_primary = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="wallets")
    backups = relationship("WalletBackup", back_populates="wallet")
    activities = relationship("WalletActivity", back_populates="wallet")


class WalletBackup(Base):
    """Encrypted wallet backup stored for a user.

    Security notes:
    - The wallet data (private key / mnemonic) is never stored in plaintext.
    - It is encrypted client-side of the API boundary using a passphrase-derived
      key (PBKDF2-SHA256) and a Fernet (AES-128-CBC + HMAC) token.
    - The server stores only the ciphertext, its salt, and metadata.
    - The passphrase itself is never persisted; a derived-key verifier hash is
      stored so the API can reject wrong-passphrase restore attempts without
      being able to decrypt the backup itself.
    """

    __tablename__ = "wallet_backups"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=True, index=True)

    # Wallet metadata
    label = Column(String, nullable=False, default="Main Wallet")
    wallet_address = Column(String, index=True)  # Public address (not secret)

    # Encryption material
    ciphertext = Column(Text, nullable=False)  # Fernet token (base64)
    salt = Column(String, nullable=False)  # Hex-encoded random salt for PBKDF2
    kdf_iterations = Column(Integer, nullable=False, default=200_000)
    verifier = Column(String, nullable=False)  # SHA-256 hash of the derived key

    # Lifecycle
    is_active = Column(Boolean, default=True)
    last_restored_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="wallet_backups")
    wallet = relationship("Wallet", back_populates="backups")


class WalletActivity(Base):
    """Append-only audit trail of wallet and backup operations."""

    __tablename__ = "wallet_activities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=True, index=True)

    action = Column(String(50), nullable=False, index=True)
    detail = Column(Text)  # JSON-encoded context
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User")
    wallet = relationship("Wallet", back_populates="activities")
