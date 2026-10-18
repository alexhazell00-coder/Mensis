from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.main import create_app


class LocalAsgiClient:
    """Small synchronous test helper over httpx's supported ASGI transport."""

    def __init__(self, application) -> None:
        self.application = application

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.application, raise_app_exceptions=True)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        settings = Settings(
            data_dir=root / "state",
            database_path=root / "state" / "mensis.sqlite3",
            workspace=root / "workspace",
        )
        self.application = create_app(settings)
        self.application.state.engine.start()
        self.client = LocalAsgiClient(self.application)

    def tearDown(self) -> None:
        self.application.state.engine.stop()
        self.temp_dir.cleanup()

    def wait_for_task(self, task_id: str, status: str) -> dict:
        deadline = time.monotonic() + 4
        last = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/tasks/{task_id}")
            self.assertEqual(response.status_code, 200)
            last = response.json()
            if last["status"] == status:
                return last
            time.sleep(0.02)
        self.fail(f"Task did not reach {status}; last state was {last and last['status']}")

    def test_health_and_tool_contracts(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertFalse(health.json()["provider"]["configured"])

        tools = self.client.get("/api/tools").json()["tools"]
        self.assertEqual(len(tools), 3)
        self.assertTrue(all(tool["permission"].endswith(".read") for tool in tools))

    def test_submit_task_and_inspect_persistent_result(self) -> None:
        created = self.client.post("/api/tasks", json={"request": "Check system health"})
        self.assertEqual(created.status_code, 202)
        task = self.wait_for_task(created.json()["id"], "completed")
        self.assertEqual(task["verification"], "verified")
        self.assertIn("evidence", task["result"])
        self.assertGreaterEqual(len(task["events"]), 5)

        listed = self.client.get("/api/tasks?limit=10").json()["tasks"]
        self.assertEqual(listed[0]["id"], task["id"])

    def test_unknown_request_is_not_sent_to_a_provider(self) -> None:
        created = self.client.post("/api/tasks", json={"request": "Draft a complete automation module"})
        self.assertEqual(created.status_code, 202)
        task = self.wait_for_task(created.json()["id"], "blocked")
        self.assertIn("no local AI model", task["result"]["answer"])

    def test_blank_task_is_rejected_and_unknown_id_is_not_found(self) -> None:
        response = self.client.post("/api/tasks", json={"request": "   "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/tasks/not-a-task").status_code, 404)

    def test_web_shell_is_served(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("MENSIS", response.text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
