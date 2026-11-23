"""Nine authored cuts through the normal HTTP queue; all inference services are local fixtures."""
import os
import shutil
import threading
import time
import unittest
from pathlib import Path

from factory import media
from factory.common import read,request,write


class FullPilotQueue(unittest.TestCase):
    def test_60_seconds_from_one_submission(self):
        import test_factory
        env=test_factory.Environment()
        env.setUp()
        self.addCleanup(env.doCleanups)
        root=env.cfg["data"].parent
        env.cfg.update(width=1920,height=1080,score_candidates=2,motion_review='adaptive')
        env.timed_media={}
        for seconds,color in ((6,"394357"),(8,"264437")):
            frames=seconds*24+1
            plate=root/f"plate-{frames}.mp4"
            media.ff("-f","lavfi","-i",f"color=c=0x{color}:s=1920x1088:r=24",
                     "-f","lavfi","-i",f"sine=f={seconds*45}:r=48000","-t",str(frames/24),
                     "-c:v","libx264","-threads:v","2","-preset","ultrafast","-c:a","aac",plate)
            env.timed_media[frames]=plate
        media.ff("-f","lavfi","-i","sine=f=190:r=48000:d=60","-c:a","pcm_s16le",root/"fixture.wav")
        elapsed=0
        script=[]
        shots=[]
        durations=[8,8,6,6,6,6,6,6,8]
        for index,sec in enumerate(durations,1):
            stop=elapsed+sec
            script.append(f"【Clip {index}｜{elapsed//60:02}:{elapsed%60:02}-{stop//60:02}:{stop%60:02}（{sec}s）】测试台保持稳定。")
            shots.append(dict(beat=f"测试台 {index}",seconds=sec,start="previous" if index>1 else "",
                              end="anchor.png" if index==9 else "",sound="test tone",dialogue="",
                              narration="",title="",caption="",overlay=""))
            elapsed=stop
        shots[1]["bubble"]="#TEST"
        env.fixture_plan={"shots":shots,"endcard":"THE END","bible":"test bench","music":"instrumental"}
        key=request(env.url+"/api/jobs",dict(script="\n".join(script),requirements="第二镜 #TEST 气泡，片尾定版 THE END，最后一镜用 anchor.png。",
                                             mode="auto",music=True))["id"]
        worker=threading.Thread(target=env.engine.worker,daemon=True)
        worker.start()
        try:
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                state=env.store.job(key)
                if state["state"] not in ("queued","running"):
                    break
                time.sleep(.2)
            self.assertEqual(state["state"],"done",state["detail"])
        finally:
            env.engine.shutdown.set()
            worker.join(30)
        release=env.store.delivery_dir(key)
        timeline=read(release/"timeline.json")
        audit=read(release/"acceptance.json")
        self.assertEqual([s["seconds"] for s in timeline],durations)
        self.assertEqual([r["source_clip"]["clip"] for r in timeline],list(range(1,10)))
        self.assertEqual([s["previous_code"] for s in timeline[1:]],[s["code"] for s in timeline[:-1]])
        self.assertEqual(audit["technical"]["frames"],1440)
        self.assertEqual(audit["qualification"],"passed")
        self.assertTrue(all(len(s["motion_checks"])==3 for s in timeline))
        self.assertEqual(audit["final_mix_dialogue"],[])
        self.assertEqual(timeline[-1]["endcard"]["first_frame"],96)
        self.assertEqual(len(list((env.cfg["data"]/key/"takes").glob("*/rejected.json"))),1)
        self.assertEqual(len(env.submitted),10)
        self.assertEqual(len(audit["score_selection"]["candidates"]),2)
        evidence_dir=os.getenv("FILM_TEST_PILOT_EVIDENCE_DIR")
        if evidence_dir:
            output=Path(evidence_dir)
            output.mkdir(parents=True,exist_ok=False)
            shutil.copytree(release,output/"delivery")
            shutil.copyfile(release/"film.mp4",output/"synthetic-queue-60s.mp4")
            write(output/"timeline.json",timeline)
            write(output/"evidence.json",{"kind":"synthetic_http_fixtures_real_worker_and_media",
                  "human_interventions":0,"video_submissions":len(env.submitted),"rejected_takes":1,
                  "inference":"All three services were HTTP fixtures on loopback, with generated color plates and tone.",
                  "factory_audit":audit})
