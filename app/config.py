from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_env: str = "development"
    app_version: str = "1.0.0"
    log_level: str = "info"

    # API Authentication
    cms_api_key: str = "change-me-to-a-secret"

    # Database
    database_url: str = "postgresql://user:pass@localhost:5432/evropuvefur"

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = "evropuvefur"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "eu-west-1"

    # Gemini
    gemini_api_key: str = ""
    gemini_pro_model: str = "gemini-3-pro"
    gemini_flash_model: str = "gemini-3-flash"
    gemini_pro_daily_limit: int = 200

    # CORS
    cors_allowed_origins: str = "https://www.evropuvefur.is,https://evropuvefur.is"

    # Rate Limiting
    query_rate_limit: str = "10/minute"
    sync_rate_limit: str = "100/minute"

    # Cache
    query_cache_ttl_hours: int = 24

    # RAG
    rag_top_k: int = 5
    rag_score_threshold: float = 0.3

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
