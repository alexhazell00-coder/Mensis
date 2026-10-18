from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel

from app.config import Settings
from app.engine import _direct_intent
from app.supervisor import ToolDefinition, ToolRejected, ToolRegistry
from app.tools import EmptyInput, build_tool_registry


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            data_dir=root / "state",
            database_path=root / "state" / "mensis.sqlite3",
            workspace=root / "workspace",
        )
        self.settings.prepare_directories()
        self.registry = build_tool_registry(self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_workspace_search_returns_paths_without_content(self) -> None:
        (self.settings.workspace / "backup-plan.txt").write_text(
            "The backup was verified on Tuesday.", encoding="utf-8"
        )
        (self.settings.workspace / "ordinary.txt").write_text(
            "backup rotation notes", encoding="utf-8"
        )
        (self.settings.workspace / ".env").write_text(
            "TOKEN=must-not-be-read", encoding="utf-8"
        )
        (self.settings.workspace / "private.pem").write_text(
            "private key", encoding="utf-8"
        )
        hidden = self.settings.workspace / ".hidden"
        hidden.mkdir()
        (hidden / "hidden.txt").write_text("backup", encoding="utf-8")

        result = self.registry.invoke("files.search", {"query": "backup"})

        self.assertEqual(result["match_count"], 2)
        self.assertEqual(
            {match["path"] for match in result["matches"]},
            {"backup-plan.txt", "ordinary.txt"},
        )
        self.assertFalse(result["content_snippets_included"])
        serialized = str(result)
        self.assertNotIn("verified on Tuesday", serialized)
        self.assertNotIn("must-not-be-read", serialized)
        self.assertNotIn("private.pem", serialized)

    def test_workspace_search_does_not_follow_symlinks(self) -> None:
        outside = Path(self.temp_dir.name) / "outside.txt"
        outside.write_text("distinctive word", encoding="utf-8")
        link = self.settings.workspace / "linked.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are not supported in this environment")

        result = self.registry.invoke("files.search", {"query": "distinctive"})
        self.assertEqual(result["match_count"], 0)

    def test_invalid_input_is_rejected(self) -> None:
        with self.assertRaises(ToolRejected):
            self.registry.invoke("files.search", {"query": "  "})
        with self.assertRaises(ToolRejected):
            self.registry.invoke("system.status", {"path": "/"})

    def test_permission_outside_allow_list_is_denied(self) -> None:
        class EmptyModel(BaseModel):
            pass

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                identifier="host.write",
                name="Write file",
                description="Not allowed",
                version="1.0.0",
                permission="host.write",
                input_model=EmptyModel,
                timeout_seconds=1,
                handler=lambda _value: {"ok": True},
            )
        )
        with self.assertRaises(ToolRejected):
            registry.invoke("host.write", {})

    def test_tool_schemas_are_discoverable_and_read_only(self) -> None:
        descriptions = self.registry.describe()
        self.assertEqual(
            {tool["id"] for tool in descriptions},
            {"system.status", "system.disk_usage", "files.search"},
        )
        self.assertTrue(all(tool["permission"].endswith(".read") for tool in descriptions))
        self.assertEqual(len(self.registry.model_schemas()), 3)


class DirectRoutingTests(unittest.TestCase):
    def test_obvious_tasks_do_not_need_a_model(self) -> None:
        cases = {
            "Check system health": "system.status",
            "Show my CPU and memory status": "system.status",
            "Check workspace disk usage": "system.disk_usage",
            "Find files named quarterly report in my workspace": "files.search",
            "Search for backup in workspace documents": "files.search",
            "Search my workspace for annual report": "files.search",
            "Find me the file called quarterly report": "files.search",
        }
        for request, expected_tool in cases.items():
            with self.subTest(request=request):
                intent = _direct_intent(request)
                self.assertIsNotNone(intent)
                self.assertEqual(intent.tool_id, expected_tool)

    def test_search_query_is_extracted_without_workspace_filler(self) -> None:
        self.assertEqual(
            _direct_intent("Search my workspace for annual report").arguments["query"],
            "annual report",
        )
        self.assertEqual(
            _direct_intent("Find me the file called quarterly report").arguments["query"],
            "quarterly report",
        )

    def test_unmapped_request_is_left_for_optional_provider(self) -> None:
        self.assertIsNone(_direct_intent("Draft a project plan for next week"))
        self.assertIsNone(_direct_intent("Check the backup service status"))
        self.assertEqual(
            _direct_intent("Find out whether there is enough disk space").tool_id,
            "system.disk_usage",
        )


if __name__ == "__main__":
    unittest.main()
