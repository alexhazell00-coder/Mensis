from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from app.config import Settings
from app.database import TaskStore
from app.engine import TaskEngine
from app.providers.ollama import ProviderReply
from app.tools import build_tool_registry


class FakeProvider:
    def __init__(self, replies: list[ProviderReply] | None = None) -> None:
        self.replies = replies or []
        self.calls = 0
        self.received_schemas: list[dict[str, Any]] | None = None
        self.received_messages: list[list[dict[str, Any]]] = []

    @property
    def configured(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "fake-local-model"

    def chat(self, messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> ProviderReply:
        self.calls += 1
        self.received_schemas = tool_schemas
        self.received_messages.append(list(messages))
        if not self.replies:
            raise AssertionError("Unexpected provider call")
        return self.replies.pop(0)


class TaskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            data_dir=root / "state",
            database_path=root / "state" / "mensis.sqlite3",
            workspace=root / "workspace",
        )
        self.settings.prepare_directories()
        self.store = TaskStore(self.settings.database_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def wait_for_status(self, engine: TaskEngine, task_id: str, expected: str) -> dict[str, Any]:
        deadline = time.monotonic() + 4
        last = None
        while time.monotonic() < deadline:
            last = engine.store.get_task(task_id)
            if last and last["status"] == expected:
                return last
            time.sleep(0.02)
        self.fail(f"Task did not reach {expected}; last status was {last and last['status']}")

    def test_deterministic_task_is_persisted_and_never_calls_provider(self) -> None:
        provider = FakeProvider()
        engine = TaskEngine(self.store, build_tool_registry(self.settings), provider)
        engine.start()
        try:
            task = engine.create_task("Check workspace disk usage")
            finished = self.wait_for_status(engine, task["id"], "completed")
            self.assertEqual(finished["verification"], "verified")
            self.assertEqual(finished["result"]["evidence"][0]["tool_id"], "system.disk_usage")
            self.assertEqual(provider.calls, 0)
            self.assertTrue(any(event["type"] == "verification" for event in finished["events"]))
        finally:
            engine.stop()

    def test_unmapped_request_without_configured_provider_is_blocked(self) -> None:
        class UnconfiguredProvider(FakeProvider):
            @property
            def configured(self) -> bool:
                return False

            @property
            def model_name(self) -> None:
                return None

        provider = UnconfiguredProvider()
        engine = TaskEngine(self.store, build_tool_registry(self.settings), provider)
        engine.start()
        try:
            task = engine.create_task("Prepare a new extension for my workflow")
            finished = self.wait_for_status(engine, task["id"], "blocked")
            self.assertEqual(finished["verification"], "not_applicable")
            self.assertIn("no local AI model", finished["result"]["answer"])
            self.assertEqual(provider.calls, 0)
        finally:
            engine.stop()

    def test_model_summary_is_unverified_and_carries_tool_evidence(self) -> None:
        provider = FakeProvider(
            [
                ProviderReply(
                    content="",
                    tool_calls=[{"name": "system.disk_usage", "arguments": {}}],
                    assistant_message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": "system.disk_usage", "arguments": {}}}],
                    },
                ),
                ProviderReply(
                    content="The workspace storage check has completed.",
                    tool_calls=[],
                    assistant_message={"role": "assistant", "content": "The workspace storage check has completed."},
                ),
            ]
        )
        engine = TaskEngine(self.store, build_tool_registry(self.settings), provider)
        engine.start()
        try:
            task = engine.create_task("Could you find out how much space is left on the workspace's filesystem?")
            finished = self.wait_for_status(engine, task["id"], "completed")
            self.assertEqual(finished["verification"], "unverified")
            self.assertIn("unverified", finished["result"]["verification_note"])
            self.assertEqual(len(finished["result"]["evidence"]), 1)
            self.assertEqual(finished["result"]["evidence"][0]["tool_id"], "system.disk_usage")
            self.assertTrue(all(schema["type"] == "function" for schema in provider.received_schemas or []))
            tool_message = provider.received_messages[1][-1]
            self.assertEqual(tool_message["role"], "tool")
            self.assertEqual(tool_message["name"], "system.disk_usage")
        finally:
            engine.stop()

    def test_queued_task_can_be_cancelled_before_it_runs(self) -> None:
        task_id = self.store.create_task("cancel before execution")
        accepted, task = self.store.request_cancel(task_id)
        self.assertTrue(accepted)
        self.assertEqual(task["status"], "cancelled")
        self.assertFalse(task["cancel_requested"])

    def test_running_task_honors_cancellation_at_provider_checkpoint(self) -> None:
        class BlockingProvider:
            entered = threading.Event()
            release = threading.Event()

            @property
            def configured(self) -> bool:
                return True

            @property
            def model_name(self) -> str:
                return "blocking-test-model"

            def chat(self, messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> ProviderReply:
                self.entered.set()
                if not self.release.wait(timeout=2):
                    raise TimeoutError("test provider timed out")
                return ProviderReply(
                    content="No external action was taken.",
                    tool_calls=[],
                    assistant_message={"role": "assistant", "content": "No external action was taken."},
                )

        provider = BlockingProvider()
        engine = TaskEngine(self.store, build_tool_registry(self.settings), provider)
        engine.start()
        try:
            task = engine.create_task("Explain the purpose of this installation")
            self.assertTrue(provider.entered.wait(timeout=2))
            accepted, current = engine.cancel_task(task["id"])
            self.assertTrue(accepted)
            self.assertTrue(current["cancel_requested"])
            provider.release.set()
            finished = self.wait_for_status(engine, task["id"], "cancelled")
            self.assertFalse(finished["cancel_requested"])
        finally:
            provider.release.set()
            engine.stop()

    def test_interrupted_read_only_task_is_recovered_to_queue(self) -> None:
        task_id = self.store.create_task("old task")
        self.assertTrue(self.store.claim_queued_task(task_id))
        self.assertTrue(self.store.transition(task_id, "running"))
        count = self.store.recover_interrupted_tasks()
        task = self.store.get_task(task_id)
        self.assertEqual(count, 1)
        self.assertEqual(task["status"], "queued")
        self.assertTrue(any(event["type"] == "recovered" for event in task["events"]))

    def test_resume_increments_attempt_and_keeps_history(self) -> None:
        task_id = self.store.create_task("needs a provider")
        self.assertTrue(self.store.claim_queued_task(task_id))
        self.assertTrue(self.store.transition(task_id, "blocked", error="provider unavailable"))
        resumed, task = self.store.resume_task(task_id)
        self.assertTrue(resumed)
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["attempt"], 2)
        self.assertIsNone(task["error"])


if __name__ == "__main__":
    unittest.main()
