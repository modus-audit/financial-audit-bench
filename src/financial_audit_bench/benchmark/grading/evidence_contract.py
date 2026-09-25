"""The evidence budget applies to a scored criterion, not each model request."""

MAX_EVIDENCE_CELLS = 3


def criterion_character_limit(full_table: bool) -> int:
    """Full-table coverage criteria may enumerate all required selection IDs."""
    return 2000 if full_table else 500


def validate_cell_count(count: int) -> None:
    if not 1 <= count <= MAX_EVIDENCE_CELLS:
        raise ValueError(
            "semantic checks must select at most three answer cells in total "
            f"(selected {count}); narrow the rubric selector rather than splitting the check"
        )
