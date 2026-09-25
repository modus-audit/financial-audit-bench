"""Auditor physical-count attendance and two-direction test counts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    AUDIT_FIRM,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)

REQUEST_ID = "DOC-INVENTORY-COUNT-OBSERVATION"
TEST_COUNT_TARGET = 30


def _test_rows(
    selected: list[dict[str, Any]],
    items: dict[str, dict[str, Any]],
    location: str,
    *,
    floor_to_listing: bool,
) -> list[list[str]]:
    rows = []
    for line in selected:
        if line.get("auditor_observation_source") != "independent physical test count":
            raise ValueError("inventory test count lacks an independent observation")
        observed = line.get("auditor_observed_quantity")
        if observed is None:
            raise ValueError("inventory test count lacks an auditor quantity")
        item = items[line["inventory_item_id"]]
        client = Decimal(str(line["recount_quantity"]))
        difference = Decimal(str(observed)) - client
        rows.append(
            [
                line["tag_number"] if floor_to_listing else item["sku"],
                item["sku"],
                line["tag_number"],
                location,
                str(client),
                str(observed),
                str(difference),
                "Exception resolved" if difference else "No exception",
            ]
        )
    return rows


def render_inventory_count_observation(
    world: World,
    output_dir: Path,
    audit_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    counts = world.get("inventory_count") or []
    lines = world.get("inventory_count_line") or []
    if not counts or not lines:
        return [], "not_applicable"
    if len(counts) != 1:
        raise ValueError("inventory observation requires one count event")
    count = counts[0]
    population = [
        row for row in lines if row["inventory_count_id"] == count["inventory_count_id"]
    ]
    if len({row["inventory_count_line_id"] for row in population}) != len(population):
        raise ValueError("inventory count contains duplicate line IDs")
    items = {row["inventory_item_id"]: row for row in world.get("inventory_item") or []}
    if any(row["inventory_item_id"] not in items for row in population):
        raise ValueError("inventory count contains an unresolved item")
    locations = {
        row["inventory_location_id"]: row
        for row in world.get("inventory_location") or []
    }
    location_row = locations.get(count["inventory_location_id"])
    if not location_row:
        raise ValueError("inventory count has no resolved location")
    location = str(location_row.get("location_name") or location_row.get("name"))

    ordered = sorted(population, key=lambda row: row["inventory_count_line_id"])
    selection_size = min(TEST_COUNT_TARGET, len(ordered))
    listing_to_floor = ordered[:selection_size]
    floor_to_listing = list(reversed(ordered))[:selection_size]
    scope_status = (
        "nominal_target_met"
        if len(ordered) >= TEST_COUNT_TARGET
        else "full_population_below_nominal_target"
    )
    unresolved = [
        row
        for row in (*listing_to_floor, *floor_to_listing)
        if str(row.get("approval_status") or "").casefold() != "approved"
    ]
    if unresolved:
        raise ValueError(f"inventory observation has {len(unresolved)} unresolved rows")

    team = audit_plan["engagement"]["audit_team"]
    observer = str(team.get("preparer") or "Assigned audit senior")
    reviewer = str(team.get("reviewer") or "Assigned audit manager")
    count_date = str(count["count_date"])
    doc = TextDoc()
    doc.center(AUDIT_FIRM.upper())
    doc.center("INVENTORY COUNT OBSERVATION AND TEST COUNTS")
    doc.rule("=")
    doc.pair("Entity", company_name(world))
    doc.tie("count_date", "Count date", fmt_date(count_date))
    doc.tie("count_location", "Location attended", location)
    doc.pair("Auditor", observer)
    doc.pair("Client count team", count["count_team"])
    doc.pair("Inventory freeze", count["inventory_freeze_timestamp"])
    doc.wrapped(
        "The auditor observed the inventory freeze and performed independent "
        "test counts from the listing to the floor and from floor tags to the listing."
    )
    if scope_status != "nominal_target_met":
        doc.wrapped(
            f"The {len(ordered)}-line population was tested in full in both directions."
        )
    headers = [
        "Source",
        "SKU",
        "Tag",
        "Location",
        "Client",
        "Auditor",
        "Difference",
        "Result",
    ]
    widths = [14, 13, 13, 16, 10, 10, 11, 18]
    for title, selection, reverse in (
        ("LISTING TO FLOOR", listing_to_floor, False),
        ("FLOOR TO LISTING", floor_to_listing, True),
    ):
        doc.line()
        doc.line(title)
        doc.table(
            headers,
            _test_rows(selection, items, location, floor_to_listing=reverse),
            widths,
        )
    doc.tie(
        "listing_to_floor_count", "Listing-to-floor selections", len(listing_to_floor)
    )
    doc.tie(
        "floor_to_listing_count", "Floor-to-listing selections", len(floor_to_listing)
    )
    doc.tie("unresolved_test_count_exceptions", "Unresolved exceptions", 0)
    doc.pair("Conclusion", "Count attendance completed with no unresolved exceptions")
    doc.pair("Reviewer", reviewer)

    path = output_dir / f"Inventory Count Observation {count_date}.txt"
    payload = entry(
        REQUEST_ID,
        "inventory",
        path,
        doc.write(path),
    )
    payload.update(
        support_type="inventory_count_observation",
        count_date=count_date,
        observer=observer,
        population_count=len(population),
        listing_to_floor_count=len(listing_to_floor),
        floor_to_listing_count=len(floor_to_listing),
        scope_status=scope_status,
        source_record_ids={
            "listing_to_floor": [
                row["inventory_count_line_id"] for row in listing_to_floor
            ],
            "floor_to_listing": [
                row["inventory_count_line_id"] for row in floor_to_listing
            ],
        },
    )
    return [payload], "generated"
