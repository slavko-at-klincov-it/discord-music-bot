#!/usr/bin/env python3
import importlib
import os
import py_compile
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
REQUIRED_ENV = ("DISCORD_TOKEN", "DISCORD_CLIENT_ID", "DISCORD_GUILD_ID")
REQUIRED_MODULES = ("discord", "dotenv", "librosa", "numpy")


def fail(message: str) -> None:
    print(f"FAIL {message}")
    raise SystemExit(1)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("'\"")


def check_env() -> None:
    load_env_file(PROJECT_DIR / ".env")
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        fail(f"env: missing {', '.join(missing)}")
    try:
        int(os.environ["DISCORD_GUILD_ID"])
    except ValueError as error:
        raise SystemExit("FAIL env: DISCORD_GUILD_ID must be numeric") from error
    print("OK env: Discord token/client/guild variables are set.")


def check_python_version() -> None:
    if sys.version_info < (3, 12):
        fail(f"python: expected >= 3.12, got {sys.version.split()[0]}")
    print(f"OK python: {sys.version.split()[0]}")


def check_modules() -> None:
    for module in REQUIRED_MODULES:
        importlib.import_module(module)
    print(f"OK modules: {', '.join(REQUIRED_MODULES)}")


def check_compile() -> None:
    for path in (PROJECT_DIR / "bot.py", PROJECT_DIR / "rhythm.py"):
        py_compile.compile(path, doraise=True)
    print("OK compile: bot.py and rhythm.py")


def check_executable(env_name: str, default: str, args: list[str]) -> None:
    command = os.environ.get(env_name, default)
    if not Path(command).is_absolute():
        fail(f"{env_name}: use an absolute path for launchd, got {command!r}")
    if not os.access(command, os.X_OK):
        fail(f"{env_name}: executable not found or not runnable at {command}")
    result = subprocess.run(
        [command, *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        fail(f"{env_name}: {result.stderr.strip() or result.stdout.strip()}")
    first_line = (result.stdout or result.stderr).strip().splitlines()[0]
    print(f"OK {env_name}: {first_line}")


def main() -> None:
    check_env()
    check_python_version()
    check_modules()
    check_compile()
    check_executable("YT_DLP_PATH", "/opt/homebrew/bin/yt-dlp", ["--version"])
    check_executable("FFMPEG_PATH", "/opt/homebrew/bin/ffmpeg", ["-version"])


if __name__ == "__main__":
    main()
