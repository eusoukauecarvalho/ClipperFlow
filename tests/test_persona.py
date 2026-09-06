"""Testes do filtro de persona (fala de quem está na câmera)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from persona import (  # noqa: E402
    PersonaError,
    SpeakerMap,
    foreign_turns,
    load_speakers,
    window_breakdown,
)


def _mapa(turns):
    totals: dict[str, float] = {}
    for start, end, speaker in turns:
        totals[speaker] = totals.get(speaker, 0.0) + (end - start)
    return SpeakerMap(turns=tuple(turns), totals_s=totals, samples={})


class ForeignTurnsTests(unittest.TestCase):
    def setUp(self):
        # S1 = persona (câmera); S2 = apresentador.
        self.mapa = _mapa([
            (0.0, 10.0, "S1"),
            (10.0, 16.0, "S2"),   # pergunta longa: remover
            (16.0, 30.0, "S1"),
            (30.0, 30.9, "S2"),   # "uhum": manter
            (30.9, 40.0, "S1"),
        ])

    def test_removes_long_foreign_turns(self):
        cuts = foreign_turns(self.mapa, "S1", 0.0, 40.0)

        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts[0].start, 10.12, places=2)
        self.assertAlmostEqual(cuts[0].end, 15.88, places=2)

    def test_keeps_short_reactions(self):
        cuts = foreign_turns(self.mapa, "S1", 0.0, 40.0)

        # O "uhum" de 0.9s não vira corte — removê-lo criaria jump cut por reação.
        self.assertFalse(any(c.start > 29 for c in cuts))

    def test_clips_turns_to_the_window(self):
        cuts = foreign_turns(self.mapa, "S1", 12.0, 40.0)

        self.assertGreaterEqual(cuts[0].start, 12.0)

    def test_rejects_unknown_persona(self):
        with self.assertRaises(PersonaError):
            foreign_turns(self.mapa, "S9", 0.0, 40.0)

    def test_no_cuts_when_only_persona_speaks(self):
        solo = _mapa([(0.0, 30.0, "S1")])

        self.assertEqual(foreign_turns(solo, "S1", 0.0, 30.0), ())

    def test_adjacent_foreign_turns_merge(self):
        mapa = _mapa([
            (0.0, 5.0, "S1"),
            (5.0, 8.0, "S2"),
            (8.2, 11.0, "S2"),   # separado por 0.2s: vira um corte só
            (11.0, 20.0, "S1"),
        ])

        cuts = foreign_turns(mapa, "S1", 0.0, 20.0)

        self.assertEqual(len(cuts), 1)


class WindowBreakdownTests(unittest.TestCase):
    def test_sums_overlap_per_speaker(self):
        mapa = _mapa([(0.0, 10.0, "S1"), (10.0, 20.0, "S2")])

        totals = window_breakdown(mapa, 5.0, 15.0)

        self.assertAlmostEqual(totals["S1"], 5.0)
        self.assertAlmostEqual(totals["S2"], 5.0)

    def test_ignores_turns_outside_the_window(self):
        mapa = _mapa([(0.0, 10.0, "S1"), (50.0, 60.0, "S2")])

        totals = window_breakdown(mapa, 0.0, 20.0)

        self.assertNotIn("S2", totals)


class LoadSpeakersTests(unittest.TestCase):
    def test_loads_turns_and_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps({
                "turns": [{"start": 0, "end": 5, "speaker": "S1"}],
                "totals_s": {"S1": 5.0},
                "samples": {"S1": ["ola"]},
            }), encoding="utf-8")

            mapa = load_speakers(path)

        self.assertEqual(mapa.speakers, ("S1",))

    def test_rejects_file_without_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text('{"turns": []}', encoding="utf-8")

            with self.assertRaises(PersonaError):
                load_speakers(path)


if __name__ == "__main__":
    unittest.main()


class PersonaReportTests(unittest.TestCase):
    def setUp(self):
        from persona_report import analyze, classify

        self.analyze = analyze
        self.classify = classify

    def test_thresholds(self):
        self.assertEqual(self.classify(0.05), "limpo")
        self.assertEqual(self.classify(0.30), "contaminado")
        self.assertEqual(self.classify(0.80), "comprometido")

    def test_analyze_measures_each_clip(self):
        mapa = _mapa([(0.0, 20.0, "S1"), (20.0, 30.0, "S2")])
        metadata = {"clips": [{
            "id": "clip_001", "start": "00:00", "end": "00:30",
            "start_seconds": 0.0, "end_seconds": 30.0,
        }]}

        rows = self.analyze(metadata, mapa, "S1")

        self.assertEqual(rows[0]["outros_pct"], 33)
        self.assertEqual(rows[0]["status"], "contaminado")

    def test_clip_with_no_speech_does_not_divide_by_zero(self):
        mapa = _mapa([(100.0, 110.0, "S1")])
        metadata = {"clips": [{
            "id": "clip_001", "start": "00:00", "end": "00:10",
            "start_seconds": 0.0, "end_seconds": 10.0,
        }]}

        rows = self.analyze(metadata, mapa, "S1")

        self.assertEqual(rows[0]["status"], "limpo")


class KeepSpanTests(unittest.TestCase):
    """Whitelist: só o que é comprovadamente da persona sobrevive."""

    def setUp(self):
        from persona import persona_keep_spans, removable_from_keep_spans

        self.keep = persona_keep_spans
        self.complement = removable_from_keep_spans
        self.mapa = _mapa([
            (0.0, 10.0, "S1"),
            (10.0, 16.0, "S2"),
            (16.0, 30.0, "S1"),
        ])

    def test_keeps_only_persona_spans(self):
        spans = self.keep(self.mapa, "S1", 0.0, 30.0)

        self.assertEqual(len(spans), 2)

    def test_trims_edges_touching_other_speakers(self):
        spans = self.keep(self.mapa, "S1", 0.0, 30.0)

        # A borda que encosta no S2 recua 0.1s para engolir vazamento de fronteira.
        self.assertAlmostEqual(spans[0][1], 9.9, places=2)
        self.assertAlmostEqual(spans[1][0], 16.1, places=2)

    def test_window_edges_are_not_trimmed(self):
        spans = self.keep(self.mapa, "S1", 0.0, 30.0)

        self.assertAlmostEqual(spans[0][0], 0.0)
        self.assertAlmostEqual(spans[1][1], 30.0)

    def test_close_spans_join_without_seam(self):
        mapa = _mapa([(0.0, 5.0, "S1"), (5.3, 9.0, "S1")])

        spans = self.keep(mapa, "S1", 0.0, 9.0)

        self.assertEqual(len(spans), 1)

    def test_unlabeled_gaps_fall_in_the_complement(self):
        # Entre 9.9 e 16.1 há S2 E o vão sem rótulo — tudo vira corte.
        spans = self.keep(self.mapa, "S1", 0.0, 30.0)
        cuts = self.complement(spans, 0.0, 30.0)

        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts[0].start, 9.9, places=2)
        self.assertAlmostEqual(cuts[0].end, 16.1, places=2)

    def test_complement_of_full_coverage_is_empty(self):
        self.assertEqual(self.complement(((0.0, 30.0),), 0.0, 30.0), ())
