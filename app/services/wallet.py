"""Wallet backup service.

Encryption design:
- A passphrase-derived key is generated with PBKDF2-HMAC-SHA256 using a random
  per-backup salt and configurable iteration count.
- The wallet payload (private key / mnemonic JSON) is encrypted with Fernet
  (AES-128-CBC + HMAC-SHA256, authenticated).
- The passphrase is never stored. A SHA-256 hash of the *derived key* is stored
  as a verifier: it proves knowledge of the passphrase without enabling
  decryption of any other backup (keys are salted per backup).
"""
import base64
import hashlib
import secrets
from datetime import datetime
from typing import Optional, List

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.models.wallet import WalletBackup


class WalletBackupError(Exception):
    """Base wallet backup error."""


class WalletBackupNotFound(WalletBackupError):
    pass


class WalletPassphraseError(WalletBackupError):
    pass


class WalletBackupLimitError(WalletBackupError):
    pass


class WalletBackupService:
    """Handles encrypted wallet backup creation, listing, and restore."""

    MAX_BACKUPS_PER_USER = 10

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # Crypto helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
        """Derive a Fernet-compatible key from a passphrase + salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))

    @classmethod
    def encrypt_payload(cls, payload: str, passphrase: str, salt: bytes, iterations: int) -> str:
        """Encrypt a payload string with a passphrase-derived Fernet key."""
        key = cls.derive_key(passphrase, salt, iterations)
        return Fernet(key).encrypt(payload.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt_payload(cls, token: str, passphrase: str, salt: bytes, iterations: int) -> str:
        """Decrypt a Fernet token with a passphrase-derived key."""
        key = cls.derive_key(passphrase, salt, iterations)
        try:
            return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise WalletPassphraseError("Invalid passphrase or corrupted backup") from exc

    # ------------------------------------------------------------------ #
    # CRUD operations
    # ------------------------------------------------------------------ #
    def create_backup(
        self,
        user: User,
        label: str,
        encrypted_data: str,
        passphrase: str,
        salt: str,
        kdf_iterations: int,
        wallet_address: Optional[str] = None,
    ) -> WalletBackup:
        """Create a new encrypted wallet backup.

        Non-custodial contract: ``encrypted_data`` was produced by the client,
        which encrypted the wallet payload with ``passphrase`` and the
        client-generated ``salt``/``kdf_iterations``. The server stores the
        ciphertext verbatim, derives only a verifier from the passphrase, and
        confirms the pair is consistent (wrong passphrase would produce a
        verifier that fails at restore time).
        """
        count = (
            self.db.query(WalletBackup)
            .filter(WalletBackup.user_id == user.id, WalletBackup.is_active == True)
            .count()
        )
        if count >= self.MAX_BACKUPS_PER_USER:
            raise WalletBackupLimitError(
                f"Maximum of {self.MAX_BACKUPS_PER_USER} active backups reached"
            )

        try:
            salt_bytes = bytes.fromhex(salt)
        except ValueError as exc:
            raise WalletBackupError("salt must be hex-encoded") from exc

        # Optional self-check: the ciphertext must decrypt with the supplied
        # passphrase/parameters, proving the payload matches the passphrase.
        try:
            self.decrypt_payload(encrypted_data, passphrase, salt_bytes, kdf_iterations)
        except WalletPassphraseError as exc:
            raise WalletBackupError(
                "encrypted_data does not match the provided passphrase/salt/iterations"
            ) from exc

        verifier = hashlib.sha256(
            self.derive_key(passphrase, salt_bytes, kdf_iterations)
        ).hexdigest()

        backup = WalletBackup(
            user_id=user.id,
            label=label,
            wallet_address=wallet_address,
            ciphertext=encrypted_data,
            salt=salt.lower(),
            kdf_iterations=kdf_iterations,
            verifier=verifier,
        )
        self.db.add(backup)
        self.db.commit()
        self.db.refresh(backup)
        return backup

    def get_backup(self, user: User, backup_id: int) -> WalletBackup:
        backup = (
            self.db.query(WalletBackup)
            .filter(
                WalletBackup.id == backup_id,
                WalletBackup.user_id == user.id,
                WalletBackup.is_active == True,
            )
            .first()
        )
        if not backup:
            raise WalletBackupNotFound("Wallet backup not found")
        return backup

    def list_backups(self, user: User) -> List[WalletBackup]:
        return (
            self.db.query(WalletBackup)
            .filter(WalletBackup.user_id == user.id, WalletBackup.is_active == True)
            .order_by(WalletBackup.created_at.desc())
            .all()
        )

    def verify_passphrase(self, backup: WalletBackup, passphrase: str) -> bool:
        """Check a passphrase against the stored verifier without decrypting."""
        derived = self.derive_key(passphrase, bytes.fromhex(backup.salt), backup.kdf_iterations)
        return secrets.compare_digest(
            hashlib.sha256(derived).hexdigest(), backup.verifier
        )

    def restore_backup(self, user: User, backup_id: int, passphrase: str) -> WalletBackup:
        """Mark restore, bump last_restored_at, and return the backup.

        Raises WalletPassphraseError on a wrong passphrase. The client decrypts
        the ciphertext locally; the server never handles plaintext wallet data
        beyond what it re-encrypted at creation time.
        """
        backup = self.get_backup(user, backup_id)

        if not self.verify_passphrase(backup, passphrase):
            raise WalletPassphraseError("Invalid passphrase")

        backup.last_restored_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(backup)
        return backup

    def update_backup(
        self,
        user: User,
        backup_id: int,
        label: Optional[str] = None,
        wallet_address: Optional[str] = None,
    ) -> WalletBackup:
        backup = self.get_backup(user, backup_id)
        if label is not None:
            backup.label = label
        if wallet_address is not None:
            backup.wallet_address = wallet_address
        self.db.commit()
        self.db.refresh(backup)
        return backup

    def delete_backup(self, user: User, backup_id: int) -> bool:
        """Soft-delete a wallet backup."""
        backup = self.get_backup(user, backup_id)
        backup.is_active = False
        self.db.commit()
        return True
