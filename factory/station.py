"""Read-only workstation telemetry. Never changes power limits or the shared queue."""
import csv
import shutil
import subprocess
import threading
import time

import json
from urllib.request import urlopen


class Station:
    def __init__(self,cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stamp = 0
        self.sample = {}

    def read(self):
        with self.lock:
            if self.sample and time.monotonic()-self.stamp < 10:
                return self.sample
            value = {"time":time.time(),"disk_free_gib":round(shutil.disk_usage(self.cfg["data"]).free/2**30,1),
                     "gpus":[],"queue":None}
            value["disk_reserve_gib"] = float(self.cfg.get("min_free_gib",2))
            value["disk_hold"] = shutil.disk_usage(self.cfg["data"]).free < value["disk_reserve_gib"]*2**30
            executable = shutil.which("nvidia-smi")
            if executable:
                try:
                    out = subprocess.run([executable,"--query-gpu=index,name,memory.used,memory.total,utilization.gpu,power.draw",
                                          "--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=3,check=True)
                    for row in csv.reader(out.stdout.splitlines(),skipinitialspace=True):
                        if len(row)==6:
                            value["gpus"].append(dict(zip(("index","name","used","total","percent","watts"),row)))
                except (OSError, subprocess.SubprocessError):
                    pass
            try:
                # Keep slow monitoring reads out of the worker and job-control requests.
                with urlopen(self.cfg["comfy"].rstrip("/")+"/queue",timeout=2) as response:
                    queue = json.load(response)
                value["queue"] = {"running":len(queue["queue_running"]),"pending":len(queue["queue_pending"])}
            except (OSError,ValueError,KeyError):
                pass
            if self.cfg.get("station_contract"):
                import socket
                try:
                    with socket.create_connection(("127.0.0.1",8765),timeout=.5):
                        value["ownership"]="旧 Studio 仍在监听，工厂生产暂停"
                except OSError:
                    value["ownership"]="独占模式；生产期间保留旧写入端口"
            self.sample,self.stamp = value,time.monotonic()
            return value
