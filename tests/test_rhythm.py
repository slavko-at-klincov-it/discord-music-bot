import json
import tempfile
import unittest
from pathlib import Path

import rhythm as rhythm_module
from rhythm import (
    TrackFeatures,
    TrackInfo,
    _features_from_cache,
    _is_near_duplicate_title,
    canonical_youtube_url,
    discover_candidates,
    extract_video_id,
    resolve_stream_url,
    run_text,
    score_similarity,
)


class RhythmTests(unittest.TestCase):
    def test_run_text_reports_missing_executable(self):
        async def run_test():
            with self.assertRaisesRegex(RuntimeError, "Executable not found"):
                await run_text("/definitely/missing/yt-dlp")

        import asyncio

        asyncio.run(run_test())

    def test_resolve_stream_uses_compatible_youtube_clients(self):
        async def run_test():
            original = rhythm_module.run_text
            captured = []

            async def fake_run_text(*args, **kwargs):
                captured.extend(args)
                return "https://example.com/playable-stream\n"

            rhythm_module.run_text = fake_run_text
            try:
                stream_url = await resolve_stream_url(
                    TrackInfo(
                        title="title",
                        url="https://www.youtube.com/watch?v=test",
                        video_id="test",
                        duration=180,
                    ),
                    "yt-dlp",
                )
            finally:
                rhythm_module.run_text = original

            self.assertEqual(stream_url, "https://example.com/playable-stream")
            self.assertIn("--extractor-args", captured)
            self.assertIn("youtube:player_client=android,web_safari", captured)

        import asyncio

        asyncio.run(run_test())

    def test_extract_video_id_from_common_youtube_urls(self):
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=8K2MxDuAJw8&list=RD8K2MxDuAJw8"),
            "8K2MxDuAJw8",
        )
        self.assertEqual(extract_video_id("https://youtu.be/8K2MxDuAJw8?t=10"), "8K2MxDuAJw8")
        self.assertEqual(
            extract_video_id("https://www.youtube.com/shorts/8K2MxDuAJw8"),
            "8K2MxDuAJw8",
        )

    def test_near_duplicate_title_detects_same_track_variants(self):
        self.assertTrue(
            _is_near_duplicate_title(
                "Themba - Who Is Themba?",
                "Themba \u2013 Who Is Themba? (Original Mix) | Afro House Source | #afrohouse",
            )
        )
        self.assertTrue(_is_near_duplicate_title("Themba - Who Is Themba?", "Who Is Themba?"))
        self.assertFalse(
            _is_near_duplicate_title(
                "Themba - Who Is Themba?",
                "Black Coffee - Wish You Were Here",
            )
        )

    def test_discover_candidates_uses_radio_and_skips_duplicate_titles(self):
        async def run_test():
            original = rhythm_module.run_text
            calls = []

            async def fake_run_text(*args, **kwargs):
                calls.append(args[-1])
                return json.dumps(
                    {
                        "entries": [
                            {
                                "id": "seedseed123",
                                "title": "Themba - Who Is Themba?",
                                "duration": 408,
                            },
                            {
                                "id": "dup00000001",
                                "title": "Themba - Who Is Themba? (Original Mix) | Afro House Source",
                                "duration": 420,
                            },
                            {
                                "id": "dup00000002",
                                "title": "Who Is Themba?",
                                "duration": 410,
                            },
                            {
                                "id": "unique00001",
                                "title": "Black Coffee - Wish You Were Here",
                                "duration": 380,
                            },
                            {
                                "id": "unique00002",
                                "title": "Shimza - Kimberley",
                                "duration": 360,
                            },
                        ]
                    }
                )

            rhythm_module.run_text = fake_run_text
            try:
                seed = TrackInfo(
                    title="Themba - Who Is Themba?",
                    url=canonical_youtube_url("seedseed123"),
                    video_id="seedseed123",
                    duration=408,
                )
                candidates = await discover_candidates(seed, seed.url, "yt-dlp")
            finally:
                rhythm_module.run_text = original

            self.assertIn("list=RDseedseed123", calls[0])
            self.assertEqual(
                [candidate.title for candidate in candidates],
                ["Black Coffee - Wish You Were Here", "Shimza - Kimberley"],
            )

        import asyncio

        asyncio.run(run_test())

    def test_score_prefers_close_bpm_and_pulse(self):
        seed = TrackFeatures(
            video_id="seed",
            title="seed",
            url="https://www.youtube.com/watch?v=seedseed123",
            duration=180,
            bpm=120.0,
            beat_stability=0.9,
            bass_energy=0.7,
            bass_pulse=[0.25, 0.25, 0.25, 0.25],
        )
        close = TrackFeatures(
            video_id="close",
            title="close",
            url="https://www.youtube.com/watch?v=close123456",
            duration=180,
            bpm=123.0,
            beat_stability=0.88,
            bass_energy=0.68,
            bass_pulse=[0.24, 0.26, 0.25, 0.25],
        )
        far = TrackFeatures(
            video_id="far",
            title="far",
            url="https://www.youtube.com/watch?v=farfar12345",
            duration=180,
            bpm=82.0,
            beat_stability=0.4,
            bass_energy=0.15,
            bass_pulse=[0.8, 0.1, 0.05, 0.05],
        )

        close_score, _ = score_similarity(seed, close)
        far_score, _ = score_similarity(seed, far)
        self.assertGreater(close_score, far_score)
        self.assertGreater(close_score, 0.78)
        self.assertLess(far_score, 0.78)

    def test_corrupted_cache_returns_none(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "bad.json"
            path.write_text("{not valid json")
            self.assertIsNone(_features_from_cache(path))

            path.write_text(json.dumps({"cache_version": 999, "features": {}}))
            self.assertIsNone(_features_from_cache(path))


if __name__ == "__main__":
    unittest.main()
