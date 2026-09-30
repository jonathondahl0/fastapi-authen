from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator

from app.schemas.wallet import validate_evm_address


class AddressBookEntryCreate(BaseModel):
    """Save a recipient address to the address book."""

    name: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1, max_length=128)
    chain: str = Field("huanchain", min_length=1, max_length=50)
    notes: Optional[str] = Field(None, max_length=500)

    @validator("address")
    def check_address(cls, v):
        v = validate_evm_address(v)
        if not v:
            raise ValueError("address is required")
        return v

    @validator("chain")
    def check_chain(cls, v):
        v = v.strip().lower()
        if not v:
            raise ValueError("chain is required")
        return v


class AddressBookEntryUpdate(BaseModel):
    """Update an address book entry."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    address: Optional[str] = Field(None, min_length=1, max_length=128)
    chain: Optional[str] = Field(None, min_length=1, max_length=50)
    notes: Optional[str] = Field(None, max_length=500)

    @validator("address")
    def check_address(cls, v):
        return validate_evm_address(v)


class AddressBookEntryResponse(BaseModel):
    """Address book entry returned by the API."""

    id: int
    name: str
    address: str
    chain: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
