"""Blob storage behind the API: a local folder (tests) or an S3 server (production: SeaweedFS)."""

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from raceforge.backend.settings import Settings

CHUNK = 1024 * 1024


class BlobStore(Protocol):
    def put_file(self, key: str, path: Path) -> None: ...
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def exists(self, key: str) -> bool: ...
    def read(self, key: str, start: int = 0, length: int | None = None) -> Iterator[bytes]: ...
    def delete(self, key: str) -> None: ...
    def ping(self) -> bool: ...


class FsBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if ".." in key.split("/"):
            raise ValueError("bad key")
        return self.root / key

    def put_file(self, key: str, path: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)

    def put_bytes(self, key: str, data: bytes) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def read(self, key: str, start: int = 0, length: int | None = None) -> Iterator[bytes]:
        with self._path(key).open("rb") as f:
            f.seek(start)
            left = length
            while left is None or left > 0:
                chunk = f.read(CHUNK if left is None else min(CHUNK, left))
                if not chunk:
                    return
                if left is not None:
                    left -= len(chunk)
                yield chunk

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def ping(self) -> bool:
        return self.root.is_dir()


class S3BlobStore:
    def __init__(self, settings: Settings) -> None:
        from minio import Minio

        self.bucket = settings.s3_bucket
        self.client = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put_file(self, key: str, path: Path) -> None:
        self.client.fput_object(self.bucket, key, str(path))

    def put_bytes(self, key: str, data: bytes) -> None:
        import io

        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data))

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        try:
            self.client.stat_object(self.bucket, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return False
            raise
        return True

    def read(self, key: str, start: int = 0, length: int | None = None) -> Iterator[bytes]:
        resp = self.client.get_object(self.bucket, key, offset=start, length=length or 0)
        try:
            yield from resp.stream(CHUNK)
        finally:
            resp.close()
            resp.release_conn()

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)

    def ping(self) -> bool:
        try:
            return self.client.bucket_exists(self.bucket)
        except Exception:
            return False


def make_store(settings: Settings) -> BlobStore:
    if settings.blob_backend == "s3":
        return S3BlobStore(settings)
    return FsBlobStore(settings.blob_dir)


def blob_key(sha: str) -> str:
    return f"blobs/{sha[:2]}/{sha}"


def part_key(upload_id: str, number: int) -> str:
    return f"uploads/{upload_id}/{number:06d}"
