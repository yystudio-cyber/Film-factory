from __future__ import annotations

import base64
import json
import math
import os
import shutil
import subprocess
import time
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


class ServiceHTTPError(RuntimeError):
    def __init__(self, status, detail, retry_after):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.retry_after = retry_after


class Rejected(ValueError):
    """Candidate failed an acceptance test and can be regenerated."""


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf8", delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(f.name, path)


def config(path):
    path = Path(path).resolve()
    cfg = read(path)
    if cfg.get("text_provider","qwen") not in {"qwen","deepseek"}:
        raise ValueError("text_provider 仅支持 qwen 或 deepseek")
    if not math.isfinite(float(cfg.get("min_free_gib",2))) or float(cfg.get("min_free_gib",2))<0:
        raise ValueError("min_free_gib 需要非负有限值")
    for k in ("data", "assets", "workflows"):
        cfg[k] = (path.parent / cfg[k]).resolve()
        cfg[k].mkdir(parents=True, exist_ok=True)
    for tool in ("ffmpeg", "ffprobe"):
        if cfg.get(tool):
            binary = Path(cfg[tool])
            if not binary.is_absolute():
                raise ValueError(f"{tool} 配置的绝对路径不可用：{binary}")
            os.environ["FILM_" + tool.upper()] = str(binary)
        else:
            os.environ.pop("FILM_" + tool.upper(), None)
    if cfg.get("motion_review","adaptive") not in {"adaptive","basic"}:
        raise ValueError("motion_review 须为 adaptive 或 basic")
    rules = cfg.get("aesthetic_rules",[])
    if not isinstance(rules,list) or len(rules)>12 or any(not isinstance(r,str) or not 0<len(r.strip())<=400 for r in rules):
        raise ValueError("aesthetic_rules 接受最多 12 条审美标准，每条 1–400 字")
    if cfg.get("asr", "required") not in {"off", "required"}:
        raise ValueError("asr 须为 off 或 required")
    if cfg.get("dialogue_fallback","report") not in {"report","subtitles","dub"}:
        raise ValueError("dialogue_fallback 须为 report、subtitles 或 dub")
    if cfg.get("score_candidates",1) not in (1,2):
        raise ValueError("配乐候选数只支持 1–2")
    for k in ("width", "height"):
        if type(cfg[k]) is not int or cfg[k] < 32 or cfg[k] % 2:
            raise ValueError(f"{k} must be a positive even dimension")
    if not 1 <= cfg["attempts"] <= 8 or not 1 <= cfg["threshold"] <= 5:
        raise ValueError("attempts 1–8, threshold 1–5")
    return cfg


def executable(name):
    return os.getenv("FILM_" + name.upper()) or name


def run(args, cwd=None, timeout=3600):
    p = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, timeout=timeout)
    if p.returncode:
        raise ValueError(p.stderr.decode("utf8", "replace")[-2200:] or "Media command failed")
    return p.stdout


def request(url, body=None, *, headers=None, timeout=60, dest=None):
    # Retry only read operations. A lost response to a POST may hide an accepted task.
    for attempt in range(3):
        try:
            return _request(url, body, headers=headers, timeout=timeout, dest=dest)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            if body is not None or attempt == 2:
                raise
            time.sleep(1 + attempt)


def _request(url, body=None, *, headers=None, timeout=60, dest=None):
    hdr = dict(headers or {})
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if dest is None:
                return json.load(response)
            target = Path(dest)
            partial = target.with_name(target.name+".part")
            try:
                with partial.open("wb") as out:
                    shutil.copyfileobj(response,out)
                length = response.headers.get("Content-Length")
                if length and partial.stat().st_size != int(length):
                    raise ConnectionError("媒体下载不完整")
                os.replace(partial,target)
            finally:
                partial.unlink(missing_ok=True)
    except urllib.error.HTTPError as err:
        detail = err.read(1500).decode("utf8","replace")
        for secret in (os.getenv("FILM_LLM_KEY"),os.getenv("FILM_DS_KEY")):
            if secret:
                detail = detail.replace(secret,"[redacted]")
        raise ServiceHTTPError(err.code,detail,err.headers.get("Retry-After")) from err


def image_url(path):
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode()


def frame_count(timeline_frames):
    return 8 * math.ceil((timeline_frames - 1) / 8) + 1


def canvas(cfg):
    return tuple(32 * math.ceil(cfg[k] / 32) for k in ("width", "height"))


def assets(cfg):
    return {p.name: p for p in sorted(cfg["assets"].iterdir())
            if ("asset_allowlist" not in cfg or p.name in cfg["asset_allowlist"])
            and p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
            and p.resolve().parent == cfg["assets"]}

def disk_budget(cfg, seconds=0):
    reserve = float(cfg.get("min_free_gib",2))
    needed = int(reserve * 2**30) + seconds*48000*4
    available = shutil.disk_usage(cfg["data"]).free
    if available < needed:
        raise ValueError(f"工作盘剩余 {available/2**30:.2f} GiB，当前阶段预留 {needed/2**30:.2f} GiB；"
                         "请释放空间后继续")
