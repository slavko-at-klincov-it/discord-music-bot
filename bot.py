import asyncio
import datetime
import json
import os
import re
import signal
import time
from pathlib import Path
from dataclasses import dataclass, replace
from typing import Optional
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from dotenv import load_dotenv
from rhythm import QUEUE_THRESHOLD, find_similar_tracks

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
YT_DLP = os.environ.get("YT_DLP_PATH", "yt-dlp")
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
CACHE_DIR = Path(os.environ.get("MUSIC_BOT_CACHE_DIR", "cache"))
CLEAR_BULK_AGE_DAYS = 14
AUTO_SIMILAR_LIMIT = 3
FFMPEG_RECONNECT_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
# The default YouTube client can hand FFmpeg short-lived Googlevideo URLs that
# YouTube rejects with HTTP 403. These clients prefer playable HLS/audio formats.
YT_DLP_STREAM_CLIENT_ARGS = (
    "--extractor-args",
    "youtube:player_client=android,web_safari",
)
TIMESTAMP_TOKEN_PATTERN = re.compile(r"(\d+)([hms])", re.IGNORECASE)
EQ_PRESETS = {
    "flat": "",
    "bass": "bass=g=3:f=90:w=0.7,alimiter=limit=0.95:attack=5:release=60",
    "jazz": (
        "equalizer=f=120:t=q:w=0.8:g=1,"
        "equalizer=f=500:t=q:w=1:g=1.5,"
        "equalizer=f=3000:t=q:w=1:g=1.2,"
        "alimiter=limit=0.95:attack=5:release=80"
    ),
    "rock": (
        "equalizer=f=80:t=q:w=1:g=2,"
        "equalizer=f=250:t=q:w=1:g=-1,"
        "equalizer=f=2500:t=q:w=1:g=1.5,"
        "treble=g=1:f=10000,"
        "alimiter=limit=0.94:attack=5:release=80"
    ),
    "wow": (
        "dynaudnorm=f=250:g=15:p=0.93:m=8:s=6,"
        "equalizer=f=120:t=q:w=0.8:g=1.5,"
        "equalizer=f=3500:t=q:w=1:g=1.5,"
        "alimiter=limit=0.92:attack=5:release=100"
    ),
}
EQ_PRESET_ALIASES = {"normal": "flat", **{name: name for name in EQ_PRESETS}}


@dataclass
class Track:
    title: str
    url: str
    duration: Optional[int]
    requested_by: str
    start_seconds: Optional[int] = None


def _parse_timestamp_seconds(value: Optional[str]) -> Optional[int]:
    if not value:
        return None

    normalized = value.strip().lower().replace(" ", "")
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)

    if ":" in normalized:
        parts = normalized.split(":")
        if 2 <= len(parts) <= 3 and all(part.isdigit() for part in parts):
            values = [int(part) for part in parts]
            if len(values) == 2:
                minutes, seconds = values
                return minutes * 60 + seconds
            hours, minutes, seconds = values
            return hours * 3600 + minutes * 60 + seconds
        return None

    matches = list(TIMESTAMP_TOKEN_PATTERN.finditer(normalized))
    if not matches or "".join(match.group(0) for match in matches) != normalized:
        return None

    total = 0
    for match in matches:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "h":
            total += amount * 3600
        elif unit == "m":
            total += amount * 60
        else:
            total += amount
    return total


def extract_start_seconds(query: str) -> Optional[int]:
    if not query.startswith(("http://", "https://")):
        return None

    parsed = urlparse(query)
    for raw_params in (parsed.query, parsed.fragment):
        if not raw_params:
            continue

        params = parse_qs(raw_params)
        for name in ("t", "start", "time_continue"):
            seconds = _parse_timestamp_seconds((params.get(name) or [None])[0])
            if seconds and seconds > 0:
                return seconds

        if "=" not in raw_params:
            seconds = _parse_timestamp_seconds(raw_params)
            if seconds and seconds > 0:
                return seconds

    return None


def ffmpeg_before_options(track: Track) -> str:
    options = [FFMPEG_RECONNECT_OPTIONS]
    if track.start_seconds and track.start_seconds > 0:
        options.append(f"-ss {track.start_seconds}")
    return " ".join(options)


def normalize_eq_preset(name: str) -> str:
    preset = EQ_PRESET_ALIASES.get(name.strip().lower())
    if not preset:
        raise RuntimeError(
            "Unknown sound preset. Use `m!bass`, `m!jazz`, `m!rock`, `m!wow`, or `m!flat`."
        )
    return preset


def eq_filter_chain(preset: str) -> str:
    return EQ_PRESETS[normalize_eq_preset(preset)]


def ffmpeg_audio_options(preset: str = "flat") -> str:
    filter_chain = eq_filter_chain(preset)
    if not filter_chain:
        return "-vn"
    return f"-vn -af {filter_chain}"


def fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "live/unknown"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def require_manage_messages(member: discord.Member):
    if not member.guild_permissions.manage_messages:
        raise RuntimeError("You need the `Manage Messages` permission.")


async def clear_channel_messages(channel: discord.abc.GuildChannel) -> tuple[int, int]:
    permissions = channel.permissions_for(channel.guild.me)
    if not permissions.manage_messages:
        raise RuntimeError("The bot needs the `Manage Messages` permission in this channel.")
    if not hasattr(channel, "history"):
        raise RuntimeError("This command only works in text channels.")

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=CLEAR_BULK_AGE_DAYS
    )
    recent_batch = []
    deleted = 0
    skipped = 0

    async def flush_recent_batch():
        nonlocal deleted, recent_batch
        if len(recent_batch) == 1:
            await recent_batch[0].delete()
            deleted += 1
        elif recent_batch:
            await channel.delete_messages(recent_batch)
            deleted += len(recent_batch)
        recent_batch = []
        await asyncio.sleep(0.5)

    async for message in channel.history(limit=None):
        if message.pinned:
            skipped += 1
            continue

        if message.created_at > cutoff:
            recent_batch.append(message)
            if len(recent_batch) == 100:
                await flush_recent_batch()
            continue

        await flush_recent_batch()
        try:
            await message.delete()
            deleted += 1
        except discord.HTTPException:
            skipped += 1
        await asyncio.sleep(0.5)

    await flush_recent_batch()
    return deleted, skipped


async def run_text(*args: str, timeout: int = 30) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Executable not found: {args[0]}. Check YT_DLP_PATH/FFMPEG_PATH and run npm run doctor:python."
        ) from error
    except PermissionError as error:
        raise RuntimeError(f"Executable is not runnable: {args[0]}") from error
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{args[0]} timed out")
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or f"{args[0]} failed")
    return stdout.decode()


async def resolve_track(query: str, requested_by: str) -> Track:
    input_value = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
    start_seconds = extract_start_seconds(query)
    text = await run_text(
        YT_DLP,
        "--dump-single-json",
        "--no-playlist",
        "--default-search",
        "ytsearch",
        "--format",
        "bestaudio/best",
        input_value,
        timeout=30,
    )
    info = json.loads(text)
    entry = next(iter(info.get("entries") or []), info)
    if not entry:
        raise RuntimeError("No YouTube result found.")
    return Track(
        title=entry.get("title") or "Unknown title",
        url=entry.get("webpage_url") or entry.get("original_url") or query,
        duration=entry.get("duration"),
        requested_by=requested_by,
        start_seconds=start_seconds,
    )


async def resolve_stream_url(track: Track) -> str:
    text = (
        await run_text(
            YT_DLP,
            "--no-playlist",
            "--format",
            "bestaudio/best",
            *YT_DLP_STREAM_CLIENT_ARGS,
            "--get-url",
            track.url,
            timeout=30,
        )
    ).strip()
    lines = text.splitlines()
    if not lines:
        raise RuntimeError(f"yt-dlp returned no stream URL for {track.url}")
    return lines[0]


class GuildPlayer:
    def __init__(self, bot: "MusicBot", guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.current: Optional[Track] = None
        self.voice: Optional[discord.VoiceClient] = None
        self.worker: Optional[asyncio.Task] = None
        self.autoplay_channel: Optional[discord.abc.Messageable] = None
        self.autofill_seeds: set[str] = set()
        self.played_urls: set[str] = set()
        self.suggested_urls: set[str] = set()
        self.autoplay_enabled = False
        self.eq_preset = "flat"
        self.play_started_at: Optional[float] = None
        self.play_started_offset_seconds = 0
        self.playback_generation = 0

    async def connect(self, channel: discord.VoiceChannel):
        if self.voice and self.voice.is_connected():
            if self.voice.channel.id != channel.id:
                await self.voice.move_to(channel)
            return
        self.voice = await channel.connect(self_deaf=True)

    async def enqueue(self, track: Track):
        await self.queue.put(track)
        self.ensure_worker()

    def ensure_worker(self):
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self.play_loop())

    def enqueue_next(self, track: Track, interrupt: bool = True) -> bool:
        self.queue.put_nowait(track)
        self.queue._queue.rotate(1)
        self.ensure_worker()

        if interrupt and self.current:
            self.playback_generation += 1

        if interrupt and self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()
            return True
        return False

    def clear_queue(self) -> int:
        count = len(self.queue._queue)
        self.queue._queue.clear()
        return count

    def contains_url(self, url: str) -> bool:
        if self.current and self.current.url == url:
            return True
        return any(track.url == url for track in list(self.queue._queue))

    def has_session_url(self, url: str) -> bool:
        return self.contains_url(url) or url in self.played_urls or url in self.suggested_urls

    def reserve_autofill_seed(self, url: str) -> bool:
        if url in self.autofill_seeds:
            return False
        self.autofill_seeds.add(url)
        return True

    def mark_suggested(self, url: str):
        self.suggested_urls.add(url)

    def schedule_autoplay(self, track: Track) -> bool:
        if not self.autoplay_enabled or not self.autoplay_channel:
            return False
        if not self.reserve_autofill_seed(track.url):
            return False
        asyncio.create_task(
            auto_queue_similar(
                self.autoplay_channel,
                self,
                track,
                track.requested_by,
                seed_reserved=True,
            )
        )
        return True

    def elapsed_playback_seconds(self) -> int:
        if self.play_started_at is None:
            return self.play_started_offset_seconds
        return self.play_started_offset_seconds + max(0, int(time.monotonic() - self.play_started_at))

    def set_eq_preset(self, preset: str) -> Optional[int]:
        self.eq_preset = normalize_eq_preset(preset)
        if not self.current or not self.voice:
            return None
        if not (self.voice.is_playing() or self.voice.is_paused()):
            return None

        start_seconds = self.elapsed_playback_seconds()
        restart_track = replace(
            self.current,
            start_seconds=start_seconds if start_seconds > 0 else None,
        )
        self.enqueue_next(restart_track, interrupt=False)
        self.playback_generation += 1
        self.voice.stop()
        return start_seconds

    async def play_loop(self):
        while True:
            try:
                self.current = await asyncio.wait_for(self.queue.get(), timeout=60)
            except asyncio.TimeoutError:
                self.current = None
                if self.voice and not self.voice.is_playing():
                    await self.voice.disconnect()
                    self.voice = None
                return

            if not self.voice or not self.voice.is_connected():
                self.current = None
                continue

            try:
                playback_generation = self.playback_generation
                stream_url = await resolve_stream_url(self.current)
                if playback_generation != self.playback_generation:
                    continue
                source = discord.FFmpegPCMAudio(
                    stream_url,
                    executable=FFMPEG,
                    before_options=ffmpeg_before_options(self.current),
                    options=ffmpeg_audio_options(self.eq_preset),
                )
                done = asyncio.Event()

                def after(error: Optional[Exception]):
                    if error:
                        print(f"Playback error: {error}", flush=True)
                    self.bot.loop.call_soon_threadsafe(done.set)

                self.play_started_offset_seconds = self.current.start_seconds or 0
                self.play_started_at = time.monotonic()
                self.voice.play(source, after=after)
                self.played_urls.add(self.current.url)
                self.schedule_autoplay(self.current)
                await done.wait()
            except Exception as error:
                print(f"Track failed: {error}", flush=True)
            finally:
                self.play_started_at = None
                self.play_started_offset_seconds = 0
                self.current = None

    def skip(self):
        if self.voice and self.voice.is_playing():
            self.playback_generation += 1
            self.voice.stop()
            return True
        return False

    async def stop(self):
        self.playback_generation += 1
        while not self.queue.empty():
            self.queue.get_nowait()
        self.current = None
        self.play_started_at = None
        self.play_started_offset_seconds = 0
        if self.voice:
            if self.voice.is_playing():
                self.voice.stop()
            await self.voice.disconnect(force=True)
            self.voice = None

    def pause(self) -> bool:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            return True
        return False

    def queue_summary(self) -> str:
        lines = []
        if self.current:
            lines.append(f"Now: {self.current.title} ({fmt_duration(self.current.duration)})")
        queued = list(self.queue._queue)
        if not queued:
            lines.append("Queue is empty.")
        else:
            for index, track in enumerate(queued[:10], start=1):
                lines.append(f"{index}. {track.title} ({fmt_duration(track.duration)})")
            if len(queued) > 10:
                lines.append(f"...and {len(queued) - 10} more.")
        return "\n".join(lines)


class MusicBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.players: dict[int, GuildPlayer] = {}

    def player_for(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(self, guild_id)
        return self.players[guild_id]

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})", flush=True)

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not message.content.lower().startswith("m!"):
            return

        raw = message.content[2:].strip()
        if not raw:
            await send_prefix_help(message.channel)
            return

        command, _, argument = raw.partition(" ")
        command = command.lower()
        argument = argument.strip()
        player = self.player_for(message.guild.id)

        try:
            if command in {"help", "commands"}:
                await send_prefix_help(message.channel)
            elif command == "play":
                await prefix_play(message, argument)
            elif command == "now":
                await prefix_now(message, argument)
            elif command == "clear":
                removed = player.clear_queue()
                await message.channel.send(
                    f"Cleared {removed} queued track(s). Current track keeps playing."
                )
            elif command in {"next", "skip"}:
                await message.channel.send("Skipped." if player.skip() else "Nothing is playing.")
            elif command == "stop":
                await player.stop()
                await message.channel.send("Stopped and cleared the queue.")
            elif command == "pause":
                await message.channel.send("Paused." if player.pause() else "Nothing is playing.")
            elif command == "resume":
                await message.channel.send("Resumed." if player.resume() else "Nothing is paused.")
            elif command in {"queue", "q"}:
                await message.channel.send(player.queue_summary())
            elif command in {"np", "nowplaying"}:
                current = player.current
                await message.channel.send(
                    f"Now playing: {current.title} ({fmt_duration(current.duration)})"
                    if current
                    else "Nothing is playing."
                )
            elif command == "similar":
                await prefix_similar(message, argument)
            elif command == "autoplay":
                await prefix_autoplay(message, argument, player)
            elif command in EQ_PRESET_ALIASES:
                await prefix_eq_preset(message, command, player)
            else:
                await message.channel.send("Unknown command. Use `m!help`.")
        except Exception as error:
            print(f"Prefix command failed: {error}", flush=True)
            await message.channel.send(f"Command failed: {error}")


bot = MusicBot()


async def send_prefix_help(channel: discord.abc.Messageable):
    await channel.send(
        "\n".join(
            [
                "`m!play <YouTube URL or search>` - play or queue a track",
                "`m!now <YouTube URL or search>` - play immediately and keep the queue",
                "`m!clear` - clear queued tracks, keep the current song",
                "`m!next` / `m!skip` - skip current track",
                "`m!queue` - show queue",
                "`m!np` - show current track",
                "`m!pause` / `m!resume` - pause or resume",
                "`m!stop` - stop, clear queue, and leave voice",
                "`m!bass` / `m!jazz` / `m!rock` / `m!wow` / `m!flat` - set sound preset",
                "`m!similar [YouTube URL or search]` - find rhythm-similar tracks",
                "`m!similar play [YouTube URL or search]` - queue the best strict matches",
                "`m!autoplay on/off/status` - continuous rhythm radio queue (default off)",
                "`m!help` - show this help",
            ]
        )
    )


async def prefix_play(message: discord.Message, query: str):
    if not query:
        await message.channel.send("Usage: `m!play <YouTube URL or search>`")
        return
    if not isinstance(message.author, discord.Member):
        await message.channel.send("Use this command inside a server.")
        return
    voice_state = message.author.voice
    if not voice_state or not voice_state.channel:
        await message.channel.send("Join a voice channel first.")
        return

    status = await message.channel.send("Searching...")
    player = bot.player_for(message.guild.id)
    player.autoplay_channel = message.channel
    await player.connect(voice_state.channel)
    track = await resolve_track(query, message.author.display_name)
    await player.enqueue(track)
    await status.edit(content=f"Queued: {track.title} ({fmt_duration(track.duration)})")


async def prefix_now(message: discord.Message, query: str):
    if not query:
        await message.channel.send("Usage: `m!now <YouTube URL or search>`")
        return
    if not isinstance(message.author, discord.Member):
        await message.channel.send("Use this command inside a server.")
        return
    voice_state = message.author.voice
    if not voice_state or not voice_state.channel:
        await message.channel.send("Join a voice channel first.")
        return

    status = await message.channel.send("Searching...")
    player = bot.player_for(message.guild.id)
    player.autoplay_channel = message.channel
    await player.connect(voice_state.channel)
    track = await resolve_track(query, message.author.display_name)
    player.enqueue_next(track, interrupt=True)
    await status.edit(content=f"Playing now: {track.title} ({fmt_duration(track.duration)})")


async def prefix_eq_preset(message: discord.Message, preset: str, player: GuildPlayer):
    restart_seconds = player.set_eq_preset(preset)
    preset_name = normalize_eq_preset(preset)
    if restart_seconds is None:
        await message.channel.send(
            f"Sound preset is `{preset_name}`. It will apply to the next track."
        )
        return

    await message.channel.send(
        f"Sound preset is `{preset_name}`. Restarted current track at about {fmt_duration(restart_seconds)}."
    )


async def prefix_autoplay(message: discord.Message, argument: str, player: GuildPlayer):
    value = argument.strip().lower()
    if value in {"", "status"}:
        state = "on" if player.autoplay_enabled else "off"
        await message.channel.send(f"Autoplay is `{state}`.")
        return
    if value in {"on", "enable", "enabled"}:
        player.autoplay_enabled = True
        player.autoplay_channel = message.channel
        scheduled = player.current is not None and player.schedule_autoplay(player.current)
        suffix = " Current track is being used as the next seed." if scheduled else ""
        await message.channel.send(
            f"Autoplay is `on`. Every played track will auto-fill rhythm matches.{suffix}"
        )
        return
    if value in {"off", "disable", "disabled"}:
        player.autoplay_enabled = False
        await message.channel.send("Autoplay is `off`. `m!play` will only queue the requested song.")
        return
    await message.channel.send("Usage: `m!autoplay on`, `m!autoplay off`, or `m!autoplay status`.")


async def auto_queue_similar(
    channel: discord.abc.Messageable,
    player: GuildPlayer,
    seed_track: Track,
    requested_by: str,
    *,
    seed_reserved: bool = False,
):
    if not seed_reserved and not player.reserve_autofill_seed(seed_track.url):
        return

    try:
        seed, _, results = await find_similar_tracks(
            seed_track.url,
            CACHE_DIR,
            YT_DLP,
            FFMPEG,
        )
        strong = [result for result in results if result.score >= QUEUE_THRESHOLD]
        if not player.autoplay_enabled:
            return
        added = []
        for result in strong:
            if not player.autoplay_enabled:
                return
            if len(added) >= AUTO_SIMILAR_LIMIT:
                break
            if player.has_session_url(result.track.url):
                continue
            player.mark_suggested(result.track.url)
            track = Track(
                title=result.track.title,
                url=result.track.url,
                duration=result.track.duration,
                requested_by=f"similar:{requested_by}",
            )
            await player.enqueue(track)
            added.append(result)

        if not added:
            return

        await channel.send(
            "Auto-queued rhythm matches:\n"
            + "\n".join(
                f"{index}. {result.track.title} - score {result.score:.2f}"
                for index, result in enumerate(added, start=1)
            )
        )
    except Exception as error:
        print(f"Auto-similar failed for {seed_track.url}: {error}", flush=True)


def _current_or_argument(message: discord.Message, argument: str) -> str:
    if argument:
        return argument
    player = bot.player_for(message.guild.id)
    if not player.current:
        raise RuntimeError("Nothing is playing. Use `m!similar <YouTube URL or search>`.")
    return player.current.url


async def prefix_similar(message: discord.Message, argument: str):
    should_queue = False
    query = argument.strip()
    if query.lower() == "play" or query.lower().startswith("play "):
        should_queue = True
        query = query[4:].strip()

    query = _current_or_argument(message, query)
    if should_queue:
        player = bot.player_for(message.guild.id)
        if not player.voice or not player.voice.is_connected():
            if not isinstance(message.author, discord.Member):
                await message.channel.send("Use this command inside a server.")
                return
            voice_state = message.author.voice
            if not voice_state or not voice_state.channel:
                await message.channel.send("Join a voice channel first.")
                return
    status = await message.channel.send("Analyzing seed track...")

    last_update = 0

    async def progress(index: int, total: int):
        nonlocal last_update
        if total == 0:
            return
        if index == total or index - last_update >= 3:
            last_update = index
            await status.edit(content=f"Analyzing candidates {index}/{total}...")

    seed, seed_features, results = await find_similar_tracks(
        query,
        CACHE_DIR,
        YT_DLP,
        FFMPEG,
        progress=progress,
    )

    if should_queue:
        strong = [result for result in results if result.score >= QUEUE_THRESHOLD][:3]
        if not strong:
            await status.edit(
                content=(
                    f"No strong rhythm match found for **{seed.title}**. "
                    "Autoplay stayed off."
                )
            )
            return
        player = bot.player_for(message.guild.id)
        player.autoplay_channel = message.channel
        if not player.voice or not player.voice.is_connected():
            await player.connect(message.author.voice.channel)
        for result in strong:
            await player.enqueue(
                Track(
                    title=result.track.title,
                    url=result.track.url,
                    duration=result.track.duration,
                    requested_by=message.author.display_name,
                )
            )
        await status.edit(
            content="Queued strict rhythm matches:\n"
            + "\n".join(
                f"{idx}. {item.track.title} - score {item.score:.2f}"
                for idx, item in enumerate(strong, start=1)
            )
        )
        return

    if not results:
        await status.edit(
            content=(
                f"No rhythm-similar tracks above the display threshold for **{seed.title}**. "
                "Nothing queued."
            )
        )
        return

    lines = [
        f"Seed: **{seed.title}**",
        f"Seed BPM: {seed_features.bpm:.0f}, bass energy: {seed_features.bass_energy:.2f}",
        "Top rhythm matches:",
    ]
    for index, result in enumerate(results[:5], start=1):
        lines.append(
            f"{index}. {result.track.title} - score {result.score:.2f} "
            f"({', '.join(result.reasons)})"
        )
    lines.append("Use `m!similar play <url or search>` to queue the best strict matches.")
    await status.edit(content="\n".join(lines))


@bot.tree.command(name="play", description="Play a YouTube URL or search query in your current voice channel.")
@app_commands.describe(query="YouTube URL or search text")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
        return
    voice_state = interaction.user.voice
    if not voice_state or not voice_state.channel:
        await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
        return

    await interaction.response.defer()
    player = bot.player_for(interaction.guild.id)
    await player.connect(voice_state.channel)
    player.autoplay_channel = interaction.channel
    track = await resolve_track(query, interaction.user.display_name)
    await player.enqueue(track)
    await interaction.followup.send(f"Queued: {track.title} ({fmt_duration(track.duration)})")


@bot.tree.command(name="skip", description="Skip the current track.")
async def skip(interaction: discord.Interaction):
    ok = bot.player_for(interaction.guild_id).skip()
    await interaction.response.send_message("Skipped." if ok else "Nothing is playing.")


@bot.tree.command(name="stop", description="Stop playback, clear the queue, and leave the voice channel.")
async def stop(interaction: discord.Interaction):
    await bot.player_for(interaction.guild_id).stop()
    await interaction.response.send_message("Stopped and cleared the queue.")


@bot.tree.command(name="pause", description="Pause playback.")
async def pause(interaction: discord.Interaction):
    ok = bot.player_for(interaction.guild_id).pause()
    await interaction.response.send_message("Paused." if ok else "Nothing is playing.")


@bot.tree.command(name="resume", description="Resume playback.")
async def resume(interaction: discord.Interaction):
    ok = bot.player_for(interaction.guild_id).resume()
    await interaction.response.send_message("Resumed." if ok else "Nothing is paused.")


@bot.tree.command(name="queue", description="Show the current queue.")
async def queue(interaction: discord.Interaction):
    await interaction.response.send_message(bot.player_for(interaction.guild_id).queue_summary())


@bot.tree.command(name="nowplaying", description="Show the currently playing track.")
async def nowplaying(interaction: discord.Interaction):
    current = bot.player_for(interaction.guild_id).current
    await interaction.response.send_message(
        f"Now playing: {current.title} ({fmt_duration(current.duration)})"
        if current
        else "Nothing is playing."
    )


@bot.tree.command(name="clear", description="Delete all non-pinned messages in this channel.")
@app_commands.default_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
        return
    require_manage_messages(interaction.user)
    await interaction.response.defer(ephemeral=True)
    deleted, skipped = await clear_channel_messages(interaction.channel)
    await interaction.followup.send(
        f"Deleted {deleted} message(s). Skipped {skipped} pinned or undeletable message(s).",
        ephemeral=True,
    )


async def shutdown():
    for player in bot.players.values():
        await player.stop()
    await bot.close()


def handle_signal():
    asyncio.create_task(shutdown())


if __name__ == "__main__":
    print("Starting Discord music bot...", flush=True)
    bot.run(TOKEN, log_handler=None)
