"""Address book service: saved recipient addresses for the wallet system."""
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.address_book import AddressBookEntry
from app.models.user import User


class AddressBookError(Exception):
    """Base address book error."""


class AddressBookNotFound(AddressBookError):
    pass


class AddressBookAlreadyExists(AddressBookError):
    pass


class AddressBookLimitError(AddressBookError):
    pass


class AddressBookService:
    MAX_ENTRIES_PER_USER = 100

    def __init__(self, db: Session):
        self.db = db

    def create_entry(
        self,
        user: User,
        name: str,
        address: str,
        chain: str = "huanchain",
        notes: Optional[str] = None,
    ) -> AddressBookEntry:
        chain = (chain or "huanchain").strip().lower()
        address = address.strip()

        count = (
            self.db.query(AddressBookEntry)
            .filter(AddressBookEntry.user_id == user.id)
            .count()
        )
        if count >= self.MAX_ENTRIES_PER_USER:
            raise AddressBookLimitError(
                f"Maximum of {self.MAX_ENTRIES_PER_USER} address book entries reached"
            )

        duplicate = (
            self.db.query(AddressBookEntry)
            .filter(
                AddressBookEntry.user_id == user.id,
                AddressBookEntry.chain == chain,
                func.lower(AddressBookEntry.address) == address.lower(),
            )
            .first()
        )
        if duplicate:
            raise AddressBookAlreadyExists(
                "This address is already in your address book for this chain"
            )

        entry = AddressBookEntry(
            user_id=user.id,
            name=name,
            address=address,
            chain=chain,
            notes=notes,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get_entry(self, user: User, entry_id: int) -> AddressBookEntry:
        entry = (
            self.db.query(AddressBookEntry)
            .filter(
                AddressBookEntry.id == entry_id,
                AddressBookEntry.user_id == user.id,
            )
            .first()
        )
        if not entry:
            raise AddressBookNotFound("Address book entry not found")
        return entry

    def list_entries(
        self,
        user: User,
        chain: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[AddressBookEntry]:
        query = self.db.query(AddressBookEntry).filter(
            AddressBookEntry.user_id == user.id
        )
        if chain:
            query = query.filter(AddressBookEntry.chain == chain.strip().lower())
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    AddressBookEntry.name.ilike(like),
                    AddressBookEntry.address.ilike(like),
                )
            )
        return query.order_by(AddressBookEntry.created_at.desc()).all()

    def update_entry(
        self,
        user: User,
        entry_id: int,
        name: Optional[str] = None,
        address: Optional[str] = None,
        chain: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> AddressBookEntry:
        entry = self.get_entry(user, entry_id)

        new_chain = chain.strip().lower() if chain else entry.chain
        new_address = address.strip() if address else entry.address

        if address is not None or chain is not None:
            duplicate = (
                self.db.query(AddressBookEntry)
                .filter(
                    AddressBookEntry.user_id == user.id,
                    AddressBookEntry.id != entry.id,
                    AddressBookEntry.chain == new_chain,
                    func.lower(AddressBookEntry.address) == new_address.lower(),
                )
                .first()
            )
            if duplicate:
                raise AddressBookAlreadyExists(
                    "This address is already in your address book for this chain"
                )

        if name is not None:
            entry.name = name
        if address is not None:
            entry.address = new_address
        if chain is not None:
            entry.chain = new_chain
        if notes is not None:
            entry.notes = notes

        self.db.commit()
        self.db.refresh(entry)
        return entry

    def delete_entry(self, user: User, entry_id: int) -> bool:
        entry = self.get_entry(user, entry_id)
        self.db.delete(entry)
        self.db.commit()
        return True
