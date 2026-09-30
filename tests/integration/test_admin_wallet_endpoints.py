"""Integration tests for admin system wallet & withdrawal endpoints."""
import pytest

from app.core.config import settings

pytestmark = pytest.mark.integration

ADDRESS_A = "0x" + "a" * 40
ADDRESS_B = "0x" + "b" * 40
TO_ADDRESS = "0x" + "d" * 40


def create_system_wallet(client, label="Treasury", address=ADDRESS_A, **extra):
    return client.post(
        "/api/v1/admin/system-wallets",
        json={"label": label, "address": address, **extra},
    )


def create_withdrawal(client, wallet_id, amount="1.5", to_address=TO_ADDRESS, **extra):
    return client.post(
        f"/api/v1/admin/system-wallets/{wallet_id}/withdrawals",
        json={"to_address": to_address, "amount": amount, **extra},
    )


def other_admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


class TestAdminAccessControl:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/admin/system-wallets").status_code == 401

    def test_non_superuser_forbidden(self, authenticated_client):
        assert (
            authenticated_client.get("/api/v1/admin/system-wallets").status_code == 403
        )
        assert create_system_wallet(authenticated_client).status_code == 403


class TestSystemWalletRegistryEndpoints:
    def test_create_system_wallet(self, superuser_client):
        resp = create_system_wallet(
            superuser_client, signer_reference="kms://huanchain/treasury-1"
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["label"] == "Treasury"
        assert body["address"] == ADDRESS_A
        assert body["chain"] == "huanchain"
        assert body["signer_reference"] == "kms://huanchain/treasury-1"
        assert body["is_active"] is True

    def test_duplicate_conflict(self, superuser_client):
        assert create_system_wallet(superuser_client).status_code == 201
        assert create_system_wallet(superuser_client, label="Dup").status_code == 409

    def test_invalid_address_rejected(self, superuser_client):
        resp = create_system_wallet(superuser_client, address="0xNOTHEX")
        assert resp.status_code == 422

    def test_get_update_deactivate(self, superuser_client):
        created = create_system_wallet(superuser_client).json()

        assert (
            superuser_client.get(
                f"/api/v1/admin/system-wallets/{created['id']}"
            ).status_code
            == 200
        )

        resp = superuser_client.put(
            f"/api/v1/admin/system-wallets/{created['id']}", json={"label": "Renamed"}
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Renamed"

        assert (
            superuser_client.delete(
                f"/api/v1/admin/system-wallets/{created['id']}"
            ).status_code
            == 200
        )
        assert (
            superuser_client.get(
                f"/api/v1/admin/system-wallets/{created['id']}"
            ).status_code
            == 404
        )
        assert superuser_client.get("/api/v1/admin/system-wallets").json() == []
        assert (
            len(
                superuser_client.get(
                    "/api/v1/admin/system-wallets", params={"include_inactive": True}
                ).json()
            )
            == 1
        )


class TestWithdrawalEndpoints:
    def test_create_withdrawal(self, superuser_client):
        wallet = create_system_wallet(superuser_client).json()
        resp = create_withdrawal(superuser_client, wallet["id"], amount="1.25")
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["amount"] == "1.25"
        assert body["asset"] == "native"
        assert body["simulated"] is False

    def test_invalid_to_address_rejected(self, superuser_client):
        wallet = create_system_wallet(superuser_client).json()
        resp = create_withdrawal(superuser_client, wallet["id"], to_address="0xBAD")
        assert resp.status_code == 422

    def test_zero_amount_rejected(self, superuser_client):
        wallet = create_system_wallet(superuser_client).json()
        resp = create_withdrawal(superuser_client, wallet["id"], amount="0")
        assert resp.status_code == 422

    def test_unknown_wallet_404(self, superuser_client):
        resp = create_withdrawal(superuser_client, 99999)
        assert resp.status_code == 404

    def test_idempotent_replay_returns_original(self, superuser_client):
        wallet = create_system_wallet(superuser_client).json()
        first = create_withdrawal(
            superuser_client, wallet["id"], idempotency_key="withdrawal-key-1"
        )
        assert first.status_code == 201

        replay = create_withdrawal(
            superuser_client, wallet["id"], idempotency_key="withdrawal-key-1"
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == first.json()["id"]

    def test_daily_limit_conflict(self, superuser_client, monkeypatch):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_DAILY_WITHDRAWAL_LIMIT", "5")
        wallet = create_system_wallet(superuser_client).json()

        assert (
            create_withdrawal(superuser_client, wallet["id"], amount="4").status_code
            == 201
        )
        resp = create_withdrawal(superuser_client, wallet["id"], amount="4")
        assert resp.status_code == 409

    def test_dual_approval_self_rejected(self, superuser_client):
        wallet = create_system_wallet(superuser_client).json()
        withdrawal = create_withdrawal(superuser_client, wallet["id"]).json()

        resp = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/approve"
        )
        assert resp.status_code == 400  # same admin cannot approve own request

    def test_full_simulated_lifecycle(self, superuser_client, second_superuser_token):
        wallet = create_system_wallet(superuser_client).json()
        withdrawal = create_withdrawal(
            superuser_client, wallet["id"], amount="2"
        ).json()

        approved = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/approve",
            headers=other_admin_headers(second_superuser_token),
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["approved_by_user_id"] is not None

        executed = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/submit",
            headers=other_admin_headers(second_superuser_token),
        )
        assert executed.status_code == 200
        body = executed.json()
        assert body["status"] == "confirmed"
        assert body["simulated"] is True
        assert body["tx_hash"].startswith("0x") and len(body["tx_hash"]) == 66

        events = superuser_client.get(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/events"
        ).json()
        assert [e["action"] for e in events] == [
            "created",
            "approved",
            "submitted",
            "confirmed",
        ]

    def test_manual_lifecycle(
        self, superuser_client, second_superuser_token, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_EXECUTION_MODE", "manual")
        wallet = create_system_wallet(superuser_client).json()
        withdrawal = create_withdrawal(superuser_client, wallet["id"]).json()
        headers = other_admin_headers(second_superuser_token)

        assert (
            superuser_client.post(
                f"/api/v1/admin/withdrawals/{withdrawal['id']}/approve", headers=headers
            ).status_code
            == 200
        )

        # Manual mode requires the externally-signed tx hash
        resp = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/submit", headers=headers
        )
        assert resp.status_code == 400

        submitted = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/submit",
            json={"tx_hash": "0xdeadbeef"},
            headers=headers,
        )
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "submitted"

        confirmed = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/confirm",
            headers=headers,
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"

    def test_fail_flow(self, superuser_client, second_superuser_token, monkeypatch):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_EXECUTION_MODE", "manual")
        wallet = create_system_wallet(superuser_client).json()
        withdrawal = create_withdrawal(superuser_client, wallet["id"]).json()
        headers = other_admin_headers(second_superuser_token)

        superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/approve", headers=headers
        )
        superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/submit",
            json={"tx_hash": "0xf00d"},
            headers=headers,
        )

        failed = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/fail",
            json={"error": "nonce conflict"},
            headers=headers,
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert failed.json()["error"] == "nonce conflict"

    def test_cancel_flow(self, superuser_client):
        wallet = create_system_wallet(superuser_client).json()
        withdrawal = create_withdrawal(superuser_client, wallet["id"]).json()

        resp = superuser_client.post(
            f"/api/v1/admin/withdrawals/{withdrawal['id']}/cancel",
            json={"reason": "duplicate request"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_list_and_filters(self, superuser_client):
        wallet_a = create_system_wallet(
            superuser_client, label="A", address=ADDRESS_A
        ).json()
        wallet_b = create_system_wallet(
            superuser_client, label="B", address=ADDRESS_B
        ).json()
        create_withdrawal(superuser_client, wallet_a["id"])
        cancelled = create_withdrawal(superuser_client, wallet_b["id"]).json()
        superuser_client.post(f"/api/v1/admin/withdrawals/{cancelled['id']}/cancel")

        all_requests = superuser_client.get("/api/v1/admin/withdrawals").json()
        assert len(all_requests) == 2

        filtered = superuser_client.get(
            "/api/v1/admin/withdrawals", params={"status": "cancelled"}
        ).json()
        assert [w["id"] for w in filtered] == [cancelled["id"]]

        by_wallet = superuser_client.get(
            "/api/v1/admin/withdrawals", params={"system_wallet_id": wallet_a["id"]}
        ).json()
        assert len(by_wallet) == 1
        assert by_wallet[0]["system_wallet_id"] == wallet_a["id"]

    def test_unknown_withdrawal_404(self, superuser_client):
        assert (
            superuser_client.get("/api/v1/admin/withdrawals/99999").status_code == 404
        )
        assert (
            superuser_client.post("/api/v1/admin/withdrawals/99999/approve").status_code
            == 404
        )
