# paper-badger

Automated Lean 4 formalization and verification badges for research papers.

`paper-badger` fetches an arXiv paper (or uses a local LaTeX directory), extracts theorem-like statements, runs LLM-backed prover and verifier agents to formalize them in Lean 4 + Mathlib, and inserts [verified-badges](https://github.com/BioDisCo/verified-badges) links back into the paper source.

## How it works

```
arXiv paper ──> extract statements ──> LLM prover ──> Lean compilation
      │                                                       │
      │                                              LLM verifier
      │                                                       │
      └──────────────── insert badges <────────────── accept / retry
```

1. Downloads and extracts an arXiv source bundle (or copies a local directory).
2. Detects definitions, lemmas, propositions, corollaries, and theorems.
3. Initializes a Lean 4 + Mathlib workspace.
4. Loops: prover agent proposes a formalization, `lake build` compiles it, verifier agent checks correctness.
5. Accepted statements get a `\leanproof{}` or `\leanformalized{}` badge inserted into the LaTeX source.
6. Progress is saved to `state.json` after every task, so runs are fully resumable.

## Requirements

- Python 3.11+
- [Lean 4](https://leanprover.github.io/lean4/doc/quickstart.html) (`lake` on PATH)
- `git`
- At least one LLM CLI: [`claude`](https://docs.anthropic.com/en/docs/claude-cli) or [`codex`](https://github.com/openai/codex)

## Install

```bash
pip install paper-badger
```

## Usage

### Formalize an arXiv paper

```bash
paper-badger 2401.01234
```

The `run` subcommand is implied, so `paper-badger run 2401.01234` is equivalent.

### Formalize a local paper

```bash
paper-badger run my-paper --paper-dir path/to/latex/
```

### Choose backends

By default the prover is `codex` and the verifier is `claude`. To use Claude for both:

```bash
paper-badger run 2401.01234 --prover-backend claude --verifier-backend claude
```

### Monitor a run

```bash
paper-badger monitor 2401.01234          # live dashboard
paper-badger monitor 2401.01234 --once   # single snapshot
```

### Badge link modes

| Mode     | Behavior |
|----------|----------|
| `local`  | `file://` links relative to the paper directory (default) |
| `github` | GitHub blob links using `--repo-url` and `--branch` |
| `auto`   | GitHub links when repo info is provided, local otherwise |

```bash
paper-badger run 2401.01234 \
  --badge-link-mode github \
  --repo-url https://github.com/you/repo \
  --branch main
```

## Run directory layout

```
runs/<paper-id>/
  paper/                              # LaTeX sources (badges inserted here)
  <RootModule>/Formalizations/        # Generated Lean files
  state.json                          # Resumable run state
  TODO.md                             # Task checklist
  PROVER.md                           # Current prover status
  ISSUES.md                           # Paper issues found during the run
```


