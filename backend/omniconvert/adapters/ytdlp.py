"""yt-dlp based adapters.

Policy (enforced here, not just documented):
* every request goes through the SSRF-filtering egress proxy;
* no cookies, credentials, or browser sessions are ever used;
* geo-restriction bypass is disabled and DRM formats are never selected
  (``allow_unplayable_formats`` stays False);
* each adapter is limited to its own extractors via ``allowed_extractors``;
* signed media URLs never leave this module (they aren't stored or logged).
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..config import get_settings
from ..errors import OmniError
from ..security.egress_proxy import get_egress_proxy
from ..security.ssrf import redact_url, validate_public_url
from .base import FetchRequest, SourceAdapter

log = logging.getLogger("omniconvert.adapters")


class _QuietLogger:
    """Swallows yt-dlp output; keeps only redacted error summaries."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.errors.append(_strip_urls(msg))


def _strip_urls(text: str) -> str:
    import re

    return re.sub(r"https?://\S+", lambda m: redact_url(m.group(0)), str(text))[:500]


def classify_error(text: str) -> OmniError:
    t = text.lower()
    if "egress policy" in t or "forbidden by egress" in t:
        return OmniError("blocked_destination")
    if "drm" in t:
        return OmniError("drm_protected")
    if "unsupported url" in t:
        return OmniError("unsupported_link")
    if "429" in t or "too many requests" in t or "rate-limit" in t or "rate limit" in t:
        return OmniError("source_rate_limited")
    if "account credentials" in t or "only works when logged-in" in t or "--cookies" in t and "private" not in t:
        return OmniError("source_requires_access", "This platform currently only serves this media to logged-in users. We never use accounts or cookies.")
    if "sign in to confirm" in t or "confirm you" in t and "bot" in t:
        return OmniError("source_requires_access", "The platform is asking for a signed-in session to confirm this isn't a bot. We don't use accounts or cookies.")
    if any(k in t for k in ("private", "login", "log in", "sign in", "cookies", "authentication", "members only", "members-only", "age-restricted", "age restricted", "inappropriate for some users")):
        return OmniError("restricted_content")
    if "country" in t or "geo" in t and "restrict" in t or "not available in your" in t:
        return OmniError("restricted_content", "This media is region-locked. We don't bypass regional restrictions.")
    if "live" in t and ("event" in t or "stream" in t) and "will begin" in t or "is live" in t:
        return OmniError("media_unavailable", "Live streams can't be converted. Try again once the stream has ended and a recording is available.")
    if any(k in t for k in ("unavailable", "removed", "does not exist", "not found", "404", "no video formats", "no longer available", "deleted")):
        return OmniError("media_unavailable")
    if any(k in t for k in ("timed out", "timeout", "connection", "temporarily", "503", "502", "500", "unable to download webpage")):
        return OmniError("source_unavailable")
    return OmniError("source_unavailable", retryable=True)


def _base_opts(logger: _QuietLogger, extractors: list[str]) -> dict:
    s = get_settings()
    proxy = get_egress_proxy()
    ffmpeg_dir = shutil.which(s.ffmpeg_path) or s.ffmpeg_path
    return {
        "proxy": proxy.url,
        "logger": logger,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "no_color": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 3,
        "extractor_retries": 1,
        "cachedir": False,
        "geo_bypass": False,
        "allow_unplayable_formats": False,
        "cookiefile": None,
        "allowed_extractors": extractors,
        "external_downloader": {"default": "native"},
        "ffmpeg_location": str(Path(ffmpeg_dir).parent) if Path(ffmpeg_dir).exists() else None,
        "check_formats": False,
        "playlistend": 60,
        "lazy_playlist": False,
    }


def _fmt_streams(info: dict) -> dict:
    """Summarize formats into user-facing qualities and stream lists."""
    formats = [f for f in info.get("formats") or [] if not f.get("has_drm")]
    drm = any(f.get("has_drm") for f in info.get("formats") or [])
    videos = [f for f in formats if (f.get("vcodec") or "none") != "none" and (f.get("height") or f.get("width"))]
    audios = [f for f in formats if (f.get("acodec") or "none") != "none" and (f.get("vcodec") or "none") == "none"]
    combined_audio = any((f.get("acodec") or "none") != "none" for f in formats)
    duration = info.get("duration")

    def size_of(f: dict) -> Optional[int]:
        sz = f.get("filesize") or f.get("filesize_approx")
        if not sz and duration and f.get("tbr"):
            sz = int(f["tbr"] * 1000 / 8 * duration)
        return int(sz) if sz else None

    best_audio = max(audios, key=lambda f: (f.get("abr") or f.get("tbr") or 0), default=None)
    qualities: dict[int, dict] = {}
    for f in videos:
        w, h = f.get("width") or 0, f.get("height") or 0
        short = min(w, h) if w and h else (h or w)
        if not short:
            continue
        q = qualities.setdefault(short, {"short_side": short, "width": w, "height": h, "fps": 0, "codecs": set(), "size": None})
        q["fps"] = max(q["fps"], f.get("fps") or 0)
        q["codecs"].add((f.get("vcodec") or "").split(".")[0])
        sz = size_of(f)
        if sz:
            a_sz = size_of(best_audio) if best_audio and (f.get("acodec") or "none") == "none" else 0
            total = sz + (a_sz or 0)
            q["size"] = max(q["size"] or 0, total)
    qual_list = sorted(qualities.values(), key=lambda q: q["short_side"], reverse=True)
    for q in qual_list:
        q["codecs"] = sorted(c for c in q["codecs"] if c)
    best = qual_list[0] if qual_list else None
    audio_streams = [
        {"codec": (f.get("acodec") or "").split(".")[0], "bitrate_kbps": int(f.get("abr") or f.get("tbr") or 0) or None, "sample_rate": f.get("asr"), "channels": f.get("audio_channels"), "language": f.get("language")}
        for f in sorted(audios, key=lambda f: f.get("abr") or 0, reverse=True)
    ][:8]
    # yt-dlp uses "none" for an absent stream and None for "unknown" (e.g. direct
    # file links); unknown streams are treated as possibly present and the
    # worker re-plans against the real file after fetching.
    pool = formats or [info]
    has_video = bool(qual_list) or any(f.get("vcodec") != "none" for f in pool)
    has_audio = bool(audios) or combined_audio or any(f.get("acodec") != "none" for f in pool)
    vbest = best or {}
    media = {
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "size": (vbest.get("size") if vbest else (size_of(best_audio) if best_audio else None)),
        "video": {"width": vbest.get("width") or info.get("width"), "height": vbest.get("height") or info.get("height"),
                  "fps": vbest.get("fps") or info.get("fps"), "codec": (vbest.get("codecs") or [None])[0]} if has_video else None,
        "audio": {"codec": audio_streams[0]["codec"] if audio_streams else None,
                  "sample_rate": audio_streams[0]["sample_rate"] if audio_streams else None,
                  "channels": audio_streams[0]["channels"] if audio_streams else None,
                  "bit_rate": (audio_streams[0]["bitrate_kbps"] or 0) * 1000 if audio_streams else None} if has_audio else None,
    }
    return {"qualities": qual_list, "audio_streams": audio_streams, "drm": drm, "media": media}


def normalize_entry(info: dict) -> dict:
    streams = _fmt_streams(info)
    return {
        "id": str(info.get("id") or ""),
        "title": (info.get("title") or info.get("fulltitle") or "Untitled")[:300],
        "url": info.get("webpage_url") or info.get("original_url") or info.get("url"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "is_live": bool(info.get("is_live")),
        "resolved": True,
        **streams,
    }


@dataclass
class YtDlpAdapter(SourceAdapter):
    extractors: tuple[str, ...] = ()
    collection_markers: tuple[str, ...] = ("tab", "playlist", "channel", "user", "profile", "search", "feed", "album")

    def _ydl(self, opts: dict):
        import yt_dlp

        return yt_dlp.YoutubeDL(opts)

    def analyze(self, url: str) -> dict:
        logger = _QuietLogger()
        opts = _base_opts(logger, list(self.extractors))
        opts.update({"skip_download": True, "extract_flat": "in_playlist", "noplaylist": False})
        try:
            with self._ydl(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:  # yt-dlp raises DownloadError / ExtractorError
            err = classify_error(" ".join(logger.errors) or str(e))
            self.record(False, err.retryable)
            raise err
        if not info:
            self.record(False, False)
            raise OmniError("no_media_found")
        self.record(True)
        result: dict[str, Any] = {"adapter": self.id, "platform": self.name, "source_url": url}
        if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
            entries = []
            for ent in list(info.get("entries") or [])[: get_settings().max_url_items_per_batch * 6]:
                if not ent:
                    continue
                if ent.get("formats"):
                    entries.append(normalize_entry(ent))
                else:
                    entries.append({
                        "id": str(ent.get("id") or ""), "title": (ent.get("title") or "Untitled")[:300],
                        "url": ent.get("url") or ent.get("webpage_url"), "thumbnail": _thumb(ent),
                        "duration": ent.get("duration"), "resolved": False,
                    })
            key = (info.get("extractor_key") or info.get("extractor") or "").lower()
            is_collection = info.get("_type") == "playlist" and any(m in key for m in self.collection_markers)
            result.update({
                "kind": "collection" if is_collection else "multi",
                "title": (info.get("title") or "Untitled collection")[:300],
                "thumbnail": _thumb(info) or next((e.get("thumbnail") for e in entries if e.get("thumbnail")), None),
                "entries": entries,
                "entry_count": info.get("playlist_count") or len(entries),
                "truncated": (info.get("playlist_count") or len(entries)) > len(entries),
            })
            if not entries:
                raise OmniError("no_media_found")
            return result
        entry = normalize_entry(info)
        if entry["is_live"]:
            raise OmniError("media_unavailable", "Live streams can't be converted. Try again once a recording is available.")
        if entry["drm"] and not entry["qualities"] and not entry["audio_streams"]:
            raise OmniError("drm_protected")
        if not entry["media"]["has_video"] and not entry["media"]["has_audio"]:
            raise OmniError("no_media_found")
        result.update({"kind": "single", "title": entry["title"], "thumbnail": entry["thumbnail"], "entries": [entry], "entry_count": 1})
        return result

    def resolve_entry(self, url: str) -> dict:
        """Fully extract one item (used for unresolved playlist entries)."""
        validate_public_url(url)
        logger = _QuietLogger()
        opts = _base_opts(logger, list(self.extractors))
        opts.update({"skip_download": True, "noplaylist": True})
        try:
            with self._ydl(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise classify_error(" ".join(logger.errors) or str(e))
        if not info or info.get("_type") == "playlist":
            raise OmniError("no_media_found")
        entry = normalize_entry(info)
        if entry["is_live"]:
            raise OmniError("media_unavailable", "Live streams can't be converted.")
        return entry

    def fetch(self, req: FetchRequest) -> Path:
        import yt_dlp

        url = req.item.get("url") or req.url
        validate_public_url(url)
        logger = _QuietLogger()
        opts = _base_opts(logger, list(self.extractors))
        sort: list[str] = []
        if req.category == "audio":
            fmt = "ba/b"
            sort = ["acodec:m4a"] if req.prefer_container in ("m4a", "mp4") else []
        else:
            fmt = "bv*+ba/b" if req.category == "video" else "bv*/b"
            if req.target_short:
                sort.append(f"res:{req.target_short}")
            if req.prefer_container in ("mp4", "mov"):
                sort += ["vcodec:h264", "acodec:m4a"]
            elif req.prefer_container == "webm":
                sort += ["vcodec:vp9", "acodec:opus"]
        state = {"files": 0}

        def hook(d: dict) -> None:
            if req.should_cancel():
                raise yt_dlp.utils.DownloadCancelled("cancelled")
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                done = d.get("downloaded_bytes") or 0
                if total and total > req.max_bytes * 1.05:
                    raise yt_dlp.utils.DownloadError("File is larger than max-filesize")
                req.on_progress(min(0.999, done / total) if total else None)
            elif d.get("status") == "finished":
                state["files"] += 1

        opts.update({
            "format": fmt,
            "format_sort": sort,
            "noplaylist": True,
            "outtmpl": str(req.dest_dir / "source.%(ext)s"),
            "paths": {"home": str(req.dest_dir), "temp": str(req.dest_dir)},
            "merge_output_format": "mkv",
            "max_filesize": req.max_bytes,
            "overwrites": True,
            "progress_hooks": [hook],
            "concurrent_fragment_downloads": 4,
            "writethumbnail": False,
            "writesubtitles": False,
            "postprocessors": [],
        })
        req.dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self._ydl(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadCancelled:
            from ..media.ffmpeg import Cancelled

            raise Cancelled()
        except Exception as e:
            text = " ".join(logger.errors) or str(e)
            if "max-filesize" in text or "larger than" in text:
                raise OmniError("file_too_large", f"The source is larger than the {req.max_bytes // 1024**2} MB download limit.")
            err = classify_error(text)
            self.record(False, err.retryable)
            raise err
        self.record(True)
        files = [p for p in req.dest_dir.iterdir() if p.is_file() and p.name.startswith("source.") and not p.name.endswith((".part", ".ytdl", ".tmp"))]
        if not files:
            if any("max-filesize" in e or "larger than" in e for e in logger.errors):
                raise OmniError("file_too_large")
            raise OmniError("media_unavailable", "The source didn't provide a downloadable file.")
        out = max(files, key=lambda p: p.stat().st_size)
        if out.stat().st_size > req.max_bytes:
            raise OmniError("file_too_large")
        return out


def _thumb(info: dict) -> Optional[str]:
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = info.get("thumbnails") or []
    return thumbs[-1].get("url") if thumbs else None
