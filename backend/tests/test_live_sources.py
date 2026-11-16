"""Live network tests against real public sources. Opt-in: OC_LIVE_TESTS=1.

Uses openly licensed media (Sintel trailer, (c) Blender Foundation, CC BY 3.0,
hosted by the W3C for testing).
"""
from __future__ import annotations

import os

import pytest

from conftest import TMP, download, ffprobe, run_worker_until_idle

pytestmark = pytest.mark.skipif(os.environ.get("OC_LIVE_TESTS") != "1", reason="set OC_LIVE_TESTS=1 to run live network tests")

DIRECT = "https://media.w3.org/2010/05/sintel/trailer.mp4"


def test_direct_link_analyze_and_convert(client):
    r = client.post("/api/analyze", json={"url": DIRECT})
    assert r.status_code == 200, r.text
    an = r.json()
    assert an["adapter"] == "direct" and an["kind"] == "single"
    entry = an["entries"][0]
    r = client.post("/api/jobs", json={"items": [
        {"source": {"analysis_id": an["id"], "entry_id": entry["id"]}, "options": {"output": "mp3", "audio_bitrate_kbps": 128, "edit": {"trim_end": 10}}},
        {"source": {"analysis_id": an["id"], "entry_id": entry["id"]}, "options": {"output": "mp4", "resolution": "360", "quality": "small", "edit": {"trim_start": 5, "trim_end": 12}}},
    ]})
    jobs = [x["job"] for x in r.json()["results"]]
    assert "fetching" in jobs[0]["stages"] and "analyzing" in jobs[0]["stages"]
    run_worker_until_idle(600)
    j1 = client.get(f"/api/jobs/{jobs[0]['id']}").json()
    j2 = client.get(f"/api/jobs/{jobs[1]['id']}").json()
    assert j1["status"] == "completed", j1
    assert j2["status"] == "completed", j2
    a = ffprobe(download(client, j1["id"], TMP / "live.mp3"))
    assert a["streams"][0]["codec_name"] == "mp3" and abs(float(a["format"]["duration"]) - 10) < 0.3
    v = ffprobe(download(client, j2["id"], TMP / "live.mp4"))
    vs = [s for s in v["streams"] if s["codec_type"] == "video"][0]
    assert vs["height"] == 360 and abs(float(v["format"]["duration"]) - 7) < 0.3


def test_redirect_to_internal_address_is_blocked(client):
    """A public URL that redirects to the cloud metadata endpoint must not be followed into it."""
    import urllib.request

    from omniconvert.security.egress_proxy import get_egress_proxy

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": get_egress_proxy().url, "https": get_egress_proxy().url}))
    with pytest.raises(Exception) as e:
        opener.open("https://httpbin.org/redirect-to?url=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data%2F", timeout=20).read()
    assert "403" in str(e.value) or "Forbidden" in str(e.value) or "Tunnel" in str(e.value)


def test_unsupported_and_restricted_links(client):
    r = client.post("/api/analyze", json={"url": "https://www.wikipedia.org/"})
    assert r.json()["error"]["code"] == "unsupported_link"
    r = client.post("/api/analyze", json={"url": "https://media.w3.org/2010/05/sintel/does-not-exist.mp4"})
    assert r.status_code >= 400 and r.json()["error"]["code"] in ("media_unavailable", "source_unavailable", "no_media_found")
