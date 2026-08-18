import asyncio
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qs, urlparse

SAMPLE_SECONDS = 90
SAMPLE_START_SECONDS = 30
ANALYSIS_SR = 22050
MAX_CANDIDATES = 20
YT_DLP_STREAM_CLIENT_ARGS = (
    "--extractor-args",
    "youtube:player_client=android,web_safari",
)
DISCOVERY_FETCH_LIMIT = MAX_CANDIDATES * 4
MAX_RECOMMENDATION_DURATION = 20 * 60
DISPLAY_THRESHOLD = 0.65
QUEUE_THRESHOLD = 0.78
CACHE_VERSION = 1
TITLE_NOISE_WORDS = {
    "a",
    "an",
    "audio",
    "by",
    "edit",
    "extended",
    "feat",
    "featuring",
    "ft",
    "full",
    "hd",
    "hq",
    "is",
    "lyrics",
    "lyric",
    "mix",
    "music",
    "official",
    "original",
    "prod",
    "radio",
    "remaster",
    "remastered",
    "source",
    "the",
    "topic",
    "track",
    "version",
    "video",
    "visualizer",
}


@dataclass
class TrackInfo:
    title: str
    url: str
    video_id: str
    duration: Optional[int] = None


@dataclass
class TrackFeatures:
    video_id: str
    title: str
    url: str
    duration: Optional[int]
    bpm: float
    beat_stability: float
    bass_energy: float
    bass_pulse: list[float]


@dataclass
class SimilarTrack:
    track: TrackInfo
    features: TrackFeatures
    score: float
    reasons: list[str]


def extract_video_id(url_or_id: str) -> Optional[str]:
    value = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value

    parsed = urlparse(value)
    host = parsed.netloc.lower().removeprefix("www.")
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            return (parse_qs(parsed.query).get("v") or [None])[0]
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            return parts[1]
    if host == "youtu.be":
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[0]
    return None


def canonical_youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def stable_key(value: str) -> str:
    video_id = extract_video_id(value)
    if video_id:
        return video_id
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _title_tokens(title: str) -> set[str]:
    value = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"#\w+", " ", value)
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", " ", value)
    value = value.split("|", 1)[0]
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return {
        token
        for token in value.split()
        if len(token) > 1 and token not in TITLE_NOISE_WORDS and not token.isdigit()
    }


def _is_near_duplicate_title(left: str, right: str) -> bool:
    left_tokens = _title_tokens(left)
    right_tokens = _title_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    coverage = overlap / min(len(left_tokens), len(right_tokens))
    jaccard = overlap / len(left_tokens | right_tokens)
    return left_tokens == right_tokens or (coverage >= 0.8 and jaccard >= 0.6)


async def run_text(*args: str, timeout: int = 45) -> str:
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


async def run_quiet(*args: str, timeout: int = 90) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Executable not found: {args[0]}. Check YT_DLP_PATH/FFMPEG_PATH and run npm run doctor:python."
        ) from error
    except PermissionError as error:
        raise RuntimeError(f"Executable is not runnable: {args[0]}") from error
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{args[0]} timed out")
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or f"{args[0]} failed")


async def resolve_track_info(query: str, ytdlp_path: str) -> TrackInfo:
    input_value = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
    text = await run_text(
        ytdlp_path,
        "--dump-single-json",
        "--no-playlist",
        "--default-search",
        "ytsearch",
        input_value,
        timeout=45,
    )
    info = json.loads(text)
    entry = next(iter(info.get("entries") or []), info)
    if not entry:
        raise RuntimeError("No YouTube result found.")
    video_id = entry.get("id") or extract_video_id(entry.get("webpage_url") or "")
    if not video_id:
        raise RuntimeError("Could not determine YouTube video id.")
    return TrackInfo(
        title=entry.get("title") or "Unknown title",
        url=entry.get("webpage_url") or canonical_youtube_url(video_id),
        video_id=video_id,
        duration=entry.get("duration"),
    )


def _track_from_entry(entry: dict) -> Optional[TrackInfo]:
    if entry.get("is_live") or entry.get("live_status") in {"is_live", "is_upcoming"}:
        return None
    video_id = entry.get("id") or extract_video_id(entry.get("url") or entry.get("webpage_url") or "")
    if not video_id:
        return None
    duration = entry.get("duration")
    if duration and duration > MAX_RECOMMENDATION_DURATION:
        return None
    return TrackInfo(
        title=entry.get("title") or "Unknown title",
        url=entry.get("webpage_url") or canonical_youtube_url(video_id),
        video_id=video_id,
        duration=duration,
    )


async def discover_candidates(seed: TrackInfo, original_query: str, ytdlp_path: str) -> list[TrackInfo]:
    candidates: list[TrackInfo] = []
    seen = {seed.video_id}
    seen_titles = [seed.title]

    async def add_from_json(input_value: str, *extra_args: str):
        text = await run_text(
            ytdlp_path,
            "--dump-single-json",
            "--flat-playlist",
            "--playlist-end",
            str(DISCOVERY_FETCH_LIMIT),
            *extra_args,
            input_value,
            timeout=60,
        )
        info = json.loads(text)
        entries = info.get("entries") or []
        for entry in entries:
            track = _track_from_entry(entry or {})
            if not track or track.video_id in seen:
                continue
            if any(_is_near_duplicate_title(track.title, title) for title in seen_titles):
                continue
            seen.add(track.video_id)
            seen_titles.append(track.title)
            candidates.append(track)
            if len(candidates) >= MAX_CANDIDATES:
                return

    parsed = urlparse(original_query)
    has_radio_list = bool(parse_qs(parsed.query).get("list", [""])[0].startswith("RD"))
    discovery_sources = []
    if original_query.startswith(("http://", "https://")) and has_radio_list:
        discovery_sources.append(original_query)
    discovery_sources.append(f"{canonical_youtube_url(seed.video_id)}&list=RD{seed.video_id}")

    tried_sources = set()
    for source in discovery_sources:
        if source in tried_sources or len(candidates) >= MAX_CANDIDATES:
            continue
        tried_sources.add(source)
        try:
            await add_from_json(source)
        except Exception:
            pass

    if len(candidates) < MAX_CANDIDATES:
        search_query = re.sub(r"\s+", " ", seed.title).strip()
        for suffix in ("similar songs", ""):
            if len(candidates) >= MAX_CANDIDATES:
                break
            query = f"{search_query} {suffix}".strip()
            try:
                await add_from_json(f"ytsearch{DISCOVERY_FETCH_LIMIT}:{query}")
            except Exception:
                pass

    return candidates[:MAX_CANDIDATES]


async def resolve_stream_url(track: TrackInfo, ytdlp_path: str) -> str:
    text = (
        await run_text(
            ytdlp_path,
            "--no-playlist",
            "--format",
            "bestaudio/best",
            *YT_DLP_STREAM_CLIENT_ARGS,
            "--get-url",
            track.url,
            timeout=45,
        )
    ).strip()
    lines = text.splitlines()
    if not lines:
        raise RuntimeError(f"yt-dlp returned no stream URL for {track.url}")
    return lines[0]


async def ensure_sample(track: TrackInfo, cache_dir: Path, ytdlp_path: str, ffmpeg_path: str) -> Path:
    sample_dir = cache_dir / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_path = sample_dir / f"{stable_key(track.video_id)}.wav"
    if sample_path.exists() and sample_path.stat().st_size > 100_000:
        return sample_path

    stream_url = await resolve_stream_url(track, ytdlp_path)
    await run_quiet(
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(SAMPLE_START_SECONDS),
        "-t",
        str(SAMPLE_SECONDS),
        "-i",
        stream_url,
        "-ac",
        "1",
        "-ar",
        str(ANALYSIS_SR),
        sample_path.as_posix(),
        timeout=120,
    )
    return sample_path


def _extract_features_from_wav(sample_path: Path) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(sample_path.as_posix(), sr=ANALYSIS_SR, mono=True)
    if y.size < sr * 10:
        raise RuntimeError("Audio sample is too short to analyze.")

    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.asarray(tempo).reshape(-1)[0])
    intervals = np.diff(beats) if len(beats) > 2 else np.array([])
    beat_stability = 0.0
    if intervals.size:
        beat_stability = float(max(0.0, 1.0 - (np.std(intervals) / (np.mean(intervals) + 1e-9))))

    stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low_band = stft[(freqs >= 40) & (freqs <= 180), :]
    full_band = stft[(freqs >= 40) & (freqs <= 8000), :]
    bass_energy = float(np.mean(low_band) / (np.mean(full_band) + 1e-9))

    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512, aggregate=np.median)
    low_env = np.mean(low_band, axis=0)
    length = min(onset.size, low_env.size)
    bass_onset = onset[:length] * low_env[:length]
    if np.max(bass_onset) > 0:
        bass_onset = bass_onset / np.max(bass_onset)

    bins = np.array_split(bass_onset, 16)
    pulse = [float(np.mean(bin_value)) if len(bin_value) else 0.0 for bin_value in bins]
    pulse_sum = sum(pulse) or 1.0
    pulse = [value / pulse_sum for value in pulse]

    return {
        "bpm": tempo,
        "beat_stability": beat_stability,
        "bass_energy": bass_energy,
        "bass_pulse": pulse,
    }


def _features_from_cache(path: Path) -> Optional[TrackFeatures]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("cache_version") != CACHE_VERSION:
            return None
        return TrackFeatures(**data["features"])
    except Exception:
        return None


async def analyze_track(track: TrackInfo, cache_dir: Path, ytdlp_path: str, ffmpeg_path: str) -> TrackFeatures:
    analysis_dir = cache_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    cache_path = analysis_dir / f"{stable_key(track.video_id)}.json"
    cached = _features_from_cache(cache_path)
    if cached:
        return cached

    sample_path = await ensure_sample(track, cache_dir, ytdlp_path, ffmpeg_path)
    values = await asyncio.to_thread(_extract_features_from_wav, sample_path)
    features = TrackFeatures(
        video_id=track.video_id,
        title=track.title,
        url=track.url,
        duration=track.duration,
        bpm=values["bpm"],
        beat_stability=values["beat_stability"],
        bass_energy=values["bass_energy"],
        bass_pulse=values["bass_pulse"],
    )
    cache_path.write_text(
        json.dumps({"cache_version": CACHE_VERSION, "features": asdict(features)}, indent=2)
    )
    return features


def _exp_similarity(diff: float, scale: float) -> float:
    return math.exp(-abs(diff) / scale)


def _cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    l_values = list(left)
    r_values = list(right)
    dot = sum(a * b for a, b in zip(l_values, r_values))
    l_norm = math.sqrt(sum(a * a for a in l_values))
    r_norm = math.sqrt(sum(b * b for b in r_values))
    if not l_norm or not r_norm:
        return 0.0
    return max(0.0, min(1.0, dot / (l_norm * r_norm)))


def score_similarity(seed: TrackFeatures, candidate: TrackFeatures) -> tuple[float, list[str]]:
    bpm_score = _exp_similarity(seed.bpm - candidate.bpm, 12.0)
    pulse_score = _cosine_similarity(seed.bass_pulse, candidate.bass_pulse)
    stability_score = 1.0 - min(1.0, abs(seed.beat_stability - candidate.beat_stability))
    bass_score = _exp_similarity(seed.bass_energy - candidate.bass_energy, 0.35)

    total = (
        bpm_score * 0.35
        + pulse_score * 0.35
        + stability_score * 0.15
        + bass_score * 0.15
    )

    reasons = [
        f"BPM {candidate.bpm:.0f}",
        f"bass pulse {pulse_score:.2f}",
        f"beat stability {candidate.beat_stability:.2f}",
    ]
    return total, reasons


async def find_similar_tracks(
    seed_query: str,
    cache_dir: Path,
    ytdlp_path: str,
    ffmpeg_path: str,
    progress: Optional[Callable[[int, int], object]] = None,
) -> tuple[TrackInfo, TrackFeatures, list[SimilarTrack]]:
    seed = await resolve_track_info(seed_query, ytdlp_path)
    seed_features = await analyze_track(seed, cache_dir, ytdlp_path, ffmpeg_path)
    candidates = await discover_candidates(seed, seed_query, ytdlp_path)

    results: list[SimilarTrack] = []
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        if progress:
            maybe = progress(index, total)
            if asyncio.iscoroutine(maybe):
                await maybe
        try:
            features = await analyze_track(candidate, cache_dir, ytdlp_path, ffmpeg_path)
            score, reasons = score_similarity(seed_features, features)
            if score >= DISPLAY_THRESHOLD:
                results.append(SimilarTrack(candidate, features, score, reasons))
        except Exception as error:
            print(f"Candidate analysis failed for {candidate.url}: {error}", flush=True)

    results.sort(key=lambda item: item.score, reverse=True)
    return seed, seed_features, results
