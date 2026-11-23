"""Bind accepted take bytes and published media to their recorded decisions."""
import hashlib
from pathlib import Path
from .common import read

CORE=("cut.mp4","tail.png","shot.json","review.json","technical.json","source-ready.json",
      *(f"frame-{i}.jpg" for i in range(5)))
OPTIONAL=("speech.json","motion-evidence.json","overlay.png","endcard.png")
DELIVERY=("film.mp4","timeline.json","acceptance.json")
EXTRAS=("score.wav","score-ticket.json","score-ready.json","score-selection.json")


def sha(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("验收文件不存在或已被替换："+path.name)
    with path.open("rb") as file:
        return hashlib.file_digest(file,"sha256").hexdigest()


def capture(folder,required,optional=()):
    return {**{name:sha(folder/name) for name in required},
            **{name:sha(folder/name) if (folder/name).exists() else None for name in optional}}


def check(folder,record,names=None):
    for name in record if names is None else names:
        if Path(name).name!=name or name not in record:
            raise ValueError("验收清单缺少文件依据")
        path=folder/name
        expected=record[name]
        if expected is None and not path.exists() and not path.is_symlink():
            continue
        if expected!=sha(path):
            raise ValueError("采用版本的文件指纹不符："+name+"；请恢复原采用文件再继续")


def seal_take(folder):
    ready=read(folder/"source-ready.json")
    name=ready["file"]
    if Path(name).name!=name or not ready.get("sha256") or ready["sha256"]!=sha(folder/name):
        raise ValueError("生成源片与审片时的回执不符")
    return capture(folder,CORE,OPTIONAL)


def verify_bundle(folder, anchor=None):
    info=read(folder/"integrity.json")
    if set(info)!={"schema","files"} or info["schema"]!=1:
        raise ValueError("未知成片清单版本")
    files=info["files"]
    if set(files)!=set(DELIVERY+EXTRAS) or any(not files[n] for n in DELIVERY):
        raise ValueError("成片清单缺少母版或伴随文件")
    if anchor is not None and files!=anchor:
        raise ValueError("成片清单与发布事务记录不符")
    check(folder,files)
    return {"verified":True,"sha256":files["film.mp4"],"files":sum(v is not None for v in files.values())}



def verify_install(folder):
    """Read immutable package contents only; never open local config, devices, services or a run DB."""
    import re
    from pathlib import PurePosixPath
    rows=(folder/"evidence"/"checksums.sha256").read_text(encoding="utf8").splitlines()
    names=set()
    for line in rows:
        match=re.fullmatch(r"([0-9a-f]{64})  (.+)",line)
        if not match:
            raise ValueError("安装包校验清单格式异常")
        digest,name=match.groups()
        key=PurePosixPath(name)
        if key.is_absolute() or ".." in key.parts or key.as_posix()!=name or ":" in name or chr(92) in name or name in names:
            raise ValueError("安装包校验路径异常")
        names.add(name)
        if name=="config.local.json":
            continue
        path=folder.joinpath(*key.parts)
        if not path.resolve().is_relative_to(folder.resolve()) or sha(path)!=digest:
            raise ValueError("发行文件缺失或指纹不符："+name)
    if not {"film.py","BUILD.json","config.example.json","start.cmd","check.cmd"}<=names:
        raise ValueError("发行清单缺少必要入口")
    return {"verified":True,"checked_files":len(names-{"config.local.json"}),
            "commit":read(folder/"BUILD.json")["commit"],"mode":"offline_package_only"}
