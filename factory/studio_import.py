"""Bring one local Studio project into Film Factory with project-scoped immutable inputs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PureWindowsPath

from . import archive
from .common import read, write
from .store import Store


def add_arguments(subparsers):
    p = subparsers.add_parser("import-studio", help="读取旧 Studio 项目，冻结素材并可选入队")
    p.add_argument("project", type=Path, help="选定项目的 JSON 文件")
    p.add_argument("--mapping", type=Path, help="字段与外部文件映射 JSON")
    p.add_argument("--root", type=Path, action="append", default=[],
                   help="允许项目读取的额外素材根目录，可重复")
    p.add_argument("--seconds", type=int, help="覆盖成片秒数")
    p.add_argument("--requirements", help="覆盖制作要求")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--inspect", action="store_true", help="列出 JSON Pointer，无需制作")
    group.add_argument("--enqueue", action="store_true", help="冻结后提交至现有工厂队列")
    return p


def _hash(file):
    with file.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def _pointer(data, pointer):
    if pointer == "":
        return data
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"需要 JSON Pointer：{pointer!r}")
    try:
        for key in pointer[1:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            if isinstance(data, list):
                if not re.fullmatch(r"0|[1-9]\d*", key):
                    raise ValueError(f"非法列表索引 {key!r}")
                data = data[int(key)]
            else:
                data = data[key]
    except (KeyError, IndexError, TypeError) as err:
        raise ValueError(f"找不到映射字段 {pointer}") from err
    return data


def _inspect(value, pointer="", depth=0):
    """Show structure and one sample row per array, without printing private contents."""
    row = {"pointer": pointer, "type": type(value).__name__}
    if isinstance(value, (list, dict, str)):
        row["length"] = len(value)
    yield row
    if depth >= 6:
        return
    if isinstance(value, dict):
        children = value.items()
    elif isinstance(value, list):
        children = enumerate(value[:1])
    else:
        children = ()
    for key, item in children:
        part = str(key).replace("~", "~0").replace("/", "~1")
        yield from _inspect(item, f"{pointer}/{part}", depth+1)


def _field(row, pointers, field, defaults, fallback=None):
    if field in pointers:
        return _pointer(row, pointers[field])
    return next((row[k] for k in defaults if k in row), fallback)


def _allowed_file(name, base, roots):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("需要有效的本地源文件路径")
    entry = Path(name)
    candidates = [entry] if entry.is_absolute() else [r/entry for r in dict.fromkeys([base, *roots])]
    paths = {p.resolve() for p in candidates if p.is_file() and any(p.resolve().is_relative_to(r) for r in roots)}
    if len(paths) != 1:
        raise ValueError(f"源文件不存在、超出授权根目录，或多个根同时匹配：{name}")
    return paths.pop()


def _mapping_file(name, origin):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("映射文件路径须为非空字符串")
    return (origin/name).resolve(strict=True)


def _safe_name(name):
    if (not isinstance(name, str) or not name or name in {".",".."} or
            re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((" ",".")) or
            PureWindowsPath(name).is_reserved() or Path(name).suffix.lower() not in {".png",".jpg",".jpeg"}):
        raise ValueError(f"不支持的目标素材文件名：{name!r}")
    return name


def _assets(entries, selectors, captions, base, roots, target):
    from PIL import Image
    if not isinstance(entries, list):
        raise ValueError("assets 须映射到选定项目的图片数组")
    seen = set()
    result = []
    for index, item in enumerate(entries):
        if isinstance(item, str):
            item = {"path":item}
        if not isinstance(item, dict):
            raise ValueError(f"素材 {index+1} 缺少有效的路径字段")
        path = _field(item, selectors, "path", ("path","file_path","file"))
        source = _allowed_file(path, base, roots)
        # Display names often contain descriptions, so only use an explicitly mapped alias.
        name = _safe_name(_field(item,selectors,"name",(),source.name))
        stem = Path(name).stem.casefold()
        if stem in seen:
            raise ValueError(f"素材或同名描述文件冲突：{name}。请为记录映射唯一文件名")
        seen.add(stem)
        alias = _field(item,selectors,"id",("id","asset_id"),"")
        caption = _field(item,selectors,"caption",("caption","description"),None)
        if caption is None:
            caption = next((captions[k] for k in (str(alias),path,name) if k and k in captions),None)
        if caption is None and source.with_suffix(".txt").is_file():
            sidecar = _allowed_file(str(source.with_suffix(".txt")),base,roots)
            caption = sidecar.read_text(encoding="utf-8-sig")
        caption = "" if caption is None else caption
        if not isinstance(caption,str):
            raise ValueError(f"素材 {name} 的说明须为文本")
        dest = target/name
        shutil.copyfile(source,dest)
        with Image.open(dest) as image:
            expected = "PNG" if dest.suffix.lower()==".png" else "JPEG"
            if image.format != expected:
                raise ValueError(f"{name} 的后缀与实际图像格式不匹配")
            image.verify()
        if caption:
            dest.with_suffix(".txt").write_text(caption,encoding="utf-8")
        result.append({"name":name,"source_id":str(alias),"caption":caption,"sha256":_hash(dest)})
    return result


def _links(raw, names):
    if not isinstance(raw,list):
        raise ValueError("links 须为镜号与首尾帧的映射数组")
    mapped = {}
    for row in raw:
        if not isinstance(row,dict) or not {"first","last"}.intersection(row):
            raise ValueError("每条 links 需要 shot 和 first 或 last")
        label = re.fullmatch(r"S?(\d+)",str(row.get("shot","")),re.I)
        if not label or not 1 <= int(label[1]) <= 300:
            raise ValueError("links 镜号应为 1–300")
        idx = int(label[1])
        if idx in mapped:
            raise ValueError(f"重复的镜头引用 S{idx:02}")
        result = {}
        for key,field in (("first","start"),("last","end")):
            if key not in row:
                continue
            value = row[key]
            if not isinstance(value,str):
                raise ValueError(f"S{idx:02} 的 {key} 须为素材名、旧片号或空字符串")
            if re.fullmatch(r"C\d+", value, re.I):
                value = f"C{int(value[1:]):03}"
            elif value == "previous":
                if idx==1 or field=="end":
                    raise ValueError(f"S{idx:02} 的 {key} 无法使用 previous")
            elif value and value not in names:
                raise ValueError(f"S{idx:02} 引用未知的精确素材名 {value!r}")
            result[field]=value
        mapped[idx]=result
    return {str(k):mapped[k] for k in sorted(mapped)}


def apply_links(spec,plan):
    for number,bindings in spec.get("studio_links",{}).items():
        idx = int(number)
        if idx < 1 or idx > len(plan["shots"]):
            raise ValueError(f"导入引用需要 S{idx:02}，当前导演只计划了 {len(plan['shots'])} 镜")
        shot = plan["shots"][idx-1]
        shot.update(bindings)
        if shot.get("end") and not shot.get("start"):
            raise ValueError(f"S{idx:02} 尾帧需要首帧；请补充首帧映射或调整分镜")
    return plan


def project_config(cfg,spec):
    bundle = spec.get("studio_bundle")
    if bundle is None:
        return cfg
    if not isinstance(bundle,str) or not re.fullmatch(r"[a-f0-9]{64}",bundle):
        raise ValueError("无效的 Studio 项目标识")
    path = cfg["data"]/"studio-imports"/bundle/"assets"
    if not path.is_dir():
        raise ValueError("Studio 素材快照缺失，请恢复项目目录")
    return {**cfg,"assets":path}


def _commit_bundle(staged, target, manifest):
    """Concurrent identical imports converge. Never replace a published project."""
    try:
        staged.rename(target)
        return
    except OSError:
        if not target.is_dir():
            raise
    if read(target/"manifest.json") != manifest or read(target/"brief.json") != read(staged/"brief.json"):
        raise ValueError("已有 Studio 导入快照与本次清单冲突")
    for entry in manifest["assets"]:
        f = target/"assets"/entry["name"]
        if not f.is_file() or _hash(f)!=entry["sha256"]:
            raise ValueError(f"已有 Studio 快照损坏：{f.name}")
        side = f.with_suffix(".txt")
        if (side.read_text(encoding="utf-8") if side.exists() else "") != entry["caption"]:
            raise ValueError(f"已有 Studio 素材说明发生改变：{side.name}")
    if "plan_sha256" in manifest and _hash(target/"source-plan.json") != manifest["plan_sha256"]:
        raise ValueError("已有 Studio 计划快照发生改变")


def run(cfg, args):
    from .web import specification

    source = Path(args.project).resolve(strict=True)
    document = read(source)
    if args.inspect:
        return {"project":str(source),"structure":list(_inspect(document))}
    map_path = Path(args.mapping).resolve(strict=True) if args.mapping else None
    mapping = read(map_path) if map_path else {}
    if not isinstance(mapping,dict):
        raise ValueError("映射须为 JSON 对象")
    valid = {"project_pointer","fields","asset_fields","script_file","captions_file","plan_file",
             "title","seconds","requirements","music","mode","links"}
    unknown = mapping.keys()-valid
    if unknown:
        raise ValueError("不支持的映射键：" + ", ".join(sorted(unknown)))
    origin = map_path.parent if map_path else source.parent
    project = _pointer(document,mapping.get("project_pointer",""))
    if not isinstance(project,dict):
        raise ValueError("project_pointer 需要选中一个项目对象")
    fields = mapping.get("fields",{})
    asset_fields = mapping.get("asset_fields",{})
    if not isinstance(fields,dict) or fields.keys()-{"id","title","script","assets","requirements","seconds"}:
        raise ValueError("不支持的 fields 映射")
    if not isinstance(asset_fields,dict) or asset_fields.keys()-{"id","path","name","caption"}:
        raise ValueError("不支持的 asset_fields 映射")
    roots = [source.parent,*[Path(p).resolve(strict=True) for p in args.root]]
    if any(not p.is_dir() for p in roots):
        raise ValueError("--root 需要目录")
    script = (_mapping_file(mapping["script_file"],origin).read_text(encoding="utf-8-sig")
              if "script_file" in mapping else _field(project,fields,"script",("script","script_text")))
    title = mapping.get("title",_field(project,fields,"title",("title","name"),source.stem))
    seconds = args.seconds if args.seconds is not None else mapping.get("seconds",_field(project,fields,"seconds",("seconds",)))
    requirements = args.requirements if args.requirements is not None else mapping.get("requirements",
                   _field(project,fields,"requirements",("requirements",),"按原剧本制作，保留原文台词和定版文字。"))
    spec = specification({"script":script,"title":title,"seconds":seconds,"requirements":requirements,
                          "mode":mapping.get("mode","review"),"music":mapping.get("music",True)})
    project_id = _field(project,fields,"id",("id","project_id"),source.stem)
    captions = read(_mapping_file(mapping["captions_file"],origin)) if "captions_file" in mapping else {}
    if not isinstance(captions,dict):
        raise ValueError("captions_file 需要以素材 ID、源路径或文件名为键的文本映射")
    library = cfg["data"]/"studio-imports"
    library.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-",dir=library) as tmp:
        staged = Path(tmp)/"project"
        images = staged/"assets"
        images.mkdir(parents=True)
        records = _assets(_field(project,fields,"assets",("assets",),[]),asset_fields,captions,
                          source.parent,roots,images)
        links = _links(mapping.get("links",[]),{r["name"] for r in records})
        if links:
            spec["studio_links"]=links
            spec["requirements"] += "\n指定镜头引用（镜号按原叙事顺序）：\n" + "\n".join(
                f"S{int(idx):02} " + "，".join(("首帧 " if k=="start" else "尾帧 ")+(v or "无") for k,v in bindings.items())
                for idx,bindings in links.items())
        # Validate again after adding authored bindings to the creative requirements.
        specification(spec)
        manifest = {"version":1,"source_name":source.name,"project_id":project_id,"assets":records,"brief":spec.copy()}
        if "plan_file" in mapping:
            plan_file = _mapping_file(mapping["plan_file"],origin)
            read(plan_file)
            shutil.copyfile(plan_file,staged/"source-plan.json")
            manifest["plan_sha256"]=_hash(staged/"source-plan.json")
        digest = hashlib.sha256(json.dumps(manifest,ensure_ascii=False,sort_keys=True,
                                separators=(",",":")).encode()).hexdigest()
        spec["studio_bundle"]=digest
        write(staged/"manifest.json",manifest)
        write(staged/"brief.json",spec)
        target = library/digest
        _commit_bundle(staged,target,manifest)
    result = {"bundle":digest,"directory":str(target),"title":title,"project_id":project_id,
              "assets":len(records),"captions":sum(bool(a["caption"]) for a in records),
              "links":len(links),"seconds":seconds}
    if args.enqueue:
        store = Store(cfg)
        archive.references(store,spec["script"],spec["requirements"])
        result["job_id"] = store.add_many([spec],"studio-"+digest)[0]
    return result
