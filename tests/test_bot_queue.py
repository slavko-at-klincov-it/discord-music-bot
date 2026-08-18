import json
import asyncio
import time
import unittest

import bot as bot_module
from bot import GuildPlayer, Track, auto_queue_similar, prefix_autoplay, prefix_now, prefix_play
from rhythm import SimilarTrack, TrackFeatures, TrackInfo


def feature(video_id: str) -> TrackFeatures:
    return TrackFeatures(
        video_id=video_id,
        title=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        duration=180,
        bpm=120,
        beat_stability=0.9,
        bass_energy=0.7,
        bass_pulse=[0.25, 0.25, 0.25, 0.25],
    )


def similar(video_id: str, score: float) -> SimilarTrack:
    track = TrackInfo(
        title=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
        duration=180,
    )
    return SimilarTrack(track=track, features=feature(video_id), score=score, reasons=[])


class FakeNotice:
    async def edit(self, content: str):
        self.content = content


class FakeChannel:
    def __init__(self):
        self.messages = []

    async def send(self, content: str):
        self.messages.append(content)
        return FakeNotice()


class FakeMessage:
    def __init__(self, guild_id=1, author=None):
        self.channel = FakeChannel()
        self.guild = type("FakeGuild", (), {"id": guild_id})()
        self.author = author


class FakeVoice:
    def __init__(self, playing=False, paused=False):
        self.playing = playing
        self.paused = paused
        self.stop_called = False

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def stop(self):
        self.stop_called = True
        self.playing = False
        self.paused = False


class FakeConnectedVoice(FakeVoice):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    def is_connected(self):
        return True


class FakePlaybackVoice(FakeConnectedVoice):
    def __init__(self):
        super().__init__(FakeVoiceChannel())
        self.played = False

    def play(self, source, after):
        self.played = True
        after(None)


class FakeVoiceChannel:
    id = 123

    async def connect(self, self_deaf=True):
        return FakeConnectedVoice(self)


class FakeAuthor:
    display_name = "tester"

    def __init__(self):
        self.voice = type("FakeVoiceState", (), {"channel": FakeVoiceChannel()})()


class QueueTests(unittest.TestCase):
    def test_run_text_reports_missing_executable(self):
        async def run_test():
            with self.assertRaisesRegex(RuntimeError, "Executable not found"):
                await bot_module.run_text("/definitely/missing/yt-dlp")

        import asyncio

        asyncio.run(run_test())

    def test_autoplay_defaults_off(self):
        player = GuildPlayer(bot=None, guild_id=1)
        self.assertFalse(player.autoplay_enabled)

    def test_extract_start_seconds_from_youtube_urls(self):
        self.assertEqual(
            bot_module.extract_start_seconds(
                "https://www.youtube.com/watch?v=ya3rSrvxC7k&list=RDya3rSrvxC7k&start_radio=1&t=6731s"
            ),
            6731,
        )
        self.assertEqual(
            bot_module.extract_start_seconds("https://youtu.be/ya3rSrvxC7k?t=1h2m3s"),
            3723,
        )
        self.assertEqual(
            bot_module.extract_start_seconds("https://www.youtube.com/watch?v=ya3rSrvxC7k#t=1:02:03"),
            3723,
        )
        self.assertIsNone(bot_module.extract_start_seconds("themba who is themba"))

    def test_resolve_track_preserves_youtube_start_seconds(self):
        async def run_test():
            original = bot_module.run_text
            query = "https://www.youtube.com/watch?v=ya3rSrvxC7k&t=6731s"

            async def fake_run_text(*args, **kwargs):
                self.assertEqual(args[-1], query)
                return json.dumps(
                    {
                        "id": "ya3rSrvxC7k",
                        "title": "Seed track",
                        "webpage_url": "https://www.youtube.com/watch?v=ya3rSrvxC7k",
                        "duration": 7200,
                    }
                )

            bot_module.run_text = fake_run_text
            try:
                track = await bot_module.resolve_track(query, "test")
            finally:
                bot_module.run_text = original

            self.assertEqual(track.url, "https://www.youtube.com/watch?v=ya3rSrvxC7k")
            self.assertEqual(track.start_seconds, 6731)

        import asyncio

        asyncio.run(run_test())

    def test_resolve_stream_uses_compatible_youtube_clients(self):
        async def run_test():
            original = bot_module.run_text
            captured = []

            async def fake_run_text(*args, **kwargs):
                captured.extend(args)
                return "https://example.com/playable-stream\n"

            bot_module.run_text = fake_run_text
            try:
                stream_url = await bot_module.resolve_stream_url(
                    Track("title", "https://www.youtube.com/watch?v=test", 180, "tester")
                )
            finally:
                bot_module.run_text = original

            self.assertEqual(stream_url, "https://example.com/playable-stream")
            self.assertIn("--extractor-args", captured)
            self.assertIn("youtube:player_client=android,web_safari", captured)

        import asyncio

        asyncio.run(run_test())

    def test_ffmpeg_before_options_include_start_seek(self):
        track = Track("title", "https://example.com", 180, "test", start_seconds=6731)
        self.assertIn("-ss 6731", bot_module.ffmpeg_before_options(track))

        plain_track = Track("title", "https://example.com", 180, "test")
        self.assertNotIn("-ss", bot_module.ffmpeg_before_options(plain_track))

    def test_prefix_autoplay_toggles_and_reports_status(self):
        async def run_test():
            message = FakeMessage()
            player = GuildPlayer(bot=None, guild_id=1)

            await prefix_autoplay(message, "status", player)
            await prefix_autoplay(message, "off", player)
            self.assertFalse(player.autoplay_enabled)
            await prefix_autoplay(message, "status", player)
            await prefix_autoplay(message, "on", player)
            self.assertTrue(player.autoplay_enabled)

            self.assertEqual(message.channel.messages[0], "Autoplay is `off`.")
            self.assertIn("Autoplay is `off`", message.channel.messages[2])

        import asyncio

        asyncio.run(run_test())

    def test_contains_url_checks_current_and_queue(self):
        player = GuildPlayer(bot=None, guild_id=1)
        player.current = Track("current", "https://example.com/current", 10, "test")
        player.queue.put_nowait(Track("queued", "https://example.com/queued", 10, "test"))

        self.assertTrue(player.contains_url("https://example.com/current"))
        self.assertTrue(player.contains_url("https://example.com/queued"))
        self.assertFalse(player.contains_url("https://example.com/other"))

    def test_clear_queue_preserves_current(self):
        player = GuildPlayer(bot=None, guild_id=1)
        current = Track("current", "https://example.com/current", 10, "test")
        player.current = current
        player.queue.put_nowait(Track("queued1", "https://example.com/queued1", 10, "test"))
        player.queue.put_nowait(Track("queued2", "https://example.com/queued2", 10, "test"))

        self.assertEqual(player.clear_queue(), 2)
        self.assertIs(player.current, current)
        self.assertEqual(list(player.queue._queue), [])

    def test_enqueue_next_interrupts_current_and_preserves_queue(self):
        async def run_test():
            player = GuildPlayer(bot=None, guild_id=1)
            player.worker = asyncio.create_task(asyncio.sleep(60))
            player.current = Track("current", "https://example.com/current", 10, "test")
            player.voice = FakeVoice(playing=True)
            player.queue.put_nowait(Track("queued", "https://example.com/queued", 10, "test"))

            interrupted = player.enqueue_next(
                Track("now", "https://example.com/now", 10, "test"),
                interrupt=True,
            )
            queued_titles = [track.title for track in list(player.queue._queue)]

            self.assertTrue(interrupted)
            self.assertTrue(player.voice.stop_called)
            self.assertEqual(queued_titles, ["now", "queued"])
            self.assertEqual(player.playback_generation, 1)
            player.worker.cancel()

        import asyncio

        asyncio.run(run_test())

    def test_ffmpeg_audio_options_for_presets(self):
        self.assertEqual(bot_module.ffmpeg_audio_options("flat"), "-vn")
        self.assertEqual(bot_module.ffmpeg_audio_options("normal"), "-vn")

        for preset in ("bass", "jazz", "rock", "wow"):
            options = bot_module.ffmpeg_audio_options(preset)
            self.assertIn("-vn -af ", options)
            self.assertIn("alimiter", options)

        self.assertIn("dynaudnorm", bot_module.ffmpeg_audio_options("wow"))

    def test_eq_restart_requeues_current_with_seek(self):
        async def run_test():
            player = GuildPlayer(bot=None, guild_id=1)
            player.worker = asyncio.create_task(asyncio.sleep(60))
            player.current = Track(
                "current",
                "https://example.com/current",
                180,
                "test",
                start_seconds=10,
            )
            player.voice = FakeVoice(playing=True)
            player.play_started_at = time.monotonic() - 42
            player.play_started_offset_seconds = 10

            restart_seconds = player.set_eq_preset("wow")
            queued = list(player.queue._queue)

            self.assertEqual(player.eq_preset, "wow")
            self.assertGreaterEqual(restart_seconds, 51)
            self.assertLessEqual(restart_seconds, 53)
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0].title, "current")
            self.assertEqual(queued[0].start_seconds, restart_seconds)
            self.assertTrue(player.voice.stop_called)
            player.worker.cancel()

        import asyncio

        asyncio.run(run_test())

    def test_autoplay_triggers_when_track_starts(self):
        async def run_test():
            original_resolve_stream_url = bot_module.resolve_stream_url
            original_audio = bot_module.discord.FFmpegPCMAudio
            original_auto_queue = bot_module.auto_queue_similar
            calls = []

            async def fake_resolve_stream_url(track):
                return "https://example.com/stream"

            def fake_audio(*args, **kwargs):
                return object()

            async def fake_auto_queue(channel, player_arg, track, requested_by, **kwargs):
                calls.append((channel, player_arg, track, requested_by, kwargs))

            bot_module.resolve_stream_url = fake_resolve_stream_url
            bot_module.discord.FFmpegPCMAudio = fake_audio
            bot_module.auto_queue_similar = fake_auto_queue
            player = GuildPlayer(
                bot=type("FakeBot", (), {"loop": asyncio.get_running_loop()})(),
                guild_id=1,
            )
            player.voice = FakePlaybackVoice()
            player.autoplay_channel = FakeChannel()
            player.autoplay_enabled = True
            player.queue.put_nowait(Track("seed", "https://seed", 180, "tester"))
            task = asyncio.create_task(player.play_loop())

            try:
                await asyncio.sleep(0.01)
                self.assertTrue(player.voice.played)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][2].url, "https://seed")
                self.assertTrue(calls[0][4]["seed_reserved"])
                self.assertIn("https://seed", player.played_urls)
            finally:
                task.cancel()
                bot_module.resolve_stream_url = original_resolve_stream_url
                bot_module.discord.FFmpegPCMAudio = original_audio
                bot_module.auto_queue_similar = original_auto_queue

        asyncio.run(run_test())

    def test_autoplay_does_not_trigger_when_off(self):
        async def run_test():
            original_resolve_stream_url = bot_module.resolve_stream_url
            original_audio = bot_module.discord.FFmpegPCMAudio
            original_auto_queue = bot_module.auto_queue_similar
            calls = []

            async def fake_resolve_stream_url(track):
                return "https://example.com/stream"

            def fake_audio(*args, **kwargs):
                return object()

            async def fake_auto_queue(*args, **kwargs):
                calls.append((args, kwargs))

            bot_module.resolve_stream_url = fake_resolve_stream_url
            bot_module.discord.FFmpegPCMAudio = fake_audio
            bot_module.auto_queue_similar = fake_auto_queue
            player = GuildPlayer(
                bot=type("FakeBot", (), {"loop": asyncio.get_running_loop()})(),
                guild_id=1,
            )
            player.voice = FakePlaybackVoice()
            player.autoplay_channel = FakeChannel()
            player.autoplay_enabled = False
            player.queue.put_nowait(Track("seed", "https://seed", 180, "tester"))
            task = asyncio.create_task(player.play_loop())

            try:
                await asyncio.sleep(0.01)
                self.assertTrue(player.voice.played)
                self.assertEqual(calls, [])
            finally:
                task.cancel()
                bot_module.resolve_stream_url = original_resolve_stream_url
                bot_module.discord.FFmpegPCMAudio = original_audio
                bot_module.auto_queue_similar = original_auto_queue

        asyncio.run(run_test())

    def test_autoplay_runs_for_auto_queued_tracks(self):
        async def run_test():
            original_auto_queue = bot_module.auto_queue_similar
            calls = []

            async def fake_auto_queue(channel, player_arg, track, requested_by, **kwargs):
                calls.append((track, requested_by, kwargs))

            bot_module.auto_queue_similar = fake_auto_queue
            try:
                player = GuildPlayer(bot=None, guild_id=1)
                player.autoplay_channel = FakeChannel()
                player.autoplay_enabled = True
                track = Track("auto", "https://auto", 180, "similar:tester")

                self.assertTrue(player.schedule_autoplay(track))
                await asyncio.sleep(0)

                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0].url, "https://auto")
                self.assertEqual(calls[0][1], "similar:tester")
            finally:
                bot_module.auto_queue_similar = original_auto_queue

        asyncio.run(run_test())

    def test_autoplay_seed_runs_once(self):
        async def run_test():
            original_auto_queue = bot_module.auto_queue_similar
            calls = []

            async def fake_auto_queue(*args, **kwargs):
                calls.append((args, kwargs))

            bot_module.auto_queue_similar = fake_auto_queue
            try:
                player = GuildPlayer(bot=None, guild_id=1)
                player.autoplay_channel = FakeChannel()
                player.autoplay_enabled = True
                track = Track("seed", "https://seed", 180, "tester")

                self.assertTrue(player.schedule_autoplay(track))
                self.assertFalse(player.schedule_autoplay(track))
                await asyncio.sleep(0)

                self.assertEqual(len(calls), 1)
            finally:
                bot_module.auto_queue_similar = original_auto_queue

        asyncio.run(run_test())

    def test_auto_queue_skips_played_history(self):
        async def run_test():
            original = bot_module.find_similar_tracks

            async def fake_find_similar_tracks(*args, **kwargs):
                return (
                    TrackInfo("seed", "https://seed", "seed", 180),
                    feature("seedseed123"),
                    [
                        similar("current0000", 0.99),
                        similar("played00000", 0.98),
                        similar("suggested00", 0.97),
                        similar("queued00000", 0.96),
                        similar("fresh000001", 0.95),
                    ],
                )

            bot_module.find_similar_tracks = fake_find_similar_tracks
            try:
                player = GuildPlayer(bot=None, guild_id=1)
                player.worker = asyncio.create_task(asyncio.sleep(60))
                player.autoplay_enabled = True
                player.current = Track(
                    "current",
                    "https://www.youtube.com/watch?v=current0000",
                    180,
                    "tester",
                )
                player.played_urls.add("https://www.youtube.com/watch?v=played00000")
                player.suggested_urls.add("https://www.youtube.com/watch?v=suggested00")
                player.queue.put_nowait(
                    Track(
                        "queued",
                        "https://www.youtube.com/watch?v=queued00000",
                        180,
                        "tester",
                    )
                )

                await auto_queue_similar(
                    FakeChannel(),
                    player,
                    Track("seed", "https://seed", 180, "tester"),
                    "tester",
                )
                queued_urls = [track.url for track in list(player.queue._queue)]

                self.assertIn("https://www.youtube.com/watch?v=fresh000001", queued_urls)
                self.assertNotIn("https://www.youtube.com/watch?v=current0000", queued_urls)
                self.assertNotIn("https://www.youtube.com/watch?v=played00000", queued_urls)
                self.assertNotIn("https://www.youtube.com/watch?v=suggested00", queued_urls)
                self.assertEqual(
                    queued_urls.count("https://www.youtube.com/watch?v=queued00000"),
                    1,
                )
                self.assertIn(
                    "https://www.youtube.com/watch?v=fresh000001",
                    player.suggested_urls,
                )
            finally:
                bot_module.find_similar_tracks = original
                if player.worker:
                    player.worker.cancel()

        asyncio.run(run_test())

    def test_auto_queue_stops_when_autoplay_was_disabled(self):
        async def run_test():
            original = bot_module.find_similar_tracks

            async def fake_find_similar_tracks(*args, **kwargs):
                return (
                    TrackInfo("seed", "https://seed", "seed", 180),
                    feature("seedseed123"),
                    [similar("fresh000001", 0.95)],
                )

            bot_module.find_similar_tracks = fake_find_similar_tracks
            try:
                player = GuildPlayer(bot=None, guild_id=1)
                player.worker = asyncio.create_task(asyncio.sleep(60))
                player.autoplay_enabled = False

                await auto_queue_similar(
                    FakeChannel(),
                    player,
                    Track("seed", "https://seed", 180, "tester"),
                    "tester",
                )

                self.assertEqual(list(player.queue._queue), [])
            finally:
                bot_module.find_similar_tracks = original
                if player.worker:
                    player.worker.cancel()

        asyncio.run(run_test())

    def test_play_and_now_do_not_double_schedule_autoplay(self):
        async def run_test():
            original_member = bot_module.discord.Member
            original_resolve_track = bot_module.resolve_track
            original_auto_queue = bot_module.auto_queue_similar
            calls = []
            guild_id = 9876
            player = GuildPlayer(bot=bot_module.bot, guild_id=guild_id)
            player.worker = asyncio.create_task(asyncio.sleep(60))
            player.autoplay_enabled = True

            async def fake_resolve_track(query, requested_by):
                return Track(query, f"https://example.com/{query}", 180, requested_by)

            async def fake_auto_queue(*args, **kwargs):
                calls.append((args, kwargs))

            bot_module.discord.Member = FakeAuthor
            bot_module.resolve_track = fake_resolve_track
            bot_module.auto_queue_similar = fake_auto_queue
            bot_module.bot.players[guild_id] = player

            try:
                message = FakeMessage(guild_id=guild_id, author=FakeAuthor())
                await prefix_play(message, "play-seed")
                await prefix_now(message, "now-seed")

                self.assertEqual(calls, [])
                self.assertIs(player.autoplay_channel, message.channel)
            finally:
                bot_module.discord.Member = original_member
                bot_module.resolve_track = original_resolve_track
                bot_module.auto_queue_similar = original_auto_queue
                bot_module.bot.players.pop(guild_id, None)
                if player.worker:
                    player.worker.cancel()

        asyncio.run(run_test())

    def test_auto_queue_similar_adds_only_strict_non_duplicates(self):
        async def run_test():
            original = bot_module.find_similar_tracks

            async def fake_find_similar_tracks(*args, **kwargs):
                return (
                    TrackInfo("seed", "https://seed", "seed", 180),
                    feature("seedseed123"),
                    [
                        similar("duplicate1", 0.99),
                        similar("strong00001", 0.92),
                        similar("weak0000001", 0.70),
                    ],
                )

            bot_module.find_similar_tracks = fake_find_similar_tracks
            try:
                player = GuildPlayer(bot=None, guild_id=1)
                player.worker = asyncio.create_task(asyncio.sleep(60))
                player.autoplay_enabled = True
                player.queue.put_nowait(
                    Track(
                        "duplicate",
                        "https://www.youtube.com/watch?v=duplicate1",
                        180,
                        "test",
                    )
                )
                await auto_queue_similar(
                    FakeChannel(),
                    player,
                    Track("seed", "https://seed", 180, "test"),
                    "test",
                )
                queued_urls = [track.url for track in list(player.queue._queue)]
                self.assertIn("https://www.youtube.com/watch?v=strong00001", queued_urls)
                self.assertNotIn("https://www.youtube.com/watch?v=weak0000001", queued_urls)
                self.assertEqual(
                    queued_urls.count("https://www.youtube.com/watch?v=duplicate1"),
                    1,
                )
            finally:
                bot_module.find_similar_tracks = original
                if player.worker:
                    player.worker.cancel()

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
