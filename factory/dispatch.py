"""Durable write-ahead identity for a non-idempotent ComfyUI POST."""
import hashlib
import json
import sqlite3
from contextlib import closing

from .common import read, write


def journal(folder):
    if folder.parent.name in {"takes","drafts"}:
        return folder.parent.parent/"comfy-dispatch.sqlite3",folder.parent.name+"/"+folder.name
    return folder/"dispatch.sqlite3","local"


def digest(intent):
    return hashlib.sha256(json.dumps(intent,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def saved(folder):
    path,key=journal(folder)
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
            row=db.execute("SELECT body FROM dispatch WHERE slot=?",(key,)).fetchone()
        if row:
            return json.loads(row[0])
    receipt=folder/"remote.json"
    return read(receipt) if receipt.exists() else None


def check_working_state(job,pattern,intent):
    path=job/"comfy-dispatch.sqlite3"
    if not path.exists():
        return
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        owners=db.execute("SELECT DISTINCT owner FROM dispatch WHERE owner GLOB ? AND intent=?",
                          (pattern+"*",digest(intent))).fetchall()
    for (owner,) in owners:
        folder=job/"takes"/owner
        if not all((folder/name).is_file() for name in ("intent.json","shot.json")) or read(folder/"intent.json")!=intent:
            raise ValueError("已有远端投递但镜头工作文件缺失或改变；请恢复该镜记录，未重复提交")


def save(folder,record):
    path,key=journal(folder)
    owner=folder
    if folder.parent.name=="drafts" and (folder/"owner.json").is_file():
        owner=folder.parent.parent/"takes"/read(folder/"owner.json")["candidate"]
    fingerprint=digest(read(owner/"intent.json")) if (owner/"intent.json").is_file() else ""
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA synchronous=FULL")
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS dispatch(slot TEXT PRIMARY KEY,owner TEXT,intent TEXT,body TEXT NOT NULL)")
            row=db.execute("SELECT body FROM dispatch WHERE slot=?",(key,)).fetchone()
            if row:
                prior=json.loads(row[0])
                if (any(prior.get(k)!=record.get(k) for k in ("client_id","base","output"))
                    or prior.get("prompt_id") and prior["prompt_id"]!=record.get("prompt_id")):
                    raise ValueError("投递日志与已有回执冲突")
            db.execute("INSERT OR REPLACE INTO dispatch VALUES(?,?,?,?)",
                       (key,owner.name,fingerprint,json.dumps(record)))
    write(folder/"remote.json",record)
