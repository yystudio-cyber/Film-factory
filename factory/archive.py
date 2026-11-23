"""Bring explicitly identified Studio clips into the persistent factory clip namespace."""
import json
import re
import shutil
import tempfile
from pathlib import Path

from . import media
from .common import read


def references(store, *texts):
    codes = set(re.findall(r"(?<![A-Za-z0-9])C(\d+)(?![A-Za-z0-9])", " ".join(texts), re.I))
    catalog = {}
    records=[]
    with store.tx() as db:
        for code in codes:
            row=db.execute("SELECT t.path,t.job,i.files FROM takes t LEFT JOIN take_integrity i ON i.code=t.code "
                           "WHERE t.code=? AND t.eligible=1 AND (t.selected=1 OR t.job='studio-archive')",(int(code),)).fetchone()
            if row is None:
                raise ValueError(f"找不到指定的 C{int(code):03}。旧片库须先执行 import-clips")
            records.append((code,*row))
    from .integrity import check
    for code,name,job,files in records:
        folder=Path(name)
        if files:
            check(folder,json.loads(files),("tail.png",))
        elif job=="studio-archive":
            originals=list(folder.glob("original.*"))
            if len(originals)!=1:
                raise ValueError("旧片引用缺少唯一原片副本")
            from PIL import Image, ImageChops
            with tempfile.TemporaryDirectory() as tmp:
                last=Path(tmp)/"tail.png"
                media.ff("-i",originals[0],"-map","0:v:0","-an","-update","1","-fps_mode","passthrough",last)
                with Image.open(last) as expected, Image.open(folder/"tail.png") as cached:
                    if expected.size!=cached.size or ImageChops.difference(expected.convert("RGB"),cached.convert("RGB")).getbbox():
                        raise ValueError("旧片 C 号的缓存尾帧与原片最后解码帧不符")
        else:
            raise ValueError("旧工厂候选缺少采用指纹，请先核实该历史版本")
        catalog[f"C{int(code):03}"]=folder/"tail.png"
    return catalog


def scan(cfg):
    from .ownership import counter_value
    folder = Path(cfg["studio_root"])/"approved"
    high = counter_value(cfg)
    result = {}
    for file in sorted(folder.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in {".mp4",".mkv",".webm",".mov"}:
            continue
        codes = set(re.findall(r"(?<![A-Za-z0-9])C(\d+)(?![A-Za-z0-9])",file.relative_to(folder).as_posix(),re.I))
        if len(codes)!=1:
            raise ValueError(f"请依据 Studio 片库索引显式映射历史视频：{file.name}")
        number = int(codes.pop())
        code = f"C{number:03}"
        if number<1 or number>high or code in result:
            raise ValueError(f"历史编号重复或超出计数器：{code}")
        result[code]=str(file.resolve())
    if not {"C001","C006"} <= result.keys():
        raise ValueError("无法确认 C001 与 C006，请按实际索引人工生成清单")
    return {"last_issued":high,"clips":result}


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
                    from .integrity import capture
                    seal=capture(dest,["tail.png",next(dest.glob("original.*")).name])
                    db.execute("INSERT INTO take_integrity VALUES(?,?)",(code,json.dumps(seal)))
                    dest.rename(target)
                    moved.append(target)
                if not db.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='takes'",(high,)).rowcount:
                    db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('takes',?)",(high,))
        except Exception:
            for p in moved:
                shutil.rmtree(p)
            raise
