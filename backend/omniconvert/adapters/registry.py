"""Source adapter registry and public capability matrix.

Statuses are intentionally conservative. Many platforms restrict anonymous
server-side access (login walls, bot checks, rate limits); those are marked
``limited`` and the notes explain what to expect. A source that keeps failing
with temporary errors is automatically reported as temporarily unavailable.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from ..errors import OmniError
from .base import LIMITED, SUPPORTED, SourceAdapter
from .ytdlp import YtDlpAdapter

MEDIA_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".oga", ".opus")


class DirectMediaAdapter(YtDlpAdapter):
    def matches(self, host: str, path: str) -> bool:
        return os.path.splitext(path.lower())[1] in MEDIA_EXTENSIONS


ADAPTERS: list[SourceAdapter] = [
    YtDlpAdapter(
        id="youtube", name="YouTube", domains=("youtube.com", "youtu.be", "youtube-nocookie.com"),
        status=LIMITED, extractors=("youtube", "youtube:.*"),
        notes="Public videos only. YouTube often blocks anonymous server requests with bot checks; when that happens we can't continue because we never use accounts or cookies. Playlists and channels require selecting individual videos.",
    ),
    YtDlpAdapter(
        id="instagram", name="Instagram", domains=("instagram.com",),
        status=LIMITED, extractors=("instagram", "instagram:.*"),
        notes="Only public posts and reels that Instagram serves without login. Many posts require a login and will be reported as restricted. Multi-video posts let you choose items.",
    ),
    YtDlpAdapter(
        id="facebook", name="Facebook", domains=("facebook.com", "fb.watch", "fb.com"),
        status=LIMITED, extractors=("facebook", "facebook:.*"),
        notes="Only public videos available without login. Group, private, and friends-only posts can't be accessed.",
    ),
    YtDlpAdapter(
        id="tiktok", name="TikTok", domains=("tiktok.com",),
        status=LIMITED, extractors=("tiktok", "tiktok:.*", "vm.tiktok"),
        notes="Public videos. TikTok may rate-limit or block server requests; profile pages require selecting individual videos.",
    ),
    YtDlpAdapter(
        id="x", name="X (Twitter)", domains=("x.com", "twitter.com"),
        status=LIMITED, extractors=("twitter", "twitter:.*"),
        notes="Public posts with video. Posts from protected accounts, or ones X restricts for logged-out visitors, can't be accessed.",
    ),
    YtDlpAdapter(
        id="vimeo", name="Vimeo", domains=("vimeo.com",),
        status=LIMITED, extractors=("vimeo", "vimeo:.*"),
        notes="Vimeo currently serves many videos only to logged-in users, which we can't process. Public embeddable videos may work. Password-protected and private videos aren't supported.",
    ),
    YtDlpAdapter(
        id="dailymotion", name="Dailymotion", domains=("dailymotion.com", "dai.ly"),
        status=LIMITED, extractors=("dailymotion", "dailymotion:.*"),
        notes="Public videos. Some videos are geo-restricted or blocked for server requests.",
    ),
    YtDlpAdapter(
        id="soundcloud", name="SoundCloud", domains=("soundcloud.com",),
        status=LIMITED, extractors=("soundcloud", "soundcloud:.*"),
        notes="Public tracks that the uploader allows to stream. Go+ and private tracks aren't available.",
    ),
    YtDlpAdapter(
        id="reddit", name="Reddit", domains=("reddit.com", "redd.it"),
        status=LIMITED, extractors=("reddit",),
        notes="Public posts with Reddit-hosted video. Reddit may block server requests.",
    ),
    DirectMediaAdapter(
        id="direct", name="Direct media links", domains=(),
        status=SUPPORTED, extractors=("generic",),
        notes="Public links that point directly to a media file (for example, https://example.com/clip.mp4).",
    ),
]


def find_adapter(url: str) -> SourceAdapter:
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path or "/"
    for a in ADAPTERS:
        if a.matches(host, path):
            return a
    raise OmniError("unsupported_link")


def get_adapter(adapter_id: str) -> SourceAdapter:
    for a in ADAPTERS:
        if a.id == adapter_id:
            return a
    raise OmniError("unsupported_link")


def source_matrix() -> list[dict]:
    return [a.public() for a in ADAPTERS]
