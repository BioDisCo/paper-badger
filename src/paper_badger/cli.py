from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dashboard import load_state, monitor_run, render_dashboard
from .workflow import run_formalization


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Formalize arXiv proofs and insert verification badges.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Start or resume a formalization run",
    )
    run_parser.add_argument(
        "paper_id",
        help="Run identifier, e.g. 2401.01234 or trivial-even",
    )
    run_parser.add_argument(
        "--runs-dir",
        default="runs",
        help="Directory for per-paper workspaces",
    )
    run_parser.add_argument(
        "--paper-dir",
        default=None,
        help=(
            "Local directory containing LaTeX paper sources; "
            "if omitted, the positional identifier is treated as an arXiv ID"
        ),
    )
    run_parser.add_argument(
        "--repo-url",
        default=None,
        help="GitHub repository URL used for badge links",
    )
    run_parser.add_argument(
        "--branch",
        default=None,
        help="Git branch name used for badge links",
    )
    run_parser.add_argument(
        "--badge-link-mode",
        choices=["auto", "local", "github"],
        default="local",
        help="How badge links should be generated",
    )
    run_parser.add_argument(
        "--prover-backend",
        choices=["claude", "codex"],
        default="codex",
        help="Prover backend",
    )
    run_parser.add_argument(
        "--verifier-backend",
        choices=["claude", "codex"],
        default="claude",
        help="Verifier backend",
    )
    run_parser.add_argument(
        "--verified-badges-repo",
        default=None,
        help="Path to a local verified-badges checkout",
    )
    run_parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=1800,
        help="Max seconds per prover/verifier invocation",
    )
    run_parser.add_argument(
        "--codex-model",
        default=None,
        help="Optional Codex model override",
    )
    run_parser.add_argument(
        "--claude-model",
        default=None,
        help="Optional Claude model override",
    )
    run_parser.add_argument(
        "--max-attempts-per-task",
        type=int,
        default=None,
        help="Per-task attempt cap; unresolved tasks stay in retry",
    )
    run_parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Process at most this many tasks in one invocation",
    )

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Render a live dashboard for an existing run",
    )
    monitor_parser.add_argument(
        "paper_id",
        help="Run identifier to inspect",
    )
    monitor_parser.add_argument(
        "--runs-dir",
        default="runs",
        help="Directory containing per-paper workspaces",
    )
    monitor_parser.add_argument(
        "--interval-seconds",
        type=float,
        default=5.0,
        help="Refresh interval for the live dashboard",
    )
    monitor_parser.add_argument(
        "--once",
        action="store_true",
        help="Render a single snapshot and exit",
    )
    return parser


def main() -> None:
    parser = build_parser()
    argv = sys.argv[1:]
    if argv and argv[0] not in {"run", "monitor", "-h", "--help"}:
        if argv[0].startswith("-"):
            parser.parse_args(argv)
            return
        argv = ["run", *argv]
    args = parser.parse_args(argv)
    if args.command == "monitor":
        run_dir = Path(args.runs_dir).resolve() / args.paper_id
        if args.once:
            print(render_dashboard(load_state(run_dir)))
            return
        monitor_run(run_dir, args.interval_seconds)
        return
    state = run_formalization(
        arxiv_id=args.paper_id,
        base_dir=Path(args.runs_dir).resolve(),
        local_paper_dir=(Path(args.paper_dir).resolve() if args.paper_dir else None),
        repo_url=args.repo_url,
        branch=args.branch,
        prover_backend=args.prover_backend,
        verifier_backend=args.verifier_backend,
        badge_link_mode=args.badge_link_mode,
        verified_badges_repo=(Path(args.verified_badges_repo).resolve() if args.verified_badges_repo else None),
        agent_timeout_seconds=args.agent_timeout_seconds,
        codex_model=args.codex_model,
        claude_model=args.claude_model,
        max_attempts_per_task=args.max_attempts_per_task,
        max_tasks=args.max_tasks,
    )
    completed = sum(1 for task in state.tasks if task.status in {"verified", "formalized"})
    weeks = sum(1 for task in state.tasks if task.status == "weeks_scale")
    retries = sum(1 for task in state.tasks if task.status == "retry")
    pending = sum(1 for task in state.tasks if task.status in {"pending", "proving"})
    print(
        f"Run completed for {state.arxiv_id}: "
        f"{completed} completed, {retries} retry, "
        f"{pending} pending, {weeks} weeks-scale."
    )


if __name__ == "__main__":
    main()
