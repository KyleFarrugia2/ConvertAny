"""Central configuration. Every limit is overridable through environment variables
(prefix ``OC_``) so operators can tune resource use without code changes."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OC_", env_file=str(BASE_DIR / ".env"), extra="ignore")

    # --- core -----------------------------------------------------------
    env: str = "development"
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'omniconvert.db').as_posix()}"
    secret_key: str = ""  # if empty, a key is generated once and persisted to data/secret.key
    public_api_base: str = ""  # optional absolute base used when building download links
    cookie_secure: bool = False

    # --- storage --------------------------------------------------------
    storage_backend: str = "local"  # local | s3
    storage_root: Path = BASE_DIR / "data" / "storage"
    scratch_root: Path = BASE_DIR / "data" / "scratch"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_endpoint_url: str = ""  # endpoint reachable by browsers for presigned URLs

    # --- tools ----------------------------------------------------------
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    rife_ncnn_path: str = ""  # path to rife-ncnn-vulkan executable (optional)
    rife_model: str = "rife-v4.6"
    realesrgan_ncnn_path: str = ""  # path to realesrgan-ncnn-vulkan executable (optional)
    realesrgan_model: str = "realesr-animevideov3"

    # --- limits ---------------------------------------------------------
    max_upload_bytes: int = 2 * 1024**3
    max_url_download_bytes: int = 2 * 1024**3
    max_duration_seconds: int = 3 * 3600
    max_gif_seconds: int = 30
    upload_chunk_bytes: int = 8 * 1024**2
    max_files_per_batch: int = 20
    max_url_items_per_batch: int = 10
    max_active_jobs_per_session: int = 25
    max_queue_depth: int = 500
    job_timeout_seconds: int = 3 * 3600
    file_ttl_seconds: int = 6 * 3600
    upload_ttl_seconds: int = 6 * 3600
    max_manual_retries: int = 3
    max_auto_attempts: int = 3
    analysis_timeout_seconds: int = 45
    max_output_height: int = 2160

    # motion interpolation limits (CPU minterpolate is slow; keep defaults modest)
    interp_cpu_max_height: int = 1080
    interp_cpu_max_seconds_60: int = 600
    interp_cpu_max_seconds_120: int = 120
    interp_gpu_max_height: int = 2160
    interp_gpu_max_seconds_60: int = 1800
    interp_gpu_max_seconds_120: int = 600
    enable_120fps: bool = True
    upscale_ai_max_input_height: int = 1080
    upscale_ai_max_seconds: int = 300
    preview_seconds: int = 4

    # --- rate limiting --------------------------------------------------
    rate_analyze_per_min: int = 20
    rate_upload_per_min: int = 60
    rate_jobs_per_min: int = 40
    rate_general_per_min: int = 600

    # --- workers --------------------------------------------------------
    worker_queues: str = "cpu"  # comma separated: cpu,gpu
    worker_concurrency: int = 2
    worker_lease_seconds: int = 60
    worker_poll_seconds: float = 1.0
    worker_cpu_seconds_limit: int = 4 * 3600  # RLIMIT_CPU for ffmpeg (POSIX only)
    worker_memory_limit_bytes: int = 8 * 1024**3  # RLIMIT_AS for ffmpeg (POSIX only)
    worker_min_free_disk_bytes: int = 2 * 1024**3
    cleanup_interval_seconds: int = 120

    # --- egress ---------------------------------------------------------
    egress_allowed_ports: str = "80,443"
    egress_extra_blocked_cidrs: str = ""
    egress_upstream_proxy: str = ""  # reserved; not used for media fetches

    # --- product --------------------------------------------------------
    abuse_contact_email: str = ""

    @property
    def allowed_ports(self) -> set[int]:
        return {int(p) for p in self.egress_allowed_ports.split(",") if p.strip()}

    @property
    def queues(self) -> list[str]:
        return [q.strip() for q in self.worker_queues.split(",") if q.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.storage_root.mkdir(parents=True, exist_ok=True)
    s.scratch_root.mkdir(parents=True, exist_ok=True)
    if s.database_url.startswith("sqlite:///"):
        Path(s.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    if not s.secret_key:
        if s.env == "production":
            raise RuntimeError("OC_SECRET_KEY must be set in production")
        key_file = BASE_DIR / "data" / "secret.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        if not key_file.exists():
            key_file.write_text(secrets.token_hex(32))
        s.secret_key = key_file.read_text().strip()
    return s
