import json
from pathlib import Path

from paper_badger.dashboard import load_state, render_dashboard
from paper_badger.models import RunState, StatementTask


def test_render_dashboard_includes_summary_and_current_task(tmp_path: Path) -> None:
    run_dir = tmp_path / "paper-id"
    run_dir.mkdir()
    state = RunState(
        arxiv_id="paper-id",
        run_dir=str(run_dir),
        paper_dir=str(run_dir / "paper"),
        main_tex=str(run_dir / "paper" / "main.tex"),
        repo_url=None,
        branch=None,
        prover_backend="codex",
        verifier_backend="codex",
        badge_link_mode="local",
        root_module="Paper",
        tasks=[
            StatementTask(
                task_id="main.tex::0::lem:main",
                tex_path="main.tex",
                env_name="lemma",
                label="lem:main",
                title=None,
                sequence_index=0,
                kind="lemma",
                source_excerpt="If x = y then y = x.",
                status="proving",
                attempts=2,
                attempt_history=["Attempt 2: retry requested."],
            ),
            StatementTask(
                task_id="main.tex::1::thm:done",
                tex_path="main.tex",
                env_name="theorem",
                label="thm:done",
                title=None,
                sequence_index=1,
                kind="theorem",
                source_excerpt="Done theorem.",
                status="verified",
            ),
        ],
    )

    output = render_dashboard(state)

    assert "Run: paper-id" in output
    assert "1/2 completed (1 verified, 0 formalized)" in output
    assert "- lemma lem:main [proving]" in output
    assert "- lem:main [proving] attempts=2" in output
    assert "- [!] `lemma` `lem:main`: in progress" in output
    assert "- [x] `theorem` `thm:done`: verified (verified)" in output


def test_load_state_reads_state_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "paper-id"
    run_dir.mkdir()
    state = RunState(
        arxiv_id="paper-id",
        run_dir=str(run_dir),
        paper_dir=str(run_dir / "paper"),
        main_tex=str(run_dir / "paper" / "main.tex"),
        repo_url=None,
        branch=None,
        prover_backend="codex",
        verifier_backend="codex",
        tasks=[],
    )
    (run_dir / "state.json").write_text(json.dumps(state.to_dict()), encoding="utf-8")

    loaded = load_state(run_dir)

    assert loaded.arxiv_id == "paper-id"
