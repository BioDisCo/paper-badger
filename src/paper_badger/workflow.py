from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .agents import (
    AgentInvocationError,
    AgentRunner,
    AgentTimeoutError,
    AgentTransportError,
)
from .arxiv import download_and_extract_arxiv_source
from .badges import (
    build_blob_url,
    build_local_file_target,
    copy_verified_badges_assets,
    ensure_verified_badges_repo,
    infer_repo_url_and_branch,
)
from .latex import (
    detect_main_tex,
    ensure_verified_badges_package,
    extract_statements_from_tree,
    insert_badge_for_task,
    set_badge_for_task,
)
from .models import RunState, StatementTask


def run_formalization(
    arxiv_id: str,
    base_dir: Path,
    local_paper_dir: Path | None,
    repo_url: str | None,
    branch: str | None,
    prover_backend: str,
    verifier_backend: str,
    badge_link_mode: str,
    verified_badges_repo: Path | None,
    agent_timeout_seconds: int | None,
    codex_model: str | None,
    claude_model: str | None,
    max_attempts_per_task: int | None,
    max_tasks: int | None,
) -> RunState:
    run_dir = base_dir / normalize_arxiv_id(arxiv_id)
    state = _load_or_initialize_state(
        arxiv_id=arxiv_id,
        run_dir=run_dir,
        local_paper_dir=local_paper_dir,
        repo_url=repo_url,
        branch=branch,
        prover_backend=prover_backend,
        verifier_backend=verifier_backend,
        badge_link_mode=badge_link_mode,
        verified_badges_repo=verified_badges_repo,
    )
    runner = AgentRunner(
        run_dir=Path(state.run_dir),
        paper_dir=Path(state.paper_dir),
        main_tex=Path(state.main_tex),
        root_module=state.root_module,
        prover_backend=state.prover_backend,
        verifier_backend=state.verifier_backend,
        agent_timeout_seconds=agent_timeout_seconds,
        codex_model=codex_model,
        claude_model=claude_model,
    )
    _persist_state(state)
    tasks_processed = 0
    while True:
        if max_tasks is not None and tasks_processed >= max_tasks:
            _persist_state(state)
            return state
        pending = [
            task
            for task in state.tasks
            if task.status in {"pending", "retry"}
            and (max_attempts_per_task is None or task.attempts < max_attempts_per_task)
        ]
        if not pending:
            _persist_state(state)
            return state
        task = pending[0]
        task.status = "proving"
        _persist_state(state)

        try:
            prover_result = runner.run_prover(task)
        except AgentTransportError as exc:
            task.status = "retry"
            task.summary = str(exc)
            task.last_error = str(exc)
            task.transport_failures += 1
            _append_attempt_history(
                task,
                f"Transport failure while starting attempt {task.attempts + 1}. {str(exc).strip()}",
            )
            tasks_processed += 1
            _persist_state(state)
            _sleep_after_transport_failure(task.transport_failures)
            continue
        except (AgentTimeoutError, AgentInvocationError) as exc:
            task.status = "retry"
            task.summary = str(exc)
            task.last_error = str(exc)
            _append_attempt_history(task, f"Attempt {task.attempts + 1}: agent failure. {str(exc).strip()}")
            tasks_processed += 1
            _persist_state(state)
            continue

        task.attempts += 1
        task.transport_failures = 0

        if prover_result.paper_issue:
            _append_issue(state, task, prover_result.paper_issue)

        missing_lean_reason = _missing_lean_path_reason(run_dir, prover_result.lean_path)
        if missing_lean_reason is not None:
            task.status = "retry"
            task.lean_path = prover_result.lean_path
            task.summary = missing_lean_reason
            task.last_error = missing_lean_reason
            _append_attempt_history(
                task,
                (
                    f"Attempt {task.attempts}: retry requested before verification. "
                    f"{missing_lean_reason} "
                    f"Prover: {prover_result.summary.strip()}"
                ),
            )
            tasks_processed += 1
            _persist_state(state)
            continue

        try:
            compile_ok, compile_output = _compile_candidate(run_dir, state.root_module, prover_result.lean_path)
            verifier_result = runner.run_verifier(task, prover_result, compile_ok, compile_output)
        except AgentTransportError as exc:
            task.status = "retry"
            task.lean_path = prover_result.lean_path
            task.summary = str(exc)
            task.last_error = str(exc)
            task.transport_failures += 1
            _append_attempt_history(
                task,
                f"Transport failure after substantive attempt {task.attempts}. {str(exc).strip()}",
            )
            tasks_processed += 1
            _persist_state(state)
            _sleep_after_transport_failure(task.transport_failures)
            continue
        except (AgentTimeoutError, AgentInvocationError) as exc:
            task.status = "retry"
            task.lean_path = prover_result.lean_path
            task.summary = str(exc)
            task.last_error = str(exc)
            _append_attempt_history(task, f"Attempt {task.attempts}: agent failure. {str(exc).strip()}")
            tasks_processed += 1
            _persist_state(state)
            continue

        if verifier_result.decision == "verified":
            task.transport_failures = 0
            _append_attempt_history(
                task,
                f"Attempt {task.attempts}: verifier accepted verified. compile_ok={compile_ok}. {verifier_result.summary.strip()}",
            )
            _mark_completed(state, task, prover_result.lean_path, "verified")
        elif verifier_result.decision == "formalized":
            task.transport_failures = 0
            _append_attempt_history(
                task,
                f"Attempt {task.attempts}: verifier accepted formalized. compile_ok={compile_ok}. {verifier_result.summary.strip()}",
            )
            _mark_completed(state, task, prover_result.lean_path, "formalized")
        elif verifier_result.decision == "weeks_scale" or prover_result.weeks_scale:
            task.status = "weeks_scale"
            task.summary = verifier_result.summary
            task.weeks_scale_reason = verifier_result.weeks_scale_reason or prover_result.weeks_scale_reason
            task.transport_failures = 0
            _append_attempt_history(
                task,
                f"Attempt {task.attempts}: classified weeks_scale. compile_ok={compile_ok}. {task.weeks_scale_reason or verifier_result.summary.strip()}",
            )
        else:
            task.status = "retry"
            task.lean_path = prover_result.lean_path
            task.summary = verifier_result.summary
            task.last_error = verifier_result.summary
            task.transport_failures = 0
            _append_attempt_history(
                task,
                (
                    f"Attempt {task.attempts}: retry requested. "
                    f"compile_ok={compile_ok}, exact_match={verifier_result.exact_match}, "
                    f"extra_hypotheses={verifier_result.extra_hypotheses}, lean_path={prover_result.lean_path or 'none'}. "
                    f"Verifier: {verifier_result.summary.strip()}. "
                    f"Prover: {prover_result.summary.strip()}"
                ),
            )

        tasks_processed += 1
        _persist_state(state)


def normalize_arxiv_id(arxiv_id: str) -> str:
    return arxiv_id.replace("/", "_").replace(":", "_")


def _load_or_initialize_state(
    arxiv_id: str,
    run_dir: Path,
    local_paper_dir: Path | None,
    repo_url: str | None,
    branch: str | None,
    prover_backend: str,
    verifier_backend: str,
    badge_link_mode: str,
    verified_badges_repo: Path | None,
) -> RunState:
    state_path = run_dir / "state.json"
    explicit_repo_target = repo_url is not None or branch is not None
    if state_path.exists():
        state = RunState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
        for task in state.tasks:
            if task.status == "proving":
                task.status = "retry"
                if task.last_error is None:
                    task.last_error = "Resuming after interrupted proving attempt."
                if task.summary is None:
                    task.summary = task.last_error
                _append_attempt_history(task, f"Supervisor resumed interrupted proving task `{task.label}` as retry.")
        if repo_url is not None:
            state.repo_url = repo_url
        if branch is not None:
            state.branch = branch
        if explicit_repo_target:
            state.explicit_repo_target = True
        state.prover_backend = prover_backend
        state.verifier_backend = verifier_backend
        state.badge_link_mode = badge_link_mode
        _synchronize_badges(state)
        _save_state(state)
        return state

    run_dir.mkdir(parents=True, exist_ok=True)
    paper_dir = run_dir / "paper"
    _populate_paper_dir(arxiv_id, paper_dir, local_paper_dir)
    tex_files = list((paper_dir).rglob("*.tex"))
    if not tex_files:
        raise RuntimeError(f"loaded paper source for {arxiv_id}, but found no .tex files")
    main_tex = detect_main_tex(tex_files)
    _initialize_lean_project(run_dir)
    root_module = _detect_root_module(run_dir)
    if root_module is None:
        raise RuntimeError("failed to detect generated Lean root module after `lake init`")
    badges_repo = ensure_verified_badges_repo(run_dir / ".cache", verified_badges_repo)
    copy_verified_badges_assets(badges_repo, main_tex.parent)
    ensure_verified_badges_package(main_tex)
    if repo_url is None or branch is None:
        inferred_repo_url, inferred_branch = infer_repo_url_and_branch(run_dir)
        repo_url = repo_url or inferred_repo_url
        branch = branch or inferred_branch

    tasks = [
        StatementTask(
            task_id=item.task_id,
            tex_path=item.tex_path,
            env_name=item.env_name,
            label=item.label,
            title=item.title,
            sequence_index=item.sequence_index,
            kind=item.kind,
            source_excerpt=item.source_excerpt,
        )
        for item in extract_statements_from_tree(paper_dir)
    ]
    if not tasks:
        raise RuntimeError("no tracked theorem-like environments found in the paper source")
    _write_instructions(run_dir)
    state = RunState(
        arxiv_id=arxiv_id,
        run_dir=str(run_dir),
        paper_dir=str(paper_dir),
        main_tex=str(main_tex),
        root_module=root_module,
        repo_url=repo_url,
        branch=branch,
        prover_backend=prover_backend,
        verifier_backend=verifier_backend,
        badge_link_mode=badge_link_mode,
        explicit_repo_target=explicit_repo_target,
        is_local_paper=local_paper_dir is not None,
        tasks=tasks,
    )
    _synchronize_badges(state)
    _save_state(state)
    return state


def _initialize_lean_project(run_dir: Path) -> None:
    if (run_dir / "lakefile.toml").exists() or (run_dir / "lakefile.lean").exists():
        return
    package_name = _lean_package_name_for_run(run_dir.name)
    subprocess.run(["lake", "init", package_name, "math"], cwd=run_dir, check=True)
    root_module = _detect_root_module(run_dir)
    if root_module is None:
        raise RuntimeError("failed to detect Lean root module after initialization")
    formalizations_dir = run_dir / root_module / "Formalizations"
    formalizations_dir.mkdir(exist_ok=True)
    for subdir in ["Definition", "Notation", "Lemma", "Proposition", "Corollary", "Theorem"]:
        (formalizations_dir / subdir).mkdir(parents=True, exist_ok=True)


def _compile_candidate(run_dir: Path, root_module: str | None, lean_path: str | None) -> tuple[bool, str]:
    missing_lean_reason = _missing_lean_path_reason(run_dir, lean_path)
    if missing_lean_reason is not None:
        return False, missing_lean_reason
    assert lean_path is not None
    lean_file = run_dir / lean_path
    text = lean_file.read_text(encoding="utf-8", errors="ignore")
    if "sorry" in text:
        return False, "Lean file still contains `sorry`."
    module_name = _module_name_for_file(run_dir, lean_file, root_module)
    if module_name is None:
        return False, f"Could not map {lean_path} to a Lean module in this package."
    result = subprocess.run(
        ["lake", "build", module_name],
        cwd=run_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.returncode == 0, result.stdout.strip()


def _missing_lean_path_reason(run_dir: Path, lean_path: str | None) -> str | None:
    if lean_path is None:
        return "No Lean file path returned by prover."
    lean_file = run_dir / lean_path
    if not lean_file.exists():
        return f"Lean file does not exist: {lean_path}"
    return None


def _mark_completed(state: RunState, task: StatementTask, lean_path: str | None, badge_kind: str) -> None:
    task.status = badge_kind
    task.badge_kind = badge_kind
    task.lean_path = lean_path
    badge_url = _badge_target_for_task(state, lean_path)
    if badge_url:
        macro = "\\leanproof" if badge_kind == "verified" else "\\leanformalized"
        insert_badge_for_task(
            Path(state.paper_dir),
            task.task_id,
            task.tex_path,
            task.sequence_index,
            f"{macro}{{{badge_url}}}",
        )


def _persist_state(state: RunState) -> None:
    _synchronize_badges(state)
    _write_progress_files(state)
    _save_state(state)


def _write_progress_files(state: RunState) -> None:
    run_dir = Path(state.run_dir)
    todo_lines = ["# TODO", ""]
    for task in state.tasks:
        badge = f" ({task.badge_kind})" if task.badge_kind else ""
        todo_lines.append(f"- [{_checkbox(task.status)}] `{task.kind}` `{task.label}`: {task.status}{badge}")
    (run_dir / "TODO.md").write_text("\n".join(todo_lines) + "\n", encoding="utf-8")

    prover_lines = ["# PROVER", ""]
    active = [task for task in state.tasks if task.status == "proving"]
    if active:
        prover_lines.extend(
            f"- Working on `{task.label}` from `{task.tex_path}` (attempt {task.attempts})" for task in active
        )
    else:
        prover_lines.append("- Idle")
    (run_dir / "PROVER.md").write_text("\n".join(prover_lines) + "\n", encoding="utf-8")

    issues_path = run_dir / "ISSUES.md"
    if not issues_path.exists():
        issues_path.write_text("# ISSUES\n\n", encoding="utf-8")


def _append_issue(state: RunState, task: StatementTask, issue: str) -> None:
    issues_path = Path(state.run_dir) / "ISSUES.md"
    if not issues_path.exists():
        issues_path.write_text("# ISSUES\n\n", encoding="utf-8")
    with issues_path.open("a", encoding="utf-8") as handle:
        handle.write(f"- `{task.label}`: {issue.strip()}\n")


def _write_instructions(run_dir: Path) -> None:
    instructions = """# Role of Agents

## Prover

Use Codex as a prover. Formalize statements from `paper/` in Lean 4. Keep `PROVER.md` current while working. Create one readable Lean file per statement where practical. Fix linter warnings in touched Lean files.

## Verifier

Use the verifier to check that each Lean formalization matches the paper exactly and that there are no hidden hypotheses. If a proof is complete, the supervisor may add a verified badge. If only the statement is exact, the supervisor may add a formalized badge.

## General Instructions

Do not stop a task early except for genuine paper issues or when the remaining work would honestly take LLM-weeks. Put paper issues into `ISSUES.md`. Keep `TODO.md` current.
"""
    (run_dir / "INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")


def _save_state(state: RunState) -> None:
    tmp_path = state.state_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(state.state_path)


def _append_attempt_history(task: StatementTask, entry: str) -> None:
    task.attempt_history.append(entry.strip())
    if len(task.attempt_history) > 12:
        task.attempt_history[:] = task.attempt_history[-12:]


def _sleep_after_transport_failure(failures: int) -> None:
    delay_seconds = min(60, 5 * (2 ** max(0, failures - 1)))
    time.sleep(delay_seconds)


def _checkbox(status: str) -> str:
    return "x" if status in {"verified", "formalized", "weeks_scale"} else " "


def _badge_target_for_task(state: RunState, lean_path: str | None) -> str | None:
    if lean_path is None:
        return None
    absolute_path = Path(state.run_dir) / lean_path
    mode = state.badge_link_mode
    if mode == "local":
        return build_local_file_target(Path(state.paper_dir), absolute_path)
    if mode == "github":
        return build_blob_url(state.repo_url, state.branch, absolute_path)

    # auto
    if state.explicit_repo_target and state.repo_url and state.branch:
        return build_blob_url(state.repo_url, state.branch, absolute_path)
    return build_local_file_target(Path(state.paper_dir), absolute_path)


def _synchronize_badges(state: RunState) -> None:
    paper_dir = Path(state.paper_dir)
    for task in state.tasks:
        badge_kind = None
        if task.status in {"verified", "formalized"}:
            badge_kind = task.badge_kind or task.status
        if badge_kind is None:
            set_badge_for_task(paper_dir, task.task_id, task.tex_path, task.sequence_index, None)
            continue
        badge_target = _badge_target_for_task(state, task.lean_path)
        if badge_target is None:
            set_badge_for_task(paper_dir, task.task_id, task.tex_path, task.sequence_index, None)
            continue
        macro = "\\leanproof" if badge_kind == "verified" else "\\leanformalized"
        set_badge_for_task(
            paper_dir,
            task.task_id,
            task.tex_path,
            task.sequence_index,
            f"{macro}{{{badge_target}}}",
        )


def _lean_package_name_for_run(run_name: str) -> str:
    sanitized = "".join(ch if ch.isalnum() else "_" for ch in run_name)
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    sanitized = sanitized.strip("_")
    if not sanitized:
        sanitized = "ArxivPaper"
    if not sanitized[0].isalpha():
        sanitized = f"Arxiv_{sanitized}"
    return sanitized


def _detect_root_module(run_dir: Path) -> str | None:
    candidates = sorted(
        path.parent.name for path in run_dir.glob("*/Basic.lean") if path.parent.name not in {"paper", ".lake"}
    )
    return candidates[0] if candidates else None


def _module_name_for_file(run_dir: Path, lean_file: Path, root_module: str | None) -> str | None:
    try:
        relative = lean_file.resolve().relative_to(run_dir.resolve())
    except ValueError:
        return None
    if relative.suffix != ".lean":
        return None
    parts = list(relative.with_suffix("").parts)
    if root_module is not None and (not parts or parts[0] != root_module):
        return None
    return ".".join(parts)


def _populate_paper_dir(arxiv_id: str, paper_dir: Path, local_paper_dir: Path | None) -> None:
    if local_paper_dir is None:
        download_and_extract_arxiv_source(arxiv_id, paper_dir)
        return
    if not local_paper_dir.exists():
        raise RuntimeError(f"local paper directory does not exist: {local_paper_dir}")
    if not local_paper_dir.is_dir():
        raise RuntimeError(f"local paper path is not a directory: {local_paper_dir}")
    shutil.copytree(local_paper_dir, paper_dir, dirs_exist_ok=True)
