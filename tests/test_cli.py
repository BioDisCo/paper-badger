from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paper_badger import cli


def test_main_defaults_to_run_mode_for_legacy_invocation(tmp_path: Path) -> None:
    with (
        patch("paper_badger.cli.run_formalization") as run_formalization,
        patch(
            "sys.argv",
            ["paper_badger.cli", "2401.01234", "--runs-dir", str(tmp_path)],
        ),
    ):
        run_formalization.return_value = type(
            "State",
            (),
            {"arxiv_id": "2401.01234", "tasks": []},
        )()
        cli.main()

    assert run_formalization.call_args.kwargs["arxiv_id"] == "2401.01234"
    assert run_formalization.call_args.kwargs["base_dir"] == tmp_path.resolve()
    assert run_formalization.call_args.kwargs["prover_backend"] == "codex"
    assert run_formalization.call_args.kwargs["verifier_backend"] == "claude"


def test_main_allows_explicit_role_backend_overrides(tmp_path: Path) -> None:
    with (
        patch("paper_badger.cli.run_formalization") as run_formalization,
        patch(
            "sys.argv",
            [
                "paper_badger.cli",
                "2401.01234",
                "--runs-dir",
                str(tmp_path),
                "--prover-backend",
                "claude",
                "--verifier-backend",
                "codex",
            ],
        ),
    ):
        run_formalization.return_value = type(
            "State",
            (),
            {"arxiv_id": "2401.01234", "tasks": []},
        )()
        cli.main()

    assert run_formalization.call_args.kwargs["prover_backend"] == "claude"
    assert run_formalization.call_args.kwargs["verifier_backend"] == "codex"


def test_main_monitor_once_renders_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = tmp_path / "paper-id"
    run_dir.mkdir()

    with (
        patch("paper_badger.cli.render_dashboard", return_value="dashboard"),
        patch("paper_badger.cli.load_state", return_value=MagicMock()),
        patch(
            "sys.argv",
            ["paper_badger.cli", "monitor", "paper-id", "--runs-dir", str(tmp_path), "--once"],
        ),
    ):
        cli.main()

    captured = capsys.readouterr()
    assert captured.out.strip() == "dashboard"
