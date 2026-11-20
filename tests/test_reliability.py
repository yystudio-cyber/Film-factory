import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.common import read, write
from factory.store import Store


class WorkerStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store({"data": Path(tmp.name)})
        self.job = self.store.add({"mode": "auto"})
        self.folder = self.store.root / self.job
        write(self.folder/"plan.json", {"shots": [{}, {}]})
        self.assertEqual(self.store.claim(), self.job)

    def edit(self, shot, text, variants=1):
        self.store.command(self.job, "revise", shot=shot, note=text, variants=variants)

    def publish(self, code, data=b"v1"):
        candidate = self.folder/"candidate.mp4"
        candidate.write_bytes(data)
        return self.store.publish(self.job, {1: code}, candidate, [{"code": code}], {"ver": data.decode()})

    def select(self, take):
        code = self.store.register(self.job, 1, self.folder/take)
        self.assertTrue(self.store.advance(self.job, 1, code))
        return code

    def test_edits_survive_reused_queue_ids_and_legacy_edit_file(self):
        write(self.folder/"revisions-S01.json", [{"id":1,"note":"old","variants":1}])
        self.edit(1,"closer",2)
        self.store.revisions(self.job)
        self.edit(1,"also look left",1)
        self.store.revisions(self.job)
        history = self.store.instructions(self.job,1)
        self.assertEqual([row["note"] for row in history],["old","closer","also look left"])
        self.assertEqual([row.get("origin","legacy") for row in history],["legacy","user","user"])

    def test_edits_keep_their_own_variant_count(self):
        self.select("first")
        self.edit(1,"warm light",2)
        self.edit(2,"change title",1)
        self.store.revisions(self.job)
        self.store.revisions(self.job)
        self.assertEqual(self.store.instructions(self.job,1)[-1]["variants"],2)
        self.assertEqual(self.store.instructions(self.job,2)[-1]["variants"],1)
        self.assertFalse(any(t["selected"] for t in self.store.takes(self.job)))

    def test_stop_is_not_undone_by_late_audit(self):
        self.store.command(self.job,"stop")
        review = {"repair_shot":1, "notes":"change shot"}
        with self.assertRaises(InterruptedError):
            self.store.handle_rejection(self.job,"reject",review=review,limit=1)
        self.store.command(self.job,"resume")
        with self.assertRaises(InterruptedError):
            self.store.handle_rejection(self.job,"reject",review=review,limit=1)
        self.store.claim()
        self.assertIsNone(self.store.revisions(self.job))

    def test_repair_limit_and_pending_edit_priority(self):
        review = {"repair_shot":1, "notes":"fix mismatch"}
        self.edit(1,"new user edit")
        self.assertTrue(self.store.handle_rejection(self.job,"reject",review=review,limit=1))
        self.store.revisions(self.job)
        self.assertIsNone(self.store.revisions(self.job))
        self.assertTrue(self.store.handle_rejection(self.job,"reject",review=review,limit=1))
        self.assertEqual(self.store.revisions(self.job)["note"],"fix mismatch")
        self.assertEqual([r["origin"] for r in self.store.instructions(self.job,1)],["user","editorial"])
        self.assertFalse(self.store.handle_rejection(self.job,"reject",review=review,limit=1))

    def test_late_priority_edit_survives_exhaustion(self):
        self.assertFalse(self.store.handle_rejection(self.job,"limit"))
        self.edit(1,"redo with different camera")
        self.store.status(self.job,"failed","limit",allow_pending_edit=True)
        self.assertEqual(self.store.claim(),self.job)
        self.assertEqual(self.store.revisions(self.job)["note"],"redo with different camera")

    def test_releases_are_coherent_and_independent_of_future_assemblies(self):
        first = self.select("one")
        self.assertTrue(self.publish(first))
        release = self.store.delivery_dir(self.job)
        self.assertEqual((release/"film.mp4").read_bytes(),b"v1")
        self.edit(1,"new camera")
        self.assertIsNone(self.store.delivery_dir(self.job))
        self.store.claim()
        self.store.revisions(self.job)
        second = self.select("two")
        self.assertTrue(self.publish(second,b"v2"))
        updated = self.store.delivery_dir(self.job)
        self.assertNotEqual(release,updated)
        self.assertEqual((release/"film.mp4").read_bytes(),b"v1")
        self.assertEqual(read(release/"acceptance.json"),{"ver":"v1"})
        self.assertEqual((updated/"film.mp4").read_bytes(),b"v2")
        self.assertEqual(read(updated/"timeline.json"),[{"code":second}])

    def test_stale_or_partially_installed_release_is_not_delivered(self):
        code = self.select("one")
        self.assertFalse(self.publish(code+1))
        self.edit(1,"replace shot")
        self.assertFalse(self.publish(code))
        self.store.revisions(self.job)
        new = self.select("two")
        from factory import store as storage
        replace = storage.os.replace
        def fail_last_alias(src,dst):
            if Path(dst) == self.folder/"film.mp4":
                raise OSError("simulated file sharing violation")
            return replace(src,dst)
        with patch.object(storage.os,"replace",side_effect=fail_last_alias):
            with self.assertRaises(OSError):
                self.publish(new,b"interrupted")
        self.assertIsNone(self.store.delivery_dir(self.job))
        self.assertEqual(list((self.folder/"deliveries").iterdir()),[])
        self.assertTrue(self.publish(new,b"final"))
        self.assertEqual((self.store.delivery_dir(self.job)/"film.mp4").read_bytes(),b"final")

    def test_published_score_is_independent_from_regenerating_the_working_cache(self):
        code = self.select("one")
        score = self.folder/"score.wav"
        score.write_bytes(b"first score")
        movie = self.folder/"candidate.mp4"
        movie.write_bytes(b"first master")
        self.store.publish(self.job,{1:code},movie,[{"code":code}],{"audio_mix":{"score":True}})
        score.write_bytes(b"replacement")
        self.assertEqual((self.store.delivery_dir(self.job)/"score.wav").read_bytes(),b"first score")

    def test_legacy_completed_job_remains_visible(self):
        self.store.status(self.job,"done","existing")
        self.assertEqual(self.store.delivery_dir(self.job),self.folder)


if __name__ == "__main__":
    unittest.main()
