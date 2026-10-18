"""The initial, deliberately read-only MENSIS tool set."""

from __future__ import annotations

import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings
from app.supervisor import ToolDefinition, ToolRegistry


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200, description="Text to find in names or text files.")
    max_results: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def query_must_contain_non_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must contain non-whitespace characters")
        return value


def _human_bytes(size: int) -> str:
    amount = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{size} B"


def _memory_info() -> dict[str, int | None]:
    """Read Linux memory totals when available, with a portable fallback."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values: dict[str, int] = {}
        try:
            for line in meminfo.read_text(encoding="ascii", errors="ignore").splitlines():
                key, _, rest = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    match = re.search(r"(\d+)", rest)
                    if match:
                        values[key] = int(match.group(1)) * 1024
            return {
                "total_bytes": values.get("MemTotal"),
                "available_bytes": values.get("MemAvailable"),
            }
        except OSError:
            pass

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        return {"total_bytes": page_size * total_pages, "available_bytes": None}
    except (AttributeError, OSError, ValueError):
        return {"total_bytes": None, "available_bytes": None}


def _disk_usage(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path_label": "configured workspace",
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_human": _human_bytes(usage.total),
        "used_human": _human_bytes(usage.used),
        "free_human": _human_bytes(usage.free),
        "percent_used": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
    }


def _system_status(settings: Settings) -> dict[str, Any]:
    memory = _memory_info()
    try:
        disk = _disk_usage(settings.workspace)
    except OSError:
        disk = None
    return {
        "operating_system": platform.system(),
        "platform_version": platform.release(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory": {
            **memory,
            "total_human": _human_bytes(memory["total_bytes"])
            if memory["total_bytes"] is not None
            else None,
            "available_human": _human_bytes(memory["available_bytes"])
            if memory["available_bytes"] is not None
            else None,
        },
        "workspace_storage": disk,
    }


_SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "secrets.json",
}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".secret")
_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".cache",
    "dist",
    "build",
}
_MAX_SCANNED_FILES = 5000
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 12 * 1024 * 1024


def _is_sensitive(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in _SENSITIVE_NAMES
        or name.startswith(".env.")
        or name.endswith(_SENSITIVE_SUFFIXES)
    )


def _search_workspace(settings: Settings, arguments: SearchInput) -> dict[str, Any]:
    """Search ordinary files under the configured root without following symlinks."""
    root = settings.workspace.resolve()
    query = arguments.query.casefold().strip()
    matches: list[dict[str, str]] = []
    scanned_files = 0
    scanned_bytes = 0
    truncated = False

    for directory, subdirectories, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        subdirectories[:] = [
            name
            for name in subdirectories
            if not name.startswith(".")
            and name.casefold() not in _IGNORED_DIRS
            and not (directory_path / name).is_symlink()
        ]
        for filename in filenames:
            path = directory_path / filename
            if filename.startswith(".") or _is_sensitive(path) or path.is_symlink():
                continue
            try:
                resolved = path.resolve(strict=True)
                relative = resolved.relative_to(root).as_posix()
                info = resolved.stat()
            except (OSError, ValueError):
                continue
            if not resolved.is_file():
                continue
            if scanned_files >= _MAX_SCANNED_FILES or scanned_bytes >= _MAX_TOTAL_BYTES:
                truncated = True
                break
            scanned_files += 1
            if query in filename.casefold():
                matches.append({"path": relative, "match": "filename"})
            elif info.st_size <= _MAX_FILE_BYTES:
                try:
                    content = resolved.read_bytes()
                except OSError:
                    continue
                scanned_bytes += len(content)
                if b"\x00" not in content and query in content.decode("utf-8", errors="ignore").casefold():
                    matches.append({"path": relative, "match": "file contents"})
            if len(matches) >= arguments.max_results:
                truncated = True
                break
        if truncated:
            break

    return {
        "query": arguments.query,
        "workspace_label": root.name or "workspace",
        "matches": matches,
        "match_count": len(matches),
        "scanned_files": scanned_files,
        "truncated": truncated,
        "content_snippets_included": False,
    }


def build_tool_registry(settings: Settings) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            identifier="system.status",
            name="System status",
            description="Read basic host, runtime, memory, CPU, and configured-workspace storage information. Read-only.",
            version="1.0.0",
            permission="system.read",
            input_model=EmptyInput,
            timeout_seconds=5,
            handler=lambda _input: _system_status(settings),
        )
    )
    registry.register(
        ToolDefinition(
            identifier="system.disk_usage",
            name="Workspace disk usage",
            description="Read total, used, and free storage for the configured MENSIS workspace filesystem. Read-only.",
            version="1.0.0",
            permission="system.read",
            input_model=EmptyInput,
            timeout_seconds=5,
            handler=lambda _input: _disk_usage(settings.workspace),
        )
    )
    registry.register(
        ToolDefinition(
            identifier="files.search",
            name="Search workspace files",
            description="Search ordinary filenames and small UTF-8 text files inside the configured workspace. Hidden paths, common build folders, symlinks, and likely secrets are skipped; file contents are never returned. Read-only.",
            version="1.0.0",
            permission="workspace.read",
            input_model=SearchInput,
            timeout_seconds=15,
            handler=lambda arguments: _search_workspace(settings, arguments),
        )
    )
    return registry
