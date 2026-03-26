import json
from pathlib import Path
from unittest.mock import patch

from paper_badger.agents import AgentTransportError, ProverResult, VerifierResult
from paper_badger.models import RunState, StatementTask
from paper_badger.workflow import (
    _append_attempt_history,
    _badge_target_for_task,
    _load_or_initialize_state,
    run_formalization,
)


def _task(
    label: str = "thm:main",
    kind: str = "theorem",
    status: str = "pending",
    sequence_index: int = 0,
) -> StatementTask:
    return StatementTask(
        task_id=f"main.tex::{sequence_index}::{label}",
        tex_path="main.tex",
        env_name=kind,
        label=label,
        title="Main",
        sequence_index=sequence_index,
        kind=kind,
        source_excerpt="If m and n are even, then m+n is even.",
        status=status,
    )


def _write_state(run_dir: Path, task: StatementTask) -> None:
    paper_dir = run_dir / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "main.tex").write_text(
        r"""
\documentclass{article}
\begin{document}
\begin{theorem}[Main]
\label{thm:main}
If $m$ and $n$ are even, then $m+n$ is even.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )
    root = run_dir / "Paper"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Basic.lean").write_text("namespace Paper\nend Paper\n", encoding="utf-8")
    state = RunState(
        arxiv_id="paper-id",
        run_dir=str(run_dir),
        paper_dir=str(paper_dir),
        main_tex=str(paper_dir / "main.tex"),
        repo_url="https://github.com/example/repo",
        branch="main",
        prover_backend="codex",
        verifier_backend="codex",
        badge_link_mode="auto",
        explicit_repo_target=False,
        is_local_paper=False,
        root_module="Paper",
        tasks=[task],
    )
    state.state_path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def test_attempt_history_keeps_recent_entries() -> None:
    task = _task()

    for index in range(15):
        _append_attempt_history(task, f"Attempt {index + 1}: note {index + 1}")

    assert len(task.attempt_history) == 12
    assert task.attempt_history[0] == "Attempt 4: note 4"
    assert task.attempt_history[-1] == "Attempt 15: note 15"


def test_load_existing_state_resumes_interrupted_proving_task(tmp_path: Path) -> None:
    run_dir = tmp_path / "paper-id"
    task = _task(status="proving")
    _write_state(run_dir, task)

    state = _load_or_initialize_state(
        arxiv_id="paper-id",
        run_dir=run_dir,
        local_paper_dir=None,
        repo_url=None,
        branch=None,
        prover_backend="codex",
        verifier_backend="claude",
        badge_link_mode="auto",
        verified_badges_repo=None,
    )

    assert state.verifier_backend == "claude"
    assert state.tasks[0].status == "retry"
    assert "interrupted proving task" in state.tasks[0].attempt_history[-1]
    assert state.tasks[0].summary == "Resuming after interrupted proving attempt."


def test_load_existing_state_removes_stale_badge_for_nonfinal_task(tmp_path: Path) -> None:
    run_dir = tmp_path / "paper-id"
    task = _task(status="proving")
    _write_state(run_dir, task)
    main_tex = run_dir / "paper" / "main.tex"
    main_tex.write_text(
        r"""
\documentclass{article}
\begin{document}
\begin{theorem}[Main \leanproof{Paper/Formalizations/Theorem/ThmMain.lean}]
\label{thm:main}
If $m$ and $n$ are even, then $m+n$ is even.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    state = _load_or_initialize_state(
        arxiv_id="paper-id",
        run_dir=run_dir,
        local_paper_dir=None,
        repo_url=None,
        branch=None,
        prover_backend="codex",
        verifier_backend="codex",
        badge_link_mode="auto",
        verified_badges_repo=None,
    )

    updated = main_tex.read_text(encoding="utf-8")

    assert state.tasks[0].status == "retry"
    assert r"\leanproof{Paper/Formalizations/Theorem/ThmMain.lean}" not in updated
    assert r"\begin{theorem}[Main]" in updated


def test_local_paper_initialization_extracts_tasks_and_inserts_badges_package(tmp_path: Path) -> None:
    run_dir = tmp_path / "local-paper"
    local_paper_dir = tmp_path / "sample"
    local_paper_dir.mkdir()
    main_tex = local_paper_dir / "main.tex"
    main_tex.write_text(
        r"""
\documentclass{article}
\title{A Note About Naturals}
\begin{document}
\begin{definition}[Even]
\label{def:even}
A natural number $n$ is even if $n = 2k$ for some natural number $k$.
\end{definition}
\begin{theorem}[Main]
\label{thm:main}
If $n$ is even, then $n + 2$ is even.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )
    badges_repo = tmp_path / "badges"
    badges_repo.mkdir()

    def fake_init(project_dir: Path) -> None:
        (project_dir / "lakefile.toml").write_text('name = "local_paper"\n', encoding="utf-8")
        root = project_dir / "LocalPaper"
        root.mkdir(parents=True, exist_ok=True)
        (root / "Basic.lean").write_text("namespace LocalPaper\nend LocalPaper\n", encoding="utf-8")
        for subdir in ["Definition", "Notation", "Lemma", "Proposition", "Corollary", "Theorem"]:
            (root / "Formalizations" / subdir).mkdir(parents=True, exist_ok=True)

    with (
        patch("paper_badger.workflow._initialize_lean_project", side_effect=fake_init),
        patch("paper_badger.workflow.ensure_verified_badges_repo", return_value=badges_repo),
        patch("paper_badger.workflow.copy_verified_badges_assets"),
        patch(
            "paper_badger.workflow.infer_repo_url_and_branch",
            return_value=("https://github.com/example/repo", "main"),
        ),
    ):
        state = _load_or_initialize_state(
            arxiv_id="local-paper",
            run_dir=run_dir,
            local_paper_dir=local_paper_dir,
            repo_url=None,
            branch=None,
            prover_backend="codex",
            verifier_backend="codex",
            badge_link_mode="auto",
            verified_badges_repo=badges_repo,
        )

    assert state.root_module == "LocalPaper"
    assert state.is_local_paper
    assert [task.label for task in state.tasks] == ["def:even", "thm:main"]
    updated_main = (run_dir / "paper" / "main.tex").read_text(encoding="utf-8")
    assert r"\usepackage{verified-badges}" in updated_main


def test_run_formalization_retries_then_verifies_and_inserts_badge(tmp_path: Path) -> None:
    base_dir = tmp_path
    run_dir = base_dir / "paper-id"
    task = _task()
    _write_state(run_dir, task)
    theorem_dir = run_dir / "Paper" / "Formalizations" / "Theorem"
    theorem_dir.mkdir(parents=True, exist_ok=True)
    (theorem_dir / "ThmMain.lean").write_text("namespace Paper\nend Paper\n", encoding="utf-8")

    class FakeRunner:
        verifier_calls = 0

        def __init__(self, **_: object) -> None:
            pass

        def run_prover(self, task: StatementTask) -> ProverResult:
            return ProverResult(
                status="verified_candidate",
                summary="Built theorem.",
                lean_path="Paper/Formalizations/Theorem/ThmMain.lean",
                proof_complete=True,
                weeks_scale=False,
            )

        def run_verifier(
            self,
            task: StatementTask,
            prover_result: ProverResult,
            compile_ok: bool,
            compile_output: str,
        ) -> VerifierResult:
            FakeRunner.verifier_calls += 1
            if FakeRunner.verifier_calls == 1:
                return VerifierResult(
                    decision="retry",
                    summary="Need one more exact-match clarification.",
                    exact_match=False,
                    extra_hypotheses=True,
                )
            return VerifierResult(
                decision="verified",
                summary="Exact and complete.",
                exact_match=True,
                extra_hypotheses=False,
            )

    with (
        patch("paper_badger.workflow.AgentRunner", FakeRunner),
        patch(
            "paper_badger.workflow._compile_candidate",
            return_value=(True, "ok"),
        ),
        patch(
            "paper_badger.workflow.build_blob_url",
            return_value="https://github.com/example/repo/blob/main/Paper/Formalizations/Theorem/ThmMain.lean",
        ),
    ):
        state = run_formalization(
            arxiv_id="paper-id",
            base_dir=base_dir,
            local_paper_dir=None,
            repo_url="https://github.com/example/repo",
            branch="main",
            prover_backend="codex",
            verifier_backend="codex",
            badge_link_mode="auto",
            verified_badges_repo=None,
            agent_timeout_seconds=10,
            codex_model=None,
            claude_model=None,
            max_attempts_per_task=5,
            max_tasks=None,
        )

    task = state.tasks[0]
    updated_main = (run_dir / "paper" / "main.tex").read_text(encoding="utf-8")

    assert task.status == "verified"
    assert task.attempts == 2
    assert "retry requested" in task.attempt_history[0]
    assert "verifier accepted verified" in task.attempt_history[-1]
    assert (
        r"\leanproof{https://github.com/example/repo/blob/main/Paper/Formalizations/Theorem/ThmMain.lean}"
        in updated_main
    )


def test_transport_failure_does_not_consume_substantive_attempt(tmp_path: Path) -> None:
    base_dir = tmp_path
    run_dir = base_dir / "paper-id"
    task = _task()
    _write_state(run_dir, task)
    theorem_dir = run_dir / "Paper" / "Formalizations" / "Theorem"
    theorem_dir.mkdir(parents=True, exist_ok=True)
    (theorem_dir / "ThmMain.lean").write_text("namespace Paper\nend Paper\n", encoding="utf-8")

    class FakeRunner:
        prover_calls = 0

        def __init__(self, **_: object) -> None:
            pass

        def run_prover(self, task: StatementTask) -> ProverResult:
            FakeRunner.prover_calls += 1
            if FakeRunner.prover_calls == 1:
                raise AgentTransportError("Codex transport disconnected before returning a result.")
            return ProverResult(
                status="verified_candidate",
                summary="Built theorem.",
                lean_path="Paper/Formalizations/Theorem/ThmMain.lean",
                proof_complete=True,
                weeks_scale=False,
            )

        def run_verifier(
            self,
            task: StatementTask,
            prover_result: ProverResult,
            compile_ok: bool,
            compile_output: str,
        ) -> VerifierResult:
            return VerifierResult(
                decision="verified",
                summary="Exact and complete.",
                exact_match=True,
                extra_hypotheses=False,
            )

    with (
        patch("paper_badger.workflow.AgentRunner", FakeRunner),
        patch(
            "paper_badger.workflow._compile_candidate",
            return_value=(True, "ok"),
        ),
        patch(
            "paper_badger.workflow.build_blob_url",
            return_value="https://github.com/example/repo/blob/main/Paper/Formalizations/Theorem/ThmMain.lean",
        ),
        patch("paper_badger.workflow._sleep_after_transport_failure"),
    ):
        state = run_formalization(
            arxiv_id="paper-id",
            base_dir=base_dir,
            local_paper_dir=None,
            repo_url="https://github.com/example/repo",
            branch="main",
            prover_backend="codex",
            verifier_backend="codex",
            badge_link_mode="auto",
            verified_badges_repo=None,
            agent_timeout_seconds=10,
            codex_model=None,
            claude_model=None,
            max_attempts_per_task=5,
            max_tasks=None,
        )

    task = state.tasks[0]

    assert task.status == "verified"
    assert task.attempts == 1
    assert task.transport_failures == 0
    assert "Transport failure while starting attempt 1" in task.attempt_history[0]


def test_missing_prover_output_file_retries_before_verifier(tmp_path: Path) -> None:
    base_dir = tmp_path
    run_dir = base_dir / "paper-id"
    task = _task()
    _write_state(run_dir, task)

    class FakeRunner:
        verifier_calls = 0

        def __init__(self, **_: object) -> None:
            pass

        def run_prover(self, task: StatementTask) -> ProverResult:
            return ProverResult(
                status="verified_candidate",
                summary="Wrote the theorem file.",
                lean_path="Paper/Formalizations/Theorem/Missing.lean",
                proof_complete=True,
                weeks_scale=False,
            )

        def run_verifier(
            self,
            task: StatementTask,
            prover_result: ProverResult,
            compile_ok: bool,
            compile_output: str,
        ) -> VerifierResult:
            FakeRunner.verifier_calls += 1
            return VerifierResult(
                decision="verified",
                summary="Should never be called.",
                exact_match=True,
                extra_hypotheses=False,
            )

    with patch("paper_badger.workflow.AgentRunner", FakeRunner):
        state = run_formalization(
            arxiv_id="paper-id",
            base_dir=base_dir,
            local_paper_dir=None,
            repo_url="https://github.com/example/repo",
            branch="main",
            prover_backend="codex",
            verifier_backend="codex",
            badge_link_mode="auto",
            verified_badges_repo=None,
            agent_timeout_seconds=10,
            codex_model=None,
            claude_model=None,
            max_attempts_per_task=1,
            max_tasks=None,
        )

    task = state.tasks[0]

    assert FakeRunner.verifier_calls == 0
    assert task.status == "retry"
    assert task.attempts == 1
    assert task.summary == "Lean file does not exist: Paper/Formalizations/Theorem/Missing.lean"
    assert "retry requested before verification" in task.attempt_history[-1]


def test_badge_target_uses_local_relative_path_for_local_runs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    paper_dir = run_dir / "paper"
    paper_dir.mkdir(parents=True)
    state = RunState(
        arxiv_id="local-paper",
        run_dir=str(run_dir),
        paper_dir=str(paper_dir),
        main_tex=str(paper_dir / "main.tex"),
        repo_url="https://github.com/example/repo",
        branch="main",
        prover_backend="codex",
        verifier_backend="codex",
        badge_link_mode="auto",
        explicit_repo_target=False,
        is_local_paper=True,
        root_module="Paper",
        tasks=[],
    )

    target = _badge_target_for_task(state, "Paper/Formalizations/Theorem/ThmMain.lean")

    assert target == (run_dir / "Paper" / "Formalizations" / "Theorem" / "ThmMain.lean").resolve().as_uri()


def test_badge_target_uses_local_file_uri_for_non_explicit_auto_mode(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    paper_dir = run_dir / "paper"
    paper_dir.mkdir(parents=True)
    state = RunState(
        arxiv_id="arxiv-paper",
        run_dir=str(run_dir),
        paper_dir=str(paper_dir),
        main_tex=str(paper_dir / "main.tex"),
        repo_url="https://github.com/example/repo",
        branch="main",
        prover_backend="codex",
        verifier_backend="codex",
        badge_link_mode="auto",
        explicit_repo_target=False,
        is_local_paper=False,
        root_module="Paper",
        tasks=[],
    )

    target = _badge_target_for_task(state, "Paper/Formalizations/Theorem/ThmMain.lean")

    assert target == (run_dir / "Paper" / "Formalizations" / "Theorem" / "ThmMain.lean").resolve().as_uri()


def test_badge_target_uses_github_for_explicit_auto_mode(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    paper_dir = run_dir / "paper"
    paper_dir.mkdir(parents=True)
    state = RunState(
        arxiv_id="arxiv-paper",
        run_dir=str(run_dir),
        paper_dir=str(paper_dir),
        main_tex=str(paper_dir / "main.tex"),
        repo_url="https://github.com/example/repo",
        branch="main",
        prover_backend="codex",
        verifier_backend="codex",
        badge_link_mode="auto",
        explicit_repo_target=True,
        is_local_paper=False,
        root_module="Paper",
        tasks=[],
    )

    with patch(
        "paper_badger.workflow.build_blob_url",
        return_value="https://github.com/example/repo/blob/main/Paper/Formalizations/Theorem/ThmMain.lean",
    ):
        target = _badge_target_for_task(state, "Paper/Formalizations/Theorem/ThmMain.lean")

    assert target == "https://github.com/example/repo/blob/main/Paper/Formalizations/Theorem/ThmMain.lean"
