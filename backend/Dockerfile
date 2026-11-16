# OmniConvert API + CPU worker image.
# Same image runs either role:  api  |  worker  |  migrate
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# FFmpeg from Debian (includes libx264/libx265/libvpx/libaom/libopus/libvorbis/libmp3lame
# and the minterpolate filter). Capabilities are detected at runtime, so a custom
# FFmpeg build can be dropped in without code changes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 omni
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN mkdir -p /data/storage /data/scratch && chown -R omni:omni /data /app

USER omni
ENV OC_STORAGE_ROOT=/data/storage OC_SCRATCH_ROOT=/data/scratch
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
CMD ["api"]
