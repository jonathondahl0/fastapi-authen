"""Integration tests for address book endpoints."""
import pytest

pytestmark = pytest.mark.integration

ADDRESS_A = "0x" + "a" * 40
ADDRESS_B = "0x" + "b" * 40


def add_entry(client, name="Alice", address=ADDRESS_A, **extra):
    return client.post(
        "/api/v1/address-book/",
        json={"name": name, "address": address, **extra},
    )


class TestAddressBookEndpoints:
    def test_requires_auth(self, client):
        resp = client.post(
            "/api/v1/address-book/", json={"name": "Alice", "address": ADDRESS_A}
        )
        assert resp.status_code == 401

    def test_create_entry(self, authenticated_client):
        resp = add_entry(authenticated_client, notes="friend")
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Alice"
        assert body["address"] == ADDRESS_A
        assert body["chain"] == "huanchain"
        assert body["notes"] == "friend"

    def test_invalid_address_rejected(self, authenticated_client):
        resp = add_entry(authenticated_client, address="0xNOTHEX")
        assert resp.status_code == 422

    def test_duplicate_conflict(self, authenticated_client):
        assert add_entry(authenticated_client).status_code == 201
        resp = add_entry(authenticated_client, name="Alice Again")
        assert resp.status_code == 409

    def test_list_and_search(self, authenticated_client):
        add_entry(authenticated_client, name="Alice", address=ADDRESS_A)
        add_entry(authenticated_client, name="Bob", address=ADDRESS_B)

        assert len(authenticated_client.get("/api/v1/address-book/").json()) == 2

        resp = authenticated_client.get(
            "/api/v1/address-book/", params={"search": "bob"}
        )
        assert [e["name"] for e in resp.json()] == ["Bob"]

    def test_get_update_delete(self, authenticated_client):
        created = add_entry(authenticated_client).json()

        assert (
            authenticated_client.get(
                f"/api/v1/address-book/{created['id']}"
            ).status_code
            == 200
        )

        resp = authenticated_client.put(
            f"/api/v1/address-book/{created['id']}", json={"name": "Alice Smith"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Alice Smith"

        assert (
            authenticated_client.delete(
                f"/api/v1/address-book/{created['id']}"
            ).status_code
            == 200
        )
        assert (
            authenticated_client.get(
                f"/api/v1/address-book/{created['id']}"
            ).status_code
            == 404
        )

    def test_unknown_entry_404(self, authenticated_client):
        assert authenticated_client.get("/api/v1/address-book/99999").status_code == 404
        assert (
            authenticated_client.put(
                "/api/v1/address-book/99999", json={"name": "X"}
            ).status_code
            == 404
        )
        assert (
            authenticated_client.delete("/api/v1/address-book/99999").status_code == 404
        )
