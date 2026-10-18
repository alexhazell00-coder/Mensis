"""Small policy boundary for tool execution.

The language model never receives direct host access. Every request passes through
this registry and supervisor, which currently allow only bounded read operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError


class ToolRejected(Exception):
    """A tool request was invalid or outside the supervisor's policy."""


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    identifier: str
    name: str
    description: str
    version: str
    permission: str
    input_model: Type[BaseModel]
    timeout_seconds: float
    handler: Callable[[BaseModel], dict[str, Any]]


class Supervisor:
    """Enforces the fixed, least-privilege tool allow-list for this MVP."""

    ALLOWED_PERMISSIONS = frozenset({"system.read", "workspace.read"})

    def authorize(self, tool: ToolDefinition) -> None:
        if tool.permission not in self.ALLOWED_PERMISSIONS:
            raise ToolRejected(
                f"Permission '{tool.permission}' is not enabled in this installation."
            )


class ToolRegistry:
    def __init__(self, supervisor: Supervisor | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._supervisor = supervisor or Supervisor()

    def register(self, tool: ToolDefinition) -> None:
        if tool.identifier in self._tools:
            raise ValueError(f"Duplicate tool identifier: {tool.identifier}")
        self._tools[tool.identifier] = tool

    def get(self, identifier: str) -> ToolDefinition | None:
        return self._tools.get(identifier)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "id": tool.identifier,
                "name": tool.name,
                "description": tool.description,
                "version": tool.version,
                "permission": tool.permission,
                "timeout_seconds": tool.timeout_seconds,
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def model_schemas(self) -> list[dict[str, Any]]:
        """Return the read-only function tools understood by local model APIs."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.identifier,
                    "description": tool.description,
                    "parameters": tool.input_model.model_json_schema(),
                },
            }
            for tool in self._tools.values()
        ]

    def invoke(self, identifier: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        tool = self.get(identifier)
        if tool is None:
            raise ToolRejected(f"Unknown tool '{identifier}'.")
        self._supervisor.authorize(tool)
        try:
            validated = tool.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'input'}: {error['msg']}"
                for error in exc.errors()[:4]
            )
            raise ToolRejected(f"Invalid input for {identifier}: {details}") from exc
        result = tool.handler(validated)
        if not isinstance(result, dict):
            raise RuntimeError(f"Tool '{identifier}' returned a non-object result.")
        return result
