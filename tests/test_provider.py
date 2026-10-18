from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from app.providers.ollama import OllamaProvider, ProviderError


class OllamaProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = OllamaProvider(
            base_url="http://127.0.0.1:11434/",
            model="test-model",
            timeout_seconds=3,
        )

    def test_tool_call_response_is_parsed_and_timeout_is_set(self) -> None:
        body = json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "files.search",
                                "arguments": {"query": "quarterly report"},
                            }
                        }
                    ],
                }
            }
        ).encode()
        with patch("app.providers.ollama.urllib.request.urlopen", return_value=io.BytesIO(body)) as urlopen:
            reply = self.provider.chat(
                [{"role": "user", "content": "Find the report"}],
                [{"type": "function", "function": {"name": "files.search"}}],
            )
        self.assertEqual(reply.tool_calls[0]["name"], "files.search")
        self.assertEqual(reply.tool_calls[0]["arguments"]["query"], "quarterly report")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 3)

    def test_malformed_tool_arguments_are_rejected(self) -> None:
        body = json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "system.status", "arguments": ["invalid"]}}
                    ],
                }
            }
        ).encode()
        with patch("app.providers.ollama.urllib.request.urlopen", return_value=io.BytesIO(body)):
            with self.assertRaises(ProviderError):
                self.provider.chat([], [])

    def test_unconfigured_provider_refuses_to_make_a_request(self) -> None:
        provider = OllamaProvider(
            base_url="http://127.0.0.1:11434",
            model="",
        )
        with patch("app.providers.ollama.urllib.request.urlopen") as urlopen:
            with self.assertRaises(ProviderError):
                provider.chat([], [])
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
