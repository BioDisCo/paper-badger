from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StatementTask:
    task_id: str
    tex_path: str
    env_name: str
    label: str
    title: str | None
    sequence_index: int
    kind: str
    source_excerpt: str
    status: str = "pending"
    attempts: int = 0
    lean_path: str | None = None
    badge_kind: str | None = None
    summary: str | None = None
    weeks_scale_reason: str | None = None
    last_error: str | None = None
    attempt_history: list[str] = field(default_factory=lambda: list[str]())  # noqa: PLW0108
    transport_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatementTask:
        return cls(**data)


@dataclass
class RunState:
    arxiv_id: str
    run_dir: str
    paper_dir: str
    main_tex: str
    repo_url: str | None
    branch: str | None
    prover_backend: str
    verifier_backend: str
    badge_link_mode: str = "auto"
    explicit_repo_target: bool = False
    is_local_paper: bool = False
    root_module: str | None = None
    tasks: list[StatementTask] = field(default_factory=lambda: list[StatementTask]())  # noqa: PLW0108

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tasks"] = [task.to_dict() for task in self.tasks]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunState:
        data = dict(data)
        data.setdefault("root_module", None)
        data.setdefault("explicit_repo_target", False)
        data.setdefault("prover_backend", "codex")
        data.setdefault("verifier_backend", "claude")
        data["tasks"] = [StatementTask.from_dict(item) for item in data.get("tasks", [])]
        return cls(**data)

    @property
    def state_path(self) -> Path:
        return Path(self.run_dir) / "state.json"
