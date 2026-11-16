# OmniConvert

Convert supported online media links and your own uploaded files into downloadable video or audio. MP4 and MP3 are the defaults, and advanced users get precise control over codecs, quality, frame rate, resolution and editing.

Everything the site offers is real: conversion runs FFmpeg on background workers, job state is stored durably, progress figures are measured, and outputs are verified with FFprobe and a decode pass before they're offered for download.

---

## Contents
1. [Quick start (local, no Docker)](#quick-start-local-no-docker)
2. [Docker Compose](#docker-compose)
3. [Deploy the website on Vercel](#deploy-the-website-on-vercel)
4. [Architecture](#architecture)
5. [Capability table](#capability-table)
6. [API](#api)
7. [Security & privacy](#security--privacy)
8. [Testing & results](#testing--results)
9. [Hardware requirements for enhancements](#hardware-requirements-for-enhancements)
10. [Production checklist / external infrastructure](#production-checklist--external-infrastructure)
11. [Known limitations](#known-limitations)

---

## Quick start (local, no Docker)

Prerequisites: **Python 3.12**, **Node 22**, and **FFmpeg + FFprobe on PATH**. A "full" build is recommended, for example `winget install Gyan.FFmpeg` on Windows or `apt install ffmpeg` on Linux.

```powershell
# Windows: one command runs the API, worker and web app
./dev.ps1
```

Or step by step (any OS):

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head                               # creates backend/data/omniconvert.db (SQLite)
uvicorn omniconvert.api.main:app --port 8000       # terminal 1
python -m omniconvert.worker.main --queues cpu,gpu # terminal 2

# frontend
cd ../frontend
npm ci
npm run dev                                        # http://localhost:3000
```

The web app proxies `/api/*` to `API_ORIGIN` (default `http://127.0.0.1:8000`). Interactive API docs are at `http://127.0.0.1:8000/api/docs`.

Configuration lives in environment variables with the `OC_` prefix. Copy [`.env.example`](.env.example) to `backend/.env`; every limit has a working default.

## Docker Compose

```bash
cp .env.example .env            # set OC_SECRET_KEY and POSTGRES_PASSWORD
docker compose up --build       # web :3000, api, cpu worker, postgres
docker compose --profile gpu up --build   # + GPU worker (RIFE / Real-ESRGAN), needs the NVIDIA Container Toolkit
docker compose --profile s3  up --build   # + MinIO; set OC_STORAGE_BACKEND=s3 and the OC_S3_* variables
```

The worker containers are hardened: read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, and memory, CPU and PID limits. The `migrate` service runs `alembic upgrade head` before the API and workers start.

> The Docker setup is written but was **not built or run** in the development environment, which had no Docker. The application itself was verified natively (see [Testing](#testing--results)).

## Deploy the website on Vercel

The Next.js UI deploys to [Vercel](https://vercel.com). Conversion itself (FFmpeg, workers, uploads, PostgreSQL) cannot run on Vercel — those stay on a host that can run [Docker Compose](#docker-compose) or the API + worker processes, with a public HTTPS URL.

1. Host the API/workers (Compose, a VM, Railway, Fly, Render, …). Give the API a public origin such as `https://api.example.com`.
2. On that API, set:
   - `OC_ENV=production`
   - `OC_SECRET_KEY` (random 64 hex chars)
   - `OC_COOKIE_SECURE=true`
   - `OC_TRUST_PROXY=1` (only behind a reverse proxy)
   - `OC_PUBLIC_API_BASE=https://api.example.com`
   - `OC_CORS_ORIGINS=https://your-app.vercel.app` (and your custom domain if you add one)
   - `OC_CORS_ORIGIN_REGEX=https://.*\.vercel\.app` (preview deployments)
3. Import [this GitHub repo](https://github.com/KyleFarrugia2/ConvertAny) into Vercel.
4. Set **Root Directory** to `frontend` (Framework: Next.js, Node 22).
5. Add the environment variable `NEXT_PUBLIC_API_ORIGIN=https://api.example.com` (no trailing slash) and deploy.

The browser talks to the API origin directly so large uploads and long jobs are not limited by Vercel’s request size or timeout. Session cookies are `SameSite=None; Secure` when CORS is enabled. For large file uploads in production, use S3 (`OC_STORAGE_BACKEND=s3`) so chunks go straight to the bucket.

## Architecture

```
Browser ──► Next.js (TypeScript, App Router) ──/api/*──► FastAPI (Python)
                                                        │  sessions, validation, planning, uploads, downloads
                                                        ▼
                                        PostgreSQL / SQLite  ◄──── workers (CPU queue, GPU queue)
                                        (jobs = durable queue)        │ FFmpeg / FFprobe / yt-dlp / RIFE / Real-ESRGAN
                                                                      ▼
                                              Storage: local disk or S3-compatible
```

| Piece | Where | Notes |
|---|---|---|
| Frontend | [`frontend/src`](frontend/src) | Converter, queue, supported sources & formats, how it works, FAQ, privacy, terms, report. |
| API | [`backend/omniconvert/api/main.py`](backend/omniconvert/api/main.py) | Anonymous sessions, CSRF header, rate limits, all endpoints. |
| Planner | [`backend/omniconvert/media/planner.py`](backend/omniconvert/media/planner.py) | One source of truth for validation, codec choice, stream-copy detection, filter graphs, stages and estimates. The API uses it before queueing; the worker uses it again against the real fetched file. |
| Worker | [`backend/omniconvert/worker/main.py`](backend/omniconvert/worker/main.py) | Atomic claims (`FOR UPDATE SKIP LOCKED` on Postgres), leases plus a reaper for crash recovery, cancellation, bounded retries with backoff, verification, scheduled cleanup. |
| Source adapters | [`backend/omniconvert/adapters`](backend/omniconvert/adapters) | Per-platform adapters on yt-dlp, each restricted to its own extractors; direct-media-link adapter. |
| GPU pipeline | [`backend/omniconvert/media/enhancement.py`](backend/omniconvert/media/enhancement.py) | Frame extraction → scene-split RIFE → Real-ESRGAN → encode with the original audio. |
| Schema | [`backend/omniconvert/models.py`](backend/omniconvert/models.py), [`backend/migrations`](backend/migrations) | `alembic check` confirms the models and migrations match. |

**Why no Redis?** The job queue is the `jobs` table itself: claims are atomic conditional updates (plus `SKIP LOCKED` on Postgres), workers hold renewable leases, and expired leases are re-queued. That gives durable, crash-safe jobs with one fewer service to run. Redis is only worth adding for a shared rate limiter when you run several API replicas (see [limitations](#known-limitations)).

**Progress delivery:** the client polls (about every 1.2 s while jobs are active, 15 s when idle, paused in background tabs). Progress comes from FFmpeg's `-progress` output time, yt-dlp byte counts, or counted output frames for the GPU tools. When none of these exist, the UI shows an indeterminate bar with the stage name instead of a percentage.

## Capability table

Status in the development environment (Windows 11, FFmpeg 8.1.2 full build, RTX 4070 Ti SUPER):

| Feature | Status | Notes |
|---|---|---|
| Upload → MP4 (H.264/AAC), MP3 | ✅ Implemented & tested | Core flows, verified with FFprobe. |
| WEBM (VP9/AV1 + Opus/Vorbis), MOV (H.264/HEVC/ProRes), MKV | ✅ Implemented & tested | Only valid container/codec pairs are offered; unavailable encoders are disabled at runtime. |
| AAC/M4A, WAV (16/24-bit), FLAC, OGG (Vorbis/Opus) | ✅ Implemented & tested | Format-appropriate controls; no bitrate settings for lossless formats. |
| GIF, audio-only extraction, video-only (audio removed) | ✅ Implemented & tested | GIF limited to 30 s by default. |
| Stream copy / remux when possible | ✅ Implemented & tested | e.g. MOV(H.264/AAC) → MP4 copies bit-identical streams. |
| Simple and advanced quality controls, bitrate, sample rate, channels, loudness normalization | ✅ Implemented & tested | |
| Trim, crop, rotate, aspect fit/fill (16:9, 9:16, 1:1), volume, fades, metadata removal, filename | ✅ Implemented & tested | Never stretches unless "Preserve aspect ratio" is turned off. Visual trim with a local video preview. |
| Standard frame-rate conversion | ✅ Implemented & tested | Labeled as not making motion smoother. |
| Motion interpolation 60 / 120 FPS (CPU, FFmpeg `minterpolate`) | ✅ Implemented & tested | Motion-compensated, with scene-change detection. Proven to generate new frames (see results). Slow; limits are configurable. |
| Motion interpolation (GPU, RIFE via `rife-ncnn-vulkan`) | ⚙️ Implemented, **not installed here** | Enabled when `OC_RIFE_NCNN_PATH` points to the binary and the startup self-test passes. Scene-split processing. Not exercised in this environment. |
| Resolution upscale, standard (Lanczos) | ✅ Implemented & tested | Labeled "upscaled (not native)". |
| AI upscaling (Real-ESRGAN via `realesrgan-ncnn-vulkan`) | ⚙️ Implemented, **not installed here** | Enabled by `OC_REALESRGAN_NCNN_PATH` plus the self-test. Otherwise shown as unavailable with the reason. |
| Preview clip for expensive enhancements | ✅ Implemented & tested | 4 s by default (`OC_PREVIEW_SECONDS`). |
| Batch: per-item settings, apply to all, per-item status, retry, ZIP download | ✅ Implemented & tested | Failed items never block successful downloads; the ZIP is streamed to disk, not memory. |
| Chunked, resumable, parallel uploads with per-file progress | ✅ Implemented & tested (local storage) | Chunks are written at their offset; resuming sends only missing chunks. |
| Direct-to-S3 uploads (presigned multipart) and presigned downloads | ⚙️ Implemented, **not tested** | Needs an S3/MinIO bucket with CORS that exposes `ETag`. |
| Postgres | ⚙️ Implemented, **not tested here** | The schema and migration are dialect-neutral; tests ran on SQLite. |
| Direct media links | ✅ Supported, tested live | |
| YouTube | 🟡 Limited | Analysis and download of a public CC-BY video verified working. YouTube frequently bot-checks server IPs, and we never use cookies. |
| Vimeo | 🟡 Limited | Its web client currently requires login for many videos (verified); these are reported as "requires access". |
| Instagram, Facebook, TikTok, X, Dailymotion, SoundCloud, Reddit | 🟡 Limited | Adapters implemented; public content only. Not live-tested; platforms change often. |
| "Temporarily unavailable" source status | ✅ Implemented | Switches automatically after repeated upstream failures. |
| Accounts | Not needed | Anonymous sessions only, by design. |

Unsupported by design: DRM-protected, private, login-only, paywalled, age-gated and region-locked content, and live streams.

## API

| Method & path | Purpose |
|---|---|
| `GET /api/capabilities` | Formats, codecs, enhancement availability and reasons, source statuses, limits |
| `GET /api/health` | DB, FFmpeg, queue depth, overload flag, free disk |
| `POST /api/analyze` | Analyze a URL → items, qualities, streams, size estimates |
| `POST /api/analyze/{id}/entries/{entry}/resolve` | Fetch full details for one collection item |
| `POST /api/uploads` | Initiate an upload (returns chunk size and transport) |
| `PUT /api/uploads/{id}/chunks/{n}` · `POST /api/uploads/{id}/parts/{n}/url` | Upload a chunk (API) or get a presigned part URL (S3) |
| `GET /api/uploads/{id}` · `POST …/complete` · `DELETE …` | Resume state · finish and inspect content · discard |
| `POST /api/plan` | Validate settings and return the summary, stages and estimates |
| `POST /api/jobs` | Create a batch of jobs (`idempotency_key` supported) |
| `GET /api/jobs` · `GET /api/jobs/{id}` | Status |
| `POST /api/jobs/{id}/cancel` · `…/retry` · `DELETE /api/jobs/{id}` | Cancel · bounded retry · delete now |
| `POST /api/jobs/{id}/download-link` · `POST /api/batches/{id}/zip-link` · `GET /api/download?t=` | Signed, session-bound download access |
| `POST /api/reports` | Abuse or copyright report |

Job states: `pending · analyzing · queued · processing · completed · failed · cancelled · expired`. Stages: `queued · analyzing · fetching · converting · interpolating · enhancing · finalizing · ready`. Each job lists only the stages that apply to it.

## Security & privacy

- **SSRF:** user URLs are validated (scheme, credentials, ports 80/443 only, IP literals including decimal/hex/IPv6-mapped/6to4 forms, every resolved address). *All* outbound traffic from analysis and fetching, including redirects and media segment requests, is forced through an in-process egress proxy ([`egress_proxy.py`](backend/omniconvert/security/egress_proxy.py)). The proxy resolves DNS once, rejects private, loopback, link-local, CGNAT and metadata ranges, and connects to the IP it just checked, which defeats DNS rebinding.
- **FFmpeg as an SSRF vector:** every input is opened with `-protocol_whitelist file,pipe`, stored without its extension, and its detected demuxer must be in an allowlist. A test confirms that an HLS playlist disguised as `.mp4` and pointing at `169.254.169.254` is rejected.
- **No access-control bypass:** no cookies, credentials or tokens are ever used, `geo_bypass` is off, DRM formats are never selected, and each adapter is limited to its own extractors.
- **Process safety:** commands are built as argument arrays only. Children have resource limits (`RLIMIT_CPU`, `RLIMIT_AS` and `PR_SET_PDEATHSIG` on Linux; a kill-on-close Job Object with a memory cap on Windows), so a crashed worker can't leave orphaned FFmpeg processes behind. Jobs have timeouts, and each attempt gets its own scratch directory, which is deleted afterwards.
- **Access isolation:** anonymous HttpOnly session cookie (only a SHA-256 of the token is stored). Every lookup is scoped to the session, and other sessions get a 404. Download tokens are HMAC-signed, expire after 15 minutes, and are bound to both the job and the session. A custom header is required on state-changing requests (CSRF).
- **Abuse limits:** per-IP rate limits, per-session active-job cap, global queue-depth cap (returns `server_busy`), and size, duration, batch and enhancement limits.
- **Retention:** uploads and outputs expire after `OC_FILE_TTL_SECONDS` (6 h by default). "Delete now" removes the output, the unused source and the job record. Expired records are purged a day later. Logs are structured JSON and URLs in them are redacted to scheme, host and path.

## Testing & results

All tests use real FFmpeg/FFprobe, a real (temporary) database and storage, the real API and the real worker code. Sample media is synthetic and generated by FFmpeg. Live tests use the CC-BY *Sintel* trailer hosted by the W3C.

```bash
cd backend
pytest -q                     # offline suite
OC_LIVE_TESTS=1 pytest -q     # + live network tests (direct link analyze/convert, redirect-to-metadata blocking)
```

**Result (2026-11-13, Windows 11, Python 3.12, FFmpeg 8.1.2): 61 passed, 1 skipped.** The skip is RIFE, which isn't installed.

| Area | Covered by |
|---|---|
| Video → MP3 (192 kbps verified), video → MP4 (H.264/yuv420p/AAC), remux without re-encoding | `test_conversions.py` |
| Audio conversions FLAC/OGG/M4A (48 kHz mono)/WAV 24-bit/MP3 64 & 320 kbps | `test_conversions.py` |
| Resolution selection (854×480), upscale labeling, AI upscale refused when not installed | `test_conversions.py` |
| Trim 1.0–3.5 s → 2.5 s with A/V in sync, fades, volume, metadata strip | `test_conversions.py` |
| Crop + rotate, 9:16 fit, stretch refused, WEBM/ProRes/HEVC/AV1/GIF, loudnorm | `test_conversions.py` |
| Invalid combinations, content-based upload validation (fake MP4, HLS SSRF payload, .exe, oversize) | `test_conversions.py` |
| Batch with mixed success/failure + ZIP, idempotency, cancel (running and queued), expiry, delete now | `test_jobs.py` |
| Download isolation across sessions, tampered tokens, CSRF | `test_jobs.py` |
| Lease expiry → re-queue → bounded failure; **real worker process hard-killed mid-interpolation, then recovered by a fresh worker** | `test_jobs.py` |
| SSRF: 17 blocked URL forms, egress proxy refusals, API refusals, live redirect to the metadata IP blocked | `test_security.py`, `test_live_sources.py` |
| Live direct-link analyze → MP3 and 360p MP4 conversions | `test_live_sources.py` |

**Interpolation proof.** A file reporting 60 FPS proves nothing, so the tests decode every output frame, hash it, and compare against the source frames, using a 24 FPS clip with a moving box:

| Output | Frames | Unique frames | Frames not present in source |
|---|---|---|---|
| Standard conversion 24→60 (control) | 120 | 44% | — (duplicates) |
| Interpolated 60 FPS (minterpolate) | 116 | **100%** | **96.6%** |
| Interpolated 120 FPS (minterpolate) | 231 | **96.1%** | **99.1%** |

Duration (2.0 s) and A/V sync are preserved in each case.

**Browser end-to-end check (Playwright, production build):** upload → MP3 → download (193 KB = 8 s × 192 kbps); 60 FPS interpolation preview; direct link → 360p MP4; unsupported-link and SSRF error states; light theme; 390 px mobile layout with no horizontal scroll. The only console errors were the expected 422 responses from the two rejected links.

Also verified manually: YouTube analysis plus a fetch/convert of a public CC-BY Blender film; Vimeo correctly reported as "requires access".

## Hardware requirements for enhancements

| Feature | Requirement | Guidance |
|---|---|---|
| Conversion, CPU interpolation | Any x86-64/ARM64 CPU; ~2 GB RAM per concurrent job | `minterpolate` runs well below real time: roughly 1–10 fps at 720p–1080p on desktop CPUs, and 120 FPS is about twice as slow. Defaults cap it at 1080p, 10 min (60 FPS) and 2 min (120 FPS). |
| RIFE interpolation | Vulkan-capable GPU (NVIDIA/AMD/Intel), ≥4 GB VRAM for 1080p; [rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan) (MIT) with `rife-v4.x` models | Uses PNG frames on disk. Budget about 6 MB per 1080p frame × (input + output frames). `OC_WORKER_MIN_FREE_DISK_BYTES` and a pre-check reject jobs that wouldn't fit. |
| AI upscaling | Same GPU class; [realesrgan-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN) (BSD-3) | Defaults: source ≤1080p, ≤300 s. |
| Scratch disk | Fast local SSD | Several GB per concurrent GPU job. |

To enable the GPU tools natively, download the release archives, unzip them (keep the model folders next to the executables), and set `OC_RIFE_NCNN_PATH` / `OC_REALESRGAN_NCNN_PATH`. Restart the worker; `/supported` then shows whether the self-test passed.

## Production checklist / external infrastructure

- [ ] `OC_ENV=production`, a strong `OC_SECRET_KEY`, `OC_COOKIE_SECURE=true`, HTTPS at the edge.
- [ ] PostgreSQL (managed or the Compose service) and `alembic upgrade head` on deploy.
- [ ] Shared storage between API and workers: a shared volume, or S3/MinIO (`OC_STORAGE_BACKEND=s3`) with bucket CORS allowing `PUT` from your origin and exposing `ETag`, plus a lifecycle rule as a backstop for expiry.
- [ ] Route `/api` from your load balancer to the API (or keep the Next.js rewrite locally). For a Vercel website, set `NEXT_PUBLIC_API_ORIGIN`, `OC_PUBLIC_API_BASE`, `OC_CORS_ORIGINS` / `OC_CORS_ORIGIN_REGEX`, and `OC_TRUST_PROXY=1` only behind a trusted proxy.
- [ ] Run separate CPU and GPU worker pools (`--queues cpu` / `--queues gpu`) sized to your hardware.
- [ ] Shared rate limiting (Redis or the load balancer) when running more than one API replica.
- [ ] Restrict worker egress at the network level as defense in depth (allow only 80/443 to the internet; deny the metadata service and internal ranges).
- [ ] Keep FFmpeg and **yt-dlp** updated; platforms change often. Re-run `OC_LIVE_TESTS=1 pytest`.
- [ ] Set `OC_ABUSE_CONTACT_EMAIL`, then review the Privacy and Terms pages with your legal counsel. They describe software behavior only; no legal assurances are invented.
- [ ] Ship logs (JSON on stdout) and monitor `/api/health` (`queued`, `processing`, `overloaded`, `free_disk_bytes`).

## Known limitations

- The rate limiter is in-process per API instance (the DB-backed per-session and queue-depth caps are global).
- Docker images, Postgres, S3 transport, and the RIFE / Real-ESRGAN pipelines are implemented but weren't executed in the development environment. Everything else listed as tested was run for real.
- Processing-time estimates appear only after at least 3 similar jobs have completed on the server, by design.
- URL sources are downloaded in full (within the size limit) before trimming; uploaded files are trimmed with input seeking, so only the needed range is decoded.
- The ZIP is assembled as a temporary file on disk before streaming, so very large batches take a moment before the download starts.
