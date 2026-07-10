# Application Configuration
import os
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # App
    APP_NAME: str = "BaseAgent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "******")

    # Database
    DB_HOST: str = os.getenv("DB_HOST", "mysql")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_USER: str = os.getenv("DB_USER", "baseagent")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "******")
    DB_NAME: str = os.getenv("DB_NAME", "baseagent")

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))

    # JWT
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "******")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Qdrant Vector DB
    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "qdrant")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://qdrant:6333")

    # BM25 (Hybrid Search Phase 1)
    BM25_DIR: str = os.getenv("BM25_DIR", "data/db/bm25")

    # Embedding
    EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # File Upload
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./data/uploads")
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "40"))

    # Celery
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/1")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/2")

    # External APIs
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # Ollama
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")

    # CORS
    CORS_ORIGINS: list[str] = os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://localhost:8080"
    ).split(",")

    # ReAct Loop 配置
    # v2.0: 从默认 5 提升至 20，以支持标准 SKILL.md 的 8+ 步复杂工作流
    REACT_MAX_ITERATIONS: int = int(os.getenv("REACT_MAX_ITERATIONS", "20"))
    REACT_GLOBAL_TIMEOUT: int = int(os.getenv("REACT_GLOBAL_TIMEOUT", "120"))
    REACT_TOOL_TIMEOUT: int = int(os.getenv("REACT_TOOL_TIMEOUT", "30"))
    REACT_SHOW_THINKING: bool = os.getenv("REACT_SHOW_THINKING", "false").lower() == "true"
    REACT_MAX_OBSERVATION_LENGTH: int = int(os.getenv("REACT_MAX_OBSERVATION_LENGTH", "8000"))
    # v17.0: ReAct 收敛三机制
    REACT_MAX_ROUNDS: int = int(os.getenv("REACT_MAX_ROUNDS", "10"))

    # Tool Semantic Retrieval
    TOOL_RETRIEVAL_TOP_K: int = int(os.getenv("TOOL_RETRIEVAL_TOP_K", "3"))
    TOOL_RETRIEVAL_THRESHOLD: float = float(os.getenv("TOOL_RETRIEVAL_THRESHOLD", "0.25"))
    TOOL_RETRIEVAL_FALLBACK_ENABLED: bool = os.getenv("TOOL_RETRIEVAL_FALLBACK_ENABLED", "true").lower() in ("true", "1", "yes")
    TOOL_ROUTING_SEMANTIC_THRESHOLD: float = float(os.getenv("TOOL_ROUTING_SEMANTIC_THRESHOLD", "0.25"))
    TOOL_FALLBACK_SEMANTIC_THRESHOLD: float = float(os.getenv("TOOL_FALLBACK_SEMANTIC_THRESHOLD", "0.22"))
    TOOL_FALLBACK_WHITELIST: list[str] = os.getenv(
        "TOOL_FALLBACK_WHITELIST",
        "image_search,tavily_web_search,web_fetch_content,document_reader,get_current_time"
    ).split(",")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()