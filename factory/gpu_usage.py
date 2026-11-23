"""Sample whole-card memory only. Monitoring failure must not take down the queue."""
import csv
import hashlib
import shutil
import subprocess
import threading
from contextlib import contextmanager

from .common import read, write


def observe(executable):
    query="--query-gpu=index,uuid,pci.bus_id,memory.used,memory.total,display_active"
    proc=subprocess.run([executable,query,"--format=csv,noheader,nounits"],capture_output=True,
                        text=True,timeout=4,check=True)
    result={}
    for row in csv.reader(proc.stdout.splitlines(),skipinitialspace=True):
        if len(row)!=6:
            continue
        index,uid,pci,used,total,display=(s.strip() for s in row)
        if index not in ("0","1") or not uid.startswith("GPU-") or pci in {"","N/A"}:
            continue
        # Raw device identifiers stay out of the on-disk sample and exported packet.
        identity=hashlib.sha256((uid+" "+pci).encode()).hexdigest()
        result[index]=(identity,int(used),int(total),display.lower()=="enabled")
    return result


def update(record,observed):
    for index,(identity,used,total,display) in observed.items():
        row=record["devices"].setdefault(index,{"identity":identity,"peak_mib":0,"samples":0,
                                                "initial_used_mib":used,"total_mib":total,"display_active":display})
        if row["identity"]!=identity:
            record["identity_changed"]=True
            continue
        row["samples"]+=1
        row["peak_mib"]=max(used,row["peak_mib"])


@contextmanager
def sampling(cfg,folder):
    smi=shutil.which("nvidia-smi") if cfg.get("station_contract") and cfg.get("sample_gpu_usage") else None
    if not smi:
        yield
        return
    path=folder/"gpu-usage.json"
    record=read(path) if path.exists() else {"interval_seconds":5,"devices":{},"identity_changed":False,"missed":0}
    stop=threading.Event()
    def monitor():
        while True:
            try:
                update(record,observe(smi))
            except (OSError,ValueError,subprocess.SubprocessError):
                record["missed"]+=1
            try:
                write(path,record)
            except OSError:
                return
            if stop.wait(5):
                return
    worker=threading.Thread(target=monitor,name="card-memory-sampling",daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()
