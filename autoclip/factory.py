#!/usr/bin/env python3
"""Free, scheduled YouTube VOD-to-Shorts starter pipeline.

This uses public YouTube watch pages as its source, yt-dlp/FFmpeg for media,
faster-whisper for captions, and the YouTube Data API for publishing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from yt_dlp import YoutubeDL

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "autoclip/config.json").read_text(encoding="utf-8"))
OUT = ROOT / CFG.get("output_dir", "output")
DATA = ROOT / "data"
PROCESSED = ROOT / CFG.get("processed_file", "data/processed.json")
OUT.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def processed_ids() -> set[str]:
    if PROCESSED.exists():
        return set(json.loads(PROCESSED.read_text(encoding="utf-8")))
    return set()


def save_processed(ids: set[str]) -> None:
    PROCESSED.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED.write_text(json.dumps(sorted(ids), indent=2), encoding="utf-8")


def latest_finished_video() -> dict | None:
    opts = {
        "quiet": True, "no_warnings": True, "extract_flat": False,
        "playlistend": 8, "skip_download": True,
    }
    # First try channel uploads. YouTube may expose the newest finished VOD here.
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CFG["channel_url"], download=False)
    entries = info.get("entries") or [info]
    # Only consider the newest upload. If it is live/upcoming or lacks a
    # completed duration, wait rather than accidentally clipping an older VOD.
    entry = next((item for item in entries if item), None)
    if not entry:
        return None
    if entry.get("live_status") in ("is_live", "is_upcoming", "was_live") and not entry.get("duration"):
        return None
    if entry.get("is_live") or not entry.get("duration") or not entry.get("webpage_url"):
        return None
    return {
        "id": entry.get("id"),
        "url": entry.get("webpage_url"),
        "title": entry.get("title", "Stream highlights"),
        "duration": entry.get("duration", 0),
    }


def download_video(url: str, video_id: str) -> Path:
    template = str(DATA / f"{video_id}.%(ext)s")
    opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": template,
        "quiet": False,
        "noplaylist": True,
        "max_filesize": 8 * 1024 * 1024 * 1024,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    if not path.exists():
        candidates = list(DATA.glob(f"{video_id}.*"))
        if not candidates:
            raise FileNotFoundError("yt-dlp finished but no media file was found")
        path = candidates[0]
    return path


def choose_segments(video: Path, duration: int, count: int) -> list[tuple[int, int]]:
    """Pick candidate windows from audio energy; this is a heuristic, not semantic AI."""
    wav = DATA / "audio.wav"
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-f", "wav", str(wav)])
    import wave
    with wave.open(str(wav), "rb") as f:
        rate = f.getframerate()
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32)
    window = 2 * rate
    scores = []
    for start in range(0, max(1, len(samples) - window), rate * 5):
        chunk = samples[start:start + window]
        if len(chunk) < window:
            continue
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        scores.append((rms, start / rate))
    clip_len = min(int(CFG["clip_max_seconds"]), max(int(CFG["clip_min_seconds"]), 40))
    candidates = sorted(scores, reverse=True)
    chosen = []
    for score, peak in candidates:
        start = max(0, min(int(peak - clip_len / 2), max(0, duration - clip_len)))
        end = min(duration, start + clip_len)
        if end - start < int(CFG["clip_min_seconds"]):
            continue
        if all(abs(start - old[0]) > clip_len for old in chosen):
            chosen.append((start, end))
        if len(chosen) >= count:
            break
    return sorted(chosen)


def srt_time(seconds: float) -> str:
    ms = int((seconds - int(seconds)) * 1000)
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def transcribe(model: WhisperModel, video: Path, start: int, end: int, base: str) -> Path:
    audio = DATA / f"{base}.wav"
    run(["ffmpeg", "-y", "-ss", str(start), "-i", str(video), "-t", str(end-start),
         "-vn", "-ac", "1", "-ar", "16000", str(audio)])
    segments, _ = model.transcribe(str(audio), vad_filter=True, word_timestamps=False)
    srt = OUT / f"{base}.srt"
    with srt.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            text = re.sub(r"\s+", " ", seg.text).strip()
            if text:
                f.write(f"{i}\n{srt_time(seg.start)} --> {srt_time(seg.end)}\n{text}\n\n")
    return srt


def make_vertical(video: Path, start: int, end: int, srt: Path, base: str) -> Path:
    target = OUT / f"{base}.mp4"
    # Center crop to 9:16. This is a generic crop, not character tracking.
    escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:")
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,"
        f"subtitles='{escaped}':force_style='FontName=Arial,FontSize=18,"
        "Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=100'"
    )
    run(["ffmpeg", "-y", "-ss", str(start), "-i", str(video), "-t", str(end-start),
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target)])
    return target


def publish(video: Path, title: str, description: str) -> str:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    token = os.environ.get("YOUTUBE_TOKEN_JSON")
    if not token:
        raise RuntimeError("YOUTUBE_TOKEN_JSON secret is missing; clips were rendered but not uploaded.")
    creds = Credentials.from_authorized_user_info(json.loads(token),
        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title[:100], "description": description[:5000],
                    "categoryId": CFG.get("category_id", "20"), "tags": ["shorts", "highlights"]},
        "status": {"privacyStatus": CFG.get("privacy_status", "public"),
                   "selfDeclaredMadeForKids": False}
    }
    request = youtube.videos().insert(part="snippet,status", body=body,
        media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    return "https://youtu.be/" + response["id"]


def main() -> None:
    seen = processed_ids()
    item = latest_finished_video()
    if not item:
        print("No finished stream/VOD found.")
        return
    if item["id"] in seen:
        print("Latest VOD already processed.")
        return
    if item["duration"] > int(CFG.get("max_source_hours", 4)) * 3600:
        print("VOD exceeds configured maximum duration; skipping to protect free runner limits.")
        return

    source = download_video(item["url"], item["id"])
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments = choose_segments(source, int(item["duration"]), int(CFG["clips_per_video"]))
    if not segments:
        raise RuntimeError("Could not find candidate segments.")
    for number, (start, end) in enumerate(segments, 1):
        slug = f"{item['id']}-clip-{number:02d}"
        srt = transcribe(model, source, start, end, slug)
        clip = make_vertical(source, start, end, srt, slug)
        title = f"{item['title']} | Best moment #{number} #Shorts"
        link = publish(clip, title, f"Highlights from: {item['title']}\n#Shorts")
        print(f"Published: {link}")
    seen.add(item["id"])
    save_processed(seen)


if __name__ == "__main__":
    main()
