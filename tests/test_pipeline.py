"""Testes das partes puras do pipeline — sem ffmpeg e sem modelos de transcrição."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from config import ClipperConfig  # noqa: E402
from ffmpeg_utils import format_timecode  # noqa: E402
from render import build_filter_complex, encoder_args, scale_filter  # noqa: E402
from trimming import keep_ranges  # noqa: E402
from segment import Clip, build_clips, score_clip  # noqa: E402
from silence import Silence, _parse_silencedetect, silences_within  # noqa: E402
from transcribe import Utterance, _collect_with_progress  # noqa: E402

SILENCEDETECT_SAMPLE = """
[silencedetect @ 0x1] silence_start: 10.0
[silencedetect @ 0x1] silence_end: 13.5 | silence_duration: 3.5
[silencedetect @ 0x1] silence_start: 50.0
[silencedetect @ 0x1] silence_end: 52.4 | silence_duration: 2.4
"""


def _config(**overrides) -> ClipperConfig:
    base = ClipperConfig(source=Path("dummy.mp4"), output_dir=Path("out"))
    return base.with_overrides(**overrides)


class ParseSilencedetectTests(unittest.TestCase):
    def test_parses_paired_start_and_end_markers(self):
        # Arrange / Act
        silences = _parse_silencedetect(SILENCEDETECT_SAMPLE, total_duration_s=100.0)

        # Assert
        self.assertEqual(len(silences), 2)
        self.assertAlmostEqual(silences[0].start, 10.0)
        self.assertAlmostEqual(silences[0].end, 13.5)
        self.assertAlmostEqual(silences[1].duration, 2.4)

    def test_closes_trailing_silence_at_end_of_file(self):
        stderr = "[silencedetect] silence_start: 90.0\n"

        silences = _parse_silencedetect(stderr, total_duration_s=100.0)

        self.assertEqual(len(silences), 1)
        self.assertAlmostEqual(silences[0].end, 100.0)

    def test_returns_empty_when_no_silence_reported(self):
        self.assertEqual(_parse_silencedetect("nada aqui", 100.0), ())


class SilencesWithinTests(unittest.TestCase):
    def test_clips_silence_to_window_bounds(self):
        silences = (Silence(8.0, 14.0),)

        result = silences_within(silences, start=10.0, end=20.0, min_duration_s=2.0)

        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].start, 10.0)
        self.assertAlmostEqual(result[0].end, 14.0)

    def test_drops_silence_shorter_than_minimum_after_clipping(self):
        silences = (Silence(9.0, 10.5),)

        result = silences_within(silences, start=10.0, end=20.0, min_duration_s=2.0)

        self.assertEqual(result, ())


class BuildClipsTests(unittest.TestCase):
    def setUp(self):
        self.silences = (Silence(10.0, 13.0), Silence(50.0, 53.0))
        self.utterances = tuple(
            Utterance(start, start + 5.0, f"fala de teste numero {index} com palavras")
            for index, start in enumerate(range(0, 100, 5))
        )

    def test_splits_on_long_pauses(self):
        clips = build_clips(self.utterances, self.silences, 100.0, _config())

        self.assertEqual(len(clips), 3)
        self.assertAlmostEqual(clips[0].start, 0.0)
        self.assertAlmostEqual(clips[1].start, 13.0)
        self.assertAlmostEqual(clips[2].start, 53.0)

    def test_reindexes_clips_chronologically(self):
        clips = build_clips(self.utterances, self.silences, 100.0, _config())

        self.assertEqual([c.clip_id for c in clips], ["clip_001", "clip_002", "clip_003"])

    def test_enforces_max_duration(self):
        config = _config(max_clip_duration_s=20.0)

        clips = build_clips(self.utterances, self.silences, 100.0, config)

        self.assertTrue(all(c.original_duration <= 20.0 + 1e-6 for c in clips))

    def test_drops_windows_shorter_than_minimum(self):
        config = _config(min_clip_duration_s=15.0)

        clips = build_clips(self.utterances, self.silences, 100.0, config)

        self.assertTrue(all(c.original_duration >= 15.0 for c in clips))
        self.assertNotIn(0.0, [c.start for c in clips])

    def test_respects_max_clips_cap(self):
        config = _config(max_clips=2)

        clips = build_clips(self.utterances, self.silences, 100.0, config)

        self.assertEqual(len(clips), 2)

    def test_returns_empty_without_transcription(self):
        clips = build_clips((), self.silences, 100.0, _config())

        self.assertEqual(clips, ())


class KeepRangesTests(unittest.TestCase):
    def test_returns_whole_clip_when_nothing_to_remove(self):
        self.assertEqual(keep_ranges(0.0, 30.0, (), 0.15), ((0.0, 30.0),))

    def test_splits_around_internal_silence_keeping_padding(self):
        ranges = keep_ranges(0.0, 30.0, (Silence(10.0, 14.0),), 0.5)

        self.assertEqual(len(ranges), 2)
        self.assertAlmostEqual(ranges[0][1], 10.5)
        self.assertAlmostEqual(ranges[1][0], 13.5)

    def test_silence_shorter_than_padding_is_not_cut(self):
        self.assertEqual(keep_ranges(0.0, 30.0, (Silence(10.0, 10.4),), 0.5), ((0.0, 30.0),))


class ClipDurationTests(unittest.TestCase):
    """A duração relatada precisa ser a que o ffmpeg produz, não a teórica."""

    def test_padding_returns_to_the_clip(self):
        clip = Clip(1, 0.0, 30.0, "texto", 8.0, (Silence(10.0, 14.0),), edge_padding_s=0.15)

        # 4s de silêncio, mas 2 x 0.15s de respiro voltam: saem 3.7s, não 4.0s.
        self.assertAlmostEqual(clip.removed_silence_duration, 3.7)
        self.assertAlmostEqual(clip.final_duration, 26.3)

    def test_final_duration_equals_sum_of_kept_ranges(self):
        clip = Clip(1, 0.0, 30.0, "texto", 8.0, (Silence(10.0, 14.0), Silence(20.0, 22.0)), 0.15)

        self.assertAlmostEqual(
            clip.final_duration, sum(e - s for s, e in clip.keep_ranges)
        )

    def test_no_silence_means_no_removal(self):
        clip = Clip(1, 0.0, 30.0, "texto", 8.0, ())

        self.assertAlmostEqual(clip.removed_silence_duration, 0.0)
        self.assertAlmostEqual(clip.final_duration, 30.0)

    def test_zero_padding_removes_the_whole_silence(self):
        clip = Clip(1, 0.0, 30.0, "texto", 8.0, (Silence(10.0, 14.0),), edge_padding_s=0.0)

        self.assertAlmostEqual(clip.removed_silence_duration, 4.0)


class _FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class CollectWithProgressTests(unittest.TestCase):
    def test_collects_every_non_empty_segment(self):
        segments = [_FakeSegment(0.0, 5.0, " ola "), _FakeSegment(5.0, 9.0, "mundo")]

        result = _collect_with_progress(iter(segments), 9.0)

        self.assertEqual([u.text for u in result], ["ola", "mundo"])

    def test_skips_blank_segments(self):
        segments = [_FakeSegment(0.0, 5.0, "   "), _FakeSegment(5.0, 9.0, "vale")]

        result = _collect_with_progress(iter(segments), 9.0)

        self.assertEqual(len(result), 1)

    def test_handles_unknown_duration_without_dividing_by_zero(self):
        segments = [_FakeSegment(0.0, 5.0, "ok")]

        result = _collect_with_progress(iter(segments), 0.0)

        self.assertEqual(len(result), 1)


class ScaleFilterTests(unittest.TestCase):
    def test_disabled_when_height_is_zero(self):
        self.assertEqual(scale_filter(0), "")

    def test_targets_short_side_not_height(self):
        result = scale_filter(1080)

        self.assertIn("force_original_aspect_ratio=increase", result)
        self.assertNotIn("scale=-2:", result)  # escalar por altura quebra vídeo vertical

    def test_forces_even_dimensions(self):
        self.assertIn("force_divisible_by=2", scale_filter(1080))

    def test_never_upscales_smaller_sources(self):
        result = scale_filter(1080)

        self.assertIn("min(1080", result)


class EncoderArgsTests(unittest.TestCase):
    def test_videotoolbox_uses_bitrate_never_qscale(self):
        args = encoder_args("h264_videotoolbox")

        self.assertIn("-b:v", args)
        self.assertNotIn("-q:v", args)  # o encoder rejeita qscale e falha ao abrir
        self.assertNotIn("-crf", args)

    def test_software_encoder_uses_crf_and_preset(self):
        args = encoder_args("libx264")

        self.assertIn("-crf", args)
        self.assertIn("-preset", args)


class FilterComplexTests(unittest.TestCase):
    def test_concat_counts_every_range(self):
        graph, _label = build_filter_complex(((0.0, 5.0), (8.0, 12.0)), scale_short_side=0)

        self.assertIn("concat=n=2:v=1:a=1", graph)

    def test_returns_concat_label_without_scaling(self):
        _graph, label = build_filter_complex(((0.0, 5.0), (8.0, 12.0)), scale_short_side=0)

        self.assertEqual(label, "[outv]")

    def test_scales_once_after_concat(self):
        graph, label = build_filter_complex(((0.0, 5.0), (8.0, 12.0)), scale_short_side=1080)

        self.assertEqual(label, "[scaled]")
        self.assertEqual(graph.count("scale=w="), 1)
        self.assertLess(graph.index("concat=n=2"), graph.index("scale=w="))


class FilterTimingTests(unittest.TestCase):
    def test_uses_absolute_source_times(self):
        graph, _ = build_filter_complex(((60.0, 65.0), (70.0, 75.0)), 0)

        # Absolutos porque o render usa -copyts; relativizar cortaria no lugar errado.
        self.assertIn("trim=start=60.000:end=65.000", graph)
        self.assertIn("trim=start=70.000:end=75.000", graph)

    def test_concat_render_preserves_timestamps(self):
        from render import _concat_seek_args  # noqa: PLC0415

        args = _concat_seek_args(60.0, 100.0)

        self.assertEqual(args[0], "-copyts")
        self.assertIn("-t", args)

    def test_audio_and_video_trims_stay_aligned(self):
        graph, _ = build_filter_complex(((60.0, 65.0),), 0)

        self.assertEqual(graph.count("trim=start=60.000:end=65.000"), 2)


class ScoreClipTests(unittest.TestCase):
    def test_returns_zero_for_empty_text(self):
        self.assertEqual(score_clip("", 30.0), 0.0)

    def test_stays_within_bounds(self):
        score = score_clip("palavra " * 500, 30.0)

        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 10.0)

    def test_hook_markers_raise_score(self):
        neutral = score_clip("entao a gente foi ali e voltou depois do almoco tranquilo", 20.0)
        hooked = score_clip("o segredo e que eu descobri o erro depois do almoco tranquilo", 20.0)

        self.assertGreater(hooked, neutral)

    def test_is_deterministic(self):
        self.assertEqual(score_clip("mesmo texto aqui", 20.0), score_clip("mesmo texto aqui", 20.0))


class ConfigValidationTests(unittest.TestCase):
    def test_rejects_missing_source(self):
        with self.assertRaises(ValueError):
            _config().validate()

    def test_rejects_unsupported_extension(self):
        config = _config(source=Path(__file__))

        with self.assertRaises(ValueError):
            config.validate()

    def test_rejects_positive_db_threshold(self):
        config = _config(silence_db_threshold=10.0)

        with self.assertRaises(ValueError):
            config.validate()

    def test_rejects_odd_scale_height(self):
        config = _config(source=Path("dummy.mp4"), scale_short_side=1081)

        with self.assertRaises(ValueError):
            config.validate()

    def test_rejects_negative_scale_height(self):
        config = _config(scale_short_side=-1)

        with self.assertRaises(ValueError):
            config.validate()

    def test_with_overrides_does_not_mutate_original(self):
        original = _config()

        updated = original.with_overrides(max_clips=5)

        self.assertEqual(original.max_clips, 60)
        self.assertEqual(updated.max_clips, 5)


class TimecodeTests(unittest.TestCase):
    def test_formats_under_one_hour_as_mm_ss(self):
        self.assertEqual(format_timecode(74.4), "01:14")

    def test_formats_over_one_hour_as_hh_mm_ss(self):
        self.assertEqual(format_timecode(5025.0), "01:23:45")

    def test_clamps_negative_values(self):
        self.assertEqual(format_timecode(-5.0), "00:00")


if __name__ == "__main__":
    unittest.main()
