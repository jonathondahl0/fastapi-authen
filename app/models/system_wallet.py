from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class SystemWallet(Base):
    """A server-operated (system) wallet used for platform operational flows.

    These wallets belong to the platform, not to end users. Private keys are
    intentionally NOT stored here: ``signer_reference`` points at where signing
    happens (KMS key id, HSM slot, or custody provider reference). Withdrawals
    run through an approval workflow and are recorded for audit.
    """

    __tablename__ = "system_wallets"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(100), nullable=False)
    address = Column(String(128), nullable=False, index=True)
    chain = Column(String(50), nullable=False, default="huanchain", index=True)
    description = Column(Text)
    signer_reference = Column(String(255))  # e.g. "kms://huanchain/treasury-1"

    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    withdrawals = relationship("WithdrawalRequest", back_populates="system_wallet")


class WithdrawalRequest(Base):
    """A withdrawal from a system wallet with a full approval lifecycle.

    Statuses: pending -> approved -> submitted -> confirmed | failed;
    cancelled can happen from pending/approved.
    """

    __tablename__ = "withdrawal_requests"
    __table_args__ = (
        UniqueConstraint(
            "system_wallet_id", "idempotency_key", name="uq_withdrawal_idempotency"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    system_wallet_id = Column(
        Integer, ForeignKey("system_wallets.id"), nullable=False, index=True
    )

    chain = Column(String(50), nullable=False, default="huanchain")
    asset = Column(
        String(128), nullable=False, default="native"
    )  # "native" or token contract
    to_address = Column(String(128), nullable=False)
    amount = Column(String(78), nullable=False)  # decimal string

    status = Column(String(20), nullable=False, default="pending", index=True)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"))

    tx_hash = Column(String(128))
    error = Column(Text)
    note = Column(Text)
    idempotency_key = Column(String(128))
    simulated = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    approved_at = Column(DateTime(timezone=True))
    submitted_at = Column(DateTime(timezone=True))
    confirmed_at = Column(DateTime(timezone=True))

    # Relationships
    system_wallet = relationship("SystemWallet", back_populates="withdrawals")
    events = relationship("WithdrawalEvent", back_populates="withdrawal")


class WithdrawalEvent(Base):
    """Append-only audit trail for a withdrawal request."""

    __tablename__ = "withdrawal_events"

    id = Column(Integer, primary_key=True, index=True)
    withdrawal_id = Column(
        Integer, ForeignKey("withdrawal_requests.id"), nullable=False, index=True
    )
    action = Column(String(50), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"))
    detail = Column(Text)  # JSON-encoded context
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    withdrawal = relationship("WithdrawalRequest", back_populates="events")
