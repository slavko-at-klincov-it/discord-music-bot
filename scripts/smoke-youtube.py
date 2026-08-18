#!/usr/bin/env python3
import argparse
import asyncio
import shlex
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_QUERY = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
sys.path.insert(0, str(PROJECT_DIR))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test YouTube resolution and ffmpeg playback.")
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY)
    parser.add_argument("--skip-ffmpeg", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_DIR / ".env")

    import bot

    track = await bot.resolve_track(args.query, "smoke")
    print(f"OK resolve: {track.title} ({bot.fmt_duration(track.duration)})")
    if track.start_seconds:
        print(f"OK start: playback will seek to {bot.fmt_duration(track.start_seconds)}")

    stream_url = await bot.resolve_stream_url(track)
    if not stream_url.startswith(("http://", "https://")):
        raise SystemExit(f"FAIL stream: unexpected stream URL {stream_url!r}")
    print("OK stream: yt-dlp returned a direct media URL")

    if args.skip_ffmpeg:
        return

    result = subprocess.run(
        [
            bot.FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            *shlex.split(bot.ffmpeg_before_options(track)),
            "-t",
            "2",
            "-i",
            stream_url,
            "-f",
            "null",
            "-",
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise SystemExit(f"FAIL ffmpeg: {result.stderr.strip() or result.stdout.strip()}")
    print("OK ffmpeg: decoded 2 seconds from the stream")


if __name__ == "__main__":
    asyncio.run(main())
