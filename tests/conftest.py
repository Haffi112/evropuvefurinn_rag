import pytest


@pytest.fixture
def settings():
    from app.config import Settings
    return Settings(
        cms_api_key="test-key",
        database_url="postgresql://test:test@localhost:5432/test_evropuvefur",
    )
