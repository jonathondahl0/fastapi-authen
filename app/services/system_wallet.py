"""System wallet & withdrawal services (admin/ops).

System wallets are server-operated wallets used for platform operational
flows. The platform never stores their private keys — signing happens
externally (KMS / HSM / custody provider) and ``signer_reference`` records
where. Withdrawals follow a strict lifecycle with optional dual approval,
daily limits, and a full audit trail.

Execution modes (settings.SYSTEM_WALLET_EXECUTION_MODE):
- "simulation": submit produces a simulated tx hash and auto-confirms the
  request (clearly flagged ``simulated=True``). Safe default for dev/staging.
- "manual": production mode. An operator signs externally and submits the real
  tx hash; confirmation/failure is recorded afterwards.
- "disabled": submission is locked down entirely.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system_wallet import (SystemWallet, WithdrawalEvent,
                                      WithdrawalRequest)

# Withdrawal lifecycle statuses
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_SUBMITTED = "submitted"
STATUS_CONFIRMED = "confirmed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_SUBMITTED)
COUNTED_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_SUBMITTED, STATUS_CONFIRMED)


class SystemWalletError(Exception):
    """Base system wallet / withdrawal error."""


class SystemWalletNotFound(SystemWalletError):
    pass


class SystemWalletAlreadyExists(SystemWalletError):
    pass


class WithdrawalLimitError(SystemWalletError):
    pass


class WithdrawalStateError(SystemWalletError):
    pass


class SystemWalletConfigError(SystemWalletError):
    pass


def _utcnow() -> datetime:
    return datetime.utcnow()


def _parse_amount(amount: str) -> Decimal:
    try:
        value = Decimal(str(amount))
    except InvalidOperation as exc:
        raise SystemWalletError("amount must be a decimal number") from exc
    if not value.is_finite() or value <= 0:
        raise SystemWalletError("amount must be a positive finite number")
    if value.as_tuple().exponent < -18:
        raise SystemWalletError("amount supports at most 18 decimal places")
    return value


class SystemWalletService:
    """Registry for server-operated wallets."""

    def __init__(self, db: Session):
        self.db = db

    def create_system_wallet(
        self,
        label: str,
        address: str,
        chain: str = "huanchain",
        description: Optional[str] = None,
        signer_reference: Optional[str] = None,
    ) -> SystemWallet:
        chain = (chain or "huanchain").strip().lower()
        address = address.strip()

        duplicate = (
            self.db.query(SystemWallet)
            .filter(
                SystemWallet.is_active == True,
                SystemWallet.chain == chain,
                func.lower(SystemWallet.address) == address.lower(),
            )
            .first()
        )
        if duplicate:
            raise SystemWalletAlreadyExists(
                "A system wallet with this address is already registered for this chain"
            )

        wallet = SystemWallet(
            label=label,
            address=address,
            chain=chain,
            description=description,
            signer_reference=signer_reference,
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def get_system_wallet(
        self, wallet_id: int, include_inactive: bool = False
    ) -> SystemWallet:
        query = self.db.query(SystemWallet).filter(SystemWallet.id == wallet_id)
        if not include_inactive:
            query = query.filter(SystemWallet.is_active == True)
        wallet = query.first()
        if not wallet:
            raise SystemWalletNotFound("System wallet not found")
        return wallet

    def list_system_wallets(self, include_inactive: bool = False) -> List[SystemWallet]:
        query = self.db.query(SystemWallet)
        if not include_inactive:
            query = query.filter(SystemWallet.is_active == True)
        return query.order_by(SystemWallet.created_at.desc()).all()

    def update_system_wallet(
        self,
        wallet_id: int,
        label: Optional[str] = None,
        description: Optional[str] = None,
        signer_reference: Optional[str] = None,
    ) -> SystemWallet:
        wallet = self.get_system_wallet(wallet_id)
        if label is not None:
            wallet.label = label
        if description is not None:
            wallet.description = description
        if signer_reference is not None:
            wallet.signer_reference = signer_reference
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def deactivate_system_wallet(self, wallet_id: int) -> bool:
        wallet = self.get_system_wallet(wallet_id)
        wallet.is_active = False
        self.db.commit()
        return True


class WithdrawalService:
    """Withdrawal workflow for system wallets: request, approve, submit, settle."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get_withdrawal(self, withdrawal_id: int) -> WithdrawalRequest:
        withdrawal = (
            self.db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.id == withdrawal_id)
            .first()
        )
        if not withdrawal:
            raise SystemWalletNotFound("Withdrawal request not found")
        return withdrawal

    def list_withdrawals(
        self,
        status: Optional[str] = None,
        system_wallet_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[WithdrawalRequest]:
        query = self.db.query(WithdrawalRequest)
        if status:
            query = query.filter(WithdrawalRequest.status == status.strip().lower())
        if system_wallet_id is not None:
            query = query.filter(WithdrawalRequest.system_wallet_id == system_wallet_id)
        return (
            query.order_by(
                WithdrawalRequest.created_at.desc(), WithdrawalRequest.id.desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_events(self, withdrawal_id: int) -> List[WithdrawalEvent]:
        self.get_withdrawal(withdrawal_id)  # existence check
        return (
            self.db.query(WithdrawalEvent)
            .filter(WithdrawalEvent.withdrawal_id == withdrawal_id)
            .order_by(WithdrawalEvent.created_at.asc(), WithdrawalEvent.id.asc())
            .all()
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def create_withdrawal(
        self,
        actor,
        system_wallet_id: int,
        to_address: str,
        amount: str,
        asset: str = "native",
        note: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[WithdrawalRequest, bool]:
        """Create a withdrawal request. Returns (request, created).

        Replays with the same ``idempotency_key`` return the original request
        instead of creating a duplicate.
        """
        wallet = SystemWalletService(self.db).get_system_wallet(system_wallet_id)
        amount_value = _parse_amount(amount)

        asset = asset.strip()
        if asset.lower() == "native":
            asset = "native"

        if idempotency_key:
            existing = (
                self.db.query(WithdrawalRequest)
                .filter(
                    WithdrawalRequest.system_wallet_id == wallet.id,
                    WithdrawalRequest.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing:
                return existing, False

        self._check_daily_limit(wallet, asset, amount_value)

        withdrawal = WithdrawalRequest(
            system_wallet_id=wallet.id,
            chain=wallet.chain,
            asset=asset,
            to_address=to_address.strip(),
            amount=str(amount_value),
            status=STATUS_PENDING,
            requested_by_user_id=actor.id,
            note=note,
            idempotency_key=idempotency_key,
        )
        self.db.add(withdrawal)
        self.db.commit()
        self.db.refresh(withdrawal)

        self._event(
            withdrawal.id,
            "created",
            actor.id,
            {
                "amount": withdrawal.amount,
                "asset": asset,
                "to_address": withdrawal.to_address,
            },
        )
        return withdrawal, True

    def approve_withdrawal(
        self, actor, withdrawal_id: int, note: Optional[str] = None
    ) -> WithdrawalRequest:
        withdrawal = self.get_withdrawal(withdrawal_id)
        if withdrawal.status != STATUS_PENDING:
            raise WithdrawalStateError(
                f"Only pending withdrawals can be approved (status: {withdrawal.status})"
            )
        if (
            settings.SYSTEM_WALLET_REQUIRE_DUAL_APPROVAL
            and actor.id == withdrawal.requested_by_user_id
        ):
            raise SystemWalletError(
                "Dual approval is enabled: a different admin must approve this withdrawal"
            )

        withdrawal.status = STATUS_APPROVED
        withdrawal.approved_by_user_id = actor.id
        withdrawal.approved_at = _utcnow()
        self.db.commit()
        self.db.refresh(withdrawal)

        self._event(
            withdrawal.id, "approved", actor.id, {"note": note} if note else None
        )
        return withdrawal

    def cancel_withdrawal(
        self, actor, withdrawal_id: int, reason: Optional[str] = None
    ) -> WithdrawalRequest:
        withdrawal = self.get_withdrawal(withdrawal_id)
        if withdrawal.status not in (STATUS_PENDING, STATUS_APPROVED):
            raise WithdrawalStateError(
                f"Only pending or approved withdrawals can be cancelled (status: {withdrawal.status})"
            )

        previous = withdrawal.status
        withdrawal.status = STATUS_CANCELLED
        self.db.commit()
        self.db.refresh(withdrawal)

        self._event(
            withdrawal.id,
            "cancelled",
            actor.id,
            {"from": previous, "reason": reason},
        )
        return withdrawal

    def submit_withdrawal(
        self, actor, withdrawal_id: int, tx_hash: Optional[str] = None
    ) -> WithdrawalRequest:
        """Submit an approved withdrawal for execution.

        Simulation mode auto-confirms with a simulated tx hash. Manual mode
        requires the externally-signed ``tx_hash``.
        """
        withdrawal = self.get_withdrawal(withdrawal_id)
        if withdrawal.status != STATUS_APPROVED:
            raise WithdrawalStateError(
                f"Only approved withdrawals can be submitted (status: {withdrawal.status})"
            )

        mode = (settings.SYSTEM_WALLET_EXECUTION_MODE or "simulation").strip().lower()

        if mode == "disabled":
            raise SystemWalletConfigError(
                "Withdrawal execution is disabled (SYSTEM_WALLET_EXECUTION_MODE=disabled)"
            )

        if mode == "simulation":
            withdrawal.tx_hash = self._simulated_tx_hash(withdrawal)
            withdrawal.simulated = True
            withdrawal.submitted_at = _utcnow()
            withdrawal.status = STATUS_CONFIRMED
            withdrawal.confirmed_at = _utcnow()
            self.db.commit()
            self.db.refresh(withdrawal)

            self._event(
                withdrawal.id,
                "submitted",
                actor.id,
                {"tx_hash": withdrawal.tx_hash, "simulated": True},
            )
            self._event(
                withdrawal.id,
                "confirmed",
                actor.id,
                {"tx_hash": withdrawal.tx_hash, "simulated": True},
            )
            return withdrawal

        if mode == "manual":
            tx = (tx_hash or "").strip()
            if not tx:
                raise SystemWalletError(
                    "tx_hash is required in manual execution mode: sign externally first"
                )
            withdrawal.tx_hash = tx
            withdrawal.submitted_at = _utcnow()
            withdrawal.status = STATUS_SUBMITTED
            self.db.commit()
            self.db.refresh(withdrawal)

            self._event(withdrawal.id, "submitted", actor.id, {"tx_hash": tx})
            return withdrawal

        raise SystemWalletConfigError(f"Unknown execution mode: {mode}")

    def confirm_withdrawal(
        self, actor, withdrawal_id: int, tx_hash: Optional[str] = None
    ) -> WithdrawalRequest:
        withdrawal = self.get_withdrawal(withdrawal_id)
        if withdrawal.status != STATUS_SUBMITTED:
            raise WithdrawalStateError(
                f"Only submitted withdrawals can be confirmed (status: {withdrawal.status})"
            )

        if tx_hash:
            withdrawal.tx_hash = tx_hash.strip()
        withdrawal.status = STATUS_CONFIRMED
        withdrawal.confirmed_at = _utcnow()
        self.db.commit()
        self.db.refresh(withdrawal)

        self._event(
            withdrawal.id, "confirmed", actor.id, {"tx_hash": withdrawal.tx_hash}
        )
        return withdrawal

    def fail_withdrawal(
        self, actor, withdrawal_id: int, error: str
    ) -> WithdrawalRequest:
        withdrawal = self.get_withdrawal(withdrawal_id)
        if withdrawal.status not in (STATUS_APPROVED, STATUS_SUBMITTED):
            raise WithdrawalStateError(
                f"Only approved or submitted withdrawals can be marked failed (status: {withdrawal.status})"
            )

        withdrawal.status = STATUS_FAILED
        withdrawal.error = error
        self.db.commit()
        self.db.refresh(withdrawal)

        self._event(withdrawal.id, "failed", actor.id, {"error": error})
        return withdrawal

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _check_daily_limit(
        self, wallet: SystemWallet, asset: str, amount_value: Decimal
    ) -> None:
        try:
            limit = Decimal(str(settings.SYSTEM_WALLET_DAILY_WITHDRAWAL_LIMIT))
        except InvalidOperation as exc:
            raise SystemWalletConfigError(
                "SYSTEM_WALLET_DAILY_WITHDRAWAL_LIMIT is not a valid number"
            ) from exc

        window_start = _utcnow() - timedelta(hours=24)
        rows = (
            self.db.query(WithdrawalRequest)
            .filter(
                WithdrawalRequest.system_wallet_id == wallet.id,
                WithdrawalRequest.asset == asset,
                WithdrawalRequest.status.in_(COUNTED_STATUSES),
                WithdrawalRequest.created_at >= window_start,
            )
            .all()
        )
        used = sum((Decimal(r.amount) for r in rows), Decimal("0"))
        if used + amount_value > limit:
            raise WithdrawalLimitError(
                f"Daily withdrawal limit exceeded (limit {limit}, used {used}, requested {amount_value})"
            )

    def _event(
        self,
        withdrawal_id: int,
        action: str,
        actor_id: Optional[int] = None,
        detail: Optional[Dict] = None,
    ) -> WithdrawalEvent:
        event = WithdrawalEvent(
            withdrawal_id=withdrawal_id,
            action=action,
            actor_user_id=actor_id,
            detail=json.dumps(detail) if detail else None,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    @staticmethod
    def _simulated_tx_hash(withdrawal: WithdrawalRequest) -> str:
        payload = (
            f"sim:{withdrawal.id}:{withdrawal.system_wallet_id}:{withdrawal.to_address}:"
            f"{withdrawal.amount}:{withdrawal.asset}:{secrets.token_hex(8)}"
        )
        return "0x" + hashlib.sha256(payload.encode()).hexdigest()
