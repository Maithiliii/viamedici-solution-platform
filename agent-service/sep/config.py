from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    database_url: str = "postgresql://directus:directus@localhost:5432/solution_platform"
    directus_url: str = "http://localhost:8055"
    directus_admin_email: str = "admin@example.com"
    directus_admin_password: str = ""
    directus_agent_token: str = ""
    agent_service_secret: str = ""
    data_dir: str = "/app/data"
    eval_dir: str = "/app/eval"

    # LLM: provider-agnostic. Comma separated model chain = automatic fallback order.
    llm_provider: str = "gemini"            # gemini | openai_compat
    gemini_api_key: str = ""
    gemini_models: str = "gemini-3.1-flash-lite,gemini-flash-lite-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    groq_api_key: str = ""
    groq_models: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_s: float = 60.0
    llm_min_interval_s: float = 0.5         # client-side throttle for free-tier rate limits
    llm_temperature: float = 0.2

    # Embeddings
    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    ollama_base_url: str = "http://localhost:11434"

    # Agent behaviour
    retrieval_max_steps: int = 6
    search_top_k: int = 8
    abstain_rrf_threshold: float = 0.02     # below this fused score the hit is "weak"

    @property
    def gemini_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    @property
    def groq_model_list(self) -> list[str]:
        return [m.strip() for m in self.groq_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
