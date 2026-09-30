"""Pytest configuration and fixtures."""
import os
import sys
from typing import Any, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.user import APIKey, MFASettings, User, UserSession

# Test database URL (in-memory SQLite)
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_engine():
    """Create test database engine."""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine


@pytest.fixture(scope="function")
def test_db(test_engine) -> Generator[Session, Any, None]:
    """Create test database session."""
    # Create all tables
    Base.metadata.create_all(bind=test_engine)

    # Create session
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()
        # Drop all tables after test
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(test_db: Session) -> Generator[TestClient, Any, None]:
    """Create test client with database override."""

    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def test_user_data():
    """Test user data."""
    return {
        "email": "test@example.com",
        "username": "testuser",
        "password": "TestPassword123!",
    }


@pytest.fixture
def test_user(test_db: Session, test_user_data: dict) -> User:
    """Create a test user in the database."""
    from app.core.security import get_password_hash

    user = User(
        email=test_user_data["email"],
        username=test_user_data["username"],
        hashed_password=get_password_hash(test_user_data["password"]),
        is_active=True,
        is_verified=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def test_user_token(test_user: User) -> str:
    """Create access token for test user."""
    from app.core.security import create_access_token

    return create_access_token(subject=test_user.id)


@pytest.fixture
def superuser_user(test_db: Session) -> User:
    """Create an admin (superuser) account."""
    from app.core.security import get_password_hash

    user = User(
        email="admin@example.com",
        username="adminuser",
        hashed_password=get_password_hash("AdminPassword123!"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def superuser_token(superuser_user: User) -> str:
    """Create access token for the admin account."""
    from app.core.security import create_access_token

    return create_access_token(subject=superuser_user.id)


@pytest.fixture
def superuser_client(client: TestClient, superuser_token: str) -> TestClient:
    """Create a test client authenticated as the admin account."""
    client.headers = {
        **client.headers,
        "Authorization": f"Bearer {superuser_token}",
    }
    return client


@pytest.fixture
def second_superuser_token(test_db: Session) -> str:
    """Create access token for a second admin (for dual-approval tests)."""
    from app.core.security import create_access_token, get_password_hash

    user = User(
        email="admin2@example.com",
        username="admin2user",
        hashed_password=get_password_hash("AdminPassword456!"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return create_access_token(subject=user.id)


@pytest.fixture
def test_user_session(test_db: Session, test_user: User) -> UserSession:
    """Create a test user session."""
    import secrets
    from datetime import datetime, timedelta

    from app.core.config import settings

    session = UserSession(
        user_id=test_user.id,
        session_token=secrets.token_urlsafe(32),
        refresh_token=secrets.token_urlsafe(32),
        device_info="Test Device",
        ip_address="127.0.0.1",
        is_active=True,
        expires_at=datetime.utcnow()
        + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    return session


@pytest.fixture
def authenticated_client(client: TestClient, test_user_token: str) -> TestClient:
    """Create authenticated test client."""
    client.headers = {
        **client.headers,
        "Authorization": f"Bearer {test_user_token}",
    }
    return client


@pytest.fixture
def test_mfa_user(test_db: Session, test_user: User) -> User:
    """Create a test user with MFA enabled."""
    import pyotp

    mfa_settings = MFASettings(
        user_id=test_user.id,
        totp_secret=pyotp.random_base32(),
        is_enabled=True,
    )
    test_db.add(mfa_settings)
    test_db.commit()
    test_db.refresh(test_user)
    return test_user


@pytest.fixture
def mock_settings(monkeypatch):
    """Mock application settings."""
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret-key-for-testing-only")
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 30)
    monkeypatch.setattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7)
    return settings
