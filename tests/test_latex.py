from pathlib import Path

from paper_badger.latex import (
    detect_main_tex,
    ensure_verified_badges_package,
    extract_statements_from_tree,
    insert_badge_for_task,
    locate_statement,
    set_badge_for_task,
    statement_context_summary,
)


def test_extract_statements_and_insert_badge(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    tex = paper_dir / "paper.tex"
    tex.write_text(
        r"""
\documentclass{article}
\begin{document}
\begin{definition}[Widget]
\label{def:widget}
A widget is a gadget.
\end{definition}
\begin{theorem}[Main result]
\label{thm:main}
Every widget is useful.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    statements = extract_statements_from_tree(paper_dir)

    assert [statement.label for statement in statements] == ["def:widget", "thm:main"]

    theorem = next(statement for statement in statements if statement.label == "thm:main")
    inserted = insert_badge_for_task(
        paper_dir,
        theorem.task_id,
        theorem.tex_path,
        theorem.sequence_index,
        r"\leanproof{https://example.com/proof}",
    )

    assert inserted
    updated = tex.read_text(encoding="utf-8")
    assert r"\begin{theorem}[Main result \leanproof{https://example.com/proof}]" in updated


def test_usepackage_insertion_and_main_tex_detection(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    helper = paper_dir / "helper.tex"
    helper.write_text(r"\begin{lemma}Hi\end{lemma}", encoding="utf-8")
    main = paper_dir / "main.tex"
    main.write_text(
        r"""
\documentclass{article}
\usepackage{amsmath}
\begin{document}
Hello
\end{document}
""".strip(),
        encoding="utf-8",
    )

    detected = detect_main_tex([helper, main])
    changed = ensure_verified_badges_package(main)
    changed_again = ensure_verified_badges_package(main)

    assert detected == main
    assert changed
    assert not changed_again
    assert r"\usepackage{verified-badges}" in main.read_text(encoding="utf-8")


def test_extract_ignores_preamble_and_supports_uppercase_envs(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    tex = paper_dir / "paper.tex"
    tex.write_text(
        r"""
\documentclass{article}
\newenvironment{remark}[1]{\begin{Rem}\label{R:#1}}{\end{Rem}}
\begin{document}
\begin{Prop}\label{P:main}
Useful proposition.
\end{Prop}
\begin{Rem}\label{R:obs}
Helpful observation.
\end{Rem}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    statements = extract_statements_from_tree(paper_dir)

    assert [(s.kind, s.label) for s in statements] == [("proposition", "P:main")]


def test_extract_skips_commented_out_statement_bodies_and_prioritizes_labeled_items(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    tex = paper_dir / "paper.tex"
    tex.write_text(
        r"""
\documentclass{article}
\begin{document}
\begin{definition}
Visible unlabeled definition.
\end{definition}
\begin{lemma}
\label{lem:commented}
% This whole lemma statement is commented out.
% It should not become a tracked task.
\end{lemma}
\begin{theorem}
\label{thm:labeled}
Visible labeled theorem.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    statements = extract_statements_from_tree(paper_dir)

    assert [statement.label for statement in statements] == ["thm:labeled", "synthetic:definition:definition-0"]


def test_locate_and_context_work_with_sparse_sequence_indices(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    tex = paper_dir / "paper.tex"
    tex.write_text(
        r"""
\documentclass{article}
\title{Sparse Index Note}
\begin{document}
\begin{lemma}
\label{lem:commented}
% This one is commented out.
\end{lemma}
\begin{theorem}
\label{thm:kept}
Visible theorem.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    statements = extract_statements_from_tree(paper_dir)
    theorem = statements[0]
    located = locate_statement(paper_dir, theorem.task_id, theorem.tex_path, theorem.sequence_index)
    context = statement_context_summary(paper_dir, theorem.tex_path, theorem.sequence_index)

    assert theorem.sequence_index == 1
    assert located.extracted.task_id == theorem.task_id
    assert "Paper title: Sparse Index Note" in context


def test_insert_badge_preserves_preamble_text(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    tex = paper_dir / "paper.tex"
    tex.write_text(
        r"""
\documentclass{article}
\usepackage{amsmath}
\begin{document}
\begin{definition}[Widget]
\label{def:widget}
A widget is a gadget.
\end{definition}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    statements = extract_statements_from_tree(paper_dir)
    definition = statements[0]
    inserted = insert_badge_for_task(
        paper_dir,
        definition.task_id,
        definition.tex_path,
        definition.sequence_index,
        r"\leanproof{https://example.com/proof}",
    )

    updated = tex.read_text(encoding="utf-8")

    assert inserted
    assert updated.startswith("\\documentclass{article}\n\\usepackage{amsmath}\n")
    assert r"\begin{definition}[Widget \leanproof{https://example.com/proof}]" in updated


def test_set_badge_for_task_can_replace_and_remove_badges(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    tex = paper_dir / "paper.tex"
    tex.write_text(
        r"""
\documentclass{article}
\begin{document}
\begin{theorem}[Main result \leanproof{old/path.lean}]
\label{thm:main}
Every widget is useful.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    statements = extract_statements_from_tree(paper_dir)
    theorem = statements[0]

    replaced = set_badge_for_task(
        paper_dir,
        theorem.task_id,
        theorem.tex_path,
        theorem.sequence_index,
        r"\leanformalized{new/path.lean}",
    )
    removed = set_badge_for_task(
        paper_dir,
        theorem.task_id,
        theorem.tex_path,
        theorem.sequence_index,
        None,
    )

    updated = tex.read_text(encoding="utf-8")

    assert replaced
    assert removed
    assert r"\leanproof{old/path.lean}" not in updated
    assert r"\leanformalized{new/path.lean}" not in updated
    assert r"\begin{theorem}[Main result]" in updated


def test_statement_context_summary_includes_title_and_previous_statements(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    tex = paper_dir / "paper.tex"
    tex.write_text(
        r"""
\documentclass{article}
\title{A Note About Natural Numbers}
\begin{document}
\begin{definition}[Even natural number]
\label{def:even}
A natural number $n$ is even if there exists a natural number $k$ such that $n = 2k$.
\end{definition}
\begin{theorem}[Main]
\label{thm:main}
If $m$ and $n$ are even, then $m+n$ is even.
\end{theorem}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    context = statement_context_summary(paper_dir, "paper.tex", 1)

    assert "Paper title: A Note About Natural Numbers" in context
    assert "definition `def:even`" in context
    assert "A natural number $n$ is even" in context
