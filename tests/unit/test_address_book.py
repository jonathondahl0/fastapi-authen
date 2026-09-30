"""Unit tests for the address book service."""
import pytest

from app.services.address_book import (AddressBookAlreadyExists,
                                       AddressBookLimitError,
                                       AddressBookNotFound, AddressBookService)

ADDRESS_A = "0x" + "a" * 40
ADDRESS_B = "0x" + "b" * 40


@pytest.fixture
def address_book(test_db):
    return AddressBookService(test_db)


@pytest.mark.unit
class TestAddressBookService:
    def test_create_entry(self, address_book, test_user):
        entry = address_book.create_entry(
            user=test_user, name="Alice", address=ADDRESS_A, notes="friend"
        )
        assert entry.id is not None
        assert entry.user_id == test_user.id
        assert entry.chain == "huanchain"
        assert entry.notes == "friend"

    def test_duplicate_rejected_case_insensitive(self, address_book, test_user):
        address_book.create_entry(user=test_user, name="Alice", address=ADDRESS_A)
        with pytest.raises(AddressBookAlreadyExists):
            address_book.create_entry(
                user=test_user, name="Alice Again", address="0X" + "A" * 40
            )

    def test_same_address_other_chain_allowed(self, address_book, test_user):
        address_book.create_entry(user=test_user, name="Alice", address=ADDRESS_A)
        entry = address_book.create_entry(
            user=test_user, name="Alice Testnet", address=ADDRESS_A, chain="testnet"
        )
        assert entry.chain == "testnet"

    def test_list_and_filters(self, address_book, test_user):
        address_book.create_entry(user=test_user, name="Alice", address=ADDRESS_A)
        address_book.create_entry(
            user=test_user, name="Bob", address=ADDRESS_B, chain="testnet"
        )

        assert len(address_book.list_entries(test_user)) == 2
        assert len(address_book.list_entries(test_user, chain="testnet")) == 1
        assert len(address_book.list_entries(test_user, search="ali")) == 1
        assert len(address_book.list_entries(test_user, search="bbb")) == 1

    def test_update_entry(self, address_book, test_user):
        entry = address_book.create_entry(
            user=test_user, name="Alice", address=ADDRESS_A
        )
        updated = address_book.update_entry(
            test_user, entry.id, name="Alice Smith", address=ADDRESS_B
        )
        assert updated.name == "Alice Smith"
        assert updated.address == ADDRESS_B

    def test_update_to_duplicate_rejected(self, address_book, test_user):
        first = address_book.create_entry(
            user=test_user, name="Alice", address=ADDRESS_A
        )
        address_book.create_entry(user=test_user, name="Bob", address=ADDRESS_B)
        with pytest.raises(AddressBookAlreadyExists):
            address_book.update_entry(test_user, first.id, address=ADDRESS_B)

    def test_delete_entry(self, address_book, test_user):
        entry = address_book.create_entry(
            user=test_user, name="Alice", address=ADDRESS_A
        )
        assert address_book.delete_entry(test_user, entry.id) is True
        with pytest.raises(AddressBookNotFound):
            address_book.get_entry(test_user, entry.id)

    def test_other_user_isolation(self, address_book, test_user):
        from app.core.security import get_password_hash
        from app.models.user import User

        other = User(
            email="other@example.com",
            username="otheruser",
            hashed_password=get_password_hash("OtherPass123!"),
            is_active=True,
        )
        address_book.db.add(other)
        address_book.db.commit()
        address_book.db.refresh(other)

        entry = address_book.create_entry(user=other, name="Theirs", address=ADDRESS_A)
        with pytest.raises(AddressBookNotFound):
            address_book.get_entry(test_user, entry.id)
        assert address_book.list_entries(test_user) == []

    def test_entry_limit(self, address_book, test_user, monkeypatch):
        monkeypatch.setattr(AddressBookService, "MAX_ENTRIES_PER_USER", 1)
        address_book.create_entry(user=test_user, name="Alice", address=ADDRESS_A)
        with pytest.raises(AddressBookLimitError):
            address_book.create_entry(user=test_user, name="Bob", address=ADDRESS_B)
