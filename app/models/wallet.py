from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class WalletBackup(Base):
    """Encrypted wallet backup stored for a user.

    Security notes:
    - The wallet data (private key / mnemonic) is never stored in plaintext.
    - It is encrypted client-side of the API boundary using a passphrase-derived
      key (PBKDF2-SHA256) and a Fernet (AES-128-CBC + HMAC) token.
    - The server stores only the ciphertext, its salt, and metadata.
    - The passphrase itself is never persisted; a password verifier hash is
      stored so the API can reject wrong-passphrase restore attempts without
      being able to decrypt the backup itself.
    """

    __tablename__ = "wallet_backups"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

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
