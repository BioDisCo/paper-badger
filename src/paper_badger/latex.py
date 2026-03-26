from __future__ import annotations

import logging
import re
from collections.abc import Iterator  # noqa: TC003
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


ENV_KIND_MAP = {
    "definition": "definition",
    "def": "definition",
    "notation": "notation",
    "lemma": "lemma",
    "lem": "lemma",
    "proposition": "proposition",
    "prop": "proposition",
    "corollary": "corollary",
    "cor": "corollary",
    "theorem": "theorem",
    "thm": "theorem",
}

THEOREM_PRIORITY = {
    "definition": 0,
    "notation": 1,
    "lemma": 10,
    "proposition": 11,
    "corollary": 12,
    "theorem": 13,
}

BEGIN_RE = re.compile(r"\\begin\{(?P<env>[A-Za-z*]+)\}(?P<opt>\[[^\]]*\])?", re.MULTILINE)
LABEL_RE = re.compile(r"\\label\{(?P<label>[^}]+)\}")
BADGE_RE = re.compile(r"\\lean(?:proof|formalized)\{[^}]+\}")
TITLE_RE = re.compile(r"\\title\{(?P<title>[^}]*)\}")


@dataclass
class ExtractedStatement:
    task_id: str
    tex_path: str
    env_name: str
    label: str
    title: str | None
    sequence_index: int
    kind: str
    source_excerpt: str


@dataclass
class LocatedStatement:
    extracted: ExtractedStatement
    begin_start: int
    begin_end: int
    begin_text: str


def detect_main_tex(tex_files: list[Path]) -> Path:
    document_candidates: list[tuple[int, Path]] = []
    for path in tex_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "\\documentclass" in text and "\\begin{document}" in text:
            document_candidates.append((len(text), path))
    if document_candidates:
        return max(document_candidates, key=lambda item: item[0])[1]
    if not tex_files:
        raise RuntimeError("no .tex files found in paper source")
    return max(tex_files, key=lambda path: path.stat().st_size)


def find_tex_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.tex") if path.is_file())


def extract_statements_from_tree(paper_dir: Path) -> list[ExtractedStatement]:
    extracted: list[ExtractedStatement] = []
    for tex_path in find_tex_files(paper_dir):
        text = tex_path.read_text(encoding="utf-8", errors="ignore")
        text = _document_body(text)
        extracted.extend(_extract_statements_from_text(text, tex_path.relative_to(paper_dir)))
    extracted.sort(key=_task_sort_key)
    return extracted


def locate_statement(
    paper_dir: Path,
    task_id: str,
    tex_path: str,
    sequence_index: int,  # noqa: ARG001
) -> LocatedStatement:
    path = paper_dir / tex_path
    full_text = path.read_text(encoding="utf-8", errors="ignore")
    text, body_offset = _document_body_with_offset(full_text)
    statements = list(_iter_statement_matches(text, Path(tex_path), body_offset=body_offset))
    for match in statements:
        if match.extracted.task_id == task_id:
            return match
    raise RuntimeError(f"statement identity drifted for {task_id}")


def ensure_verified_badges_package(main_tex_path: Path) -> bool:
    text = main_tex_path.read_text(encoding="utf-8", errors="ignore")
    if "\\usepackage{verified-badges}" in text:
        return False
    marker = "\\begin{document}"
    idx = text.find(marker)
    if idx == -1:
        raise RuntimeError(f"failed to find {marker} in {main_tex_path}")
    insertion = "\\usepackage{verified-badges}\n"
    text = text[:idx] + insertion + text[idx:]
    main_tex_path.write_text(text, encoding="utf-8")
    return True


def insert_badge_for_task(paper_dir: Path, task_id: str, tex_path: str, sequence_index: int, badge_macro: str) -> bool:
    return set_badge_for_task(paper_dir, task_id, tex_path, sequence_index, badge_macro)


def set_badge_for_task(
    paper_dir: Path,
    task_id: str,
    tex_path: str,
    sequence_index: int,
    badge_macro: str | None,
) -> bool:
    location = locate_statement(paper_dir, task_id, tex_path, sequence_index)
    new_begin = _set_badge_macro(location.begin_text, badge_macro)
    if new_begin == location.begin_text:
        return False
    path = paper_dir / tex_path
    text = path.read_text(encoding="utf-8", errors="ignore")
    text = text[: location.begin_start] + new_begin + text[location.begin_end :]
    path.write_text(text, encoding="utf-8")
    return True


def statement_context_summary(paper_dir: Path, tex_path: str, sequence_index: int, max_previous: int = 2) -> str:
    path = paper_dir / tex_path
    full_text = path.read_text(encoding="utf-8", errors="ignore")
    body, _ = _document_body_with_offset(full_text)
    statements = list(_iter_statement_matches(body, Path(tex_path)))
    position = next(
        (index for index, item in enumerate(statements) if item.extracted.sequence_index == sequence_index), None
    )
    if position is None:
        return ""
    pieces: list[str] = []
    title_match = TITLE_RE.search(full_text)
    if title_match:
        title = _shorten_whitespace(title_match.group("title"))
        if title:
            pieces.append(f"Paper title: {title}")
    start_index = max(0, position - max_previous)
    previous = statements[start_index:position]
    if previous:
        pieces.append("Earlier statements in the same file:")
        for item in previous:
            extracted = item.extracted
            heading = f"{extracted.kind} `{extracted.label}`"
            if extracted.title:
                heading += f" ({extracted.title})"
            pieces.append(f"- {heading}: {extracted.source_excerpt}")
    return "\n".join(pieces)


def _extract_statements_from_text(text: str, tex_path: Path) -> list[ExtractedStatement]:
    return [location.extracted for location in _iter_statement_matches(text, tex_path)]


def _iter_statement_matches(text: str, tex_path: Path, body_offset: int = 0) -> Iterator[LocatedStatement]:
    sequence_index = 0
    for match in BEGIN_RE.finditer(text):
        raw_env = match.group("env")
        env_name = raw_env.rstrip("*").lower()
        kind = ENV_KIND_MAP.get(env_name)
        if kind is None:
            continue
        end_tag = f"\\end{{{raw_env}}}"
        end_idx = text.find(end_tag, match.end())
        if end_idx == -1:
            continue
        body = text[match.end() : end_idx]
        body_without_comments = _strip_comments(body)
        title = _normalize_title(match.group("opt"))
        label_match = LABEL_RE.search(body)
        label = label_match.group("label") if label_match else _synthetic_label(env_name, title, sequence_index)
        excerpt = _shorten_whitespace(body_without_comments)
        excerpt_without_label = _shorten_whitespace(LABEL_RE.sub("", body_without_comments))
        if not excerpt_without_label:
            sequence_index += 1
            continue
        task_id = f"{tex_path.as_posix()}::{sequence_index}::{label}"
        yield LocatedStatement(
            extracted=ExtractedStatement(
                task_id=task_id,
                tex_path=tex_path.as_posix(),
                env_name=raw_env,
                label=label,
                title=title,
                sequence_index=sequence_index,
                kind=kind,
                source_excerpt=excerpt[:1600],
            ),
            begin_start=body_offset + match.start(),
            begin_end=body_offset + match.end(),
            begin_text=match.group(0),
        )
        sequence_index += 1


def _normalize_title(optional_arg: str | None) -> str | None:
    if optional_arg is None:
        return None
    stripped = optional_arg.strip()[1:-1].strip()
    stripped = BADGE_RE.sub("", stripped).strip()
    return stripped or None


def _synthetic_label(env_name: str, title: str | None, sequence_index: int) -> str:
    stem = _slugify(title or f"{env_name}-{sequence_index}")
    return f"synthetic:{env_name}:{stem}"


def _slugify(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"\\[A-Za-z]+", "", lowered)
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "item"


def sanitize_label_stem(label: str) -> str:
    return _slugify(label.replace(":", "-"))


def _task_sort_key(item: ExtractedStatement) -> tuple[int, int, str, int]:
    synthetic_penalty = 1 if item.label.startswith("synthetic:") else 0
    return (synthetic_penalty, THEOREM_PRIORITY.get(item.kind, 100), item.tex_path, item.sequence_index)


def _strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


def _shorten_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _set_badge_macro(begin_text: str, badge_macro: str | None) -> str:
    optional_match = re.search(r"\[(?P<content>[^\]]*)\]$", begin_text)
    if optional_match is None:
        if badge_macro is None:
            return begin_text
        return begin_text + f"[{badge_macro}]"

    prefix = begin_text[: optional_match.start()]
    content = optional_match.group("content")
    content = BADGE_RE.sub("", content)
    content = re.sub(r"\s+", " ", content).strip()
    if badge_macro is not None:
        content = f"{content} {badge_macro}".strip()
    if not content:
        return prefix
    return f"{prefix}[{content}]"


def _document_body(text: str) -> str:
    return _document_body_with_offset(text)[0]


def _document_body_with_offset(text: str) -> tuple[str, int]:
    start = text.find(r"\begin{document}")
    if start == -1:
        logger.warning("no \\begin{document} found; scanning entire file for statements")
        return text, 0
    start += len(r"\begin{document}")
    end = text.find(r"\end{document}", start)
    if end == -1:
        end = len(text)
    return text[start:end], start
