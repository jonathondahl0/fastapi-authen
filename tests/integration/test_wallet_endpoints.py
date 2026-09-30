"""Integration tests for wallet registry endpoints."""
import os

import pytest

from app.services.wallet import WalletBackupService

pytestmark = pytest.mark.integration

PASSPHRASE = "correct-horse-battery-staple"
KDF_ITERATIONS = 100_000

ADDRESS_A = "0x" + "a" * 40
ADDRESS_B = "0x" + "b" * 40
ADDRESS_C = "0x" + "c" * 40


@pytest.fixture
def backup_payload():
    """Encrypted backup payload built the way a client would."""
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


def register_wallet(client, label="Main Wallet", address=ADDRESS_A, **extra):
    return client.post(
        "/api/v1/wallets/",
        json={"label": label, "address": address, "wallet_type": "hd", **extra},
    )


class TestRegisterWallet:
    def test_requires_auth(self, client):
        resp = client.post(
            "/api/v1/wallets/", json={"label": "W", "address": ADDRESS_A}
        )
        assert resp.status_code == 401

    def test_register_first_wallet_is_primary(self, authenticated_client):
        resp = register_wallet(authenticated_client)
        assert resp.status_code == 201
        body = resp.json()
        assert body["wallet"]["label"] == "Main Wallet"
        assert body["wallet"]["address"] == ADDRESS_A
        assert body["wallet"]["chain"] == "huanchain"
        assert body["wallet"]["is_primary"] is True
        assert body["wallet"]["backup_count"] == 0
        assert body["backup"] is None

    def test_register_with_inline_backup(self, authenticated_client, backup_payload):
        resp = register_wallet(
            authenticated_client,
            address=ADDRESS_B,
            backup={
                "encrypted_data": backup_payload["encrypted_data"],
                "salt": backup_payload["salt"],
                "kdf_iterations": backup_payload["kdf_iterations"],
                "passphrase": backup_payload["passphrase"],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["wallet"]["backup_count"] == 1
        assert body["backup"]["wallet_id"] == body["wallet"]["id"]
        assert "ciphertext" not in body["backup"]

    def test_invalid_address_rejected(self, authenticated_client):
        resp = register_wallet(authenticated_client, address="0xNOTHEX")
        assert resp.status_code == 422

    def test_invalid_wallet_type_rejected(self, authenticated_client):
        resp = register_wallet(authenticated_client, wallet_type="custodial")
        assert resp.status_code == 422

    def test_duplicate_address_conflict(self, authenticated_client):
        assert register_wallet(authenticated_client).status_code == 201
        resp = register_wallet(authenticated_client, label="Dup")
        assert resp.status_code == 409

    def test_mismatched_backup_rejected(self, authenticated_client):
        salt = os.urandom(16).hex()
        token = WalletBackupService.encrypt_payload(
            '{"seed": "x"}', "different-passphrase", bytes.fromhex(salt), KDF_ITERATIONS
        )
        resp = register_wallet(
            authenticated_client,
            address=ADDRESS_C,
            backup={
                "encrypted_data": token,
                "salt": salt,
                "kdf_iterations": KDF_ITERATIONS,
                "passphrase": PASSPHRASE,
            },
        )
        assert resp.status_code == 400


class TestListAndGetWallets:
    def test_list_wallets_primary_first(self, authenticated_client):
        register_wallet(authenticated_client, label="One", address=ADDRESS_A)
        register_wallet(authenticated_client, label="Two", address=ADDRESS_B)

        resp = authenticated_client.get("/api/v1/wallets/")
        assert resp.status_code == 200
        labels = [w["label"] for w in resp.json()]
        assert labels == ["One", "Two"]

    def test_list_filter_by_wallet_type(self, authenticated_client):
        register_wallet(authenticated_client, label="One", address=ADDRESS_A)
        register_wallet(
            authenticated_client,
            label="Watch",
            address=ADDRESS_B,
            wallet_type="watch_only",
        )

        resp = authenticated_client.get(
            "/api/v1/wallets/", params={"wallet_type": "watch_only"}
        )
        assert [w["label"] for w in resp.json()] == ["Watch"]

    def test_get_single_wallet(self, authenticated_client):
        created = register_wallet(authenticated_client).json()["wallet"]
        resp = authenticated_client.get(f"/api/v1/wallets/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_nonexistent_wallet_404(self, authenticated_client):
        resp = authenticated_client.get("/api/v1/wallets/99999")
        assert resp.status_code == 404


class TestUpdateAndPrimary:
    def test_update_wallet_metadata(self, authenticated_client):
        created = register_wallet(authenticated_client).json()["wallet"]
        resp = authenticated_client.put(
            f"/api/v1/wallets/{created['id']}",
            json={"label": "Renamed", "description": "Savings wallet"},
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Renamed"
        assert resp.json()["description"] == "Savings wallet"

    def test_set_primary_wallet(self, authenticated_client):
        first = register_wallet(
            authenticated_client, label="One", address=ADDRESS_A
        ).json()["wallet"]
        second = register_wallet(
            authenticated_client, label="Two", address=ADDRESS_B
        ).json()["wallet"]
        assert first["is_primary"] is True

        resp = authenticated_client.post(f"/api/v1/wallets/{second['id']}/primary")
        assert resp.status_code == 200
        assert resp.json()["is_primary"] is True

        assert (
            authenticated_client.get(f"/api/v1/wallets/{first['id']}").json()[
                "is_primary"
            ]
            is False
        )

    def test_delete_wallet(self, authenticated_client):
        first = register_wallet(
            authenticated_client, label="One", address=ADDRESS_A
        ).json()["wallet"]
        second = register_wallet(
            authenticated_client, label="Two", address=ADDRESS_B
        ).json()["wallet"]

        resp = authenticated_client.delete(f"/api/v1/wallets/{first['id']}")
        assert resp.status_code == 200

        assert (
            authenticated_client.get(f"/api/v1/wallets/{first['id']}").status_code
            == 404
        )
        remaining = authenticated_client.get("/api/v1/wallets/").json()
        assert [w["id"] for w in remaining] == [second["id"]]
        assert remaining[0]["is_primary"] is True  # promoted automatically


class TestStatsAndActivity:
    def test_stats(self, authenticated_client, backup_payload):
        register_wallet(authenticated_client, label="One", address=ADDRESS_A)
        register_wallet(
            authenticated_client,
            label="Two",
            address=ADDRESS_B,
            wallet_type="watch_only",
            backup={
                "encrypted_data": backup_payload["encrypted_data"],
                "salt": backup_payload["salt"],
                "kdf_iterations": backup_payload["kdf_iterations"],
                "passphrase": backup_payload["passphrase"],
            },
        )

        resp = authenticated_client.get("/api/v1/wallets/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert stats["total_wallets"] == 2
        assert stats["total_backups"] == 1
        assert stats["by_type"] == {"hd": 1, "watch_only": 1}
        assert stats["primary_wallet_id"] is not None

    def test_activity_trail(self, authenticated_client):
        created = register_wallet(authenticated_client).json()["wallet"]
        authenticated_client.put(
            f"/api/v1/wallets/{created['id']}", json={"label": "Renamed"}
        )

        resp = authenticated_client.get(f"/api/v1/wallets/{created['id']}/activity")
        assert resp.status_code == 200
        actions = [a["action"] for a in resp.json()]
        assert "wallet_created" in actions
        assert "wallet_updated" in actions

    def test_activity_unknown_wallet_404(self, authenticated_client):
        resp = authenticated_client.get("/api/v1/wallets/99999/activity")
        assert resp.status_code == 404
