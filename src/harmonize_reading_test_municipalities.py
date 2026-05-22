"""Apply municipality-name harmonization to public reading-test data."""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag.csv"
MUNICIPALITY_CROSSWALK_PATH = PROJECT_ROOT / "data" / "manual" / "municipality_name_crosswalk.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag_harmonized.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "reading_tests_municipality_harmonization_audit.csv"

ADDED_COLUMNS = [
    "sveitarfelag_source",
    "sveitarfelag_harmonized",
    "sveitarfelag_harmonization_status",
    "sveitarfelag_harmonization_note",
]

PUNCTUATION_VARIANTS = {
    "Grímsnes– og Grafningshreppur": {
        "sveitarfelag_harmonized": "Grímsnes- og Grafningshreppur",
        "harmonization_status": "harmonized",
        "notes": "Punctuation variant normalized from en dash to hyphen for current municipality joins.",
    },
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def normalized_key(value: str) -> str:
    text = value.casefold().strip()
    text = text.replace("–", "-").replace("—", "-").replace("‐", "-")
    normalized = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_municipality_crosswalk() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    rows = read_rows(MUNICIPALITY_CROSSWALK_PATH)
    exact: dict[str, dict[str, str]] = {}
    normalized: dict[str, dict[str, str]] = {}
    for row in rows:
        source = row.get("sveitarfelag_source", "").strip()
        if not source:
            continue
        exact[source] = row
        normalized.setdefault(normalized_key(source), row)
    return exact, normalized


def harmonize(
    sveitarfelag: str,
    exact_lookup: dict[str, dict[str, str]],
    normalized_lookup: dict[str, dict[str, str]],
) -> tuple[str, str, str]:
    source = sveitarfelag.strip()
    if source in exact_lookup:
        row = exact_lookup[source]
        return row["sveitarfelag_harmonized"], row["harmonization_status"], row.get("notes", "")
    if source in PUNCTUATION_VARIANTS:
        row = PUNCTUATION_VARIANTS[source]
        return row["sveitarfelag_harmonized"], row["harmonization_status"], row["notes"]
    normalized = normalized_key(source)
    if normalized in normalized_lookup:
        row = normalized_lookup[normalized]
        note = row.get("notes", "")
        if row["sveitarfelag_source"] != source:
            note = f"Matched by punctuation/spacing-insensitive municipality name lookup. {note}".strip()
        return row["sveitarfelag_harmonized"], row["harmonization_status"], note
    return source, "unchanged", "No project-local harmonization needed; carried forward as current/source name."


def build_audit(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    source_municipalities = {row["sveitarfelag_source"] for row in rows}
    harmonized_municipalities = {row["sveitarfelag_harmonized"] for row in rows}
    status_by_source = {
        source: next(row["sveitarfelag_harmonization_status"] for row in rows if row["sveitarfelag_source"] == source)
        for source in source_municipalities
    }
    status_counts = Counter(status_by_source.values())
    unmatched = sorted(source for source, status in status_by_source.items() if status == "needs_review")
    rows_by_status = Counter(row["sveitarfelag_harmonization_status"] for row in rows)
    audit = [
        {"section": "summary", "item": "reading_test_rows", "value": len(rows)},
        {"section": "summary", "item": "source_municipalities", "value": len(source_municipalities)},
        {"section": "summary", "item": "harmonized_municipalities", "value": len(harmonized_municipalities)},
        {"section": "summary", "item": "municipalities_harmonized_unchanged", "value": status_counts["unchanged"]},
        {"section": "summary", "item": "municipalities_harmonized_by_crosswalk", "value": status_counts["harmonized"]},
        {"section": "summary", "item": "municipalities_needing_review", "value": status_counts["needs_review"]},
        {"section": "summary", "item": "rows_harmonized_unchanged", "value": rows_by_status["unchanged"]},
        {"section": "summary", "item": "rows_harmonized_by_crosswalk", "value": rows_by_status["harmonized"]},
        {"section": "summary", "item": "rows_needing_review", "value": rows_by_status["needs_review"]},
        {"section": "unmatched", "item": "municipalities", "value": "; ".join(unmatched)},
    ]
    for source in sorted(source_municipalities):
        sample = next(row for row in rows if row["sveitarfelag_source"] == source)
        audit.append(
            {
                "section": "municipality",
                "item": source,
                "value": (
                    f"{sample['sveitarfelag_harmonized']} | "
                    f"{sample['sveitarfelag_harmonization_status']} | "
                    f"{sample['sveitarfelag_harmonization_note']}"
                ),
            }
        )
    return audit


def main() -> None:
    rows = read_rows(INPUT_PATH)
    exact_lookup, normalized_lookup = load_municipality_crosswalk()
    output_rows: list[dict[str, str]] = []
    for row in rows:
        source = row["sveitarfelag"]
        harmonized, status, note = harmonize(source, exact_lookup, normalized_lookup)
        output_rows.append(
            {
                **row,
                "sveitarfelag_source": source,
                "sveitarfelag_harmonized": harmonized,
                "sveitarfelag_harmonization_status": status,
                "sveitarfelag_harmonization_note": note,
            }
        )
    fieldnames = list(rows[0].keys()) + [col for col in ADDED_COLUMNS if col not in rows[0]]
    write_csv(OUTPUT_PATH, output_rows, fieldnames)
    write_csv(AUDIT_PATH, build_audit(output_rows), ["section", "item", "value"])
    print(f"Wrote {len(output_rows)} rows to {OUTPUT_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
