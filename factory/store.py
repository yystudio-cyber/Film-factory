import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .common import write


class Store:
    def __init__(self, cfg):
        self.root = cfg["data"]
        self.db = self.root / "factory.sqlite3"
        with self.tx() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, spec TEXT, state TEXT, detail TEXT, created REAL, mode TEXT,
              gate INTEGER, batch_end INTEGER DEFAULT 0, variants INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS takes(
              code INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, shot INTEGER, path TEXT UNIQUE,
              selected INTEGER DEFAULT 0, eligible INTEGER DEFAULT 1);
            CREATE UNIQUE INDEX IF NOT EXISTS selected_take ON takes(job,shot) WHERE selected=1;
            CREATE TABLE IF NOT EXISTS notes(
              id INTEGER PRIMARY KEY, job TEXT, shot INTEGER, note TEXT, variants INTEGER);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, job TEXT, ts REAL, message TEXT);
            """)

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.db, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add(self, spec):
        jid = uuid.uuid4().hex
        folder = self.root / jid
        folder.mkdir()
        write(folder / "brief.json", spec)
        with self.tx() as db:
            db.execute("INSERT INTO jobs(id,spec,state,detail,created,mode) VALUES(?,?,'queued','',?,?)",
                       (jid, json.dumps(spec, ensure_ascii=False), time.time(), spec["mode"]))
        return jid

    def jobs(self):
        with self.tx() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,spec,state,detail,created,mode,gate FROM jobs ORDER BY created DESC LIMIT 100")]

    def job(self, jid):
        with self.tx() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if row is None:
                raise ValueError("任务不存在")
            return dict(row)

    def recover(self):
        with self.tx() as db:
            db.execute("""UPDATE jobs SET state='cancelled', detail='服务中断；已验收镜头保留，需手动继续'
                          WHERE state IN ('running','queued')""")

    def claim(self):
        with self.tx() as db:
            r = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if r:
                db.execute("UPDATE jobs SET state='running',detail='' WHERE id=?", (r[0],))
                return r[0]

    def status(self, jid, state, message, gate=None):
        with self.tx() as db:
            if db.execute("UPDATE jobs SET state=?,detail=?,gate=? WHERE id=? AND state='running'",
                          (state, message, gate, jid)).rowcount:
                self.log(db, jid, message)

    @staticmethod
    def log(db, jid, message):
        db.execute("INSERT INTO events(job,ts,message) VALUES(?,?,?)", (jid, time.time(), message))

    def event(self, jid, msg):
        with self.tx() as db:
            self.log(db, jid, msg)
            db.execute("UPDATE jobs SET detail=? WHERE id=? AND state='running'", (msg, jid))

    def takes(self, jid):
        with self.tx() as db:
            return [dict(row) for row in db.execute("SELECT * FROM takes WHERE job=? ORDER BY code", (jid,))]

    def history(self, jid):
        with self.tx() as db:
            return [dict(x) for x in db.execute("SELECT ts,message FROM events WHERE job=? ORDER BY id DESC LIMIT 50", (jid,))]

    def register(self, jid, shot, directory):
        with self.tx() as db:
            if db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0] != "running":
                raise InterruptedError()
            code = db.execute("INSERT INTO takes(job,shot,path) VALUES(?,?,?)", (jid, shot, str(directory))).lastrowid
            self.log(db, jid, f"S{shot:02} 候选 C{code:03} 通过自动质检")
            return code

    def revisions(self, jid):
        with self.tx() as db:
            r = db.execute("SELECT * FROM notes WHERE job=? ORDER BY id LIMIT 1", (jid,)).fetchone()
            if not r:
                return None
            db.execute("UPDATE takes SET selected=0,eligible=0 WHERE job=? AND shot>=?", (jid, r["shot"]))
            db.execute("UPDATE jobs SET variants=?,gate=NULL WHERE id=?", (r["variants"], jid))
            # Note remains durable in the shot's instruction history even after dequeue.
            from .common import read
            p = self.root / jid / f'revisions-S{r["shot"]:02}.json'
            notes = read(p) if p.exists() else []
            if not any(n["id"] == r["id"] for n in notes):
                notes.append(dict(r))
                write(p, notes)
            db.execute("DELETE FROM notes WHERE id=?", (r["id"],))
            return dict(r)

    def command(self, jid, op, *, shot=0, code=0, note="", variants=1, end=0):
        with self.tx() as db:
            j = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if not j:
                raise ValueError("任务不存在")
            state = j["state"]
            active = state in {"running", "queued"}
            if op == "stop" and state in {"queued","running","review"}:
                db.execute("UPDATE jobs SET state='cancelled',detail='已停止调度' WHERE id=?", (jid,))
            elif op == "resume" and state in {"cancelled", "failed"}:
                db.execute("UPDATE jobs SET state='queued',detail='等待继续' WHERE id=?", (jid,))
            elif op == "approve" and state == "review":
                self.select(db, jid, j["gate"], code)
                db.execute("UPDATE jobs SET state='queued',gate=NULL,detail='已采用' WHERE id=?", (jid,))
            elif op in {"revise", "batch"}:
                from .common import read
                p = self.root / jid / "plan.json"
                if not p.exists():
                    raise ValueError("等待分镜完成")
                length = len(read(p)["shots"])
                if op == "revise":
                    first_open = db.execute("SELECT count(*) FROM takes WHERE job=? AND selected=1", (jid,)).fetchone()[0]+1
                    if not 1 <= shot <= min(length, first_open) or variants not in (1, 2) or not note.strip():
                        raise ValueError("指定已生成或当前镜号、修改意见和 1–2 个候选")
                    db.execute("INSERT INTO notes(job,shot,note,variants) VALUES(?,?,?,?)", (jid, shot, note, variants))
                else:
                    selected = db.execute("SELECT count(*) FROM takes WHERE job=? AND selected=1", (jid,)).fetchone()[0]
                    if not selected < end <= length:
                        raise ValueError("结束镜号必须位于待生成范围内")
                    db.execute("UPDATE jobs SET batch_end=? WHERE id=?", (end, jid))
                if not active:
                    db.execute("UPDATE jobs SET state='queued',detail='等待执行' WHERE id=?", (jid,))
            else:
                raise ValueError(f"{state} 状态不接受 {op}")
            self.log(db, jid, f"{op} {note}".strip())

    @staticmethod
    def select(db, jid, shot, code):
        if not db.execute("UPDATE takes SET selected=1 WHERE code=? AND job=? AND shot=? AND eligible=1",
                          (code, jid, shot)).rowcount:
            raise ValueError("无效的候选编号")

    def advance(self, jid, idx, best):
        with self.tx() as db:
            j = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if j["state"] != "running":
                raise InterruptedError()
            if db.execute("SELECT 1 FROM notes WHERE job=?", (jid,)).fetchone():
                return True
            auto = j["mode"] == "auto" or j["batch_end"] >= idx
            db.execute("UPDATE jobs SET variants=1 WHERE id=?", (jid,))
            if auto:
                self.select(db, jid, idx, best)
                self.log(db, jid, f"采用 S{idx:02} / C{best:03}")
            else:
                db.execute("UPDATE jobs SET state='review',gate=?,detail=? WHERE id=?",
                           (idx, f"S{idx:02} 等待逐镜确认", jid))
            return auto
