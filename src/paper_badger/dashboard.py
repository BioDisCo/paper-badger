from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .models import RunState, StatementTask

logger = logging.getLogger(__name__)


def load_state(run_dir: Path) -> RunState:
    state_path = run_dir / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    return RunState.from_dict(payload)


def render_dashboard(state: RunState) -> str:
    tasks = state.tasks
    completed = sum(1 for task in tasks if task.status in {"verified", "formalized"})
    verified = sum(1 for task in tasks if task.status == "verified")
    formalized = sum(1 for task in tasks if task.status == "formalized")
    retry = sum(1 for task in tasks if task.status == "retry")
    proving = sum(1 for task in tasks if task.status == "proving")
    pending = sum(1 for task in tasks if task.status == "pending")
    weeks = sum(1 for task in tasks if task.status == "weeks_scale")
    current = next((task for task in tasks if task.status == "proving"), None)
    recent = [task for task in tasks if task.attempt_history]
    recent.sort(key=lambda task: task.attempts, reverse=True)

    lines = [
        f"Run: {state.arxiv_id}",
        f"Run dir: {state.run_dir}",
        (
            "Summary: "
            f"{completed}/{len(tasks)} completed "
            f"({verified} verified, {formalized} formalized), "
            f"{retry} retry, {proving} proving, {pending} pending, {weeks} weeks-scale"
        ),
        f"Backends: prover={state.prover_backend}, verifier={state.verifier_backend}, links={state.badge_link_mode}",
        "",
        "Current Task:",
    ]
    if current is None:
        lines.append("- none")
    else:
        lines.extend(_render_task(current))

    lines.extend(["", "Tasks:"])
    lines.extend(_render_task_list(tasks))

    lines.extend(["", "Recent Attempts:"])
    if not recent:
        lines.append("- none")
    else:
        for task in recent[:5]:
            lines.extend(_render_recent_task(task))

    lines.extend(["", "Paths:"])
    lines.append(f"- state: {Path(state.run_dir) / 'state.json'}")
    lines.append(f"- todo: {Path(state.run_dir) / 'TODO.md'}")
    lines.append(f"- prover: {Path(state.run_dir) / 'PROVER.md'}")
    lines.append(f"- issues: {Path(state.run_dir) / 'ISSUES.md'}")
    return "\n".join(lines)


def monitor_run(run_dir: Path, interval_seconds: float) -> None:
    while True:
        try:
            state = load_state(run_dir)
            state.run_dir = str(run_dir)
            snapshot = render_dashboard(state)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.debug("failed to load state: %s", exc)
            snapshot = f"Waiting for state.json in {run_dir} ..."
        print("\x1bc" + snapshot, flush=True)
        time.sleep(interval_seconds)


def _render_task(task: StatementTask) -> list[str]:
    lines = [
        f"- {task.kind} {task.label} [{task.status}]",
        f"  attempts={task.attempts}, transport_failures={task.transport_failures}",
    ]
    if task.summary:
        lines.append(f"  summary: {task.summary}")
    if task.lean_path:
        lines.append(f"  lean: {task.lean_path}")
    excerpt = _single_line(task.source_excerpt, 220)
    if excerpt:
        lines.append(f"  excerpt: {excerpt}")
    return lines


def _render_recent_task(task: StatementTask) -> list[str]:
    lines = [f"- {task.label} [{task.status}] attempts={task.attempts}"]
    if task.attempt_history:
        lines.append(f"  last: {_single_line(task.attempt_history[-1], 220)}")
    return lines


def _render_task_list(tasks: list[StatementTask]) -> list[str]:
    return [_todo_line(task) for task in tasks]


def _todo_line(task: StatementTask) -> str:
    checked = "x" if task.status in {"verified", "formalized"} else " "
    prefix = "- [!]" if task.status == "proving" else f"- [{checked}]"
    if task.status == "verified":
        detail = "verified (verified)"
    elif task.status == "formalized":
        detail = "formalized (formalized)"
    elif task.status == "proving":
        detail = _single_line(task.summary, 100) if task.summary else "in progress"
    elif task.status == "retry":
        detail = _single_line(task.summary, 100) if task.summary else "retry"
    elif task.status == "weeks_scale":
        detail = _single_line(task.weeks_scale_reason or task.summary, 100) or "weeks-scale"
    else:
        detail = "pending"
    return f"{prefix} `{task.kind}` `{task.label}`: {detail}"


def _single_line(text: str | None, limit: int) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."
