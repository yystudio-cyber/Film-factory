"""Bring explicitly identified Studio clips into the persistent factory clip namespace."""
import re
import shutil
import tempfile
from pathlib import Path

from . import media
from .common import read


def references(store, *texts):
    codes = set(re.findall(r"(?<![A-Za-z0-9])C(\d+)(?![A-Za-z0-9])", " ".join(texts), re.I))
    catalog = {}
    with store.tx() as db:
        for code in codes:
            row = db.execute("SELECT path FROM takes WHERE code=?", (int(code),)).fetchone()
            if row is None:
                raise ValueError(f"找不到指定的 C{int(code):03}。旧片库须先执行 import-clips")
            catalog[f"C{int(code):03}"] = Path(row[0])/"tail.png"
    return catalog


def import_clips(store, manifest):
    file = Path(manifest).resolve()
    doc = read(file)
    high = doc["last_issued"]
    items = doc["clips"]
    if type(high) is not int or high < 0:
        raise ValueError("last_issued 应为旧片库曾分配的最高编号，包括已删除的编号")
    archive = store.root/"studio-clips"
    archive.mkdir(exist_ok=True)
    pending = []
    with tempfile.TemporaryDirectory(dir=archive) as tmp:
        for label, name in items.items():
            if not re.fullmatch(r"C\d+", label) or not 0 < int(label[1:]) <= high:
                raise ValueError("旧片编号必须介于 C001 和 last_issued 之间")
            code = int(label[1:])
            source = (file.parent/name).resolve(strict=True)
            dest = Path(tmp)/f"C{code:03}"
            dest.mkdir()
            # Keep the original bytes and capture the last decoded frame, including VFR material.
            shutil.copyfile(source, dest/("original"+source.suffix.lower()))
            media.ff("-i", source, "-map", "0:v:0", "-an", "-update", "1", "-fps_mode", "passthrough", dest/"tail.png")
            if not (dest/"tail.png").is_file():
                raise ValueError(f"{label} 没有可读尾帧")
            pending.append((code,dest))
        moved = []
        try:
            with store.tx() as db:
                if db.execute("SELECT 1 FROM takes WHERE code<=? AND job!='studio-archive'",(high,)).fetchone():
                    raise ValueError("工厂已发放与旧库重叠的编号；请在新 data 目录首次启动时导入")
                for code,dest in pending:
                    target = archive/dest.name
                    if target.exists():
                        raise ValueError(f"{dest.name} 已导入")
                    db.execute("INSERT INTO takes(code,job,shot,path) VALUES(?,'studio-archive',0,?)",
                               (code,str(target)))
                    dest.rename(target)
                    moved.append(target)
                if not db.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='takes'",(high,)).rowcount:
                    db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('takes',?)",(high,))
        except Exception:
            for p in moved:
                shutil.rmtree(p)
            raise
