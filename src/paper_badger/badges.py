from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

VERIFIED_BADGES_REPO_URL = "https://github.com/BioDisCo/verified-badges.git"


def ensure_verified_badges_repo(cache_root: Path, provided_repo: Path | None = None) -> Path:
    if provided_repo is not None:
        return provided_repo
    repo_dir = cache_root / "verified-badges"
    if repo_dir.exists():
        return repo_dir
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", VERIFIED_BADGES_REPO_URL, str(repo_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    return repo_dir


def copy_verified_badges_assets(repo_dir: Path, paper_dir: Path) -> None:
    shutil.copy2(repo_dir / "verified-badges.sty", paper_dir / "verified-badges.sty")
    src_badges = repo_dir / "badges"
    dst_badges = paper_dir / "badges"
    if dst_badges.exists():
        shutil.rmtree(dst_badges)
    shutil.copytree(src_badges, dst_badges)


def infer_repo_url_and_branch(start_dir: Path) -> tuple[str | None, str | None]:
    root = _git_stdout(["git", "rev-parse", "--show-toplevel"], start_dir)
    if root is None:
        return None, None
    root_path = Path(root)
    remote = _git_stdout(["git", "remote", "get-url", "origin"], root_path)
    branch = _git_stdout(["git", "rev-parse", "--abbrev-ref", "HEAD"], root_path)
    if remote is None:
        return None, branch
    return _normalize_github_remote(remote), branch


def build_blob_url(repo_url: str | None, branch: str | None, absolute_path: Path) -> str | None:
    if repo_url is None or branch is None:
        return None
    root = _git_stdout(["git", "rev-parse", "--show-toplevel"], absolute_path.parent)
    if root is None:
        return None
    relative = absolute_path.resolve().relative_to(Path(root).resolve()).as_posix()
    return f"{repo_url}/blob/{branch}/{relative}"


def build_local_file_target(paper_dir: Path, absolute_path: Path) -> str:  # noqa: ARG001
    return absolute_path.resolve().as_uri()


def _git_stdout(cmd: list[str], cwd: Path) -> str | None:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _normalize_github_remote(remote: str) -> str:
    trimmed = remote.strip()
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    if trimmed.startswith("git@github.com:"):
        return "https://github.com/" + trimmed.split(":", 1)[1]
    return trimmed
