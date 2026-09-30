"""Unit tests for the wallet registry service."""
import pytest

from app.models.wallet import Wallet
from app.services.wallet import WalletBackupService
from app.services.wallet_registry import (WalletAlreadyExists,
                                          WalletLimitError, WalletNotFound,
                                          WalletRegistryError, WalletService)

SALT = "a1b2c3d4e5f60718"
ITER = 100_000
PASSPHRASE = "correct-horse-battery-staple"

ADDRESS_A = "0x" + "a" * 40
ADDRESS_B = "0x" + "b" * 40
ADDRESS_C = "0x" + "c" * 40


def enc(payload: str, passphrase: str) -> str:
    return WalletBackupService.encrypt_payload(
        payload, passphrase, bytes.fromhex(SALT), ITER
    )


def backup_payload(passphrase: str = PASSPHRASE) -> dict:
    return {
        "encrypted_data": enc('{"mnemonic": "test seed"}', passphrase),
        "salt": SALT,
        "kdf_iterations": ITER,
        "passphrase": passphrase,
    }


@pytest.fixture
def wallet_service(test_db):
    return WalletService(test_db)


@pytest.mark.unit
class TestCreateWallet:
    def test_first_wallet_becomes_primary(self, wallet_service, test_user):
        wallet, backup = wallet_service.create_wallet(
            user=test_user, label="Main", address=ADDRESS_A
        )
        assert wallet.id is not None
        assert wallet.user_id == test_user.id
        assert wallet.is_primary is True
        assert wallet.is_active is True
        assert wallet.chain == "huanchain"
        assert wallet.wallet_type == "hd"
        assert backup is None

    def test_second_wallet_is_not_primary_by_default(self, wallet_service, test_user):
        wallet_service.create_wallet(user=test_user, label="First", address=ADDRESS_A)
        second, _ = wallet_service.create_wallet(
            user=test_user, label="Second", address=ADDRESS_B
        )
        assert second.is_primary is False

    def test_make_primary_switches_primary(self, wallet_service, test_user):
        first, _ = wallet_service.create_wallet(
            user=test_user, label="First", address=ADDRESS_A
        )
        second, _ = wallet_service.create_wallet(
            user=test_user, label="Second", address=ADDRESS_B, make_primary=True
        )
        assert second.is_primary is True
        assert wallet_service.get_wallet(test_user, first.id).is_primary is False

    def test_duplicate_address_rejected_case_insensitive(
        self, wallet_service, test_user
    ):
        wallet_service.create_wallet(user=test_user, label="First", address=ADDRESS_A)
        with pytest.raises(WalletAlreadyExists):
            wallet_service.create_wallet(
                user=test_user,
                label="Dup",
                address=ADDRESS_A.upper().replace("0X", "0x"),
            )

    def test_same_address_different_chain_allowed(self, wallet_service, test_user):
        wallet_service.create_wallet(user=test_user, label="First", address=ADDRESS_A)
        wallet, _ = wallet_service.create_wallet(
            user=test_user,
            label="Testnet",
            address=ADDRESS_A,
            chain="HuanChain-Testnet",
        )
        assert wallet.chain == "huanchain-testnet"

    def test_invalid_wallet_type_rejected(self, wallet_service, test_user):
        with pytest.raises(WalletRegistryError):
            wallet_service.create_wallet(
                user=test_user, label="Bad", address=ADDRESS_A, wallet_type="custodial"
            )

    def test_wallet_limit(self, wallet_service, test_user, monkeypatch):
        monkeypatch.setattr(WalletService, "MAX_WALLETS_PER_USER", 2)
        wallet_service.create_wallet(user=test_user, label="A", address=ADDRESS_A)
        wallet_service.create_wallet(user=test_user, label="B", address=ADDRESS_B)
        with pytest.raises(WalletLimitError):
            wallet_service.create_wallet(user=test_user, label="C", address=ADDRESS_C)

    def test_create_with_backup_links_and_counts(self, wallet_service, test_user):
        wallet, backup = wallet_service.create_wallet(
            user=test_user, label="Main", address=ADDRESS_A, backup=backup_payload()
        )
        assert backup is not None
        assert backup.wallet_id == wallet.id
        assert backup.wallet_address == ADDRESS_A

        counts = wallet_service.get_backup_counts(test_user, [wallet.id])
        assert counts[wallet.id][0] == 1
        assert counts[wallet.id][1] is not None

    def test_mismatched_backup_rolls_back_wallet(self, wallet_service, test_user):
        # Ciphertext encrypted with one passphrase while claiming another:
        # the backup cannot be created and the wallet must not survive.
        mismatched = {
            "encrypted_data": enc(
                '{"mnemonic": "test seed"}', "a-different-passphrase"
            ),
            "salt": SALT,
            "kdf_iterations": ITER,
            "passphrase": PASSPHRASE,
        }
        with pytest.raises(WalletRegistryError):
            wallet_service.create_wallet(
                user=test_user, label="Main", address=ADDRESS_A, backup=mismatched
            )
        assert (
            wallet_service.db.query(Wallet)
            .filter(Wallet.user_id == test_user.id)
            .count()
            == 0
        )
        assert wallet_service.get_activity(test_user) == []


@pytest.mark.unit
class TestWalletLifecycle:
    def test_get_other_users_wallet_raises(self, wallet_service, test_user):
        from app.core.security import get_password_hash
        from app.models.user import User

        other = User(
            email="other@example.com",
            username="otheruser",
            hashed_password=get_password_hash("OtherPass123!"),
            is_active=True,
        )
        wallet_service.db.add(other)
        wallet_service.db.commit()
        wallet_service.db.refresh(other)

        wallet, _ = wallet_service.create_wallet(
            user=other, label="Theirs", address=ADDRESS_A
        )
        with pytest.raises(WalletNotFound):
            wallet_service.get_wallet(test_user, wallet.id)

    def test_set_primary_unsets_others(self, wallet_service, test_user):
        first, _ = wallet_service.create_wallet(
            user=test_user, label="First", address=ADDRESS_A
        )
        second, _ = wallet_service.create_wallet(
            user=test_user, label="Second", address=ADDRESS_B
        )

        wallet_service.set_primary(test_user, second.id)
        assert wallet_service.get_wallet(test_user, second.id).is_primary is True
        assert wallet_service.get_wallet(test_user, first.id).is_primary is False

    def test_delete_primary_promotes_oldest_remaining(self, wallet_service, test_user):
        first, _ = wallet_service.create_wallet(
            user=test_user, label="First", address=ADDRESS_A
        )
        second, _ = wallet_service.create_wallet(
            user=test_user, label="Second", address=ADDRESS_B
        )

        wallet_service.delete_wallet(test_user, first.id)

        wallets = wallet_service.list_wallets(test_user)
        assert [w.id for w in wallets] == [second.id]
        assert wallets[0].is_primary is True
        with pytest.raises(WalletNotFound):
            wallet_service.get_wallet(test_user, first.id)

    def test_delete_keeps_backups(self, wallet_service, test_user):
        wallet, backup = wallet_service.create_wallet(
            user=test_user, label="Main", address=ADDRESS_A, backup=backup_payload()
        )
        wallet_service.delete_wallet(test_user, wallet.id)

        from app.services.wallet import WalletBackupService

        backups = WalletBackupService(wallet_service.db).list_backups(test_user)
        assert [b.id for b in backups] == [backup.id]

    def test_update_wallet_metadata(self, wallet_service, test_user):
        wallet, _ = wallet_service.create_wallet(
            user=test_user, label="Old", address=ADDRESS_A
        )
        updated = wallet_service.update_wallet(
            test_user, wallet.id, label="New", description="Savings"
        )
        assert updated.label == "New"
        assert updated.description == "Savings"

    def test_stats(self, wallet_service, test_user):
        wallet_service.create_wallet(user=test_user, label="A", address=ADDRESS_A)
        wallet_service.create_wallet(
            user=test_user,
            label="B",
            address=ADDRESS_B,
            chain="huanchain-testnet",
            wallet_type="watch_only",
        )
        wallet_service.create_wallet(
            user=test_user, label="C", address=ADDRESS_C, backup=backup_payload()
        )

        stats = wallet_service.get_stats(test_user)
        assert stats["total_wallets"] == 3
        assert stats["total_backups"] == 1
        assert stats["primary_wallet_id"] is not None
        assert stats["by_chain"] == {"huanchain": 2, "huanchain-testnet": 1}
        assert stats["by_type"] == {"hd": 2, "watch_only": 1}


@pytest.mark.unit
class TestWalletActivity:
    def test_activity_logged_for_lifecycle(self, wallet_service, test_user):
        wallet, _ = wallet_service.create_wallet(
            user=test_user, label="Main", address=ADDRESS_A
        )
        wallet_service.update_wallet(test_user, wallet.id, label="Renamed")
        other, _ = wallet_service.create_wallet(
            user=test_user, label="Other", address=ADDRESS_B
        )
        wallet_service.set_primary(test_user, other.id)
        wallet_service.delete_wallet(test_user, other.id)

        actions = [a.action for a in wallet_service.get_activity(test_user)]
        assert "wallet_created" in actions
        assert "wallet_updated" in actions
        assert "wallet_primary_set" in actions
        assert "wallet_deleted" in actions

    def test_activity_pagination_and_wallet_filter(self, wallet_service, test_user):
        first, _ = wallet_service.create_wallet(
            user=test_user, label="First", address=ADDRESS_A
        )
        second, _ = wallet_service.create_wallet(
            user=test_user, label="Second", address=ADDRESS_B
        )

        first_activity = wallet_service.get_activity(test_user, wallet_id=first.id)
        assert all(a.wallet_id == first.id for a in first_activity)

        page = wallet_service.get_activity(test_user, limit=1, offset=0)
        assert len(page) == 1
