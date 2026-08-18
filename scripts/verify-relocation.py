#!/usr/bin/env python3
"""Verify that this checkout is self-contained at its current location."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SKIP_DIRECTORIES = {".venv", "node_modules", "cache", ".git", ".codex", "__pycache__"}


def fail(message: str) -> None:
    print(f"FAIL {message}")
    raise SystemExit(1)


def iter_project_files() -> list[Path]:
    files: list[Path] = []
    for root, directories, names in os.walk(PROJECT_DIR):
        directories[:] = [name for name in directories if name not in SKIP_DIRECTORIES]
        files.extend(Path(root) / name for name in names)
    return files


def check_virtualenv() -> None:
    expected = PROJECT_DIR / ".venv"
    actual = Path(sys.prefix).resolve()
    if actual != expected.resolve():
        fail(f"venv: expected {expected}, running from {actual}")
    print(f"OK venv: {actual}")


def check_forbidden_path(forbidden_path: str) -> None:
    matches: list[str] = []
    for path in iter_project_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if forbidden_path in text:
            matches.append(str(path.relative_to(PROJECT_DIR)))
    if matches:
        fail(f"stale path {forbidden_path!r} in: {', '.join(matches)}")
    print(f"OK path audit: no project source references {forbidden_path}")


def check_launch_agent(label: str) -> None:
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist.exists():
        fail(f"launchd: missing {plist}")
    contents = plist.read_text(encoding="utf-8")
    project = str(PROJECT_DIR)
    if project not in contents:
        fail(f"launchd: {plist} does not reference {project}")
    domain = f"gui/{os.getuid()}/{label}"
    result = subprocess.run(
        ["launchctl", "print", domain], capture_output=True, check=False, text=True
    )
    if result.returncode != 0:
        fail(f"launchd: {domain} is not loaded: {result.stderr.strip()}")
    if project not in result.stdout:
        fail(f"launchd: loaded service does not run from {project}")
    print(f"OK launchd: {label} is loaded from {project}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--forbid-path",
        help="Fail if this literal path occurs in project source or documentation.",
    )
    parser.add_argument(
        "--check-launch-agent",
        action="store_true",
        help="Also verify the installed and loaded macOS LaunchAgent.",
    )
    parser.add_argument(
        "--label",
        default="com.slavko.discord-music-bot",
        help="LaunchAgent label used with --check-launch-agent.",
    )
    args = parser.parse_args()

    check_virtualenv()
    print(f"OK project: {PROJECT_DIR}")
    if args.forbid_path:
        check_forbidden_path(args.forbid_path)
    if args.check_launch_agent:
        check_launch_agent(args.label)


if __name__ == "__main__":
    main()
