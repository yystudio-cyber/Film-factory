import json
import hashlib
import os
import shutil
import sqlite3
import time
import tempfile
import uuid
from contextlib import contextmanager

from pathlib import Path

from .common import read, write


class Store:
    def __init__(self, cfg):
        self.root = cfg["data"]
        self.db = self.root / "factory.sqlite3"
        with self.tx() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS batches(token TEXT PRIMARY KEY,digest TEXT,ids TEXT);
            CREATE TABLE IF NOT EXISTS stages(id INTEGER PRIMARY KEY,job TEXT,shot INTEGER,take TEXT,
                title TEXT,actor TEXT,started REAL,ended REAL,state TEXT,detail TEXT);
            CREATE INDEX IF NOT EXISTS stage_job ON stages(job,id);
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, spec TEXT, state TEXT, detail TEXT, created REAL, mode TEXT,
              gate INTEGER, batch_end INTEGER DEFAULT 0, variants INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS takes(
              code INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, shot INTEGER, path TEXT UNIQUE,
              selected INTEGER DEFAULT 0, eligible INTEGER DEFAULT 1);
            CREATE UNIQUE INDEX IF NOT EXISTS selected_take ON takes(job,shot) WHERE selected=1;
            CREATE TABLE IF NOT EXISTS notes(
              id INTEGER PRIMARY KEY, job TEXT, shot INTEGER, note TEXT, variants INTEGER);
            CREATE TABLE IF NOT EXISTS edits(
              id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, shot INTEGER, note TEXT, variants INTEGER);
            CREATE INDEX IF NOT EXISTS edits_by_shot ON edits(job,shot,id);
            CREATE TABLE IF NOT EXISTS repairs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, review TEXT);
            CREATE TABLE IF NOT EXISTS releases(
              id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, folder TEXT);
            CREATE INDEX IF NOT EXISTS releases_by_job ON releases(job,id);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, job TEXT, ts REAL, message TEXT);
            """)
            # Earlier records did not distinguish human instructions from automatic repairs.
            # An unattributed note must never authorize changing a locked source line.
            db.execute("BEGIN IMMEDIATE")
            for table in ("notes","edits"):
                if "origin" not in {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy'")

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
        return self.add_many([spec])[0]

    def add_many(self, specs, token=None):
        digest = hashlib.sha256(json.dumps(specs,sort_keys=True).encode()).hexdigest()
        folders = []
        try:
            with self.tx() as db:
                if token:
                    previous = db.execute("SELECT * FROM batches WHERE token=?", (token,)).fetchone()
                    if previous:
                        if previous["digest"] != digest:
                            raise ValueError("此提交编号已用于不同的批次")
                        return json.loads(previous["ids"])
                ids = []
                for spec in specs:
                    jid = uuid.uuid4().hex
                    folder = self.root / jid
                    folder.mkdir()
                    folders.append(folder)
                    write(folder/"brief.json", spec)
                    db.execute("INSERT INTO jobs(id,spec,state,detail,created,mode) VALUES(?,?,'queued','',?,?)",
                               (jid,json.dumps(spec,ensure_ascii=False),time.time(),spec["mode"]))
                    ids.append(jid)
                if token:
                    db.execute("INSERT INTO batches VALUES(?,?,?)",(token,digest,json.dumps(ids)))
            return ids
        except Exception:
            for folder in folders:
                shutil.rmtree(folder, ignore_errors=True)
            raise

    @contextmanager
    def stage(self, jid, shot, take, title, actor):
        with self.tx() as db:
            self.require_running(db, jid)
            key = db.execute("INSERT INTO stages(job,shot,take,title,actor,started,state) "
                             "VALUES(?,?,?,?,?,?,'running')",(jid,shot,take,title,actor,time.time())).lastrowid
        state, detail = "done", ""
        try:
            yield
        except InterruptedError as err:
            state, detail = "interrupted", str(err)
            raise
        except Exception as err:
            state, detail = "failed", str(err)[:1600]
            raise
        finally:
            with self.tx() as db:
                db.execute("UPDATE stages SET state=?,detail=?,ended=? WHERE id=?", (state,detail,time.time(),key))

    def stages(self, jid):
        with self.tx() as db:
            return [dict(r) for r in db.execute("SELECT * FROM stages WHERE job=? ORDER BY id DESC LIMIT 120",(jid,))]

    def production_times(self, jid):
        """Service wall time of completed calls; parallel lanes may overlap."""
        with self.tx() as db:
            rows = db.execute("""
                SELECT CASE WHEN title LIKE '低分辨率动作草稿 · %' THEN 'draft'
                            WHEN title LIKE '生成镜头 · %' THEN 'picture'
                            WHEN title='生成配乐' THEN 'score' END AS lane,
                       count(*) AS calls, sum(ended-started) AS elapsed
                FROM stages WHERE job=? AND state='done' AND ended IS NOT NULL
                GROUP BY lane""",(jid,)).fetchall()
        return {r["lane"]:{"calls":r["calls"],"seconds":round(r["elapsed"],2)} for r in rows if r["lane"]}

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
            db.execute("UPDATE stages SET state='interrupted',ended=? WHERE state='running'",(time.time(),))
            db.execute("""UPDATE jobs SET state='cancelled', detail='服务中断；已验收镜头保留，需手动继续'
                          WHERE state IN ('running','queued')""")

    def claim(self):
        with self.tx() as db:
            r = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if r:
                db.execute("UPDATE jobs SET state='running',detail='' WHERE id=?", (r[0],))
                return r[0]

    def status(self, jid, state, message, gate=None, *, allow_pending_edit=False):
        with self.tx() as db:
            if allow_pending_edit and db.execute("SELECT 1 FROM notes WHERE job=?", (jid,)).fetchone():
                state, message = "queued", "收到修改意见，等待重新制作"
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

    @staticmethod
    def require_running(db, jid):
        record = db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()
        if not record or record[0] != "running":
            raise InterruptedError("任务已停止")

    def register(self, jid, shot, directory):
        with self.tx() as db:
            self.require_running(db, jid)
            code = db.execute("INSERT INTO takes(job,shot,path) VALUES(?,?,?)", (jid, shot, str(directory))).lastrowid
            self.log(db, jid, f"S{shot:02} 候选 C{code:03} 通过自动质检")
            return code

    def revisions(self, jid):
        """Apply one queued edit as a single DB transaction, including downstream invalidation."""
        with self.tx() as db:
            self.require_running(db, jid)
            r = db.execute("SELECT * FROM notes WHERE job=? ORDER BY id LIMIT 1", (jid,)).fetchone()
            if not r:
                return None
            db.execute("INSERT INTO edits(job,shot,note,variants,origin) VALUES(?,?,?,?,?)",
                       (jid, r["shot"], r["note"], r["variants"],r["origin"]))
            db.execute("UPDATE takes SET selected=0,eligible=0 WHERE job=? AND shot>=?", (jid,r["shot"]))
            db.execute("UPDATE jobs SET gate=NULL WHERE id=?", (jid,))
            db.execute("DELETE FROM notes WHERE id=?", (r["id"],))
            return dict(r)

    def edit_epochs(self, jid):
        with self.tx() as db:
            return dict(db.execute("SELECT shot,max(id) FROM edits WHERE job=? GROUP BY shot",(jid,)))

    def instructions(self, jid, shot):
        """Preserve legacy edit files; new edits have durable independent journal IDs."""
        legacy_file = self.root/jid/f"revisions-S{shot:02}.json"
        history = read(legacy_file) if legacy_file.exists() else []
        with self.tx() as db:
            edits = db.execute("SELECT note,variants,origin FROM edits WHERE job=? AND shot=? ORDER BY id",
                               (jid,shot)).fetchall()
        return history + [dict(row) for row in edits]

    def handle_rejection(self, jid, reason, *, review=None, limit=0, legacy_repairs=0):
        """Resolve an exhausted shot or master without reviving a stopped job or losing an edit."""
        with self.tx() as db:
            self.require_running(db, jid)
            if db.execute("SELECT 1 FROM notes WHERE job=?", (jid,)).fetchone():
                return True
            if review is not None:
                count = db.execute("SELECT count(*) FROM repairs WHERE job=?", (jid,)).fetchone()[0]
                if count + legacy_repairs < limit:
                    db.execute("INSERT INTO repairs(job,review) VALUES(?,?)",
                               (jid,json.dumps(review,ensure_ascii=False)))
                    db.execute("INSERT INTO notes(job,shot,note,variants,origin) VALUES(?,?,?,1,'editorial')",
                               (jid,review["repair_shot"],review["notes"]))
                    self.log(db, jid, "总审要求返工：" + review["notes"])
                    return True
            return False

    def delivery_dir(self, jid):
        with self.tx() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row or row["state"] != "done":
                return None
            release = db.execute("SELECT folder FROM releases WHERE job=? ORDER BY id DESC LIMIT 1",
                                 (jid,)).fetchone()
        return self.root/jid/release["folder"] if release else self.root/jid

    @staticmethod
    def link_or_copy(source, target):
        try:
            os.link(source,target)
        except OSError:
            shutil.copyfile(source,target)

    def publish(self, jid, selected, output, timeline, report):
        """Promote a complete immutable bundle and select it in the same transaction as done."""
        folder = self.root/jid
        deliveries = folder/"deliveries"
        deliveries.mkdir(exist_ok=True)
        filenames = ("timeline.json","acceptance.json","film.mp4")
        version = f"deliveries/{uuid.uuid4().hex}"
        # Prepare outside the DB lock (a filesystem without hard links must copy video bytes).
        with tempfile.TemporaryDirectory(dir=deliveries,prefix=".pending-") as tmp:
            tmp = Path(tmp)
            bundle = tmp/"bundle"
            bundle.mkdir()
            write(bundle/"timeline.json",timeline)
            write(bundle/"acceptance.json",report)
            if report.get("audio_mix", {}).get("score"):
                # The working score cache may be regenerated in place on a future edit.
                shutil.copyfile(folder/"score.wav",bundle/"score.wav")
                for name in ("score-ticket.json","score-ready.json"):
                    if (folder/name).is_file():
                        shutil.copyfile(folder/name,bundle/name)
            output.replace(bundle/"film.mp4")
            for filename in filenames:
                self.link_or_copy(bundle/filename,tmp/filename)
            with self.tx() as db:
                self.require_running(db,jid)
                live = {row["shot"]:row["code"] for row in db.execute(
                    "SELECT shot,code FROM takes WHERE job=? AND selected=1",(jid,))}
                if live != selected or db.execute("SELECT 1 FROM notes WHERE job=?", (jid,)).fetchone():
                    return False
                for filename in filenames:
                    os.replace(tmp/filename,folder/filename)
                bundle.rename(folder/version)
                db.execute("INSERT INTO releases(job,folder) VALUES(?,?)",(jid,version))
                db.execute("UPDATE jobs SET state='done',detail='成片已通过设定的验收门槛',gate=NULL WHERE id=?",(jid,))
                self.log(db,jid,"已交付 film.mp4、timeline.json、acceptance.json")
        return True

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
                p = self.root / jid / "plan.json"
                if not p.exists():
                    raise ValueError("等待分镜完成")
                length = len(read(p)["shots"])
                if op == "revise":
                    first_open = db.execute("SELECT count(*) FROM takes WHERE job=? AND selected=1", (jid,)).fetchone()[0]+1
                    if not 1 <= shot <= min(length, first_open) or variants not in (1, 2) or not note.strip():
                        raise ValueError("指定已生成或当前镜号、修改意见和 1–2 个候选")
                    db.execute("INSERT INTO notes(job,shot,note,variants,origin) VALUES(?,?,?,?,'user')", (jid, shot, note, variants))
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
