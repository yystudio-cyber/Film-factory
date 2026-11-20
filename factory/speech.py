"""Verify literal on-camera dialogue against the audio actually used in the edit."""
import difflib
import functools
import tempfile
import unicodedata
from pathlib import Path

from . import media
from .common import Rejected


@functools.lru_cache(maxsize=1)
def _model(name):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8", local_files_only=True)


def _normal(text):
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in value if char.isalnum())


def verify_speech(cfg, path, expected, seconds=None):
    if not expected:
        return None
    name = cfg.get("asr_model")
    if not name:
        raise ValueError("有同期台词的镜头需要配置本地 faster-whisper 的 asr_model")
    if not _normal(expected):
        raise ValueError("同期台词需包含可读出的字词")
    model = _model(name)
    with tempfile.TemporaryDirectory() as tmp:
        # Decode only the picture edit. Extra 8n+1 render frames must not supply a
        # missing word that's cut before the audience hears the dialogue.
        wav = Path(tmp)/"line.wav"
        trim = [] if seconds is None else ["-t", str(seconds)]
        media.ff("-i", path, "-map", "0:a:0", "-vn", *trim, "-ar", "16000",
                 "-ac", "1", "-c:a", "pcm_s16le", wav)
        parts, info = model.transcribe(str(wav), beam_size=3, condition_on_previous_text=False,
                                       vad_filter=True, vad_parameters={"min_silence_duration_ms":300})
        segments = [{"start": float(part.start), "end": float(part.end), "text": part.text}
                    for part in parts]
    actual = "".join(part["text"] for part in segments)
    score = difflib.SequenceMatcher(None, _normal(expected), _normal(actual), autojunk=False).ratio()
    if not segments or score < float(cfg.get("speech_similarity", .8)):
        raise Rejected(f"同期台词不符 ({score:.0%})：{actual}")
    return {"expected": expected, "transcript": actual, "similarity": score,
            "language": info.language, "segments": segments}


def main():
    """Run the production SAPI-to-ASR path on the configured Windows workstation."""
    import argparse
    import json

    from .common import read

    parser = argparse.ArgumentParser(description="使用本机 SAPI 和本地 ASR 进行不占 GPU 的声音实机验收")
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    parser.add_argument("--line", default="请开始拍摄，摄影机已经准备好了。")
    parser.add_argument("--seconds", type=int, choices=range(2, 9), default=6)
    options = parser.parse_args()
    try:
        cfg = read(options.config)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            path = media.narrate({"narration":options.line,"seconds":options.seconds}, work)
            evidence = verify_speech(cfg, path, options.line, options.seconds)
            result = {"passed":True, "voice":read(work/"voice.json"), "asr":evidence}
    except Exception as err:
        result = {"passed":False,"reason":str(err) or type(err).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
