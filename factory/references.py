"""Keep project-selected identity images stable throughout a long production run."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from .common import read, write


def snapshot(folder, catalog, names):
    manifest = folder/"reference-index.json"
    index = read(manifest) if manifest.exists() else {}
    result = dict(catalog)
    for key, record in index.items():
        path = folder/"references"/record["file"]
        if not path.is_file():
            raise ValueError("项目参考图快照缺失：" + key)
        result[key] = path
    for name in sorted(set(names)-{"","previous"}-set(index)):
        if name not in catalog:
            raise ValueError("找不到分镜要求的参考图：" + name)
        target_dir = folder/"references"
        target_dir.mkdir(exist_ok=True)
        source = catalog[name]
        with tempfile.NamedTemporaryFile(dir=target_dir,delete=False) as out:
            tmp = Path(out.name)
            with source.open("rb") as src:
                shutil.copyfileobj(src,out)
        try:
            with tmp.open("rb") as image:
                digest = hashlib.file_digest(image,"sha256").hexdigest()
            dest = target_dir/(digest+source.suffix.lower())
            os.replace(tmp,dest)
            index[name]={"file":dest.name,"sha256":digest}
            write(manifest,index)
            result[name]=dest
        finally:
            tmp.unlink(missing_ok=True)
    return result


def used(shot):
    return [shot["start"],shot["end"],shot["overlay"],*shot.get("anchors",[])]
