from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User as UserModel
from app.schemas.address_book import (AddressBookEntryCreate,
                                      AddressBookEntryResponse,
                                      AddressBookEntryUpdate)
from app.services.address_book import (AddressBookAlreadyExists,
                                       AddressBookLimitError,
                                       AddressBookNotFound, AddressBookService)

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> AddressBookService:
    return AddressBookService(db)


@router.post(
    "/", response_model=AddressBookEntryResponse, status_code=status.HTTP_201_CREATED
)
async def create_address_book_entry(
    payload: AddressBookEntryCreate,
    current_user: UserModel = Depends(get_current_user),
    service: AddressBookService = Depends(_service),
):
    """Save a recipient address to the address book."""
    try:
        return service.create_entry(
            user=current_user,
            name=payload.name,
            address=payload.address,
            chain=payload.chain,
            notes=payload.notes,
        )
    except AddressBookAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except AddressBookLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/", response_model=List[AddressBookEntryResponse])
async def list_address_book_entries(
    chain: Optional[str] = Query(None, description="Filter by chain"),
    search: Optional[str] = Query(None, description="Search name or address"),
    current_user: UserModel = Depends(get_current_user),
    service: AddressBookService = Depends(_service),
):
    """List the user's saved addresses."""
    return service.list_entries(current_user, chain=chain, search=search)


@router.get("/{entry_id}", response_model=AddressBookEntryResponse)
async def get_address_book_entry(
    entry_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: AddressBookService = Depends(_service),
):
    """Fetch one address book entry."""
    try:
        return service.get_entry(current_user, entry_id)
    except AddressBookNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.put("/{entry_id}", response_model=AddressBookEntryResponse)
async def update_address_book_entry(
    entry_id: int,
    payload: AddressBookEntryUpdate,
    current_user: UserModel = Depends(get_current_user),
    service: AddressBookService = Depends(_service),
):
    """Update an address book entry."""
    fields = payload.model_dump(exclude_unset=True)
    try:
        return service.update_entry(current_user, entry_id, **fields)
    except AddressBookNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except AddressBookAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.delete("/{entry_id}")
async def delete_address_book_entry(
    entry_id: int,
    current_user: UserModel = Depends(get_current_user),
    service: AddressBookService = Depends(_service),
):
    """Delete an address book entry."""
    try:
        service.delete_entry(current_user, entry_id)
    except AddressBookNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": "Address book entry deleted successfully"}
