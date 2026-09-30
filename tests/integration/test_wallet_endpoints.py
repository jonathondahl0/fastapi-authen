"""Integration tests for wallet backup endpoints."""
import base64
import os

import pytest

from app.services.wallet import WalletBackupService

pytestmark = pytest.mark.integration


@pytest.fixture
def sample_passphrase():
    return "correct-horse-battery-staple"


@pytest.fixture
def wallet_payload(sample_passphrase):
    """Create a real encrypted payload the way a client would."""
    salt = os.urandom(16).hex()
    iterations = 200_000
    payload = '{"mnemonic": "apple banana cherry", "address": "0xabc"}'
    token = WalletBackupService.encrypt_payload(
        payload, sample_passphrase, bytes.fromhex(salt), iterations
    )
    return {
        "payload": payload,
        "encrypted_data": token,
        "salt": salt,
        "kdf_iterations": iterations,
        "passphrase": sample_passphrase,
    }


def create_backup(authenticated_client, wallet_payload, label="Main Wallet"):
    return authenticated_client.post(
        "/api/v1/wallets/",
        json={
            "label": label,
            "wallet_address": "0x" + "c" * 40,
            "encrypted_data": wallet_payload["encrypted_data"],
            "salt": wallet_payload["salt"],
            "kdf_iterations": wallet_payload["kdf_iterations"],
            "passphrase": wallet_payload["passphrase"],
        },
    )


class TestCreateWalletBackupEndpoint:
    def test_create_backup_requires_auth(self, client, wallet_payload):
        resp = client.post(
            "/api/v1/wallets/",
            json={
                "label": "Wallet",
                "encrypted_data": wallet_payload["encrypted_data"],
                "passphrase": wallet_payload["passphrase"],
            },
        )
        assert resp.status_code == 401

    def test_create_backup_success(self, authenticated_client, wallet_payload):
        resp = create_backup(authenticated_client, wallet_payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["label"] == "Main Wallet"
        assert "ciphertext" not in body  # listing responses never include ciphertext
        assert body["kdf_iterations"] > 0
        assert body["salt"]

    def test_create_backup_rejects_short_passphrase(self, authenticated_client, wallet_payload):
        resp = authenticated_client.post(
            "/api/v1/wallets/",
            json={
                "label": "Wallet",
                "encrypted_data": wallet_payload["encrypted_data"],
                "salt": wallet_payload["salt"],
                "kdf_iterations": wallet_payload["kdf_iterations"],
                "passphrase": "short",
            },
        )
        assert resp.status_code == 422

    def test_create_backup_validates_wallet_address(self, authenticated_client, wallet_payload):
        resp = authenticated_client.post(
            "/api/v1/wallets/",
            json={
                "label": "Wallet",
                "wallet_address": "0xNOTHEX",
                "encrypted_data": wallet_payload["encrypted_data"],
                "salt": wallet_payload["salt"],
                "kdf_iterations": wallet_payload["kdf_iterations"],
                "passphrase": wallet_payload["passphrase"],
            },
        )
        assert resp.status_code == 422

    def test_create_backup_rejects_bad_salt(self, authenticated_client, wallet_payload):
        resp = authenticated_client.post(
            "/api/v1/wallets/",
            json={
                "label": "Wallet",
                "encrypted_data": wallet_payload["encrypted_data"],
                "salt": "zz-not-hex",
                "kdf_iterations": 200_000,
                "passphrase": wallet_payload["passphrase"],
            },
        )
        assert resp.status_code == 422

    def test_create_backup_rejects_mismatched_passphrase(self, authenticated_client, wallet_payload):
        """Ciphertext that does not decrypt with the passphrase must be rejected."""
        other_salt = os.urandom(16).hex()
        other_token = WalletBackupService.encrypt_payload(
            wallet_payload["payload"], "a-different-pass", bytes.fromhex(other_salt), 200_000
        )
        resp = authenticated_client.post(
            "/api/v1/wallets/",
            json={
                "label": "Wallet",
                "encrypted_data": other_token,
                "salt": other_salt,
                "kdf_iterations": 200_000,
                "passphrase": wallet_payload["passphrase"],
            },
        )
        assert resp.status_code == 400


class TestListAndGetBackups:
    def test_list_backups(self, authenticated_client, wallet_payload):
        create_backup(authenticated_client, wallet_payload, label="One")
        create_backup(authenticated_client, wallet_payload, label="Two")

        resp = authenticated_client.get("/api/v1/wallets/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert {item["label"] for item in body} == {"One", "Two"}

    def test_get_single_backup_returns_ciphertext(self, authenticated_client, wallet_payload):
        created = create_backup(authenticated_client, wallet_payload).json()

        resp = authenticated_client.get(f"/api/v1/wallets/{created['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ciphertext"] == wallet_payload["encrypted_data"]

    def test_get_nonexistent_backup_404(self, authenticated_client):
        resp = authenticated_client.get("/api/v1/wallets/99999")
        assert resp.status_code == 404


class TestRestoreWalletBackup:
    def test_restore_success(self, authenticated_client, wallet_payload):
        created = create_backup(authenticated_client, wallet_payload).json()

        resp = authenticated_client.post(
            f"/api/v1/wallets/{created['id']}/restore",
            json={"passphrase": wallet_payload["passphrase"]},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Client-side decryption must reproduce the original payload
        decrypted = WalletBackupService.decrypt_payload(
            body["ciphertext"],
            wallet_payload["passphrase"],
            bytes.fromhex(body["salt"]),
            body["kdf_iterations"],
        )
        assert decrypted == wallet_payload["payload"]
        assert body["restored_at"] is not None

    def test_restore_wrong_passphrase_401(self, authenticated_client, wallet_payload):
        created = create_backup(authenticated_client, wallet_payload).json()

        resp = authenticated_client.post(
            f"/api/v1/wallets/{created['id']}/restore",
            json={"passphrase": "definitely-wrong-pass"},
        )
        assert resp.status_code == 401

    def test_restore_nonexistent_404(self, authenticated_client, wallet_payload):
        resp = authenticated_client.post(
            "/api/v1/wallets/99999/restore",
            json={"passphrase": wallet_payload["passphrase"]},
        )
        assert resp.status_code == 404


class TestUpdateAndDelete:
    def test_update_backup_metadata(self, authenticated_client, wallet_payload):
        created = create_backup(authenticated_client, wallet_payload).json()

        resp = authenticated_client.put(
            f"/api/v1/wallets/{created['id']}",
            json={"label": "Renamed", "wallet_address": "0x" + "d" * 40},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["label"] == "Renamed"
        assert body["wallet_address"] == "0x" + "d" * 40

    def test_delete_backup(self, authenticated_client, wallet_payload):
        created = create_backup(authenticated_client, wallet_payload).json()

        resp = authenticated_client.delete(f"/api/v1/wallets/{created['id']}")
        assert resp.status_code == 200

        # Deleted backups should no longer be listed or fetchable
        assert authenticated_client.get("/api/v1/wallets/").json() == []
        assert authenticated_client.get(f"/api/v1/wallets/{created['id']}").status_code == 404
