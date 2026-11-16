"""User-facing error catalogue. Every error carries a specific message, the next
useful action, an HTTP status, and whether retrying could help."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    message: str
    action: str
    retryable: bool = False


CATALOG: dict[str, ErrorSpec] = {
    "invalid_url": ErrorSpec(400, "That doesn't look like a valid web address.", "Paste a full link starting with https://."),
    "unsupported_link": ErrorSpec(422, "This link isn't from a source we can process.", "Check the Supported sources page, or download the file yourself and upload it here."),
    "blocked_destination": ErrorSpec(422, "This link points to a private or reserved network address, which we never fetch.", "Use a public link or upload the file directly."),
    "collection_url": ErrorSpec(422, "This link is a playlist, channel, or account page.", "Select individual items from the list below — whole collections are never converted automatically."),
    "media_unavailable": ErrorSpec(404, "The media at this link is unavailable. It may have been removed or never existed.", "Double-check the link, or upload a copy you own."),
    "restricted_content": ErrorSpec(403, "This media is private, age-gated, region-locked, or behind a login.", "We don't bypass access controls. If you own the media, download it from the platform and upload the file here."),
    "drm_protected": ErrorSpec(403, "This media is protected by DRM.", "DRM-protected media can't be converted. Upload a file you own instead."),
    "source_requires_access": ErrorSpec(403, "This source requires account access that OmniConvert cannot provide.", "We never ask for passwords or cookies. Download the file from the platform yourself and upload it."),
    "source_rate_limited": ErrorSpec(429, "The source platform is temporarily limiting requests.", "Wait a few minutes and try again, or upload the file directly.", True),
    "source_unavailable": ErrorSpec(503, "Support for this source is temporarily unavailable.", "Try again later or upload the file directly.", True),
    "no_media_found": ErrorSpec(422, "We couldn't find any video or audio at this link.", "Make sure the link opens a specific post or video, not a profile or search page."),
    "file_too_large": ErrorSpec(413, "This file exceeds the size limit.", "Trim or compress the file before uploading, or split it into parts."),
    "duration_too_long": ErrorSpec(413, "This media is longer than the allowed duration.", "Trim the media to a shorter section and try again."),
    "unsupported_input": ErrorSpec(415, "This file isn't a supported video or audio file.", "Upload MP4, MOV, MKV, WEBM, AVI, M4V, MP3, WAV, AAC, M4A, FLAC, or OGG."),
    "unsupported_codec": ErrorSpec(415, "The file uses a codec we can't decode.", "Re-export the file with a common codec such as H.264/AAC and try again."),
    "invalid_settings": ErrorSpec(422, "Those settings can't be combined.", "Adjust the highlighted settings."),
    "processing_failed": ErrorSpec(500, "Processing failed while converting this file.", "Retry, or choose a different format or quality.", True),
    "output_invalid": ErrorSpec(500, "The converted file failed verification, so we didn't deliver it.", "Retry, or choose a different format.", True),
    "enhancement_unavailable": ErrorSpec(503, "The requested enhancement hardware or model isn't available on this server.", "Disable the enhancement or choose 60 FPS / original frame rate."),
    "enhancement_limit": ErrorSpec(422, "This media exceeds the limits for the selected enhancement.", "Trim the clip, lower the resolution, or choose a lighter enhancement."),
    "out_of_memory": ErrorSpec(507, "This conversion needs more memory than the server allows for one job.", "Choose a lower output resolution (or standard resize instead of AI), trim the clip, or try again later."),
    "job_expired": ErrorSpec(410, "This file has expired and was deleted.", "Convert the media again."),
    "job_not_found": ErrorSpec(404, "Job not found.", "It may have been deleted or belong to another browser session."),
    "upload_not_found": ErrorSpec(404, "Upload not found.", "Upload the file again."),
    "upload_incomplete": ErrorSpec(409, "The upload hasn't finished.", "Wait for all parts to upload, then try again."),
    "rate_limited": ErrorSpec(429, "You're sending requests too quickly.", "Wait a minute and try again.", True),
    "server_busy": ErrorSpec(503, "The conversion queue is full right now.", "Try again in a few minutes.", True),
    "too_many_jobs": ErrorSpec(429, "You have too many active conversions.", "Wait for some to finish or cancel ones you don't need.", True),
    "batch_too_large": ErrorSpec(422, "Too many items in one batch.", "Split the items into smaller batches."),
    "not_cancellable": ErrorSpec(409, "This job can no longer be cancelled.", ""),
    "not_retryable": ErrorSpec(409, "This job can't be retried.", "Start a new conversion instead."),
    "retry_limit": ErrorSpec(409, "This job has been retried the maximum number of times.", "Start a new conversion with different settings."),
    "cancelled": ErrorSpec(409, "Conversion cancelled.", "", True),
    "timeout": ErrorSpec(504, "Processing took longer than the allowed time.", "Trim the media or choose lighter settings.", True),
    "worker_lost": ErrorSpec(500, "The worker processing this job stopped unexpectedly.", "Retry the conversion.", True),
    "insufficient_disk": ErrorSpec(503, "The server is low on processing space.", "Try again later.", True),
    "csrf": ErrorSpec(403, "Request blocked.", "Reload the page and try again."),
    "session_required": ErrorSpec(401, "Your browser session is missing or expired.", "Reload the page."),
}


class OmniError(Exception):
    def __init__(self, code: str, detail: str | None = None, *, fields: dict[str, str] | None = None, retryable: bool | None = None):
        spec = CATALOG.get(code, CATALOG["processing_failed"])
        self.code = code
        self.spec = spec
        self.detail = detail
        self.fields = fields or {}
        self.retryable = spec.retryable if retryable is None else retryable
        super().__init__(f"{code}: {detail or spec.message}")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.detail or self.spec.message,
            "action": self.spec.action,
            "retryable": self.retryable,
            "fields": self.fields,
        }
