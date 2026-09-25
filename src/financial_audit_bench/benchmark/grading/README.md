# Grading

```bash
uv run fab run --model gpt-6-astra --grade
```

Requires LibreOffice and grader API credentials. The grader recalculates workbook
copies, validates templates, and applies deterministic and LLM-based rubric checks.
Unchanged template content does not earn credit.

Each trial saves scores and feedback in `verifier/report.json` and annotated
workbooks in `verifier/annotated/`. Judge settings are in [judge.py](judge.py).
