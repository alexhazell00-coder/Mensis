from pathlib import Path
import unittest


class WindowsSetupScriptTests(unittest.TestCase):
    def test_batch_installs_requirements_and_starts_on_loopback_by_default(self) -> None:
        script = Path(__file__).resolve().parents[1] / "setup.bat"
        content = script.read_text(encoding="utf-8").casefold()

        self.assertIn("py -3", content)
        self.assertIn("sys.version_info >= (3, 11)", content)
        self.assertIn("-m venv .venv", content)
        self.assertIn("-m pip install -r requirements.txt", content)
        self.assertIn('set "mensis_host=127.0.0.1"', content)
        self.assertIn('set "mensis_port=8000"', content)
        self.assertIn('".venv\\scripts\\python.exe" -m app.main', content)

    def test_batch_preserves_user_paths_and_does_not_delete_existing_environment(self) -> None:
        script = Path(__file__).resolve().parents[1] / "setup.bat"
        content = script.read_text(encoding="utf-8").casefold()

        self.assertIn("if not defined mensis_data_dir", content)
        self.assertIn("if not defined mensis_workspace", content)
        self.assertIn("remove the .venv folder manually", content)
        self.assertNotIn("rmdir /s", content)
        self.assertNotIn("del /s", content)


if __name__ == "__main__":
    unittest.main()
