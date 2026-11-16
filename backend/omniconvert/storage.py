"""Storage abstraction: local filesystem (default) or S3-compatible object storage.

Keys look like ``uploads/<id>/source`` and ``outputs/<job-id>/<filename>``. Nothing
here reads whole media files into memory: copies are streamed in fixed-size blocks.
"""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from .config import get_settings

BLOCK = 1024 * 1024


def _safe_key(key: str) -> str:
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        raise ValueError("empty storage key")
    return "/".join(parts)


class LocalStorage:
    kind = "local"

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        p = (self.root / _safe_key(key)).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("key escapes storage root")
        return p

    # chunked uploads -------------------------------------------------------
    # Chunks are written straight into the final file at their offset, so
    # completing an upload needs no reassembly copy and chunks may arrive in
    # any order (resumable / parallel).
    def create_sparse(self, key: str, size: int) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as fh:
            fh.truncate(size)

    async def write_chunk_at(self, key: str, offset: int, stream, expected: int) -> int:
        p = self.path(key)
        written = 0
        with open(p, "r+b") as fh:
            fh.seek(offset)
            async for block in stream:
                written += len(block)
                if written > expected:
                    raise ValueError("chunk larger than declared")
                fh.write(block)
        if written != expected:
            raise ValueError("chunk size mismatch")
        return written

    # objects -----------------------------------------------------------------
    def put_file(self, key: str, src: Path) -> None:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dest)
        except OSError:
            shutil.copyfile(src, dest)

    @contextmanager
    def local_copy(self, key: str, scratch: Path) -> Iterator[Path]:
        """Yield a local filesystem path for a stored object."""
        yield self.path(key)

    def open(self, key: str) -> BinaryIO:
        return open(self.path(key), "rb")

    def size(self, key: str) -> int:
        return self.path(key).stat().st_size

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def delete_prefix(self, prefix: str) -> None:
        p = self.path(prefix)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink(missing_ok=True)

    def presigned_get(self, key: str, filename: str, expires: int) -> Optional[str]:
        return None  # served by the API instead

    def presigned_part(self, key: str, upload_id: str, part: int, expires: int) -> Optional[str]:
        return None


class S3Storage:
    """S3-compatible backend. Uploads go direct-to-bucket with presigned multipart
    part URLs; downloads are served via short-lived presigned GETs."""

    kind = "s3"

    def __init__(self):
        import boto3  # imported lazily so local mode needs no AWS deps
        from botocore.config import Config

        s = get_settings()
        self.bucket = s.s3_bucket
        common = dict(
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id or None,
            aws_secret_access_key=s.s3_secret_access_key or None,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self.client = boto3.client("s3", endpoint_url=s.s3_endpoint_url or None, **common)
        self.public_client = boto3.client("s3", endpoint_url=(s.s3_public_endpoint_url or s.s3_endpoint_url) or None, **common)

    def create_multipart(self, key: str) -> str:
        return self.client.create_multipart_upload(Bucket=self.bucket, Key=_safe_key(key))["UploadId"]

    def presigned_part(self, key: str, upload_id: str, part: int, expires: int) -> Optional[str]:
        return self.public_client.generate_presigned_url(
            "upload_part",
            Params={"Bucket": self.bucket, "Key": _safe_key(key), "UploadId": upload_id, "PartNumber": part + 1},
            ExpiresIn=expires,
        )

    def complete_multipart(self, key: str, upload_id: str, etags: list[str]) -> int:
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=_safe_key(key),
            UploadId=upload_id,
            MultipartUpload={"Parts": [{"ETag": e, "PartNumber": i + 1} for i, e in enumerate(etags)]},
        )
        return self.size(key)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=_safe_key(key), UploadId=upload_id)
        except Exception:
            pass

    def put_file(self, key: str, src: Path) -> None:
        self.client.upload_file(str(src), self.bucket, _safe_key(key))
        Path(src).unlink(missing_ok=True)

    @contextmanager
    def local_copy(self, key: str, scratch: Path) -> Iterator[Path]:
        scratch.mkdir(parents=True, exist_ok=True)
        dest = scratch / "source.bin"
        self.client.download_file(self.bucket, _safe_key(key), str(dest))
        try:
            yield dest
        finally:
            dest.unlink(missing_ok=True)

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"]

    def size(self, key: str) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))["ContentLength"])

    def exists(self, key: str) -> bool:
        try:
            self.size(key)
            return True
        except Exception:
            return False

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=_safe_key(prefix)):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})

    def presigned_get(self, key: str, filename: str, expires: int) -> Optional[str]:
        from urllib.parse import quote

        return self.public_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": _safe_key(key),
                "ResponseContentDisposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            },
            ExpiresIn=expires,
        )


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        s = get_settings()
        _storage = S3Storage() if s.storage_backend == "s3" else LocalStorage(s.storage_root)
    return _storage
