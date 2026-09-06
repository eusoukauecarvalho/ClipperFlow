"""Testes de legendas e pontos de corte editoriais."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from cutpoints import CutPoint, best_cut_point, build_cut_points  # noqa: E402
from silence import Silence  # noqa: E402
from subtitles import (  # noqa: E402
    SubtitleEntry,
    _chunk_text,
    _srt_timestamp,
    _wrap_lines,
    build_entries,
    map_to_clip_time,
    write_srt,
)
from transcribe import Utterance  # noqa: E402


class MapToClipTimeTests(unittest.TestCase):
    def test_maps_relative_to_clip_start(self):
        self.assertAlmostEqual(map_to_clip_time(15.0, ((10.0, 20.0),)), 5.0)

    def test_subtracts_removed_silence_from_later_times(self):
        ranges = ((10.0, 20.0), (25.0, 30.0))  # 5s removidos entre os trechos

        self.assertAlmostEqual(map_to_clip_time(27.0, ranges), 12.0)

    def test_time_inside_removed_gap_collapses_to_cut(self):
        ranges = ((10.0, 20.0), (25.0, 30.0))

        self.assertAlmostEqual(map_to_clip_time(22.0, ranges), 10.0)

    def test_returns_none_after_clip_end(self):
        self.assertIsNone(map_to_clip_time(40.0, ((10.0, 20.0),)))

    def test_time_before_clip_start_clamps_to_zero(self):
        self.assertAlmostEqual(map_to_clip_time(5.0, ((10.0, 20.0),)), 0.0)


class BuildEntriesTests(unittest.TestCase):
    def test_creates_entry_per_utterance(self):
        utterances = (Utterance(10.0, 13.0, "primeira fala"), Utterance(13.0, 16.0, "segunda"))

        entries = build_entries(utterances, ((10.0, 20.0),))

        self.assertEqual(len(entries), 2)
        self.assertAlmostEqual(entries[0].start, 0.0)

    def test_skips_utterances_outside_the_clip(self):
        utterances = (Utterance(50.0, 55.0, "fora do clip"),)

        self.assertEqual(build_entries(utterances, ((10.0, 20.0),)), ())

    def test_splits_long_utterance_into_multiple_entries(self):
        long_text = " ".join(["palavra"] * 40)
        utterances = (Utterance(10.0, 20.0, long_text),)

        entries = build_entries(utterances, ((10.0, 30.0),))

        self.assertGreater(len(entries), 1)

    def test_entries_never_run_backwards(self):
        utterances = (Utterance(10.0, 14.0, "uma frase razoavelmente longa aqui para dividir"),)

        entries = build_entries(utterances, ((10.0, 30.0),))

        for entry in entries:
            self.assertLess(entry.start, entry.end)


class SrtFormatTests(unittest.TestCase):
    def test_timestamp_uses_srt_comma_format(self):
        self.assertEqual(_srt_timestamp(3661.5), "01:01:01,500")

    def test_timestamp_clamps_negatives(self):
        self.assertEqual(_srt_timestamp(-1.0), "00:00:00,000")

    def test_chunk_respects_character_limit(self):
        chunks = _chunk_text(" ".join(["palavra"] * 30), 40)

        self.assertTrue(all(len(c) <= 40 for c in chunks))

    def test_wrap_splits_into_two_lines(self):
        wrapped = _wrap_lines(" ".join(["palavra"] * 10))

        self.assertEqual(wrapped.count("\n"), 1)

    def test_write_srt_produces_numbered_blocks(self):
        entries = (SubtitleEntry(0.0, 2.0, "ola"), SubtitleEntry(2.0, 4.0, "mundo"))

        with tempfile.TemporaryDirectory() as tmp:
            path = write_srt(entries, Path(tmp) / "x.srt")
            content = path.read_text(encoding="utf-8")

        self.assertIn("1\n00:00:00,000 --> 00:00:02,000\nola", content)
        self.assertIn("2\n00:00:02,000 --> 00:00:04,000\nmundo", content)


class CutPointTests(unittest.TestCase):
    def test_sentence_end_outranks_plain_pause(self):
        sentence = CutPoint(10.0, is_sentence_end=True, pause_duration=0.0)
        pause = CutPoint(10.0, is_sentence_end=False, pause_duration=2.0)

        self.assertGreater(sentence.quality, pause.quality)

    def test_detects_sentence_ending_punctuation(self):
        utterances = (Utterance(0.0, 5.0, "Acabou aqui."), Utterance(5.0, 9.0, "e continua"))

        points = build_cut_points(utterances, ())

        self.assertTrue(points[0].is_sentence_end)
        self.assertFalse(points[1].is_sentence_end)

    def test_associates_following_pause(self):
        utterances = (Utterance(0.0, 5.0, "fim."),)

        points = build_cut_points(utterances, (Silence(5.1, 8.1),))

        self.assertAlmostEqual(points[0].pause_duration, 3.0)

    def test_best_cut_point_prefers_quality_then_latest(self):
        points = (
            CutPoint(12.0, True, 1.0),
            CutPoint(18.0, True, 1.0),
            CutPoint(20.0, False, 0.0),
        )

        chosen = best_cut_point(points, earliest=10.0, latest=25.0)

        self.assertAlmostEqual(chosen.time, 18.0)

    def test_returns_none_when_window_has_no_candidate(self):
        points = (CutPoint(5.0, True, 1.0),)

        self.assertIsNone(best_cut_point(points, earliest=10.0, latest=25.0))


if __name__ == "__main__":
    unittest.main()


class ReportTests(unittest.TestCase):
    def setUp(self):
        from report import build_html  # noqa: PLC0415 — módulo opcional do pipeline

        self.build_html = build_html
        self.metadata = {
            "source": "entrevista.mov",
            "total_duration": "01:09:17",
            "transcription_engine": "faster-whisper",
            "clips": [
                {
                    "id": "clip_001",
                    "start": "00:12",
                    "end": "00:44",
                    "start_seconds": 12.0,
                    "final_duration": "30.0s",
                    "silence_removed": "2.0s",
                    "importance_score": 7.5,
                    "transcription": "uma fala qualquer",
                    "rendered": True,
                },
                {
                    "id": "clip_002",
                    "start": "01:00",
                    "end": "01:30",
                    "start_seconds": 60.0,
                    "final_duration": "28.0s",
                    "silence_removed": "0.0s",
                    "importance_score": 5.0,
                    "transcription": "outra fala",
                    "rendered": False,
                },
            ],
        }

    def test_includes_only_rendered_clips_when_some_rendered(self):
        html = self.build_html(self.metadata, title="T", clips_dir="x/")

        self.assertIn("clip_001", html)
        self.assertNotIn('"clip_002"', html)

    def test_falls_back_to_all_clips_when_none_rendered(self):
        for clip in self.metadata["clips"]:
            clip["rendered"] = False

        html = self.build_html(self.metadata, title="T", clips_dir="x/")

        self.assertIn("clip_002", html)

    def test_escapes_content_as_json_not_raw_html(self):
        self.metadata["clips"][0]["transcription"] = 'aspas " e <tag>'

        html = self.build_html(self.metadata, title="T", clips_dir="x/")

        self.assertNotIn("<tag>", html)

    def test_defines_colors_outside_theme_blocks(self):
        html = self.build_html(self.metadata, title="T", clips_dir="x/")

        root_block = html.split(":root {")[1].split("}")[0]
        self.assertIn("--ground:", root_block)
        self.assertIn("--ink:", root_block)

    def test_seconds_parser_tolerates_bad_input(self):
        from report import _seconds  # noqa: PLC0415

        self.assertEqual(_seconds("2.5s"), 2.5)
        self.assertEqual(_seconds("lixo"), 0.0)


class SemanticSelectionTests(unittest.TestCase):
    """A seleção vem de um modelo, então tudo é conferido contra a transcrição."""

    def setUp(self):
        import tempfile as _tempfile

        from semantic import SelectionError, load_selection

        self.load_selection = load_selection
        self.SelectionError = SelectionError
        self.tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.utterances = (
            Utterance(10.0, 20.0, "primeira fala."),
            Utterance(20.0, 40.0, "segunda fala."),
            Utterance(40.0, 60.0, "terceira fala."),
        )

    def _write(self, payload):
        path = Path(self.tmp.name) / "sel.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_accepts_valid_selection(self):
        path = self._write({"clips": [{"start": 10, "end": 40, "title": "t", "score": 8}]})

        clips = self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=90)

        self.assertEqual(len(clips), 1)
        self.assertAlmostEqual(clips[0].duration, 30.0)

    def test_rejects_timestamp_beyond_the_video(self):
        path = self._write({"clips": [{"start": 10, "end": 9999}]})

        with self.assertRaises(self.SelectionError):
            self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=90)

    def test_rejects_inverted_window(self):
        path = self._write({"clips": [{"start": 40, "end": 20}]})

        with self.assertRaises(self.SelectionError):
            self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=90)

    def test_rejects_clip_longer_than_maximum(self):
        path = self._write({"clips": [{"start": 10, "end": 60}]})

        with self.assertRaises(self.SelectionError):
            self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=30)

    def test_rejects_empty_selection(self):
        path = self._write({"clips": []})

        with self.assertRaises(self.SelectionError):
            self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=90)

    def test_snaps_loose_timestamps_to_speech(self):
        path = self._write({"clips": [{"start": 11, "end": 39}]})

        clips = self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=90)

        self.assertAlmostEqual(clips[0].start, 10.0)
        self.assertAlmostEqual(clips[0].end, 40.0)

    def test_returns_clips_in_chronological_order(self):
        path = self._write({"clips": [{"start": 40, "end": 60}, {"start": 10, "end": 20}]})

        clips = self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=90)

        self.assertLess(clips[0].start, clips[1].start)

    def test_detects_overlapping_clips(self):
        from semantic import overlaps

        path = self._write({"clips": [{"start": 10, "end": 40}, {"start": 20, "end": 60}]})
        clips = self.load_selection(path, self.utterances, min_duration_s=2, max_duration_s=90)

        self.assertEqual(len(overlaps(clips)), 1)


class AdjustmentFingerprintTests(unittest.TestCase):
    """Ajustes são indexados por id posicional; o id só vale para o conjunto original."""

    def setUp(self):
        from adjustments import (  # noqa: PLC0415
            AdjustmentError,
            AdjustmentPlan,
            fingerprint_clips,
            verify_fingerprint,
        )

        self.AdjustmentError = AdjustmentError
        self.AdjustmentPlan = AdjustmentPlan
        self.fingerprint_clips = fingerprint_clips
        self.verify_fingerprint = verify_fingerprint

        from segment import Clip  # noqa: PLC0415

        self.originais = (
            Clip(1, 10.0, 40.0, "a", 5.0, ()),
            Clip(2, 45.0, 80.0, "b", 5.0, ()),
        )
        self.deslocados = (
            Clip(1, 10.0, 52.0, "a", 5.0, ()),  # a transcrição mudou a fronteira
            Clip(2, 55.0, 80.0, "b", 5.0, ()),
        )

    def _plan(self, fingerprint):
        return self.AdjustmentPlan(5.0, 3.0, {}, fingerprint)

    def test_same_clip_set_passes(self):
        plan = self._plan(self.fingerprint_clips(self.originais))

        self.verify_fingerprint(plan, self.originais)  # não levanta

    def test_shifted_boundaries_are_refused(self):
        plan = self._plan(self.fingerprint_clips(self.originais))

        with self.assertRaises(self.AdjustmentError):
            self.verify_fingerprint(plan, self.deslocados)

    def test_error_names_both_fingerprints(self):
        plan = self._plan(self.fingerprint_clips(self.originais))

        with self.assertRaises(self.AdjustmentError) as ctx:
            self.verify_fingerprint(plan, self.deslocados)

        self.assertIn(plan.fingerprint, str(ctx.exception))

    def test_plan_without_fingerprint_is_allowed(self):
        self.verify_fingerprint(self._plan(""), self.deslocados)  # arquivo antigo

    def test_fingerprint_is_stable(self):
        self.assertEqual(
            self.fingerprint_clips(self.originais), self.fingerprint_clips(self.originais)
        )


class FrozenAdjustmentTests(unittest.TestCase):
    """Plano congelado precisa produzir a mesma janela com qualquer transcrição."""

    def setUp(self):
        from adjustments import AdjustmentPlan, ClipAdjustment, resolve_window

        self.resolve_window = resolve_window
        self.ClipAdjustment = ClipAdjustment
        self.AdjustmentPlan = AdjustmentPlan
        # Duas transcrições com fronteiras diferentes para o mesmo áudio.
        self.transcricao_a = (Utterance(10.0, 20.0, "a"), Utterance(20.0, 40.0, "b"))
        self.transcricao_b = (Utterance(12.0, 24.0, "a"), Utterance(24.0, 41.0, "b"))

    def _janela(self, utterances, snap):
        plan = self.AdjustmentPlan(0.0, 0.0, {}, "", snap)
        adjustment = self.ClipAdjustment("clip_001", start=11.0, end=39.0)
        return self.resolve_window(adjustment, (0.0, 100.0), (), utterances, plan)

    def test_frozen_window_ignores_the_transcript(self):
        a = self._janela(self.transcricao_a, snap=False)
        b = self._janela(self.transcricao_b, snap=False)

        self.assertEqual(a, b)
        self.assertEqual(a, (11.0, 39.0))

    def test_snapping_shifts_with_the_transcript(self):
        # Comportamento padrão: útil para tempo escrito à mão, perigoso para congelado.
        a = self._janela(self.transcricao_a, snap=True)
        b = self._janela(self.transcricao_b, snap=True)

        self.assertNotEqual(a, b)

    def test_snap_defaults_to_enabled(self):
        self.assertTrue(self.AdjustmentPlan(0.0, 0.0, {}).snap)

    def test_start_never_goes_negative(self):
        plan = self.AdjustmentPlan(0.0, 0.0, {}, "", False)
        adjustment = self.ClipAdjustment("clip_001", start=-5.0, end=10.0)

        start, _ = self.resolve_window(adjustment, (0.0, 100.0), (), self.transcricao_a, plan)

        self.assertGreaterEqual(start, 0.0)
