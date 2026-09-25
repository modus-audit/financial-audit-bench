"""Shared, verified recalculation batches for local grading workers."""

from __future__ import annotations

import hashlib
import json
import shutil
from itertools import batched
from pathlib import Path
from threading import Lock

from . import recalculate
from .recalculate import RecalculationError
from .results import write_json

BATCH_SIZE = 32


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RecalculationCache:
    """One producer lock per invocation; workers only read verified outputs.

    The host owns this directory. It is never supplied by an agent. Source
    content, recalculation code, and output hashes must match before reuse.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.code_hash = _digest(Path(recalculate.__file__))
        self._lock = Lock()

    def resolve(self, source: Path) -> Path:
        """Require an already prepared copy; never fall back to launching Calc."""
        key = _digest(source)
        manifest = self.directory / f"{key}.json"
        output = self.directory / f"{key}.xlsx"
        try:
            record = json.loads(manifest.read_text())
        except (OSError, ValueError) as error:
            raise RecalculationError(
                f"No valid batch recalculation for {source}"
            ) from error
        if (
            record.get("source_sha256") != key
            or record.get("recalculation_sha256") != self.code_hash
        ):
            raise RecalculationError(f"Recalculation provenance mismatch: {source}")
        if record.get("error"):
            raise RecalculationError(record["error"])
        if not output.is_file() or _digest(output) != record.get("output_sha256"):
            raise RecalculationError(
                f"Recalculated output missing or changed: {source}"
            )
        return output

    def prepare(
        self, sources: list[Path], *, batch_size: int = BATCH_SIZE
    ) -> dict[str, str]:
        """Prepare bounded batches and return per-file errors for diagnostics."""
        errors = {}
        for group in batched(sources, batch_size):
            # Shared across model jobs: at most one Calc process per CLI invocation.
            with self._lock:
                pending = {}
                for source in group:
                    try:
                        if (
                            source.is_symlink()
                            or not source.is_file()
                            or source.suffix.lower() != ".xlsx"
                        ):
                            raise RecalculationError(
                                f"Invalid .xlsx submission: {source}"
                            )
                        key = _digest(source)
                        if (self.directory / f"{key}.json").exists():
                            self.resolve(source)
                        else:
                            pending[key] = source
                    except (OSError, RecalculationError) as error:
                        errors[str(source)] = str(error)
                errors.update(self._prepare_group(pending))
        return errors

    def _prepare_group(self, sources: dict[str, Path]) -> dict[str, str]:
        # A split retry must not replace copies already published successfully.
        sources = {
            key: source
            for key, source in sources.items()
            if not (self.directory / f"{key}.json").exists()
        }
        if not sources:
            return {}
        try:
            timeout = max(recalculate.TIMEOUT_SECONDS, 15 * len(sources))
            with recalculate.recalculated_workbooks(
                sources, timeout_seconds=timeout
            ) as outputs:
                for key in sources:
                    if _digest(sources[key]) != key:
                        raise RecalculationError(
                            "Submission changed during recalculation"
                        )
                for key, output in outputs.items():
                    target = self.directory / f"{key}.xlsx"
                    temporary = target.with_suffix(".xlsx.tmp")
                    shutil.copyfile(output, temporary)
                    temporary.replace(target)
                    write_json(
                        self.directory / f"{key}.json",
                        {
                            "source_sha256": key,
                            "recalculation_sha256": self.code_hash,
                            "output_sha256": _digest(target),
                        },
                    )
            return {}
        except (OSError, RecalculationError) as error:
            if len(sources) > 1:
                items = list(sources.items())
                middle = len(items) // 2
                return {
                    **self._prepare_group(dict(items[:middle])),
                    **self._prepare_group(dict(items[middle:])),
                }
            key, source = next(iter(sources.items()))
            message = f"Batch recalculation failed for {source}: {error}"
            write_json(
                self.directory / f"{key}.json",
                {
                    "source_sha256": key,
                    "recalculation_sha256": self.code_hash,
                    "error": message,
                },
            )
            return {str(source): message}
