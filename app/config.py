"""Runtime configuration for the local-first MENSIS service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(value: str | None, default: int, *, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 1), maximum)


def _positive_float(value: str | None, default: float, *, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, maximum)


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings are deliberately small and can all be supplied through env vars."""

    data_dir: Path
    database_path: Path
    workspace: Path
    max_workers: int = 1
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    provider_timeout_seconds: float = 45.0

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(
            os.environ.get(
                "MENSIS_DATA_DIR", "~/.local/share/mensis"
            )
        ).expanduser().resolve()
        database_path = Path(
            os.environ.get("MENSIS_DB_PATH", str(data_dir / "mensis.sqlite3"))
        ).expanduser().resolve()
        workspace = Path(
            os.environ.get("MENSIS_WORKSPACE", str(data_dir / "workspace"))
        ).expanduser().resolve()
        return cls(
            data_dir=data_dir,
            database_path=database_path,
            workspace=workspace,
            max_workers=_positive_int(
                os.environ.get("MENSIS_MAX_WORKERS"), 1, maximum=4
            ),
            ollama_base_url=os.environ.get(
                "MENSIS_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
            ).rstrip("/"),
            ollama_model=os.environ.get("MENSIS_OLLAMA_MODEL", "").strip(),
            provider_timeout_seconds=_positive_float(
                os.environ.get("MENSIS_PROVIDER_TIMEOUT"), 45.0, maximum=120.0
            ),
        )

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
