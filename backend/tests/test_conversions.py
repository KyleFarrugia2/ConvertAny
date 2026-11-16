"""End-to-end conversions through the real API + worker + FFmpeg, verified with FFprobe."""
from __future__ import annotations

from fractions import Fraction

from conftest import TMP, convert, download, ffprobe, upload


def _streams(info, kind):
    return [s for s in info["streams"] if s["codec_type"] == kind]


def _fps(stream):
    return float(Fraction(stream["avg_frame_rate"]))


def test_upload_is_inspected(client, samples):
    up = upload(client, samples["mov"])
    assert up["_status"] == 200 and up["status"] == "complete"
    m = up["media"]
    assert m["has_video"] and m["has_audio"]
    assert (m["video"]["width"], m["video"]["height"], m["video"]["fps"]) == (1280, 720, 30.0)
    assert m["video"]["codec"] == "h264" and m["audio"]["codec"] == "aac"
    assert 5.9 < m["duration"] < 6.1


def test_video_to_mp3(client, samples):
    up = upload(client, samples["mov"])
    job = convert(client, up["id"], output="mp3", audio_bitrate_kbps=192)
    assert job["status"] == "completed", job
    out = download(client, job["id"], TMP / "out.mp3")
    info = ffprobe(out)
    a = _streams(info, "audio")
    assert len(a) == 1 and a[0]["codec_name"] == "mp3" and not _streams(info, "video")
    assert abs(int(a[0]["bit_rate"]) - 192000) < 4000
    assert abs(float(info["format"]["duration"]) - 6.0) < 0.2
    assert job["output"]["filename"].endswith(".mp3")


def test_video_to_mp4_h264_aac(client, samples):
    up = upload(client, samples["portrait"])  # VP9/Opus WEBM -> MP4 must re-encode
    job = convert(client, up["id"], output="mp4", quality="balanced")
    assert job["status"] == "completed", job
    info = ffprobe(download(client, job["id"], TMP / "out.mp4"))
    v, a = _streams(info, "video")[0], _streams(info, "audio")[0]
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" and a["codec_name"] == "aac"
    assert (v["width"], v["height"]) == (720, 1280)


def test_original_quality_remuxes_without_reencoding(client, samples):
    up = upload(client, samples["mov"])
    r = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "quality": "original"}})
    assert r.json()["stream_copy"] is True
    job = convert(client, up["id"], output="mp4", quality="original")
    assert job["status"] == "completed"
    src, out = ffprobe(samples["mov"]), ffprobe(download(client, job["id"], TMP / "remux.mp4"))
    # identical bitstream parameters => copied, not re-encoded
    sv, ov = _streams(src, "video")[0], _streams(out, "video")[0]
    assert ov["codec_name"] == sv["codec_name"] and ov["nb_frames"] == sv["nb_frames"] and ov["bit_rate"] == sv["bit_rate"]


def test_audio_format_conversions(client, samples):
    up = upload(client, samples["wav"])
    for output, codec, extra in [
        ("flac", "flac", {}),
        ("ogg", "vorbis", {"audio_bitrate_kbps": 128}),
        ("m4a", "aac", {"sample_rate": 48000, "channels": "mono", "audio_bitrate_kbps": 96}),
        ("wav", "pcm_s24le", {"wav_bit_depth": 24}),
        ("mp3", "mp3", {"audio_bitrate_kbps": 64}),
        ("mp3", "mp3", {"audio_bitrate_kbps": 320}),
    ]:
        job = convert(client, up["id"], output=output, **extra)
        assert job["status"] == "completed", (output, job)
        info = ffprobe(download(client, job["id"], TMP / f"a.{output}"))
        a = _streams(info, "audio")[0]
        assert a["codec_name"] == codec, (output, a["codec_name"])
        if output == "m4a":
            assert a["sample_rate"] == "48000" and a["channels"] == 1
        if "audio_bitrate_kbps" in extra and output == "mp3":
            assert abs(int(a["bit_rate"]) - extra["audio_bitrate_kbps"] * 1000) < 4000
        assert abs(float(info["format"]["duration"]) - 4.0) < 0.15


def test_resolution_selection_and_upscale_labeling(client, samples):
    up = upload(client, samples["mov"])
    job = convert(client, up["id"], output="mp4", resolution="480")
    v = _streams(ffprobe(download(client, job["id"], TMP / "480.mp4")), "video")[0]
    assert (v["width"], v["height"]) == (854, 480)
    # above source without enhancement -> rejected with explanation
    r = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "resolution": "1080"}})
    p = r.json()
    assert p["valid"] is False and "resolution" in p["errors"]
    # with standard resize -> allowed, labeled as upscaled (not native)
    r = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "resolution": "1080", "upscale": "standard"}})
    p = r.json()
    assert p["valid"] and p["upscale"]["mode"] == "standard"
    assert any("not native 1080p" in n for n in p["notes"])
    job = convert(client, up["id"], output="mp4", resolution="1080", upscale="standard", quality="small")
    assert job["output"]["upscaled"] is True
    v = _streams(ffprobe(download(client, job["id"], TMP / "1080.mp4")), "video")[0]
    assert (v["width"], v["height"]) == (1920, 1080)
    # AI upscaling must be rejected unless a real model is deployed
    r = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "resolution": "1080", "upscale": "ai"}})
    caps = client.get("/api/capabilities").json()
    if not caps["enhancement"]["upscale"]["ai"]["available"]:
        assert r.json()["valid"] is False and "upscale" in r.json()["errors"]


def test_trimming_and_audio_edits(client, samples):
    up = upload(client, samples["mov"])
    job = convert(client, up["id"], output="mp4", quality="small",
                  edit={"trim_start": 1.0, "trim_end": 3.5, "fade_in": 0.5, "fade_out": 0.5, "volume": 80, "strip_metadata": True})
    assert job["status"] == "completed", job
    info = ffprobe(download(client, job["id"], TMP / "trim.mp4"))
    assert abs(float(info["format"]["duration"]) - 2.5) < 0.1
    a = _streams(info, "audio")[0]
    v = _streams(info, "video")[0]
    assert abs(float(a["duration"]) - float(v["duration"])) < 0.1  # A/V stay in sync


def test_crop_rotate_aspect_and_remove_audio(client, samples):
    up = upload(client, samples["mov"])
    job = convert(client, up["id"], output="mp4", quality="small", include_audio=False,
                  edit={"crop": {"x": 0, "y": 0, "w": 720, "h": 720}, "rotate": 90})
    info = ffprobe(download(client, job["id"], TMP / "crop.mp4"))
    v = _streams(info, "video")[0]
    assert (v["width"], v["height"]) == (720, 720) and not _streams(info, "audio")
    job = convert(client, up["id"], output="mp4", quality="small", resolution="720", edit={"aspect": "9:16", "aspect_mode": "fit"})
    v = _streams(ffprobe(download(client, job["id"], TMP / "916.mp4")), "video")[0]
    assert (v["width"], v["height"]) == (720, 1280)
    # stretch is refused while "preserve aspect ratio" is on
    r = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "edit": {"aspect": "1:1", "aspect_mode": "stretch"}}})
    assert r.json()["valid"] is False


def test_webm_mov_mkv_gif_and_loudnorm(client, samples):
    up = upload(client, samples["mov"])
    for out, vcodec, extra in [("webm", "vp9", {}), ("mov", "prores", {"video_codec": "prores"}), ("mkv", "hevc", {"video_codec": "hevc"}),
                               ("mp4", "av1", {"video_codec": "av1"})]:
        job = convert(client, up["id"], output=out, quality="small", resolution="360", normalize=True, edit={"trim_end": 2}, **extra)
        assert job["status"] == "completed", (out, job)
        info = ffprobe(download(client, job["id"], TMP / f"x.{out}"))
        assert _streams(info, "video")[0]["codec_name"] == vcodec
    job = convert(client, up["id"], output="gif", gif_width=320, gif_fps=10, edit={"trim_end": 2})
    info = ffprobe(download(client, job["id"], TMP / "x.gif"))
    v = _streams(info, "video")[0]
    assert v["codec_name"] == "gif" and v["width"] == 320


def test_invalid_combinations_are_rejected(client, samples):
    up = upload(client, samples["silent"])
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp3"}}).json()
    assert not p["valid"] and "no audio" in p["errors"]["output"]
    up = upload(client, samples["wav"])
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4"}}).json()
    assert not p["valid"]
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "ogg", "audio_codec": "opus", "sample_rate": 44100}}).json()
    assert not p["valid"] and "sample_rate" in p["errors"]
    up2 = upload(client, samples["mov"])
    p = client.post("/api/plan", json={"source": {"upload_id": up2["id"]}, "options": {"output": "webm", "video_codec": "h264"}}).json()
    assert not p["valid"] and "video_codec" in p["errors"]
    r = client.post("/api/plan", json={"source": {"upload_id": up2["id"]}, "options": {"output": "mp3", "audio_bitrate_kbps": 100}})
    assert r.status_code == 422


def test_upload_content_is_verified_not_extension(client, samples):
    r = upload(client, samples["fake"])
    assert r["_status"] == 415 and r["error"]["code"] == "unsupported_input"
    r = upload(client, samples["hls"])  # playlist with metadata URL: rejected, never fetched
    assert r["_status"] == 415
    r = client.post("/api/uploads", json={"filename": "malware.exe", "size": 100})
    assert r.status_code == 415
    r = client.post("/api/uploads", json={"filename": "huge.mp4", "size": 50 * 1024**3})
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_lossy_to_lossless_note(client, samples):
    up = upload(client, samples["mov"])
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "flac"}}).json()
    assert p["valid"] and any("can't restore" in n for n in p["notes"])


def test_plan_with_unknown_source_duration():
    """Direct links may not advertise a duration; planning must not reject them."""
    from omniconvert.media.options import ConvertOptions
    from omniconvert.media.planner import build_plan

    media = {"duration": None, "has_video": True, "has_audio": True, "video": {"width": None, "height": None, "fps": None, "codec": None}, "audio": None}
    p = build_plan(ConvertOptions(output="mp4", resolution="360"), media, source_type="url")
    assert p.valid, p.errors
    p = build_plan(ConvertOptions(output="mp3"), media, source_type="url")
    assert p.valid, p.errors


def test_4k_upscale_from_4x3_source_fits_memory_cap(client, samples):
    """Regression: x264 auto-threading on many-core CPUs exhausted the per-process
    memory cap when upscaling to 2160p (malloc failures, endless retries)."""
    import subprocess
    src = TMP / "four-three.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30", "-f", "lavfi", "-i", "sine=sample_rate=48000",
                    "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    up = upload(client, src)
    job = convert(client, up["id"], output="mp4", resolution="2160", upscale="standard")
    assert job["status"] == "completed", job
    v = _streams(ffprobe(download(client, job["id"], TMP / "4k.mp4")), "video")[0]
    assert (v["width"], v["height"]) == (2880, 2160) and job["output"]["upscaled"]


def test_out_of_memory_is_reported_clearly_not_retried():
    from omniconvert.errors import OmniError
    from omniconvert.media.ffmpeg import run_process
    import sys
    try:
        run_process([sys.executable, "-c", "import sys; sys.stderr.write('x264 [error]: malloc of size 20034816 failed'); sys.exit(1)"], parse_ffmpeg_progress=False)
    except OmniError as e:
        assert e.code == "out_of_memory" and e.retryable is False and "lower output resolution" in e.to_dict()["action"]
    else:
        raise AssertionError("expected failure")
