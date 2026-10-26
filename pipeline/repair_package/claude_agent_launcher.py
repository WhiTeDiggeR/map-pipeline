#!/usr/bin/env python3
"""Adapt the T25 Agent Fix launcher contract to ``claude -p`` (Claude Code, non-interactive)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


VERSION = "1.0.0"
# No Bash and no network tools: the agent may only read files and edit the allowlisted ones.
# The runner still hashes the project before/after and rejects any non-allowlisted change.
AGENT_TOOLS = "Read,Edit,Write,Glob,Grep"


class LauncherError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch one restricted map repair with Claude Code")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--repair-package", required=True, type=Path)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--claude", help="Claude Code executable; defaults to resolving 'claude' from PATH")
    parser.add_argument("--claude-arg", action="append", default=[], help="Fixed argument appended after the built-in options")
    parser.add_argument("--check", action="store_true", help="Validate inputs and print the command without launching")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args(argv)


def require_directory(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise LauncherError(f"{label} does not exist or is not a directory: {resolved}")
    return resolved


def require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise LauncherError(f"{label} does not exist or is not a file: {resolved}")
    return resolved


def resolve_claude(value: str | None) -> Path:
    if value:
        candidate = Path(value).expanduser().resolve()
        if not candidate.is_file():
            raise LauncherError(f"Claude executable does not exist: {candidate}")
        return candidate
    found = shutil.which("claude")
    if not found:
        raise LauncherError("Claude executable was not provided and 'claude' is not available on PATH")
    return Path(found).resolve()


def build_command(claude: Path, project_root: Path, extra_args: Sequence[str]) -> list[str]:
    return [
        str(claude),
        "-p",
        "--tools", AGENT_TOOLS,
        "--permission-mode", "acceptEdits",
        "--no-session-persistence",
        "--output-format", "text",
        "--add-dir", str(project_root),
        *extra_args,
    ]


def build_prompt(prompt: Path, repair_package: Path) -> str:
    instructions = prompt.read_text(encoding="utf-8")
    return (
        f"Authoritative repair package: {repair_package}\n"
        "Read that JSON before editing. Treat its writable file policy and allowed actions as hard limits. "
        "Do not invoke another agent and do not expand scope.\n\n"
        f"{instructions}"
    )


def execute(args: argparse.Namespace) -> int:
    project_root = require_directory(args.project_root, "Project root")
    repair_package = require_file(args.repair_package, "Repair package")
    prompt = require_file(args.prompt, "Agent prompt")
    claude = resolve_claude(args.claude)
    command = build_command(claude, project_root, args.claude_arg)
    if args.check:
        print("Launcher inputs are valid.")
        print("Command:")
        print(repr(command))
        return 0
    process = subprocess.run(
        command,
        cwd=project_root,
        input=build_prompt(prompt, repair_package),
        text=True,
        encoding="utf-8",
        check=False,
    )
    return process.returncode


def main(argv: list[str] | None = None) -> int:
    try:
        return execute(parse_args(argv))
    except (LauncherError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
