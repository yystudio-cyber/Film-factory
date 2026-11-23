import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import dispatch, graphs, integrity, motion
from factory.common import read, write


class ReleaseIntegrityTests(unittest.TestCase):
    def setUp(self):
        box=tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.root=Path(box.name)

    def test_committed_dispatch_survives_process_death_and_missing_json(self):
        statement="""import os,sys
from pathlib import Path
from factory.dispatch import save
save(Path(sys.argv[1]), {'client_id':'only','base':'http://127.0.0.1','output':'2','prompt_id':'ok'})
os._exit(19)"""
        completed=subprocess.run([sys.executable,"-B","-c",statement,str(self.root)],check=False)
        self.assertEqual(completed.returncode,19)
        (self.root/"remote.json").unlink()
        with patch.object(graphs,"collect",return_value=self.root/"source.mp4") as collect,patch.object(graphs,"request") as req:
            graphs.render({"comfy":"http://127.0.0.1"},{},None,None,1,self.root,lambda:None)
        req.assert_not_called()
        self.assertEqual(collect.call_args.args[2],"ok")
        self.assertEqual(read(self.root/"remote.json")["prompt_id"],"ok")

    def test_job_journal_blocks_same_intent_after_take_directory_is_lost(self):
        import shutil
        folder=self.root/"takes"/"S01-V1-old"
        folder.mkdir(parents=True)
        intent={"beat":"cue","notes":[]}
        write(folder/"intent.json",intent)
        write(folder/"shot.json",{"seed":67})
        dispatch.save(folder,{"client_id":"before-post","base":"http://localhost","output":"2"})
        shutil.rmtree(folder)
        with self.assertRaisesRegex(ValueError,"未重复提交"):
            dispatch.check_working_state(self.root,"S01-V1-",intent)
        dispatch.check_working_state(self.root,"S01-V1-",dict(intent,notes=["explicit revised intent"]))

    def test_reconcile_unions_duplicate_and_refuses_ambiguous_and_unknown_layout(self):
        row=[0,"one",{},{"client_id":"mine"}]
        for queue,expect in (({"queue_pending":[row],"queue_running":[]},"one"),
                ({"queue_running":[],"queue_pending":[[0,"two",{},{"client_id":"mine"}]]},None),
                ({"new_queue":[]},None)):
            with patch.object(graphs,"request",side_effect=[queue,{"one":{"prompt":row}}]):
                if expect:self.assertEqual(graphs.reconcile("http://localhost","mine"),expect)
                else:
                    with self.assertRaisesRegex(RuntimeError,"不确定"):graphs.reconcile("http://localhost","mine")

    def test_conflicting_or_incomplete_history_never_downloads(self):
        cases=[({"status_str":"success"},None),({"status_str":"new_status","completed":True},None),
               ({"status_str":"success","completed":False},None),
               ({"status_str":"success","completed":True,"messages":[["execution_interrupted",{}]]},"interrupted"),
               ({"status_str":"error","messages":[["execution_error",{"exception_type":"torch.OutOfMemoryError",
                       "exception_message":"out of memory","current_inputs":"private placeholder"}]]},"out_of_memory")]
        for status,kind in cases:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as t:
                folder=Path(t)
                with patch.object(graphs,"request",return_value={"p":{"status":status,"outputs":{"2":{
                        "videos":[{"filename":"present.mp4","type":"output"}]}}}}) as http:
                    with self.assertRaises((ValueError,RuntimeError)):graphs.collect({},"http://localhost","p","2",folder,lambda:None)
                self.assertEqual(http.call_count,1)
                if kind:self.assertEqual(read(folder/"remote-error.json"),{"category":kind})
                else:self.assertFalse((folder/"remote-error.json").exists())

    def test_accepted_optional_absence_and_release_manifest(self):
        for name in integrity.DELIVERY:(self.root/name).write_bytes(name.encode())
        expected=integrity.capture(self.root,integrity.DELIVERY,integrity.EXTRAS)
        write(self.root/"integrity.json",{"schema":1,"files":expected})
        self.assertTrue(integrity.verify_bundle(self.root,expected)["verified"])
        (self.root/"score.wav").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError,"指纹"):integrity.verify_bundle(self.root,expected)
        (self.root/"score.wav").unlink()
        (self.root/"film.mp4").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError,"指纹"):integrity.verify_bundle(self.root,expected)

    def test_master_changed_after_review_is_not_published(self):
        from factory.store import Store
        store=Store({"data":self.root})
        job=store.add({"mode":"auto"})
        store.claim()
        output=self.root/job/"candidate.mp4"
        output.write_bytes(b"reviewed")
        digest=integrity.sha(output)
        output.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError,"总审"):
            store.publish(job,{},output,[],{"master_sha256":digest})
        self.assertIsNone(store.delivery_dir(job))

    def test_local_pulse_gate_explicit_intent_and_unverified(self):
        for intent,ok in (("motivated",True),("defect",False),("unclear",False)):
            self.assertEqual(motion.verify_pulses({"pulse_checks":[{"frame":17,"decision":intent,"evidence":"局部遮挡方向"}]},[17]),ok)
        with self.assertRaises(ValueError):motion.verify_pulses({"pulse_checks":[]},[17])
        with self.assertRaises(ValueError):motion.verify_pulses({"pulse_checks":[{"frame":16,"decision":"motivated","evidence":"x"}]},[17])

    def test_offline_verifier_ignores_local_config_and_detects_damaged_code(self):
        (self.root/"evidence").mkdir()
        for name in ("film.py","check.cmd","start.cmd","config.example.json"):
            (self.root/name).write_bytes(b"fixture")
        write(self.root/"BUILD.json",{"commit":"fixture"})
        rows=[integrity.sha(self.root/n)+"  "+n for n in ("film.py","check.cmd","start.cmd","config.example.json","BUILD.json")]
        rows.append("0"*64+"  config.local.json")
        (self.root/"evidence/checksums.sha256").write_text("\n".join(rows))
        self.assertEqual(integrity.verify_install(self.root)["checked_files"],5)
        (self.root/"film.py").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"film.py"):integrity.verify_install(self.root)
