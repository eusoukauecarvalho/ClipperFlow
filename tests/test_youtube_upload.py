"""Testes do upload em lote e do agendamento nativo do YouTube."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from youtube_upload import (  # noqa: E402
    BRAZIL_TZ,
    DEFAULT_SLOTS_LOCAL,
    build_schedule,
    load_queue,
    prioritized,
    save_queue,
)


class BuildScheduleTests(unittest.TestCase):
    """Regressão: um slot que cruza a meia-noite não pode cair no passado nem duplicar."""

    def _assert_all_future_and_ordered(self, start, slots=DEFAULT_SLOTS_LOCAL, count=12):
        schedule = build_schedule(count, start_date=start, slots=slots)
        locals_ = [datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(BRAZIL_TZ) for s in schedule]

        self.assertEqual(len(schedule), count)
        for moment in locals_:
            self.assertGreater(moment, start)
        self.assertEqual(locals_, sorted(locals_))
        return locals_

    def test_never_schedules_in_the_past_from_various_starting_hours(self):
        for hour in (0, 1, 2, 3, 5, 6, 10, 13, 18, 23):
            with self.subTest(hour=hour):
                self._assert_all_future_and_ordered(
                    datetime(2026, 9, 6, hour, 30, tzinfo=BRAZIL_TZ)
                )

    def test_maintains_four_per_day_cadence(self):
        moments = self._assert_all_future_and_ordered(
            datetime(2026, 9, 6, 10, 0, tzinfo=BRAZIL_TZ), count=8
        )
        days = {m.date() for m in moments}
        # 8 posts a 4/dia cobrem no máximo 3 datas civis (a madrugada empurra 1 dia).
        self.assertLessEqual(len(days), 3)

    def test_no_duplicate_timestamps(self):
        moments = self._assert_all_future_and_ordered(
            datetime(2026, 9, 6, 5, 0, tzinfo=BRAZIL_TZ)
        )
        self.assertEqual(len(moments), len(set(moments)))

    def test_dawn_slot_lands_before_morning_of_the_same_day(self):
        # Regressão direta do bug: 02:00 precisa vir ANTES das 06:00 do mesmo dia civil,
        # não ser empurrado para o dia seguinte por engano.
        moments = self._assert_all_future_and_ordered(
            datetime(2026, 9, 6, 0, 0, tzinfo=BRAZIL_TZ)
        )
        dawn = next(m for m in moments if m.hour == 2)
        morning_same_day = next(
            m for m in moments if m.hour == 6 and m.date() == dawn.date()
        )
        self.assertLess(dawn, morning_same_day)

    def test_custom_slots_are_respected(self):
        schedule = build_schedule(
            3, start_date=datetime(2026, 9, 6, 0, 0, tzinfo=BRAZIL_TZ), slots=("09:00",)
        )
        hours = {
            datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(BRAZIL_TZ).hour
            for s in schedule
        }
        self.assertEqual(hours, {9})


class QueuePrioritizationTests(unittest.TestCase):
    def test_stars_come_first_among_pending(self):
        queue = [
            {"id": "a", "status": "pendente", "estrela": False},
            {"id": "b", "status": "pendente", "estrela": True},
            {"id": "c", "status": "pendente", "estrela": False},
        ]

        ordered = prioritized(queue)

        self.assertEqual(ordered[0]["id"], "b")

    def test_already_published_items_are_excluded(self):
        queue = [
            {"id": "a", "status": "publicado", "estrela": True},
            {"id": "b", "status": "pendente", "estrela": False},
        ]

        ordered = prioritized(queue)

        self.assertEqual([item["id"] for item in ordered], ["b"])

    def test_stable_order_among_equal_priority(self):
        queue = [
            {"id": "a", "status": "pendente", "estrela": False},
            {"id": "b", "status": "pendente", "estrela": False},
        ]

        ordered = prioritized(queue)

        self.assertEqual([item["id"] for item in ordered], ["a", "b"])


class QueuePersistenceTests(unittest.TestCase):
    def test_round_trips_through_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fila.json"
            save_queue(path, [{"id": "a", "status": "pendente"}])

            self.assertEqual(load_queue(path)[0]["id"], "a")


if __name__ == "__main__":
    unittest.main()


class RunBatchMessagingTests(unittest.TestCase):
    """max_uploads=0 não é o mesmo que fila vazia — a mensagem precisa distinguir."""

    def test_empty_pending_queue_is_reported_as_empty(self):
        from io import StringIO
        from unittest.mock import patch

        from youtube_upload import run_batch

        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "fila.json"
            save_queue(queue_path, [{"id": "a", "status": "publicado"}])

            with patch("sys.stdout", new_callable=StringIO) as captured:
                result = run_batch(queue_path, Path(tmp), "cred", "token", max_uploads=4)

        self.assertEqual(result["enviados"], 0)
        self.assertIn("nada pendente na fila", captured.getvalue())

    def test_zero_max_uploads_with_pending_items_says_so(self):
        from io import StringIO
        from unittest.mock import patch

        from youtube_upload import run_batch

        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "fila.json"
            save_queue(queue_path, [{"id": "a", "status": "pendente", "estrela": False}])

            with patch("sys.stdout", new_callable=StringIO) as captured:
                result = run_batch(queue_path, Path(tmp), "cred", "token", max_uploads=0)

        self.assertEqual(result["enviados"], 0)
        self.assertIn("max_uploads=0", captured.getvalue())
        self.assertNotIn("nada pendente na fila", captured.getvalue())


class PublishTimestampTests(unittest.TestCase):
    """Publicação direta precisa carimbar a data: sem ela o post some da agenda."""

    def _fila(self, tmp):
        caminho = Path(tmp) / "fila.json"
        save_queue(caminho, [{
            "id": "clip_001", "status": "pendente", "estrela": False,
            "arquivo": "v.mp4", "titulo": "T", "descricao": "d", "tags": [],
        }])
        return caminho

    def _rodar(self, tmp, agendar):
        from unittest.mock import patch

        from youtube_upload import run_batch

        caminho = self._fila(tmp)
        (Path(tmp) / "v.mp4").write_bytes(b"v")
        with patch("youtube_upload.upload_one", return_value="ABC123"):
            run_batch(caminho, Path(tmp), "cred", "tok", 1, schedule=agendar)
        return json.loads(caminho.read_text(encoding="utf-8"))[0]

    def test_direct_publish_records_when(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self._rodar(tmp, agendar=False)

        self.assertEqual(item["status"], "publicado")
        self.assertTrue(item["published_at"].endswith("Z"))
        self.assertNotIn("publish_at", item)

    def test_scheduled_publish_keeps_publish_at_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self._rodar(tmp, agendar=True)

        self.assertEqual(item["status"], "agendado")
        self.assertTrue(item["publish_at"].endswith("Z"))
        self.assertNotIn("published_at", item)

    def test_timestamp_is_parseable_utc(self):
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmp:
            item = self._rodar(tmp, agendar=False)

        quando = datetime.strptime(item["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        agora = datetime.now(timezone.utc)
        self.assertLess(abs((agora - quando).total_seconds()), 60)
