from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .latex import sanitize_label_stem, statement_context_summary

if TYPE_CHECKING:
    from .models import StatementTask

PROVER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "summary",
        "lean_path",
        "proof_complete",
        "weeks_scale",
        "weeks_scale_reason",
        "paper_issue",
    ],
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "verified_candidate",
                "formalized_candidate",
                "needs_retry",
                "weeks_scale",
            ],
        },
        "summary": {"type": "string"},
        "lean_path": {"type": ["string", "null"]},
        "proof_complete": {"type": "boolean"},
        "weeks_scale": {"type": "boolean"},
        "weeks_scale_reason": {"type": ["string", "null"]},
        "paper_issue": {"type": ["string", "null"]},
    },
}

VERIFIER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision",
        "summary",
        "exact_match",
        "extra_hypotheses",
        "weeks_scale_reason",
    ],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["verified", "formalized", "retry", "weeks_scale"],
        },
        "summary": {"type": "string"},
        "exact_match": {"type": "boolean"},
        "extra_hypotheses": {"type": "boolean"},
        "weeks_scale_reason": {"type": ["string", "null"]},
    },
}


@dataclass
class ProverResult:
    status: str
    summary: str
    lean_path: str | None
    proof_complete: bool
    weeks_scale: bool
    weeks_scale_reason: str | None = None
    paper_issue: str | None = None


@dataclass
class VerifierResult:
    decision: str
    summary: str
    exact_match: bool
    extra_hypotheses: bool
    weeks_scale_reason: str | None = None


class AgentTimeoutError(RuntimeError):
    """Raised when a prover or verifier call exceeds the configured timeout."""


class AgentInvocationError(RuntimeError):
    """Invocation failed before returning valid structured output."""


class AgentTransportError(AgentInvocationError):
    """Backend connection failed before returning structured output."""


class AgentRunner:
    def __init__(
        self,
        run_dir: Path,
        paper_dir: Path,
        main_tex: Path,
        root_module: str | None,
        prover_backend: str,
        verifier_backend: str,
        agent_timeout_seconds: int | None = None,
        codex_model: str | None = None,
        claude_model: str | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.paper_dir = paper_dir
        self.main_tex = main_tex
        self.root_module = root_module
        self.prover_backend = prover_backend
        self.verifier_backend = verifier_backend
        self.agent_timeout_seconds = agent_timeout_seconds
        self.codex_model = codex_model
        self.claude_model = claude_model

    def run_prover(self, task: StatementTask) -> ProverResult:
        prompt = self._build_prover_prompt(task)
        if self.prover_backend == "claude":
            payload = self._run_claude(
                prompt,
                PROVER_SCHEMA,
                role_name="prover",
                cwd=self.run_dir,
            )
        else:
            payload = self._run_codex(prompt, PROVER_SCHEMA, sandbox="workspace-write")
        return ProverResult(**payload)

    def run_verifier(
        self,
        task: StatementTask,
        prover_result: ProverResult,
        compile_ok: bool,
        compile_output: str,
    ) -> VerifierResult:
        lean_text = ""
        if prover_result.lean_path:
            lean_file = self.run_dir / prover_result.lean_path
            if lean_file.exists():
                lean_text = lean_file.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )[:24000]
        prompt = self._build_verifier_prompt(
            task,
            prover_result,
            compile_ok,
            compile_output,
            lean_text,
        )
        if self.verifier_backend == "claude":
            payload = self._run_claude(prompt, VERIFIER_SCHEMA, role_name="verifier")
        else:
            payload = self._run_codex(prompt, VERIFIER_SCHEMA, sandbox="read-only")
        return VerifierResult(**payload)

    def _run_codex(self, prompt: str, schema: dict[str, Any], sandbox: str) -> dict[str, Any]:
        self._ensure_cli_available("codex", "Codex")
        with tempfile.TemporaryDirectory(prefix="paper-badger-") as tmp_dir:
            tmp = Path(tmp_dir)
            schema_path = tmp / "schema.json"
            out_path = tmp / "out.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            cmd = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--cd",
                str(self.run_dir),
                "--sandbox",
                sandbox,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(out_path),
                "-",
            ]
            if sandbox == "workspace-write":
                cmd.insert(2, "--full-auto")
            if self.codex_model:
                cmd[2:2] = ["--model", self.codex_model]
            try:
                result = subprocess.run(
                    cmd,
                    input=prompt,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=self.agent_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                timeout = self.agent_timeout_seconds
                raise AgentTimeoutError(
                    f"Codex agent timed out after {timeout}s.",
                ) from exc
            output = result.stdout or ""
            if result.returncode != 0:
                if self._looks_like_transport_error(output):
                    raise AgentTransportError(
                        self._transport_error_message("Codex", output),
                    )
                raise AgentInvocationError(
                    self._invocation_error_message("Codex", result.returncode, output),
                )
            try:
                result_data: dict[str, Any] = json.loads(out_path.read_text(encoding="utf-8"))
                return result_data
            except FileNotFoundError as exc:
                raise AgentInvocationError("Codex agent finished without writing structured output.") from exc
            except json.JSONDecodeError as exc:
                if self._looks_like_transport_error(output):
                    raise AgentTransportError(self._transport_error_message("Codex", output)) from exc
                raise AgentInvocationError("Codex agent returned invalid or empty structured output.") from exc

    def _run_claude(
        self,
        prompt: str,
        schema: dict[str, Any],
        role_name: str,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        self._ensure_cli_available("claude", "Claude")
        cmd = ["claude", "-p"]
        if self.claude_model:
            cmd.extend(["--model", self.claude_model])
        schema_text = json.dumps(schema)
        cmd.append(
            f"{prompt}\n\nReturn only JSON matching this schema:\n{schema_text}",
        )
        try:
            result = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                check=True,
                timeout=self.agent_timeout_seconds,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            timeout = self.agent_timeout_seconds
            raise AgentTimeoutError(
                f"Claude {role_name} timed out after {timeout}s.",
            ) from exc
        except subprocess.CalledProcessError as exc:
            output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
            if self._looks_like_transport_error(output):
                raise AgentTransportError(
                    self._transport_error_message("Claude", output),
                ) from exc
            raise AgentInvocationError(
                self._invocation_error_message(
                    "Claude",
                    exc.returncode,
                    output,
                ),
            ) from exc
        try:
            return self._extract_json_object(result.stdout)
        except json.JSONDecodeError as exc:
            if self._looks_like_transport_error(result.stdout or ""):
                raise AgentTransportError(
                    self._transport_error_message(
                        "Claude",
                        result.stdout or "",
                    ),
                ) from exc
            raise AgentInvocationError(f"Claude {role_name} returned invalid JSON.") from exc

    @staticmethod
    def _looks_like_transport_error(output: str) -> bool:
        haystack = output.lower()
        needles = [
            "stream disconnected",
            "error sending request",
            "reconnecting...",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "tls handshake",
            "timed out waiting for headers",
            "backend-api/codex/responses",
            "backend-api/codex/models",
        ]
        return any(needle in haystack for needle in needles)

    @staticmethod
    def _ensure_cli_available(command: str, label: str) -> None:
        if shutil.which(command):
            return
        raise AgentInvocationError(f"{label} CLI not found on PATH.")

    @staticmethod
    def _transport_error_message(agent_name: str, output: str) -> str:
        tail = AgentRunner._tail_lines(output)
        suffix = f" Details: {tail}" if tail else ""
        return f"{agent_name} transport disconnected before returning a result.{suffix}"

    @staticmethod
    def _invocation_error_message(agent_name: str, returncode: int, output: str) -> str:
        tail = AgentRunner._tail_lines(output)
        suffix = f" Details: {tail}" if tail else ""
        return f"{agent_name} agent failed with exit status {returncode}.{suffix}"

    @staticmethod
    def _tail_lines(output: str, max_lines: int = 4, max_chars: int = 500) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return ""
        joined = " | ".join(lines[-max_lines:])
        if len(joined) > max_chars:
            return joined[-max_chars:]
        return joined

    @staticmethod
    def _extract_json_object(output: str) -> dict[str, Any]:
        decoder = json.JSONDecoder()
        last_dict: dict[str, Any] | None = None
        for start, char in enumerate(output):
            if char != "{":
                continue
            try:
                parsed: Any = decoder.raw_decode(output[start:])
                value: Any = parsed[0]
                end: int = parsed[1]
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                trailing = output[start + end :].strip()
                if not trailing:
                    return value  # type: ignore[no-any-return,unused-ignore]
                last_dict = value  # type: ignore[assignment,unused-ignore]
        if last_dict is not None:
            return last_dict
        raise json.JSONDecodeError(
            "No top-level JSON object found.",
            output,
            0,
        )

    def _build_prover_prompt(self, task: StatementTask) -> str:
        root = self.root_module or "Paper"
        stem = sanitize_label_stem(task.label)
        kind_dir = task.kind.capitalize()
        lean_target = f"{root}/Formalizations/{kind_dir}/{stem}.lean"
        paper_context = statement_context_summary(
            self.paper_dir,
            task.tex_path,
            task.sequence_index,
        )
        history = "\n".join(f"- {entry}" for entry in task.attempt_history) or "- none"
        retry_context = ""
        if task.summary or task.last_error or task.lean_path:
            retry_context = f"""

Previous attempt context:
- previous lean file: {task.lean_path or ""}
- verifier / supervisor feedback: {task.last_error or task.summary or ""}
- recent attempt history:
{history}

Address that feedback directly in this attempt. If the formalization is mathematically correct but the verifier lacked context, make that context explicit in your final summary.
""".rstrip()
        return f"""
You are the prover in a paper-badger formalization workflow.

Work only on this single statement until one of the following is true:
1. the proof is formalized cleanly enough to be a verified candidate,
2. the statement is formalized exactly but the proof is still incomplete, or
3. further progress would genuinely take LLM-weeks.

Rules:
- Work in the current repository only.
- Do not edit files under `paper/`; the supervisor is the only component that inserts badges or changes the paper source.
- Keep `PROVER.md` updated with concise progress notes as you go.
- Keep `TODO.md` current.
- If the paper has a real ambiguity or mistake, append it to `ISSUES.md`.
- Create a human-readable Lean file for this statement.
- Do not leave `sorry`.
- Fix linter warnings in files you touch.
- Prefer creating or extending small supporting libraries rather than a monolithic file.
- When a theorem is not yet proved, it is acceptable to formalize the statement exactly in a non-verified way, but be explicit about that in the final JSON.

Statement metadata:
- kind: {task.kind}
- label: {task.label}
- title: {task.title or ""}
- tex file: {task.tex_path}
- suggested lean file: {lean_target}
{retry_context}

Statement excerpt:
{task.source_excerpt}

Bounded paper context:
{paper_context or "- none"}
""".strip()

    def _build_verifier_prompt(
        self,
        task: StatementTask,
        prover_result: ProverResult,
        compile_ok: bool,
        compile_output: str,
        lean_text: str,
    ) -> str:
        paper_context = statement_context_summary(
            self.paper_dir,
            task.tex_path,
            task.sequence_index,
        )
        history = "\n".join(f"- {entry}" for entry in task.attempt_history) or "- none"
        return f"""
You are the verifier in a paper-badger formalization workflow.

Decide whether this item deserves a `verified` badge, a `formalized` badge, should be retried, or should be classified as LLM-weeks-scale.

Important constraints:
- Use only the materials included in this prompt.
- Do not inspect the workspace.
- Do not run commands.
- Return a decision immediately from the provided excerpt, Lean text, and compile result.
- If the provided material is insufficient to justify `verified`, prefer `formalized` or `retry`.

Standards:
- The Lean statement must match the paper statement exactly in mathematical content.
- Ambient assumptions may be justified by the bounded paper context when that context explicitly states them.
- Reject additional hypotheses, weakened conclusions, or hidden assumptions.
- `verified` requires a complete proof and successful compilation.
- `formalized` means the statement is exact but the proof is not complete enough for `verified`.
- Assess globally: use the recent attempt history to judge whether the task is still improving or whether the remaining gap honestly looks like LLM-weeks.
- Return `weeks_scale` only when that global assessment justifies it, not merely because there have been several attempts.

Paper statement metadata:
- kind: {task.kind}
- label: {task.label}
- title: {task.title or ""}
- tex file: {task.tex_path}

Bounded paper context:
{paper_context or "- none"}

Recent attempt history:
{history}

Paper excerpt:
{task.source_excerpt}

Prover summary:
{prover_result.summary}

Compile succeeded: {compile_ok}
Compile output:
{compile_output[:2000]}

Lean file contents:
{lean_text[:12000]}
""".strip()
