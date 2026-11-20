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
    for k in ("data", "assets", "workflows"):
        cfg[k] = (path.parent / cfg[k]).resolve()
        cfg[k].mkdir(parents=True, exist_ok=True)
    for k in ("width", "height"):
        if type(cfg[k]) is not int or cfg[k] < 32 or cfg[k] % 2:
            raise ValueError(f"{k} must be a positive even dimension")
    if not 1 <= cfg["attempts"] <= 8 or not 1 <= cfg["threshold"] <= 5:
        raise ValueError("attempts 1–8, threshold 1–5")
    return cfg


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
            with Path(dest).open("wb") as out:
                shutil.copyfileobj(response, out)
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code}: {err.read(1500).decode('utf8', 'replace')}") from err


def image_url(path):
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode()


def frame_count(timeline_frames):
    return 8 * math.ceil((timeline_frames - 1) / 8) + 1


def canvas(cfg):
    return tuple(32 * math.ceil(cfg[k] / 32) for k in ("width", "height"))


def assets(cfg):
    return {p.name: p for p in sorted(cfg["assets"].iterdir())
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
            and p.resolve().parent == cfg["assets"]}
