"""Validate municipality harmonization for public reading-test data."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag_harmonized.csv"
MUNICIPALITY_CROSSWALK_PATH = PROJECT_ROOT / "data" / "manual" / "municipality_name_crosswalk.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "reading_tests_municipality_harmonization_audit.csv"

ADDED_COLUMNS = [
    "sveitarfelag_source",
    "sveitarfelag_harmonized",
    "sveitarfelag_harmonization_status",
    "sveitarfelag_harmonization_note",
]
KNOWN_HARMONIZATIONS = {
    "Djúpavogshreppur": "Múlaþing",
    "Fljótsdalshérað": "Múlaþing",
    "Seyðisfjarðarkaupstaður": "Múlaþing",
    "Akureyrarkaupstaður": "Akureyrarbær",
    "Seltjarnarneskaupstaður": "Seltjarnarnesbær",
    "Bolungarvík": "Bolungarvíkurkaupstaður",
    "Sandgerðisbær": "Suðurnesjabær",
    "Sveitarfélagið Garður": "Suðurnesjabær",
    "Blönduósbær": "Húnabyggð",
    "Húnavatnshreppur": "Húnabyggð",
    "Skútustaðahreppur": "Þingeyjarsveit",
    "Tálknafjarðarhreppur": "Vesturbyggð",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def comparable_original_row(row: dict[str, str], original_columns: list[str]) -> dict[str, str]:
    return {column: row[column] for column in original_columns}


def main() -> int:
    errors: list[str] = []
    for path in [INPUT_PATH, OUTPUT_PATH, MUNICIPALITY_CROSSWALK_PATH, AUDIT_PATH]:
        if not path.exists():
            errors.append(f"missing expected file: {path}")
    if errors:
        return fail(errors)

    original = read_rows(INPUT_PATH)
    harmonized = read_rows(OUTPUT_PATH)
    audit = read_rows(AUDIT_PATH)
    if len(harmonized) != len(original):
        errors.append(f"row count changed: original={len(original)}, harmonized={len(harmonized)}")
    if not harmonized:
        errors.append("harmonized reading-test dataset is empty")
        return fail(errors)

    original_columns = list(original[0].keys())
    missing_columns = set(original_columns + ADDED_COLUMNS) - set(harmonized[0])
    if missing_columns:
        errors.append(f"harmonized dataset missing columns: {sorted(missing_columns)}")

    for index, (source_row, output_row) in enumerate(zip(original, harmonized), start=2):
        if comparable_original_row(output_row, original_columns) != source_row:
            errors.append(f"row {index}: original reading-test values changed")
        if output_row["sveitarfelag_source"] != source_row["sveitarfelag"]:
            errors.append(f"row {index}: source municipality was not preserved")
        if not output_row["sveitarfelag_harmonized"]:
            errors.append(f"row {index}: sveitarfelag_harmonized is blank")
        if output_row["sveitarfelag_harmonization_status"] == "needs_review":
            errors.append(f"row {index}: municipality still needs review: {output_row['sveitarfelag_source']}")

    by_source = {}
    for row in harmonized:
        by_source.setdefault(row["sveitarfelag_source"], row["sveitarfelag_harmonized"])
    for source, expected in KNOWN_HARMONIZATIONS.items():
        if source in by_source and by_source[source] != expected:
            errors.append(f"known harmonization not applied: {source} -> {by_source[source]!r}, expected {expected!r}")
    if by_source.get("Grímsnes– og Grafningshreppur") != "Grímsnes- og Grafningshreppur":
        errors.append("Grímsnes en-dash municipality variant was not normalized to hyphen form")

    audit_items = {row["item"] for row in audit}
    for item in [
        "reading_test_rows",
        "source_municipalities",
        "harmonized_municipalities",
        "municipalities_harmonized_unchanged",
        "municipalities_harmonized_by_crosswalk",
        "municipalities_needing_review",
        "municipalities",
    ]:
        if item not in audit_items:
            errors.append(f"audit missing item: {item}")

    if errors:
        return fail(errors)
    print(
        "OK: reading-test municipality harmonization passed validation "
        f"({len(harmonized)} rows, {len(by_source)} source municipalities, "
        f"{len(set(by_source.values()))} harmonized municipalities, 0 needing review)."
    )
    return 0


def fail(errors: list[str]) -> int:
    print(f"FAIL: {len(errors)} validation issue(s) found")
    for error in errors[:30]:
        print(f"- {error}")
    if len(errors) > 30:
        print(f"- ... {len(errors) - 30} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
