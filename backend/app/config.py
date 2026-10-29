from functools import lru_cache
from urllib.parse import unquote, urlsplit

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
    STORAGE_DIR: str = "/storage"
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
    TAMARIND_API_KEY: str = ""

    # African natural-products catalogue / external identity services
    ANPDB_BASE_URL: str = "https://phabidb.vm.uni-freiburg.de"
    AFRICAN_LIBS_DIR: str = "/storage/african_libs"
    PUBCHEM_API_URL: str = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    PHYTOCHEM_MAX_IMPORT: int = Field(default=500, ge=1, le=5000)

    # AMRFinderPlus is optional and must have a separately updated database.
    AMRFINDER_PATH: str = "amrfinder"
    AMRFINDER_DB_DIR: str = "/storage/reference/amrfinder"
    AMRFINDER_REQUIRED: bool = False

    # Optional local Ollama RAG assistant; never required for deterministic analyses.
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "gemma2:2b"
    OFFLINE_PACKAGES_DIR: str = "/storage/offline_packages"

    @field_validator("ALGORITHM")
    @classmethod
    def validate_jwt_algorithm(cls, value: str) -> str:
        if value.strip() != "HS256":
            raise ValueError("Only HS256 is supported for JWT signing and verification")
        return "HS256"

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        value = value.strip()
        lowered = value.lower()
        placeholder_prefixes = ("replace_with_", "replace-me", "change_me", "change-me", "your_secret", "your-random")
        if (
            len(value) < 32
            or lowered in {"change-me-in-production", "change_me_in_production", "changeme", "secret", "secret-key"}
            or lowered.startswith(placeholder_prefixes)
        ):
            raise ValueError("SECRET_KEY must be a strong random value; sample placeholders are not accepted")
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("DATABASE_URL must be explicitly configured")
        parsed = urlsplit(value)
        if parsed.scheme.startswith("postgres"):
            password = unquote(parsed.password or "")
            lowered = password.lower()
            if (
                not password
                or lowered in {"change_me_in_production", "change-me-in-production", "changeme", "password", "postgres"}
                or lowered.startswith(("replace_", "change_me", "change-me", "your_password"))
            ):
                raise ValueError("DATABASE_URL must use a non-empty real database password; sample placeholders are not accepted")
        return value

    @field_validator("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
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