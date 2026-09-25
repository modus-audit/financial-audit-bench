"""Monospace document primitives with line-addressed lineage ties."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.public_presentation import (
    present_public_text,
)

WIDTH = 78

_LABELED_SIGNATURE = re.compile(
    r"(?i)\b(By|Prepared(?: by)?|Approved(?: by)?|Released(?: by)?|Owner|"
    r"Contractor|Acknowledged(?: by)?|Certified by|Reviewed(?: by)?|Borrower|"
    r"Lender|Portfolio Company|Fund|Accepted)"
    r":\s*/s/\s*"
)
_UNPUNCTUATED_BY_SIGNATURE = re.compile(r"(?i)\bBy\s+/s/\s*")
_CONFORMED_SIGNATURE = re.compile(r"(?i)(?<!\w)/s/\s*")


def normalize_signature_notation(text: str) -> str:
    """Remove legal-transcript ``/s/`` notation from rendered documents."""
    normalized = _LABELED_SIGNATURE.sub(lambda match: f"{match.group(1)}: ", text)
    normalized = _UNPUNCTUATED_BY_SIGNATURE.sub("By ", normalized)
    return _CONFORMED_SIGNATURE.sub("Signed: ", normalized)


class TextDoc:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.ties: list[dict[str, Any]] = []

    def _append(self, text: str = "") -> None:
        self.lines.append(
            present_public_text(normalize_signature_notation(text)).rstrip()
        )

    def line(self, text: str = "") -> None:
        normalized = present_public_text(normalize_signature_notation(text))
        self._append(normalized)

    def wrapped(self, text: str) -> None:
        """A paragraph wrapped to the document width (legal boilerplate)."""
        import textwrap

        chunk = textwrap.wrap(
            present_public_text(normalize_signature_notation(text)), WIDTH
        )
        for wrapped_line in chunk:
            self._append(wrapped_line)

    def center(self, text: str) -> None:
        text = present_public_text(normalize_signature_notation(text))
        self._append(text.center(WIDTH))

    def rule(self, char: str = "-") -> None:
        self._append(char * WIDTH)

    def pair(self, label: str, value: Any) -> None:
        """A label/value line without a lineage tie (identity, dates)."""
        label = present_public_text(normalize_signature_notation(label))
        value = present_public_text(normalize_signature_notation(str(value)))
        self._append(f"{label:<40}{str(value):>38}")

    def tie(self, field: str, label: str, value: Any) -> None:
        """A label/value line whose value ties back to the world."""
        rendered = present_public_text(str(value))
        self.pair(label, rendered)
        self.ties.append({"field": field, "line": len(self.lines), "value": rendered})

    def signature(
        self, name: str, style: int | None = None, mode: str = "handwritten"
    ) -> None:
        """Render an explicit labeled signature."""
        del style, mode
        self._append(f"  Signed by: {name}")

    def checkbox(self, label: str, *, checked: bool, mode: str) -> None:
        label = present_public_text(label)
        mark = "X" if checked else " "
        self._append(f"  [{mark}] {label}")
        del mode

    def stamp(self, lines: list[str]) -> None:
        """Render a receipt stamp as a bracketed line."""
        lines = [present_public_text(line) for line in lines]
        self._append(f"[{' - '.join(lines)}]")

    def table(self, headers: list[str], rows: list[list[Any]], widths: list[int]):
        if len(headers) != len(widths):
            raise ValueError("table headers and widths must have equal length")
        if any(width <= 0 for width in widths):
            raise ValueError("table column widths must be positive")
        headers = [
            present_public_text(normalize_signature_notation(str(header)))
            for header in headers
        ]
        rows = [
            [
                present_public_text(normalize_signature_notation(str(cell)))
                for cell in row
            ]
            for row in rows
        ]

        def render(cells: list[Any]) -> str:
            return "  ".join(
                (
                    f"{str(cell):<{width}}"[:width]
                    if index == 0
                    else (
                        f"{str(cell):>{width}}"[:width]
                        if str(cell)
                        .replace(".", "")
                        .replace("-", "")
                        .replace(",", "")
                        .isdigit()
                        else f"{str(cell):<{width}}"[:width]
                    )
                )
                for index, (cell, width) in enumerate(zip(cells, widths))
            ).rstrip()

        self._append(render(headers))
        self._append("-" * WIDTH)
        for row in rows:
            self._append(render(row))

    def write(self, path: Path) -> list[dict[str, Any]]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n")
        return self.ties
