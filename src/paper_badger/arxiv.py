from __future__ import annotations

import gzip
import io
import shutil
import tarfile
import urllib.error
import urllib.request
from pathlib import Path  # noqa: TC003

ARXIV_SOURCE_URLS = (
    "https://export.arxiv.org/e-print/{arxiv_id}",
    "https://arxiv.org/e-print/{arxiv_id}",
    "https://export.arxiv.org/src/{arxiv_id}",
    "https://arxiv.org/src/{arxiv_id}",
)


def download_and_extract_arxiv_source(arxiv_id: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _download_source_payload(arxiv_id)
    _extract_payload(payload, output_dir)
    return output_dir


def _download_source_payload(arxiv_id: str) -> bytes:
    headers = {"User-Agent": "paper-badger/0.1 (+https://github.com/BioDisCo/paper-badger)"}
    errors: list[str] = []
    for template in ARXIV_SOURCE_URLS:
        url = template.format(arxiv_id=arxiv_id)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                payload: bytes = response.read()
                if not payload:
                    errors.append(f"{url}: empty response")
                    continue
                if _looks_like_html(payload):
                    errors.append(f"{url}: returned HTML instead of source")
                    continue
                return payload
        except urllib.error.URLError as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("failed to download arXiv source:\n" + "\n".join(errors))


def _extract_payload(payload: bytes, output_dir: Path) -> None:
    if _is_tar_archive(payload):
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            _safe_extract_tar(archive, output_dir)
        return
    if _is_gzip_stream(payload):
        decompressed = gzip.decompress(payload)
        if _is_tar_archive(decompressed):
            with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:*") as archive:
                _safe_extract_tar(archive, output_dir)
            return
        (output_dir / "main.tex").write_bytes(decompressed)
        return
    (output_dir / "main.tex").write_bytes(payload)


def _looks_like_html(payload: bytes) -> bool:
    head = payload[:512].lower()
    return b"<html" in head or b"<!doctype html" in head


def _is_tar_archive(payload: bytes) -> bool:
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*"):
            return True
    except tarfile.TarError:
        return False


def _is_gzip_stream(payload: bytes) -> bool:
    return payload[:2] == b"\x1f\x8b"


def _safe_extract_tar(archive: tarfile.TarFile, output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    for member in archive.getmembers():
        if member.issym() or member.islnk():
            raise RuntimeError(f"unsafe archive member (symlink): {member.name}")
        member_path = (output_dir / member.name).resolve()
        if not member_path.is_relative_to(output_dir):
            raise RuntimeError(f"unsafe archive member: {member.name}")
    archive.extractall(output_dir)


def wipe_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
