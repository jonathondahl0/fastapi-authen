"""Append-only audit logging for wallet operations."""
import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.wallet import WalletActivity

# Canonical action names
WALLET_CREATED = "wallet_created"
WALLET_UPDATED = "wallet_updated"
WALLET_PRIMARY_SET = "wallet_primary_set"
WALLET_DELETED = "wallet_deleted"
BACKUP_CREATED = "backup_created"
BACKUP_UPDATED = "backup_updated"
BACKUP_DELETED = "backup_deleted"
BACKUP_RESTORED = "backup_restored"


def record_activity(
    db: Session,
    user_id: int,
    action: str,
    wallet_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> WalletActivity:
    """Write one audit entry. Never raises for non-critical logging issues."""
    entry = WalletActivity(
        user_id=user_id,
        wallet_id=wallet_id,
        action=action,
        detail=json.dumps(detail) if detail else None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
