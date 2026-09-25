"""PBC projection: field specs, workbooks, and documents over the world."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_registry,
    to_mutable_json,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.render import (
    render_core_pbcs,
)


_REVENUE_SOURCE_REQUESTS = frozenset({"AR-03", "AR-04", "AR-06", "AR-16"})


def _organize_shared_revenue_evidence(
    file_map: list[dict[str, Any]], package_dir: Path
) -> None:
    """Place sales/cutoff source records where the revenue task can find them."""
    revenue_dir = package_dir / "revenue"
    for entry in file_map:
        if entry.get("request_id") not in _REVENUE_SOURCE_REQUESTS:
            continue
        source = Path(entry["path"])
        if not source.is_file():
            continue
        revenue_dir.mkdir(parents=True, exist_ok=True)
        destination = revenue_dir / source.name
        source.rename(destination)
        entry["path"] = str(destination)
        entry["family"] = "revenue"


def _package_index(
    world: dict[str, Any], file_map: list[dict[str, Any]], package_dir: Path
) -> dict[str, Any]:
    """Publish one controlled index over every final delivered artifact."""
    from openpyxl import Workbook

    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
        finalize_workbook,
        write_header,
    )
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.public_presentation import (
        present_public_text,
    )

    users = world.get("accounting_user") or []
    preparer = next(
        (row["user_name"] for row in users if row.get("privilege_level") == "preparer"),
        "Finance preparer",
    )
    reviewer = next(
        (row["user_name"] for row in users if row.get("privilege_level") == "approver"),
        "Controller",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Final Package Index"
    write_header(
        sheet,
        [
            "Final File",
            "Evidence Category",
            "Purpose / Evidence Family",
            "Version Status",
            "Preparer",
            "Reviewer",
            "Package Date",
        ],
    )
    artifacts_by_path = {str(Path(row["path"]).resolve()): row for row in file_map}
    delivered_roots = (package_dir, package_dir.parent / "direct_to_auditor")
    for delivered_root in delivered_roots:
        if not delivered_root.exists():
            continue
        for delivered_path in delivered_root.rglob("*"):
            if (
                not delivered_path.is_file()
                or delivered_path.name == "Package Index.xlsx"
                or delivered_path.name.startswith("~$")
            ):
                continue
            resolved = str(delivered_path.resolve())
            if resolved in artifacts_by_path:
                continue
            lower_name = delivered_path.name.lower()
            family = (
                "foundational"
                if lower_name.startswith("tb") or "trial balance" in lower_name
                else "package_control"
            )
            artifacts_by_path[resolved] = {
                "family": family,
                "path": str(delivered_path),
            }
    for artifact in sorted(
        artifacts_by_path.values(), key=lambda row: str(row["path"])
    ):
        family = str(artifact.get("family") or "support").replace("_", " ").title()
        if family == "Foundational":
            family = "Governance Records"
        purpose = Path(str(artifact["path"])).stem.replace("_", " ")
        relative_path = str(Path(artifact["path"]).relative_to(package_dir.parent))
        sheet.append(
            [
                relative_path,
                present_public_text(family),
                present_public_text(purpose),
                "Final - authoritative",
                present_public_text(preparer),
                present_public_text(reviewer),
                world["fiscal_calendar"]["end_date"],
            ]
        )
    sheet.freeze_panes = "A2"
    path = package_dir / "Package Index.xlsx"
    finalize_workbook(workbook)
    workbook.save(path)
    return {
        "request_id": "DOC-PACKAGE-INDEX",
        "family": "package_control",
        "path": str(path),
        "cell_map": [],
    }


def package_binder(
    world: dict[str, Any],
    run_dir: Path,
    *,
    audit_plan: dict[str, Any],
) -> dict[str, Any]:
    """Render the complete public PBC package from a generated world."""
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents import (
        render_documents,
    )
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational import (
        render_foundational_pbcs,
    )
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
        materialize_pbc_workbooks,
    )

    registry = to_mutable_json(load_registry("registry.pbc.requests.v1").values)
    # Projection filters its own shallow view without mutating the sampled world.
    world = dict(world)
    # The public package is the client's books: rows tagged with a book_layer other than
    # client_book (the auditor's final-adjusted AJEs) never appear in client exports.
    # The private world keeps every layer for grading.
    for node_id, rows in list(world.items()):
        if (
            isinstance(rows, list)
            and rows
            and isinstance(rows[0], dict)
            and "book_layer" in rows[0]
        ):
            world[node_id] = [row for row in rows if row["book_layer"] == "client_book"]
    projection = render_core_pbcs(registry, world)
    package_dir = run_dir / "pbc_package"
    file_map = materialize_pbc_workbooks(projection, package_dir, world)
    file_map.extend(render_foundational_pbcs(registry, world, package_dir))
    documents, document_applicability = render_documents(
        world,
        package_dir,
        audit_plan,
    )
    file_map.extend(documents)
    _organize_shared_revenue_evidence(file_map, package_dir)
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.public_presentation import (
        apply_workbook_presentation_boundary,
    )
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.workbook_finalize import (
        finalize_workbooks,
    )

    apply_workbook_presentation_boundary(file_map, run_dir)
    finalize_workbooks(file_map, world)
    # Confirmations go direct from the institution to the auditor, so they
    # render into their own channel outside the client package.
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.confirmations import (
        render_confirmations,
    )

    confirmations, confirmation_applicability = render_confirmations(
        world, run_dir / "direct_to_auditor", audit_plan
    )
    # Confirmations are rendered after the client-workbook formatting pass; apply the
    # boundary only to these new artifacts so cached formulas in the client package
    # remain untouched.
    apply_workbook_presentation_boundary(confirmations, run_dir)
    file_map.extend(confirmations)
    document_applicability.update(confirmation_applicability)
    # Physical-count attendance is auditor-created evidence, not a client schedule. Keep
    # it in the controlled direct channel alongside returned confirmations so it cannot
    # be mistaken for management's count results.
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.inventory_count_observation import (
        render_inventory_count_observation,
    )

    count_observation, count_observation_status = render_inventory_count_observation(
        world, run_dir / "direct_to_auditor", audit_plan
    )
    file_map.extend(count_observation)
    document_applicability["inventory_count_observation"] = count_observation_status
    # Close the Core request inventory with authored, named provider routes. This runs
    # after every client/direct document channel has rendered and before
    # manifests/indexes are written, so no semantic filename heuristic can silently
    # substitute an unrelated same-family artifact.
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.request_resolution import (
        resolve_core_request_deliveries,
    )

    resolve_core_request_deliveries(projection, file_map)
    file_map.append(_package_index(world, file_map, package_dir))
    for entry in file_map:
        entry["path"] = str(Path(entry["path"]).relative_to(run_dir))
    private_dir = run_dir / "sampled_world"
    private_dir.mkdir(parents=True, exist_ok=True)
    (private_dir / "pbc_file_map.json").write_text(
        json.dumps(
            {"files": file_map},
            indent=1,
            default=str,
        )
        + "\n"
    )
    (private_dir / "document_applicability.json").write_text(
        json.dumps(document_applicability, indent=1, sort_keys=True) + "\n"
    )
    (private_dir / "pbc_projection.json").write_text(
        json.dumps(
            projection,
            indent=1,
            default=str,
        )
        + "\n"
    )
    # The cached identity allocation is render-scoped; never let it leak
    # into later serialization of the world.
    world.pop("__package_identity", None)
    return projection
