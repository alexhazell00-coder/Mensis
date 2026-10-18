"""Optional server-side adapter for a user-managed local Ollama instance."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings


class ProviderError(RuntimeError):
    """A provider request could not be completed safely."""


@dataclass(frozen=True, slots=True)
class ProviderReply:
    content: str
    tool_calls: list[dict[str, Any]]
    assistant_message: dict[str, Any]


class OllamaProvider:
    """Minimal non-streaming Ollama chat adapter with bounded request timeouts."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MENSIS_OLLAMA_BASE_URL must be an http(s) URL.")

    @classmethod
    def from_settings(cls, settings: Settings) -> "OllamaProvider":
        return cls(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.provider_timeout_seconds,
        )

    @property
    def configured(self) -> bool:
        return bool(self.model)

    @property
    def model_name(self) -> str | None:
        return self.model or None

    def chat(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ProviderReply:
        if not self.configured:
            raise ProviderError("No local model is configured.")
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "tools": tool_schemas,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"Local model returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            suffix = f" ({reason})" if reason else ""
            raise ProviderError(f"Local model is unavailable{suffix}.") from exc
        if len(body) > 4 * 1024 * 1024:
            raise ProviderError("Local model response exceeded the 4 MiB response limit.")
        try:
            response_data = json.loads(body.decode("utf-8"))
            message = response_data["message"]
            if not isinstance(message, dict):
                raise TypeError("message is not an object")
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            raw_calls = message.get("tool_calls", []) or []
            if not isinstance(raw_calls, list):
                raise TypeError("tool_calls is not a list")
            calls: list[dict[str, Any]] = []
            for raw_call in raw_calls[:8]:
                if not isinstance(raw_call, dict):
                    raise ProviderError("Local model returned an invalid tool request.")
                function = raw_call.get("function", {})
                if not isinstance(function, dict):
                    raise ProviderError("Local model returned an invalid tool function.")
                name = function.get("name")
                if not isinstance(name, str):
                    raise ProviderError("Local model returned a tool request without a name.")
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as exc:
                        raise ProviderError("Local model returned malformed tool arguments.") from exc
                if not isinstance(arguments, dict):
                    raise ProviderError("Local model returned tool arguments that are not an object.")
                calls.append({"name": name, "arguments": arguments})
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderError("Local model returned an invalid chat response.") from exc

        assistant_message = {
            "role": "assistant",
            "content": content,
        }
        if calls:
            assistant_message["tool_calls"] = [
                {
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments"],
                    }
                }
                for call in calls
            ]
        return ProviderReply(
            content=content,
            tool_calls=calls,
            assistant_message=assistant_message,
        )
