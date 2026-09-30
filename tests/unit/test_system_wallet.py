"""Unit tests for system wallet registry and withdrawal workflow."""
import pytest

from app.core.config import settings
from app.models.system_wallet import WithdrawalRequest
from app.services.system_wallet import (STATUS_APPROVED, STATUS_CANCELLED,
                                        STATUS_CONFIRMED, STATUS_FAILED,
                                        STATUS_PENDING, STATUS_SUBMITTED,
                                        SystemWalletAlreadyExists,
                                        SystemWalletConfigError,
                                        SystemWalletError,
                                        SystemWalletNotFound,
                                        SystemWalletService,
                                        WithdrawalLimitError,
                                        WithdrawalService,
                                        WithdrawalStateError)

ADDRESS_A = "0x" + "a" * 40
ADDRESS_B = "0x" + "b" * 40
TO_ADDRESS = "0x" + "d" * 40
TOKEN_ADDRESS = "0x" + "e" * 40


@pytest.fixture
def wallets(test_db):
    return SystemWalletService(test_db)


@pytest.fixture
def withdrawals(test_db):
    return WithdrawalService(test_db)


@pytest.fixture
def approver(test_db):
    """A second admin used as approver in dual-approval tests."""
    from app.core.security import get_password_hash
    from app.models.user import User

    user = User(
        email="approver@example.com",
        username="approveruser",
        hashed_password=get_password_hash("ApproverPass123!"),
        is_active=True,
        is_superuser=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def system_wallet(wallets):
    return wallets.create_system_wallet(
        label="Treasury",
        address=ADDRESS_A,
        signer_reference="kms://huanchain/treasury-1",
    )


@pytest.mark.unit
class TestSystemWalletRegistry:
    def test_create_system_wallet(self, wallets):
        wallet = wallets.create_system_wallet(label="Ops", address=ADDRESS_A)
        assert wallet.id is not None
        assert wallet.chain == "huanchain"
        assert wallet.is_active is True
        assert wallet.signer_reference is None

    def test_duplicate_address_conflict_case_insensitive(self, wallets, system_wallet):
        with pytest.raises(SystemWalletAlreadyExists):
            wallets.create_system_wallet(label="Dup", address="0X" + "A" * 40)

    def test_same_address_other_chain_allowed(self, wallets, system_wallet):
        wallet = wallets.create_system_wallet(
            label="Testnet", address=ADDRESS_A, chain="HuanChain-Testnet"
        )
        assert wallet.chain == "huanchain-testnet"

    def test_get_and_list(self, wallets, system_wallet):
        assert wallets.get_system_wallet(system_wallet.id).id == system_wallet.id
        assert len(wallets.list_system_wallets()) == 1
        with pytest.raises(SystemWalletNotFound):
            wallets.get_system_wallet(99999)

    def test_update_system_wallet(self, wallets, system_wallet):
        updated = wallets.update_system_wallet(
            system_wallet.id, label="Renamed", signer_reference="kms://new"
        )
        assert updated.label == "Renamed"
        assert updated.signer_reference == "kms://new"

    def test_deactivate_hides_from_default_views(self, wallets, system_wallet):
        wallets.deactivate_system_wallet(system_wallet.id)
        assert wallets.list_system_wallets() == []
        assert len(wallets.list_system_wallets(include_inactive=True)) == 1
        with pytest.raises(SystemWalletNotFound):
            wallets.get_system_wallet(system_wallet.id)

    def test_recreate_after_deactivate_allowed(self, wallets, system_wallet):
        wallets.deactivate_system_wallet(system_wallet.id)
        wallet = wallets.create_system_wallet(label="Fresh", address=ADDRESS_A)
        assert wallet.is_active is True


@pytest.mark.unit
class TestWithdrawalCreation:
    def test_create_withdrawal(self, withdrawals, system_wallet, test_user):
        request, created = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="12.5",
        )
        assert created is True
        assert request.status == STATUS_PENDING
        assert request.amount == "12.5"
        assert request.asset == "native"
        assert request.chain == "huanchain"
        assert request.requested_by_user_id == test_user.id

        events = withdrawals.get_events(request.id)
        assert [e.action for e in events] == ["created"]

    def test_create_withdrawal_unknown_wallet(self, withdrawals, test_user):
        with pytest.raises(SystemWalletNotFound):
            withdrawals.create_withdrawal(
                actor=test_user,
                system_wallet_id=99999,
                to_address=TO_ADDRESS,
                amount="1",
            )

    @pytest.mark.parametrize("bad_amount", ["0", "-5", "abc", "1.1234567890123456789"])
    def test_invalid_amounts_rejected(
        self, withdrawals, system_wallet, test_user, bad_amount
    ):
        with pytest.raises(SystemWalletError):
            withdrawals.create_withdrawal(
                actor=test_user,
                system_wallet_id=system_wallet.id,
                to_address=TO_ADDRESS,
                amount=bad_amount,
            )

    def test_daily_limit_enforced(
        self, withdrawals, system_wallet, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_DAILY_WITHDRAWAL_LIMIT", "10")

        withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="6",
        )
        with pytest.raises(WithdrawalLimitError):
            withdrawals.create_withdrawal(
                actor=test_user,
                system_wallet_id=system_wallet.id,
                to_address=TO_ADDRESS,
                amount="5",
            )

    def test_cancelled_withdrawals_freed_up_limit(
        self, withdrawals, system_wallet, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_DAILY_WITHDRAWAL_LIMIT", "10")

        first, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="9",
        )
        withdrawals.cancel_withdrawal(test_user, first.id, reason="oops")

        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="9",
        )
        assert request.status == STATUS_PENDING

    def test_limits_are_per_asset(
        self, withdrawals, system_wallet, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_DAILY_WITHDRAWAL_LIMIT", "10")

        withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="10",
        )
        token_request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="10",
            asset=TOKEN_ADDRESS,
        )
        assert token_request.asset == TOKEN_ADDRESS

    def test_idempotency_replay_returns_original(
        self, withdrawals, system_wallet, test_user
    ):
        first, created1 = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="1",
            idempotency_key="key-12345678",
        )
        second, created2 = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="1",
            idempotency_key="key-12345678",
        )
        assert created1 is True
        assert created2 is False
        assert second.id == first.id
        assert (
            withdrawals.db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.id == first.id)
            .count()
            == 1
        )


@pytest.mark.unit
class TestWithdrawalApproval:
    def test_approve_with_second_admin(
        self, withdrawals, system_wallet, test_user, approver
    ):
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="1",
        )
        approved = withdrawals.approve_withdrawal(approver, request.id)
        assert approved.status == STATUS_APPROVED
        assert approved.approved_by_user_id == approver.id
        assert approved.approved_at is not None

    def test_dual_approval_blocks_self_approval(
        self, withdrawals, system_wallet, test_user
    ):
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="1",
        )
        with pytest.raises(SystemWalletError):
            withdrawals.approve_withdrawal(test_user, request.id)

    def test_single_admin_mode_allows_self_approval(
        self, withdrawals, system_wallet, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_REQUIRE_DUAL_APPROVAL", False)
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="1",
        )
        approved = withdrawals.approve_withdrawal(test_user, request.id)
        assert approved.status == STATUS_APPROVED

    def test_cannot_approve_non_pending(
        self, withdrawals, system_wallet, test_user, approver
    ):
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="1",
        )
        withdrawals.cancel_withdrawal(approver, request.id)
        with pytest.raises(WithdrawalStateError):
            withdrawals.approve_withdrawal(approver, request.id)


@pytest.mark.unit
class TestWithdrawalExecution:
    def test_simulation_submit_auto_confirms(
        self, withdrawals, system_wallet, test_user, approver
    ):
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="3",
        )
        withdrawals.approve_withdrawal(approver, request.id)
        executed = withdrawals.submit_withdrawal(approver, request.id)

        assert executed.status == STATUS_CONFIRMED
        assert executed.simulated is True
        assert executed.tx_hash.startswith("0x") and len(executed.tx_hash) == 66
        assert executed.submitted_at is not None
        assert executed.confirmed_at is not None

        actions = [e.action for e in withdrawals.get_events(request.id)]
        assert actions == ["created", "approved", "submitted", "confirmed"]

    def test_manual_mode_requires_tx_hash(
        self, withdrawals, system_wallet, test_user, approver, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_EXECUTION_MODE", "manual")
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="3",
        )
        withdrawals.approve_withdrawal(approver, request.id)

        with pytest.raises(SystemWalletError):
            withdrawals.submit_withdrawal(approver, request.id)

    def test_manual_mode_submit_confirm(
        self, withdrawals, system_wallet, test_user, approver, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_EXECUTION_MODE", "manual")
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="3",
        )
        withdrawals.approve_withdrawal(approver, request.id)

        submitted = withdrawals.submit_withdrawal(
            approver, request.id, tx_hash="0xdeadbeef"
        )
        assert submitted.status == STATUS_SUBMITTED
        assert submitted.tx_hash == "0xdeadbeef"
        assert submitted.simulated is False

        confirmed = withdrawals.confirm_withdrawal(approver, request.id)
        assert confirmed.status == STATUS_CONFIRMED

    def test_disabled_mode_blocks_submission(
        self, withdrawals, system_wallet, test_user, approver, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_EXECUTION_MODE", "disabled")
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="3",
        )
        withdrawals.approve_withdrawal(approver, request.id)
        with pytest.raises(SystemWalletConfigError):
            withdrawals.submit_withdrawal(approver, request.id)

    def test_fail_from_submitted(
        self, withdrawals, system_wallet, test_user, approver, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_EXECUTION_MODE", "manual")
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="3",
        )
        withdrawals.approve_withdrawal(approver, request.id)
        withdrawals.submit_withdrawal(approver, request.id, tx_hash="0xf00d")

        failed = withdrawals.fail_withdrawal(
            approver, request.id, error="nonce conflict"
        )
        assert failed.status == STATUS_FAILED
        assert failed.error == "nonce conflict"

    def test_cancel_only_before_submission(
        self, withdrawals, system_wallet, test_user, approver, monkeypatch
    ):
        monkeypatch.setattr(settings, "SYSTEM_WALLET_EXECUTION_MODE", "manual")
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="3",
        )
        withdrawals.approve_withdrawal(approver, request.id)
        withdrawals.submit_withdrawal(approver, request.id, tx_hash="0xabc")

        with pytest.raises(WithdrawalStateError):
            withdrawals.cancel_withdrawal(approver, request.id)

    def test_confirm_requires_submitted(self, withdrawals, system_wallet, test_user):
        request, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="3",
        )
        with pytest.raises(WithdrawalStateError):
            withdrawals.confirm_withdrawal(test_user, request.id)


@pytest.mark.unit
class TestWithdrawalQueries:
    def test_list_filters(self, withdrawals, system_wallet, test_user, approver):
        first, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="1",
        )
        second, _ = withdrawals.create_withdrawal(
            actor=test_user,
            system_wallet_id=system_wallet.id,
            to_address=TO_ADDRESS,
            amount="2",
        )
        withdrawals.cancel_withdrawal(approver, second.id)

        assert len(withdrawals.list_withdrawals()) == 2
        assert [w.id for w in withdrawals.list_withdrawals(status="cancelled")] == [
            second.id
        ]
        assert [w.id for w in withdrawals.list_withdrawals(status="pending")] == [
            first.id
        ]

    def test_pagination(self, withdrawals, system_wallet, test_user):
        for amount in ("1", "2", "3"):
            withdrawals.create_withdrawal(
                actor=test_user,
                system_wallet_id=system_wallet.id,
                to_address=TO_ADDRESS,
                amount=amount,
            )
        page = withdrawals.list_withdrawals(limit=2, offset=0)
        assert len(page) == 2
        page2 = withdrawals.list_withdrawals(limit=2, offset=2)
        assert len(page2) == 1

    def test_events_unknown_withdrawal(self, withdrawals):
        with pytest.raises(SystemWalletNotFound):
            withdrawals.get_events(99999)
