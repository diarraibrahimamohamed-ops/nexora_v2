import pytest
from pydantic import ValidationError

from app.config import Settings


def valid_settings(**overrides):
    values = {
        "SECRET_KEY": "ci-only-key-" + ("f3" * 32),
        "DATABASE_URL": "postgresql://nexora:unit-test-db-secret-987654321@db:5432/nexora",
        "REDIS_URL": "redis://redis:6379/0",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "CELERY_RESULT_BACKEND": "redis://redis:6379/1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_settings_accept_unique_non_placeholder_secrets():
    configured = valid_settings()
    assert configured.SECRET_KEY.startswith("ci-only-key-")
    assert "unit-test-db-secret" in configured.DATABASE_URL


@pytest.mark.parametrize("secret", [
    "",
    "change_me_in_production",
    "replace_with_at_least_32_random_characters_generated_locally",
    "your_secret_should_be_generated",
])
def test_settings_reject_jwt_secret_placeholders(secret):
    with pytest.raises(ValidationError):
        valid_settings(SECRET_KEY=secret)


@pytest.mark.parametrize("password", [
    "",
    "change_me_in_production",
    "replace_with_unique_database_password",
    "postgres",
])
def test_settings_reject_postgres_password_placeholders(password):
    url = f"postgresql://nexora:{password}@db:5432/nexora"
    with pytest.raises(ValidationError):
        valid_settings(DATABASE_URL=url)
