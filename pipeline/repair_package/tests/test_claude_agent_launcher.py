from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("claude_agent_launcher", ROOT / "claude_agent_launcher.py")
assert SPEC and SPEC.loader
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


class ClaudeAgentLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "проект с пробелом"
        self.project.mkdir()
        self.package = self.project / "repair package.json"
        self.package.write_text("{}\n", encoding="utf-8")
        self.prompt = self.project / "agent prompt.md"
        self.prompt.write_text("Repair only OBJ-A.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def arguments(self) -> list[str]:
        return [
            "--project-root", str(self.project),
            "--repair-package", str(self.package),
            "--prompt", str(self.prompt),
            "--claude", sys.executable,
        ]

    @mock.patch.object(LAUNCHER.subprocess, "run")
    def test_launches_claude_print_with_stdin_and_project_cwd(self, run: mock.Mock) -> None:
        run.return_value.returncode = 0
        self.assertEqual(LAUNCHER.main(self.arguments()), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(Path(sys.executable).resolve()))
        self.assertIn("-p", command)
        self.assertEqual(command[command.index("--tools") + 1], LAUNCHER.AGENT_TOOLS)
        self.assertNotIn("Bash", LAUNCHER.AGENT_TOOLS)
        self.assertEqual(command[command.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(command[command.index("--add-dir") + 1], str(self.project.resolve()))
        self.assertEqual(run.call_args.kwargs["cwd"], self.project.resolve())
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        stdin = run.call_args.kwargs["input"]
        self.assertIn(str(self.package.resolve()), stdin)
        self.assertIn("Repair only OBJ-A.", stdin)

    @mock.patch.object(LAUNCHER.subprocess, "run")
    def test_returns_claude_exit_code(self, run: mock.Mock) -> None:
        run.return_value.returncode = 7
        self.assertEqual(LAUNCHER.main(self.arguments()), 7)

    @mock.patch.object(LAUNCHER.subprocess, "run")
    def test_check_validates_without_launching(self, run: mock.Mock) -> None:
        self.assertEqual(LAUNCHER.main([*self.arguments(), "--check"]), 0)
        run.assert_not_called()

    def test_missing_input_fails_before_launch(self) -> None:
        self.package.unlink()
        self.assertEqual(LAUNCHER.main(self.arguments()), 2)

    @mock.patch.object(LAUNCHER.shutil, "which", return_value=None)
    def test_missing_claude_on_path_is_reported(self, _which: mock.Mock) -> None:
        args = self.arguments()
        args = args[: args.index("--claude")]
        self.assertEqual(LAUNCHER.main(args), 2)


if __name__ == "__main__":
    unittest.main()
