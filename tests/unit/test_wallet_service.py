"""Unit tests for the wallet backup service."""
import pytest

from app.services.wallet import (WalletBackupError, WalletBackupLimitError,
                                 WalletBackupNotFound, WalletBackupService,
                                 WalletPassphraseError)

SALT = "a1b2c3d4e5f60718"
ITER = 100_000


def enc(payload: str, passphrase: str) -> str:
    """Encrypt like a client would, with fixed test KDF parameters."""
    return WalletBackupService.encrypt_payload(
        payload, passphrase, bytes.fromhex(SALT), ITER
    )


@pytest.fixture
def wallet_service(test_db):
    return WalletBackupService(test_db)


@pytest.fixture
def sample_passphrase():
    return "correct-horse-battery-staple"


class TestCryptoHelpers:
    """Test encryption/decryption helpers."""

    def test_derive_key_is_deterministic(self, sample_passphrase):
        salt = b"0123456789abcdef"
        k1 = WalletBackupService.derive_key(sample_passphrase, salt, 10_000)
        k2 = WalletBackupService.derive_key(sample_passphrase, salt, 10_000)
        assert k1 == k2

    def test_derive_key_changes_with_salt(self, sample_passphrase):
        k1 = WalletBackupService.derive_key(
            sample_passphrase, b"salt-one---------", 10_000
        )
        k2 = WalletBackupService.derive_key(
            sample_passphrase, b"salt-two---------", 10_000
        )
        assert k1 != k2

    def test_encrypt_decrypt_roundtrip(self, sample_passphrase):
        payload = '{"mnemonic": "test words here", "private_key": "0xabc"}'
        salt = b"0123456789abcdef"
        token = WalletBackupService.encrypt_payload(
            payload, sample_passphrase, salt, 10_000
        )

        assert token != payload  # actually encrypted

        decrypted = WalletBackupService.decrypt_payload(
            token, sample_passphrase, salt, 10_000
        )
        assert decrypted == payload

    def test_decrypt_wrong_passphrase_raises(self):
        payload = "secret wallet data"
        salt = b"0123456789abcdef"
        token = WalletBackupService.encrypt_payload(
            payload, "right-passphrase", salt, 10_000
        )

        with pytest.raises(WalletPassphraseError):
            WalletBackupService.decrypt_payload(token, "wrong-passphrase", salt, 10_000)

    def test_decrypt_tampered_token_raises(self, sample_passphrase):
        salt = b"0123456789abcdef"
        token = WalletBackupService.encrypt_payload(
            "data", sample_passphrase, salt, 10_000
        )
        tampered = token[:-4] + "AAAA"

        with pytest.raises(WalletPassphraseError):
            WalletBackupService.decrypt_payload(
                tampered, sample_passphrase, salt, 10_000
            )


@pytest.mark.unit
class TestWalletBackupService:
    """Test wallet backup CRUD operations."""

    def test_create_backup(self, wallet_service, test_user, sample_passphrase):
        backup = wallet_service.create_backup(
            user=test_user,
            label="Main Wallet",
            encrypted_data=enc("my-secret-seed-phrase", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
            wallet_address="0x" + "a" * 40,
        )

        assert backup.id is not None
        assert backup.user_id == test_user.id
        assert backup.label == "Main Wallet"
        assert backup.ciphertext != "my-secret-seed-phrase"
        assert backup.salt
        assert backup.verifier
        assert backup.is_active is True

    def test_create_backup_stores_no_plaintext(
        self, wallet_service, test_user, sample_passphrase
    ):
        secret = "super-secret-mnemonic"
        backup = wallet_service.create_backup(
            user=test_user,
            label="Wallet",
            encrypted_data=enc(secret, sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )

        assert secret not in backup.ciphertext
        assert sample_passphrase not in backup.ciphertext
        assert sample_passphrase not in (backup.verifier or "")

    def test_create_backup_rejects_mismatched_passphrase(
        self, wallet_service, test_user, sample_passphrase
    ):
        """Ciphertext encrypted with a different passphrase must be rejected."""
        encrypted = enc("secret", "a-different-passphrase")

        with pytest.raises(WalletBackupError):
            wallet_service.create_backup(
                user=test_user,
                label="Wallet",
                encrypted_data=encrypted,
                passphrase=sample_passphrase,
                salt=SALT,
                kdf_iterations=ITER,
            )

    def test_verify_passphrase_correct(
        self, wallet_service, test_user, sample_passphrase
    ):
        backup = wallet_service.create_backup(
            user=test_user,
            label="W",
            encrypted_data=enc("data", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )
        assert wallet_service.verify_passphrase(backup, sample_passphrase) is True

    def test_verify_passphrase_incorrect(
        self, wallet_service, test_user, sample_passphrase
    ):
        backup = wallet_service.create_backup(
            user=test_user,
            label="W",
            encrypted_data=enc("data", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )
        assert wallet_service.verify_passphrase(backup, "wrong-passphrase") is False

    def test_restore_with_correct_passphrase(
        self, wallet_service, test_user, sample_passphrase
    ):
        backup = wallet_service.create_backup(
            user=test_user,
            label="W",
            encrypted_data=enc("data", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )
        assert backup.last_restored_at is None

        restored = wallet_service.restore_backup(
            test_user, backup.id, sample_passphrase
        )
        assert restored.id == backup.id
        assert restored.last_restored_at is not None

    def test_restore_with_wrong_passphrase_raises(
        self, wallet_service, test_user, sample_passphrase
    ):
        backup = wallet_service.create_backup(
            user=test_user,
            label="W",
            encrypted_data=enc("data", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )
        with pytest.raises(WalletPassphraseError):
            wallet_service.restore_backup(test_user, backup.id, "wrong-passphrase-123")

    def test_restore_nonexistent_raises(
        self, wallet_service, test_user, sample_passphrase
    ):
        with pytest.raises(WalletBackupNotFound):
            wallet_service.restore_backup(test_user, 99999, sample_passphrase)

    def test_cannot_access_other_users_backup(
        self, wallet_service, test_user, sample_passphrase
    ):
        from app.core.security import get_password_hash
        from app.models.user import User

        other_user = User(
            email="other@example.com",
            username="otheruser",
            hashed_password=get_password_hash("OtherPass123!"),
            is_active=True,
        )
        wallet_service.db.add(other_user)
        wallet_service.db.commit()
        wallet_service.db.refresh(other_user)

        backup = wallet_service.create_backup(
            user=other_user,
            label="Other Wallet",
            encrypted_data=enc("data", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )

        with pytest.raises(WalletBackupNotFound):
            wallet_service.get_backup(test_user, backup.id)

    def test_list_backups_orders_and_filters(
        self, wallet_service, test_user, sample_passphrase
    ):
        b1 = wallet_service.create_backup(
            user=test_user,
            label="First",
            encrypted_data=enc("d1", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )
        b2 = wallet_service.create_backup(
            user=test_user,
            label="Second",
            encrypted_data=enc("d2", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )

        listings = wallet_service.list_backups(test_user)
        assert {b.id for b in listings} == {b1.id, b2.id}

        # Soft-delete one and confirm it disappears
        wallet_service.delete_backup(test_user, b1.id)
        listings = wallet_service.list_backups(test_user)
        assert [b.id for b in listings] == [b2.id]

    def test_max_backups_limit(
        self, wallet_service, test_user, sample_passphrase, monkeypatch
    ):
        monkeypatch.setattr(WalletBackupService, "MAX_BACKUPS_PER_USER", 2)

        wallet_service.create_backup(
            user=test_user,
            label="A",
            encrypted_data=enc("d", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )
        wallet_service.create_backup(
            user=test_user,
            label="B",
            encrypted_data=enc("d", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )

        with pytest.raises(WalletBackupLimitError):
            wallet_service.create_backup(
                user=test_user,
                label="C",
                encrypted_data=enc("d", sample_passphrase),
                passphrase=sample_passphrase,
                salt=SALT,
                kdf_iterations=ITER,
            )

    def test_update_backup_metadata(self, wallet_service, test_user, sample_passphrase):
        backup = wallet_service.create_backup(
            user=test_user,
            label="Old",
            encrypted_data=enc("d", sample_passphrase),
            passphrase=sample_passphrase,
            salt=SALT,
            kdf_iterations=ITER,
        )
        updated = wallet_service.update_backup(
            test_user, backup.id, label="New Label", wallet_address="0x" + "b" * 40
        )
        assert updated.label == "New Label"
        assert updated.wallet_address == "0x" + "b" * 40
