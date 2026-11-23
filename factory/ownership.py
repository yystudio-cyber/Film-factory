"""Lifetime exclusion of the known legacy producer, with a durable namespace handoff."""
import hashlib
import os
import socket
from pathlib import Path

from .common import read, request, write
from .lock import exclusive


def counter_value(cfg):
    source = Path(cfg["studio_root"])/"clip_code_counter.json"
    value = read(source)
    pointer = cfg.get("counter_field","last_issued")
    if isinstance(value,dict):
        if pointer not in value:
            raise ValueError("请在 counter_field 指定旧计数器实际的已发最高号字段，可用 counter-info 查看字段")
        value = value[pointer]
    if type(value) is not int or value < 16:
        raise ValueError("旧计数器缺少有效的历史最高已发编号（至少 C016）")
    return value


def queue_state(cfg):
    data = request(cfg["comfy"].rstrip("/")+"/queue",timeout=6)
    return [row for key in ("queue_running","queue_pending") for row in data[key]]


class StationOwner:
    def __init__(self,cfg):
        self.cfg = cfg
        self.guard = None

    def acquire(self,store):
        if not self.cfg.get("station_contract"):
            return
        if self.guard is None:
            guard = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            try:
                if os.name=="nt":
                    guard.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                guard.bind(("127.0.0.1",8765))
                self.check_namespace(store)
            except OSError as err:
                guard.close()
                raise ValueError("无法接管旧 Studio 写入端口或打开编号档案。请保持 8765 写入端关闭，检查历史计数器") from err
            except Exception:
                guard.close()
                raise
            self.guard = guard
        else:
            self.check_namespace(store)

    def check_namespace(self,store):
        high = counter_value(self.cfg)
        root = str(Path(self.cfg["studio_root"]).resolve()).casefold()
        key = hashlib.sha256(root.encode()).hexdigest()[:24]
        registry = Path(os.environ.get("LOCALAPPDATA",Path.home()))/"FilmFactory"/"namespaces"
        registry.mkdir(parents=True,exist_ok=True)
        record = registry/(key+".json")
        current = {"data":str(store.root.resolve()),"legacy_high":high}
        with exclusive(record.with_suffix(".lock")):
            saved = read(record) if record.exists() else {}
            if saved and any(saved.get(k)!=v for k,v in current.items()):
                raise ValueError("此旧库的发号权或计数器已改变。请核对历史占号与唯一生产目录")
            with store.tx() as db:
                row = db.execute("SELECT seq FROM sqlite_sequence WHERE name='takes'").fetchone()
                if row is None or row[0] < high or not db.execute(
                        "SELECT 1 FROM takes WHERE job='studio-archive'").fetchone():
                    raise ValueError("请先按实际旧片清单和计数器 import-clips，再启用生产")
                if db.execute("SELECT 1 FROM takes WHERE code<=? AND job!='studio-archive'",(high,)).fetchone():
                    raise ValueError("工厂与历史库已经发出重叠编号，需核对后再生产")
                # A restored database must skip numbers already reserved outside that database.
                watermark = max(row[0],saved.get("factory_high",high))
                db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='takes'",(watermark,))
            write(record,dict(current,factory_high=watermark))
            store.namespace = record

    def close(self):
        if self.guard:
            self.guard.close()
            self.guard=None
