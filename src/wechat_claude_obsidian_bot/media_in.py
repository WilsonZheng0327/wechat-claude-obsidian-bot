"""Incoming media: download images/files into the vault, transcribe voice.

Media lands in <vault>/Wechat_Saved/ so notes can embed it with ![[...]].
Downloads are capped at MAX_MEDIA_MB (config). Voice transcription is a
local-ASR fallback for when WeChat sends no transcript: SILK -> WAV via
weixin-ilink's pilk helper, then faster-whisper (both optional deps —
`pip install "wechat-claude-obsidian-bot[voice]"`).
"""

import re
import tempfile
from datetime import datetime
from pathlib import Path

from .config import MAX_MEDIA_MB

SAVE_DIR_NAME = "Wechat_Saved"


class MediaTooLarge(Exception):
    pass


def _declared_size(item: dict) -> int:
    """Size the sender declared, so we can refuse before downloading."""
    file_item = item.get("file_item") or {}
    if file_item.get("len"):
        return int(file_item["len"])
    for kind, key in (("image_item", "mid_size"), ("video_item", "video_size")):
        media = item.get(kind) or {}
        if media.get(key):
            return int(media[key])
    return 0


def _fetch(msg) -> bytes:
    limit = MAX_MEDIA_MB * 1024 * 1024
    if _declared_size(msg.raw_item) > limit:
        raise MediaTooLarge(f"over the {MAX_MEDIA_MB} MB limit")
    data = msg.download()
    if not data:
        raise ValueError("download returned no data")
    if len(data) > limit:
        raise MediaTooLarge(f"over the {MAX_MEDIA_MB} MB limit")
    return data


def _unique_path(vault: Path, name: str) -> Path:
    save_dir = vault / SAVE_DIR_NAME
    save_dir.mkdir(exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "unnamed"
    path = save_dir / safe
    counter = 1
    while path.exists():
        path = save_dir / f"{Path(safe).stem}-{counter}{Path(safe).suffix}"
        counter += 1
    return path


def _image_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def save_image(msg, vault: Path) -> Path:
    data = _fetch(msg)
    name = f"wechat-{datetime.now():%Y%m%d-%H%M%S}{_image_ext(data)}"
    path = _unique_path(vault, name)
    path.write_bytes(data)
    return path


def save_file(msg, vault: Path) -> Path:
    data = _fetch(msg)
    path = _unique_path(vault, msg.file_name or "unnamed")
    path.write_bytes(data)
    return path


_whisper_model = None
_WHISPER_SIZE = "small"


def _whisper_model_cached() -> bool:
    """True if the model is already on disk (no download needed).

    Uses faster-whisper's own resolver so we check exactly the files the
    loader needs — the repo holds extra files (README etc.) that are never
    downloaded, so a bare snapshot_download check would always say no.
    """
    try:
        from faster_whisper.utils import download_model

        download_model(_WHISPER_SIZE, local_files_only=True)
        return True
    except Exception:
        return False


def transcribe_voice(msg) -> str | None:
    """Local ASR fallback. Returns None when the optional deps are missing."""
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
        from weixin_ilink.voice import silk_to_wav
    except ImportError as err:
        print(f"[claude-bot] local ASR unavailable: {err}", flush=True)
        return None

    data = _fetch(msg)
    with tempfile.TemporaryDirectory() as tmp:
        silk = Path(tmp) / "voice.silk"
        silk.write_bytes(data)
        wav = Path(tmp) / "voice.wav"
        silk_to_wav(silk, wav)
        if _whisper_model is None:
            if not _whisper_model_cached():
                from .settings import load, tr

                msg.reply_text(tr("whisper_download", load()["language"]))
            print("[claude-bot] loading whisper model (first voice message)...", flush=True)
            _whisper_model = WhisperModel(_WHISPER_SIZE, device="cpu", compute_type="int8")
        segments, _ = _whisper_model.transcribe(str(wav))
        text = "".join(segment.text for segment in segments).strip()
    return text or None
