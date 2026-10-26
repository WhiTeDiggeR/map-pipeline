#!/usr/bin/env python3
"""Adapt the T25 Agent Fix launcher contract to ``codex exec``."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


VERSION = "1.0.0"


class LauncherError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch one restricted map repair with Codex")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--repair-package", required=True, type=Path)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--codex", help="Codex executable; defaults to resolving 'codex' from PATH")
    parser.add_argument("--codex-arg", action="append", default=[], help="Fixed argument inserted before 'exec'")
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


def resolve_codex(value: str | None) -> Path:
    if value:
        candidate = Path(value).expanduser().resolve()
        if not candidate.is_file():
            raise LauncherError(f"Codex executable does not exist: {candidate}")
        return candidate
    found = shutil.which("codex")
    if not found:
        raise LauncherError("Codex executable was not provided and 'codex' is not available on PATH")
    return Path(found).resolve()


def build_command(codex: Path, project_root: Path, fixed_args: Sequence[str]) -> list[str]:
    return [
        str(codex),
        *fixed_args,
        "exec",
        "--cd",
        str(project_root),
        "--sandbox",
        "workspace-write",
        "--approve-for-me",
        "--ephemeral",
        "--color",
        "never",
        "-",
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
    codex = resolve_codex(args.codex)
    command = build_command(codex, project_root, args.codex_arg)
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
