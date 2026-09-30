"""Wallet registry service: user wallet management (non-custodial).

Stores public wallet metadata only (address, label, chain, type). Encrypted
secret material lives exclusively in WalletBackup.
"""
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.wallet import (WALLET_TYPES, Wallet, WalletActivity,
                               WalletBackup)
from app.services import activity
from app.services.wallet import WalletBackupError, WalletBackupService


class WalletRegistryError(Exception):
    """Base wallet registry error."""


class WalletNotFound(WalletRegistryError):
    pass


class WalletAlreadyExists(WalletRegistryError):
    pass


class WalletLimitError(WalletRegistryError):
    pass


class WalletService:
    """Wallet registry CRUD, primary-wallet management, stats, and audit."""

    MAX_WALLETS_PER_USER = 25

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # Registry CRUD
    # ------------------------------------------------------------------ #
    def create_wallet(
        self,
        user: User,
        label: str,
        address: str,
        chain: str = "huanchain",
        wallet_type: str = "hd",
        description: Optional[str] = None,
        make_primary: bool = False,
        backup: Optional[Dict] = None,
    ) -> Tuple[Wallet, Optional[WalletBackup]]:
        """Register a wallet, optionally with an encrypted backup atomically.

        ``backup`` is a dict with the WalletBackupService.create_backup
        parameters: encrypted_data, salt, kdf_iterations, passphrase, label.
        """
        if wallet_type not in WALLET_TYPES:
            raise WalletRegistryError(
                f"wallet_type must be one of: {', '.join(WALLET_TYPES)}"
            )

        chain = (chain or "huanchain").strip().lower()
        address = address.strip()

        active_count = (
            self.db.query(Wallet)
            .filter(Wallet.user_id == user.id, Wallet.is_active == True)
            .count()
        )
        if active_count >= self.MAX_WALLETS_PER_USER:
            raise WalletLimitError(
                f"Maximum of {self.MAX_WALLETS_PER_USER} active wallets reached"
            )

        duplicate = (
            self.db.query(Wallet)
            .filter(
                Wallet.user_id == user.id,
                Wallet.is_active == True,
                Wallet.chain == chain,
                func.lower(Wallet.address) == address.lower(),
            )
            .first()
        )
        if duplicate:
            raise WalletAlreadyExists(
                "A wallet with this address is already registered for this chain"
            )

        has_primary = (
            self.db.query(Wallet)
            .filter(
                Wallet.user_id == user.id,
                Wallet.is_active == True,
                Wallet.is_primary == True,
            )
            .first()
            is not None
        )
        is_primary = bool(make_primary) or not has_primary
        if make_primary and has_primary:
            self._unset_primary(user)

        wallet = Wallet(
            user_id=user.id,
            label=label,
            address=address,
            chain=chain,
            wallet_type=wallet_type,
            description=description,
            is_primary=is_primary,
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(wallet)

        activity.record_activity(
            self.db,
            user.id,
            activity.WALLET_CREATED,
            wallet_id=wallet.id,
            detail={"label": label, "chain": chain, "wallet_type": wallet_type},
        )

        created_backup = None
        if backup:
            try:
                created_backup = WalletBackupService(self.db).create_backup(
                    user=user,
                    wallet_id=wallet.id,
                    label=backup.get("label") or f"{label} backup",
                    encrypted_data=backup["encrypted_data"],
                    passphrase=backup["passphrase"],
                    salt=backup["salt"],
                    kdf_iterations=backup["kdf_iterations"],
                    wallet_address=address,
                )
            except WalletBackupError as exc:
                # Compensate: the wallet was created only for this flow.
                self._rollback_wallet(wallet)
                raise WalletRegistryError(str(exc)) from exc

        return wallet, created_backup

    def get_wallet(self, user: User, wallet_id: int) -> Wallet:
        wallet = (
            self.db.query(Wallet)
            .filter(
                Wallet.id == wallet_id,
                Wallet.user_id == user.id,
                Wallet.is_active == True,
            )
            .first()
        )
        if not wallet:
            raise WalletNotFound("Wallet not found")
        return wallet

    def list_wallets(
        self,
        user: User,
        chain: Optional[str] = None,
        wallet_type: Optional[str] = None,
    ) -> List[Wallet]:
        query = self.db.query(Wallet).filter(
            Wallet.user_id == user.id, Wallet.is_active == True
        )
        if chain:
            query = query.filter(Wallet.chain == chain.strip().lower())
        if wallet_type:
            query = query.filter(Wallet.wallet_type == wallet_type)
        return query.order_by(Wallet.is_primary.desc(), Wallet.created_at.desc()).all()

    def update_wallet(
        self,
        user: User,
        wallet_id: int,
        label: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Wallet:
        wallet = self.get_wallet(user, wallet_id)
        changed = []
        if label is not None:
            wallet.label = label
            changed.append("label")
        if description is not None:
            wallet.description = description
            changed.append("description")
        self.db.commit()
        self.db.refresh(wallet)

        if changed:
            activity.record_activity(
                self.db,
                user.id,
                activity.WALLET_UPDATED,
                wallet_id=wallet.id,
                detail={"fields": changed},
            )
        return wallet

    def set_primary(self, user: User, wallet_id: int) -> Wallet:
        wallet = self.get_wallet(user, wallet_id)
        if not wallet.is_primary:
            self._unset_primary(user)
            wallet.is_primary = True
            self.db.commit()
            self.db.refresh(wallet)

            activity.record_activity(
                self.db,
                user.id,
                activity.WALLET_PRIMARY_SET,
                wallet_id=wallet.id,
                detail={"label": wallet.label},
            )
        return wallet

    def delete_wallet(self, user: User, wallet_id: int) -> bool:
        """Soft-delete a wallet; promote the oldest remaining wallet if needed.

        Backups are intentionally kept: they are the user's recovery data and
        remain accessible through the backup endpoints regardless of wallet
        lifecycle state.
        """
        wallet = self.get_wallet(user, wallet_id)
        was_primary = wallet.is_primary
        wallet.is_active = False
        wallet.is_primary = False
        self.db.commit()

        if was_primary:
            next_wallet = (
                self.db.query(Wallet)
                .filter(Wallet.user_id == user.id, Wallet.is_active == True)
                .order_by(Wallet.id.asc())
                .first()
            )
            if next_wallet:
                next_wallet.is_primary = True
                self.db.commit()

        activity.record_activity(
            self.db,
            user.id,
            activity.WALLET_DELETED,
            wallet_id=wallet.id,
            detail={"label": wallet.label},
        )
        return True

    # ------------------------------------------------------------------ #
    # Aggregates & audit
    # ------------------------------------------------------------------ #
    def get_backup_counts(
        self, user: User, wallet_ids: List[int]
    ) -> Dict[int, Tuple[int, Optional[object]]]:
        """Map wallet_id -> (active backup count, last backup created_at)."""
        if not wallet_ids:
            return {}
        rows = (
            self.db.query(
                WalletBackup.wallet_id,
                func.count(WalletBackup.id),
                func.max(WalletBackup.created_at),
            )
            .filter(
                WalletBackup.user_id == user.id,
                WalletBackup.is_active == True,
                WalletBackup.wallet_id.in_(wallet_ids),
            )
            .group_by(WalletBackup.wallet_id)
            .all()
        )
        return {row[0]: (row[1], row[2]) for row in rows}

    def get_stats(self, user: User) -> Dict:
        wallets = (
            self.db.query(Wallet)
            .filter(Wallet.user_id == user.id, Wallet.is_active == True)
            .all()
        )
        by_chain: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for wallet in wallets:
            by_chain[wallet.chain] = by_chain.get(wallet.chain, 0) + 1
            by_type[wallet.wallet_type] = by_type.get(wallet.wallet_type, 0) + 1

        total_backups = (
            self.db.query(WalletBackup)
            .filter(WalletBackup.user_id == user.id, WalletBackup.is_active == True)
            .count()
        )
        primary_wallet_id = next((w.id for w in wallets if w.is_primary), None)

        return {
            "total_wallets": len(wallets),
            "total_backups": total_backups,
            "primary_wallet_id": primary_wallet_id,
            "by_chain": by_chain,
            "by_type": by_type,
        }

    def get_activity(
        self,
        user: User,
        wallet_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[WalletActivity]:
        if wallet_id is not None:
            self.get_wallet(user, wallet_id)  # ownership check
        query = self.db.query(WalletActivity).filter(WalletActivity.user_id == user.id)
        if wallet_id is not None:
            query = query.filter(WalletActivity.wallet_id == wallet_id)
        return (
            query.order_by(WalletActivity.created_at.desc(), WalletActivity.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _unset_primary(self, user: User) -> None:
        self.db.query(Wallet).filter(
            Wallet.user_id == user.id,
            Wallet.is_active == True,
            Wallet.is_primary == True,
        ).update({Wallet.is_primary: False})
        self.db.commit()

    def _rollback_wallet(self, wallet: Wallet) -> None:
        """Remove a just-created wallet and its activity entries."""
        self.db.query(WalletActivity).filter(
            WalletActivity.wallet_id == wallet.id
        ).delete()
        self.db.delete(wallet)
        self.db.commit()


__all__ = [
    "WalletService",
    "WalletRegistryError",
    "WalletNotFound",
    "WalletAlreadyExists",
    "WalletLimitError",
]
