from __future__ import annotations

import subprocess
from pathlib import Path  # noqa: TC003
from typing import Any
from unittest.mock import patch

from paper_badger.agents import AgentRunner


def test_detects_transport_disconnect_output() -> None:
    output = """
2026-03-21 WARN codex_core::codex: stream disconnected before completion
Reconnecting... 1/5 (stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses))
""".strip()

    assert AgentRunner._looks_like_transport_error(output)


def test_ignores_non_transport_output() -> None:
    output = "error: Lean elaboration failed because theorem statement is ill-typed"

    assert not AgentRunner._looks_like_transport_error(output)


def test_extract_json_object_from_mixed_output() -> None:
    output = """
Some progress text
{"status":"verified_candidate","summary":"ok","lean_path":"Paper/Test.lean","proof_complete":true,"weeks_scale":false,"weeks_scale_reason":null,"paper_issue":null}
""".strip()

    payload = AgentRunner._extract_json_object(output)

    assert payload["status"] == "verified_candidate"
    assert payload["lean_path"] == "Paper/Test.lean"


def test_run_claude_prover_uses_run_dir_cwd_and_extracts_json(tmp_path: Path) -> None:
    runner = AgentRunner(
        run_dir=tmp_path,
        paper_dir=tmp_path / "paper",
        main_tex=tmp_path / "paper" / "main.tex",
        root_module="Paper",
        prover_backend="claude",
        verifier_backend="claude",
        agent_timeout_seconds=30,
    )

    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, "cwd": kwargs.get("cwd")})
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=(
                "Some progress text\n"
                '{"status":"verified_candidate","summary":"ok","lean_path":"Paper/Test.lean",'
                '"proof_complete":true,"weeks_scale":false,"weeks_scale_reason":null,"paper_issue":null}\n'
            ),
        )

    with (
        patch("paper_badger.agents.shutil.which", return_value="/usr/bin/claude"),
        patch("paper_badger.agents.subprocess.run", side_effect=fake_run),
    ):
        payload = runner._run_claude("Prove something.", {"type": "object"}, role_name="prover", cwd=tmp_path)

    assert payload["lean_path"] == "Paper/Test.lean"
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["cmd"][:2] == ["claude", "-p"]
