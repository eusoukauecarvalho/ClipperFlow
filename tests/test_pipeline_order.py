"""Regressão: ajustes e filtro de persona precisam compor, não se cancelar.

Bug real (2026-09-05): com persona aplicada ANTES dos ajustes, _apply_adjustments
reconstruía removable_silences só a partir do silêncio acústico e descartava em
silêncio os cortes de persona já calculados — a fala de outro locutor voltava a vazar
em qualquer clip cuja janela (start/end) tivesse sido tocada por um ajuste. Passou
despercebido porque os testes unitários cobriam cada função isolada, nunca em série.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from adjustments import AdjustmentPlan, ClipAdjustment  # noqa: E402
from clipper import _apply_adjustments, _apply_persona  # noqa: E402
from config import ClipperConfig  # noqa: E402
from persona import SpeakerMap  # noqa: E402
from segment import Clip  # noqa: E402
from silence import Silence  # noqa: E402
from transcribe import Utterance  # noqa: E402


class AdjustmentsThenPersonaTests(unittest.TestCase):
    """A ordem correta é: ajustes fixam a janela, persona filtra ESSA janela."""

    def setUp(self):
        self.config = ClipperConfig(
            source=Path("dummy.mp4"), output_dir=Path("out"), persona="S1",
        )
        self.utterances = (
            Utterance(0.0, 10.0, "fala inicial"),
            Utterance(10.0, 40.0, "fala estendida da persona"),
        )
        # S2 (outro locutor) fala 10s bem no meio da janela estendida.
        self.speakers = SpeakerMap(
            turns=((0.0, 15.0, "S1"), (15.0, 25.0, "S2"), (25.0, 40.0, "S1")),
            totals_s={"S1": 30.0, "S2": 10.0},
            samples={},
        )
        self.clip = Clip(1, 0.0, 10.0, "original", 5.0, ())

    def _run_in_order(self, first, second):
        plan = AdjustmentPlan(
            0.0, 0.0, {"clip_001": ClipAdjustment("clip_001", start=0.0, end=40.0)}
        )
        clips = (self.clip,)

        for step in (first, second):
            if step == "adjustments":
                clips = _apply_adjustments(clips, plan, (), self.utterances, self.config)
            else:
                clips = _apply_persona(clips, self.speakers, self.config)
        return clips[0]

    def test_correct_order_removes_the_other_speaker(self):
        result = self._run_in_order("adjustments", "persona")

        # O trecho 15-25s (S2) precisa estar marcado como removível.
        cut_covers_foreign_turn = any(
            s.start <= 15.0 and s.end >= 25.0 for s in result.removable_silences
        )
        self.assertTrue(cut_covers_foreign_turn)

    def test_wrong_order_loses_the_persona_cut(self):
        # Documenta o bug: nesta ordem, o corte de persona é calculado e depois
        # apagado por rebuild_silences, que só conhece silêncio acústico.
        result = self._run_in_order("persona", "adjustments")

        cut_covers_foreign_turn = any(
            s.start <= 15.0 and s.end >= 25.0 for s in result.removable_silences
        )
        self.assertFalse(cut_covers_foreign_turn)

    def test_final_duration_excludes_the_other_speaker(self):
        result = self._run_in_order("adjustments", "persona")

        # Janela de 40s menos os 10s de S2 (sem padding, que é 0 no filtro de persona).
        self.assertLess(result.final_duration, 40.0 - 10.0 + 1.0)


if __name__ == "__main__":
    unittest.main()
