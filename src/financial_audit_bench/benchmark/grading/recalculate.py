"""Recalculate isolated workbook copies without modifying submitted artifacts."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Recalculation of isolated temporary workbook copies


TIMEOUT_SECONDS = 180


# The runtime's default is to trust existing OOXML formula caches. A plain
# --convert-to therefore need not recalculate even when those caches are stale.
# Calc ScLkUpdMode uses LM_NEVER=1 (sc/inc/global.hxx), unlike the UNO enum.
_RECALCULATION_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Calc/Formula/Load">
    <prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Calc/Content/Update">
    <prop oor:name="Link" oor:op="fuse"><value>1</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  </item>
</oor:items>
"""


class RecalculationError(RuntimeError):
    """A workbook could not be recalculated safely and deterministically."""


@contextmanager
def recalculated_workbooks(
    workbooks: Mapping[str, Path],
    *,
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> Iterator[dict[str, Path]]:
    """Yield recalculated copies without changing submissions or inheriting secrets."""
    if not workbooks:
        raise RecalculationError("no workbooks supplied")
    soffice = shutil.which("soffice")
    if soffice is None:
        raise RecalculationError(
            "Install LibreOffice and make soffice available on PATH for local grading"
        )

    with tempfile.TemporaryDirectory(prefix="fab-recalculate-") as temporary:
        root = Path(temporary)
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        profile = root / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(
            _RECALCULATION_PROFILE, encoding="utf-8"
        )

        filenames: dict[str, str] = {}
        for index, (name, source) in enumerate(sorted(workbooks.items()), start=1):
            source = Path(source)
            if source.suffix.casefold() != ".xlsx" or not source.is_file():
                raise RecalculationError(f"invalid .xlsx workbook: {source}")
            filename = f"workbook_{index}.xlsx"
            shutil.copy2(source, input_dir / filename)
            filenames[name] = filename

        command = [
            soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(output_dir),
            *[str(input_dir / filename) for filename in filenames.values()],
        ]
        try:
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                env={
                    "PATH": os.defpath,
                    "HOME": str(root),
                    "LANG": "C.UTF-8",
                    "XDG_CACHE_HOME": str(root / "cache"),
                },
            ) as process:
                try:
                    stdout, stderr = process.communicate(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RecalculationError(
                f"LibreOffice recalculation failed: {error}"
            ) from error
        if process.returncode:
            detail = (stderr or stdout).strip()
            raise RecalculationError(f"LibreOffice recalculation failed: {detail}")

        recalculated = {
            name: output_dir / filename for name, filename in filenames.items()
        }
        missing = [name for name, path in recalculated.items() if not path.is_file()]
        if missing:
            raise RecalculationError(f"LibreOffice produced no output for: {missing}")
        yield recalculated
