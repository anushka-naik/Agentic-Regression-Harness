"""Runtime settings loaded from the environment or a local ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    artifacts_dir: Path = Field(default=Path("artifacts"), alias="ARTIFACTS_DIR")
    project_root: Path = Field(default_factory=Path.cwd, alias="AGENTCHECK_PROJECT_ROOT")
    dataset_dir: Path = Field(default=Path("data/retail_v1"), alias="AGENTCHECK_DATASET_DIR")

    def resolve(self, path: Path | str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else (self.project_root / candidate)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
