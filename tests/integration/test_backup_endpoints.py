"""Integration tests for wallet backup endpoints (/api/v1/backups)."""
import os

import pytest

from app.services.wallet import WalletBackupService

pytestmark = pytest.mark.integration

PASSPHRASE = "correct-horse-battery-staple"
KDF_ITERATIONS = 100_000
WALLET_ADDRESS = "0x" + "c" * 40


@pytest.fixture
def backup_payload():
    """Create a real encrypted payload the way a client would."""
    salt = os.urandom(16).hex()
    payload = '{"mnemonic": "apple banana cherry", "address": "0xabc"}'
    token = WalletBackupService.encrypt_payload(
        payload, PASSPHRASE, bytes.fromhex(salt), KDF_ITERATIONS
    )
    return {
        "payload": payload,
        "encrypted_data": token,
        "salt": salt,
        "kdf_iterations": KDF_ITERATIONS,
        "passphrase": PASSPHRASE,
    }


def create_backup(client, backup_payload, label="Main backup", wallet_id=None):
    body = {
        "label": label,
        "encrypted_data": backup_payload["encrypted_data"],
        "salt": backup_payload["salt"],
        "kdf_iterations": backup_payload["kdf_iterations"],
        "passphrase": backup_payload["passphrase"],
    }
    if wallet_id is not None:
        body["wallet_id"] = wallet_id
    return client.post("/api/v1/backups/", json=body)


class TestCreateBackupEndpoint:
    def test_create_backup_requires_auth(self, client, backup_payload):
        resp = client.post(
            "/api/v1/backups/",
            json={
                "label": "W",
                "encrypted_data": backup_payload["encrypted_data"],
                "salt": backup_payload["salt"],
                "kdf_iterations": backup_payload["kdf_iterations"],
                "passphrase": backup_payload["passphrase"],
            },
        )
        assert resp.status_code == 401

    def test_create_backup_success(self, authenticated_client, backup_payload):
        resp = create_backup(authenticated_client, backup_payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["label"] == "Main backup"
        assert "ciphertext" not in body  # metadata response never exposes it
        assert body["kdf_iterations"] > 0
        assert body["salt"]

    def test_create_backup_rejects_short_passphrase(
        self, authenticated_client, backup_payload
    ):
        resp = authenticated_client.post(
            "/api/v1/backups/",
            json={
                "label": "W",
                "encrypted_data": backup_payload["encrypted_data"],
                "salt": backup_payload["salt"],
                "kdf_iterations": backup_payload["kdf_iterations"],
                "passphrase": "short",
            },
        )
        assert resp.status_code == 422

    def test_create_backup_rejects_bad_salt(self, authenticated_client, backup_payload):
        resp = authenticated_client.post(
            "/api/v1/backups/",
            json={
                "label": "W",
                "encrypted_data": backup_payload["encrypted_data"],
                "salt": "zz-not-hex",
                "kdf_iterations": KDF_ITERATIONS,
                "passphrase": backup_payload["passphrase"],
            },
        )
        assert resp.status_code == 422

    def test_create_backup_rejects_mismatched_passphrase(
        self, authenticated_client, backup_payload
    ):
        other_salt = os.urandom(16).hex()
        other_token = WalletBackupService.encrypt_payload(
            backup_payload["payload"],
            "a-different-pass",
            bytes.fromhex(other_salt),
            KDF_ITERATIONS,
        )
        resp = authenticated_client.post(
            "/api/v1/backups/",
            json={
                "label": "W",
                "encrypted_data": other_token,
                "salt": other_salt,
                "kdf_iterations": KDF_ITERATIONS,
                "passphrase": backup_payload["passphrase"],
            },
        )
        assert resp.status_code == 400

    def test_create_backup_linked_to_wallet(self, authenticated_client, backup_payload):
        wallet = authenticated_client.post(
            "/api/v1/wallets/", json={"label": "Main", "address": WALLET_ADDRESS}
        ).json()["wallet"]

        resp = create_backup(
            authenticated_client, backup_payload, wallet_id=wallet["id"]
        )
        assert resp.status_code == 201
        assert resp.json()["wallet_id"] == wallet["id"]

        # Wallet aggregates reflect the linked backup
        wallet_resp = authenticated_client.get(f"/api/v1/wallets/{wallet['id']}").json()
        assert wallet_resp["backup_count"] == 1
        assert wallet_resp["last_backup_at"] is not None

    def test_create_backup_unknown_wallet_404(
        self, authenticated_client, backup_payload
    ):
        resp = create_backup(authenticated_client, backup_payload, wallet_id=99999)
        assert resp.status_code == 404


class TestListAndGetBackups:
    def test_list_backups(self, authenticated_client, backup_payload):
        create_backup(authenticated_client, backup_payload, label="One")
        create_backup(authenticated_client, backup_payload, label="Two")

        resp = authenticated_client.get("/api/v1/backups/")
        assert resp.status_code == 200
        assert {item["label"] for item in resp.json()} == {"One", "Two"}

    def test_list_backups_filters_by_wallet(self, authenticated_client, backup_payload):
        wallet = authenticated_client.post(
            "/api/v1/wallets/", json={"label": "Main", "address": WALLET_ADDRESS}
        ).json()["wallet"]

        create_backup(
            authenticated_client, backup_payload, label="Linked", wallet_id=wallet["id"]
        )
        create_backup(authenticated_client, backup_payload, label="Standalone")

        resp = authenticated_client.get(
            "/api/v1/backups/", params={"wallet_id": wallet["id"]}
        )
        assert [b["label"] for b in resp.json()] == ["Linked"]

    def test_get_single_backup_returns_ciphertext(
        self, authenticated_client, backup_payload
    ):
        created = create_backup(authenticated_client, backup_payload).json()

        resp = authenticated_client.get(f"/api/v1/backups/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["ciphertext"] == backup_payload["encrypted_data"]

    def test_get_nonexistent_backup_404(self, authenticated_client):
        assert authenticated_client.get("/api/v1/backups/99999").status_code == 404


class TestRestoreBackup:
    def test_restore_success(self, authenticated_client, backup_payload):
        created = create_backup(authenticated_client, backup_payload).json()

        resp = authenticated_client.post(
            f"/api/v1/backups/{created['id']}/restore",
            json={"passphrase": backup_payload["passphrase"]},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Client-side decryption must reproduce the original payload
        decrypted = WalletBackupService.decrypt_payload(
            body["ciphertext"],
            backup_payload["passphrase"],
            bytes.fromhex(body["salt"]),
            body["kdf_iterations"],
        )
        assert decrypted == backup_payload["payload"]
        assert body["restored_at"] is not None

    def test_restore_wrong_passphrase_401(self, authenticated_client, backup_payload):
        created = create_backup(authenticated_client, backup_payload).json()
        resp = authenticated_client.post(
            f"/api/v1/backups/{created['id']}/restore",
            json={"passphrase": "definitely-wrong-pass"},
        )
        assert resp.status_code == 401

    def test_restore_nonexistent_404(self, authenticated_client, backup_payload):
        resp = authenticated_client.post(
            "/api/v1/backups/99999/restore",
            json={"passphrase": backup_payload["passphrase"]},
        )
        assert resp.status_code == 404


class TestUpdateAndDeleteBackup:
    def test_update_backup_metadata(self, authenticated_client, backup_payload):
        created = create_backup(authenticated_client, backup_payload).json()
        resp = authenticated_client.put(
            f"/api/v1/backups/{created['id']}",
            json={"label": "Renamed", "wallet_address": "0x" + "d" * 40},
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Renamed"

    def test_delete_backup(self, authenticated_client, backup_payload):
        created = create_backup(authenticated_client, backup_payload).json()

        resp = authenticated_client.delete(f"/api/v1/backups/{created['id']}")
        assert resp.status_code == 200

        assert authenticated_client.get("/api/v1/backups/").json() == []
        assert (
            authenticated_client.get(f"/api/v1/backups/{created['id']}").status_code
            == 404
        )
