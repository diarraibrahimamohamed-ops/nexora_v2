from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Nexora API"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    DATABASE_URL: str = ""
    REDIS_URL: str = ""
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    SECRET_KEY: str = ""
    ADMIN_EMAIL: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=5, le=1440)
    UPLOAD_DIR: str = "/storage/uploads"
    RESULTS_DIR: str = "/storage/results"
    TEMP_DIR: str = "/storage/temp"
    VINA_PATH: str = "/usr/local/bin/vina"
    VINA_CPU: int = Field(default=4, ge=1, le=32)
    VINA_EXHAUSTIVENESS: int = Field(default=8, ge=1, le=64)
    STRUCTURE_PROVIDER: str = "auto_rcsb"
    RECEPTOR_PDB_PATH: str = ""
    STRUCTURE_IDENTITY_CUTOFF: float = Field(default=0.7, ge=0.0, le=1.0)
    STRUCTURE_EVALUE_CUTOFF: float = Field(default=10.0, gt=0.0)
    STRUCTURE_COVERAGE_CUTOFF: float = Field(default=0.70, ge=0.0, le=1.0)
    COLABFOLD_BINARY: str = "colabfold_batch"
    COLABFOLD_OUTPUT_DIR: str = "/storage/temp/colabfold"
    COLABFOLD_NUM_MODELS: int = Field(default=5, ge=1, le=10)
    COLABFOLD_NUM_RECYCLE: int = Field(default=3, ge=1, le=20)
    NCBI_EMAIL: str = ""
    NCBI_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 32 or value.lower() in {"change-me-in-production", "changeme", "secret", "secret-key"}:
            raise ValueError("SECRET_KEY must be a strong random value of at least 32 characters")
        return value

    @field_validator("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
    @classmethod
    def validate_runtime_urls(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Runtime service URLs must be explicitly configured")
        return value

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()