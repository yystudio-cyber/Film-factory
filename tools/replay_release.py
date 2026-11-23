"""Reproduce release hazards with disposable files and loopback inference doubles only."""
import argparse
import hashlib
import json
import random
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch


def run_case(repo,out,scenario,seed):
    sys.path[:0]=[str(repo),str(repo/"tests")]
    from test_factory import Environment
    from factory import media,graphs,engine
    from factory.common import read, request
    from PIL import Image
    env=Environment()
    env.setUp()
    try:
        env.reject_first=False
        rnd=random.Random(seed)
        began=time.monotonic()
        job=env.new()
        root=env.cfg["data"]/job
        log={"kind":"synthetic_loopback_real_media","scenario":scenario,"seed":seed,"runs":[]}
        original=graphs.request
        advance=env.store.advance
        damaged=None
        good=None
        restore=False
        nonce=None
        def adapter(url,body=None,**kw):
            nonlocal nonce
            if scenario in {"lost","lost_json","ambiguous"} and url.endswith("/prompt") and nonce is None:
                nonce=body["client_id"]
                original(url,body,**kw)
                if scenario=="lost_json":
                    receipt=next((root/"takes").glob("*/remote.json"))
                    receipt.unlink()
                    raise InterruptedError("fixture process interruption; JSON receipt unavailable")
                raise ConnectionError("fixture: reply lost after acceptance")
            if nonce and "/history?" in url:
                return {"1":{"prompt":[0,"1",{},{"client_id":nonce}]}}
            if nonce and url.endswith("/queue"):
                extra=[[0,"different-ticket",{},{"client_id":nonce}]] if scenario=="ambiguous" and not restore else []
                return {"queue_running":[],"queue_pending":extra}
            result=original(url,body,**kw)
            if "/history/" in url:  # Normalize the baseline's older HTTP test double to the documented protocol.
                for row in result.values():
                    if row.get("status",{}).get("status_str")=="success":
                        row["status"].setdefault("completed",True)
            if scenario in {"unknown","incomplete"} and "/history/" in url and not restore:
                for row in result.values():
                    if scenario=="unknown":
                        row["status"]={"status_str":"unexpected"}
                    else:
                        row["status"]={"status_str":"success","completed":False}
            return result
        def change(jid,i,code):
            nonlocal good,damaged
            value=advance(jid,i,code)
            if scenario in {"changed_cut","changed_tail"} and i==1 and damaged is None:
                take=Path(next(r["path"] for r in env.store.takes(jid) if r["code"]==code))
                if scenario=="changed_tail":
                    damaged=take/"tail.png"
                    good=damaged.read_bytes()
                    with Image.open(damaged) as img:size=img.size
                    Image.new("RGB",size,"magenta").save(damaged)
                else:
                    damaged=take/"cut.mp4"
                    good=damaged.read_bytes()
                    alt=env.cfg["data"].parent/"different.mp4"
                    media.ff("-f","lavfi","-i","color=c=magenta:s=320x180:r=24:d=2",
                             "-f","lavfi","-i","sine=f=880:r=48000:d=2","-ac","2",
                             "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",alt)
                    shutil.copyfile(alt,damaged)
                log["different_accepted_bytes"]=good!=damaged.read_bytes()
            return value
        def run():
            started=time.monotonic()
            try:env.execute(job)
            except (ValueError,RuntimeError,ConnectionError,InterruptedError,OSError) as err:
                env.store.status(job,"failed",str(err))
            state=env.store.job(job)
            delivery=env.store.delivery_dir(job)
            log["runs"].append({"run_wall_seconds":round(time.monotonic()-started,3),"state":state["state"],"detail":state["detail"],"submissions":len(env.submitted),
                                "selected":len([t for t in env.store.takes(job) if t["selected"]]),"delivered":bool(delivery)})
            return delivery
        with patch.object(graphs,"request",side_effect=adapter),patch.object(env.store,"advance",side_effect=change),patch.object(engine.secrets,"randbelow",rnd.randrange):
            release=run()
            if not release and scenario in {"lost","lost_json","ambiguous","unknown","incomplete","changed_cut","changed_tail"}:
                if scenario=="ambiguous":
                    env.store.command(job,"resume");release=run()
                if not release:
                    restore=True
                    if damaged and good:
                        damaged.write_bytes(good)
                    env.store.command(job,"resume")
                    release=run()
            if release:
                movie=release/"film.mp4"
                info=media.technical(movie,env.cfg,144,master=True)
                log["movie"]={"facts":info,"sha256":hashlib.sha256(movie.read_bytes()).hexdigest()}
                if scenario=="changed_cut":
                    pixel=media.ff("-i",movie,"-frames:v","1","-vf","crop=2:2:160:90,format=rgb24","-f","rawvideo","-")
                    log["first_pixel_rgb"]=list(pixel[:3])
                log["submission_to_delivery_seconds"]=round(time.monotonic()-began,3)
                if scenario=="published_damage":
                    trusted=movie.read_bytes()
                    movie.write_bytes(b"simulated storage error")
                    try:
                        request(env.url+"/api/jobs/"+job+"/film.mp4",dest=root/"observed.bin")
                        log["corrupt_download_offered"]=True
                    except RuntimeError:
                        log["corrupt_download_offered"]=False
                    movie.write_bytes(trusted)
                    request(env.url+"/api/jobs/"+job+"/film.mp4",dest=root/"restored.bin")
                    log["restored_download_matches"]=hashlib.sha256((root/"restored.bin").read_bytes()).hexdigest()==log["movie"]["sha256"]
                if scenario=="alias":
                    original_digest=log["movie"]["sha256"]
                    (root/"film.mp4").write_bytes(b"fixture corrupt convenience copy")
                    log["canonical_unchanged"]=hashlib.sha256(movie.read_bytes()).hexdigest()==original_digest
        return log
    finally:env.doCleanups()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository",type=Path)
    parser.add_argument("new_output",type=Path)
    parser.add_argument("scenarios",nargs="+",choices=("clean","unknown","incomplete","lost","ambiguous","changed_cut","changed_tail","alias","lost_json","published_damage"))
    parser.add_argument("--seed",type=int,default=17017)
    args=parser.parse_args()
    args.new_output.mkdir(parents=True,exist_ok=False)
    for case in args.scenarios:
        data=run_case(args.repository.resolve(),args.new_output,case,args.seed)
        (args.new_output/(case+".json")).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf8")
        print(case,[(r["state"],r["submissions"]) for r in data["runs"]],flush=True)
