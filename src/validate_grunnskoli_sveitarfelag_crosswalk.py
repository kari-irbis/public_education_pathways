"""Validate the Phase 4 grunnskoli -> sveitarfelag crosswalk outputs."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CROSSWALK_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_sveitarfelag_crosswalk.csv"
MANUAL_PATH = PROJECT_ROOT / "data" / "manual" / "manual_school_crosswalk.csv"
REVIEW_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_review.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_audit.csv"
PRESENCE_COLUMNS = [
    "in_althingi_graduates",
    "in_althingi_framhaldsskoli_grunnskoli",
    "in_althingi_grunnskoli_grades",
    "in_hagstofa_student_counts",
    "in_hagstofa_10th_grade_counts",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    errors: list[str] = []
    for path in [CROSSWALK_PATH, MANUAL_PATH, REVIEW_PATH, AUDIT_PATH]:
        if not path.exists():
            errors.append(f"missing expected file: {path}")
    if errors:
        return fail(errors)

    crosswalk = read_rows(CROSSWALK_PATH)
    manual = read_rows(MANUAL_PATH)
    review = read_rows(REVIEW_PATH)
    audit = read_rows(AUDIT_PATH)
    if not crosswalk:
        errors.append("crosswalk is empty")
    if not audit:
        errors.append("audit is empty")

    required_crosswalk = {
        "source_school_name",
        "normalized_school_name",
        "canonical_school_name",
        "sveitarfelag",
        *PRESENCE_COLUMNS,
        "match_status",
        "match_confidence",
        "match_source",
        "notes",
    }
    required_review = {
        "source_school_name",
        "normalized_school_name",
        "possible_match_1",
        "possible_match_1_sveitarfelag",
        "possible_match_1_score",
        "possible_match_2",
        "possible_match_2_sveitarfelag",
        "possible_match_2_score",
        *PRESENCE_COLUMNS,
        "notes",
    }
    required_manual = {"source_school_name", "normalized_school_name", "sveitarfelag", "canonical_school_name", "manual_status", "notes"}
    required_audit = {"section", "dataset", "item", "value"}
    if crosswalk and required_crosswalk - set(crosswalk[0]):
        errors.append(f"crosswalk missing columns: {sorted(required_crosswalk - set(crosswalk[0]))}")
    if review and required_review - set(review[0]):
        errors.append(f"review missing columns: {sorted(required_review - set(review[0]))}")
    if manual and required_manual - set(manual[0]):
        errors.append(f"manual file missing columns: {sorted(required_manual - set(manual[0]))}")
    if audit and required_audit - set(audit[0]):
        errors.append(f"audit missing columns: {sorted(required_audit - set(audit[0]))}")

    names = [row["source_school_name"] for row in crosswalk]
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    if duplicates:
        errors.append(f"duplicate source_school_name rows: {duplicates[:20]}")

    unresolved = []
    for row in crosswalk:
        status = row["match_status"]
        if status == "excluded_group_or_aggregate":
            if row["sveitarfelag"]:
                errors.append(f"excluded row has sveitarfelag assigned: {row['source_school_name']}")
            continue
        if not row["sveitarfelag"] and status != "needs_manual_review":
            errors.append(f"non-excluded row lacks sveitarfelag without review status: {row['source_school_name']}")
        if status == "needs_manual_review":
            unresolved.append(row["source_school_name"])

    review_names = {row["source_school_name"] for row in review}
    if unresolved and not review:
        errors.append("unresolved rows exist but review file is empty")
    missing_review = sorted(set(unresolved) - review_names)
    if missing_review:
        errors.append(f"unresolved rows missing from review file: {missing_review[:20]}")

    audit_items = {row["item"] for row in audit}
    for item in ["total_unique_source_school_names", "exact_matches", "fuzzy_high_confidence_matches", "manual_matches_applied", "rows_needing_manual_review", "external_mapping_source_used"]:
        if item not in audit_items:
            errors.append(f"audit missing item: {item}")

    if errors:
        return fail(errors)

    statuses = Counter(row["match_status"] for row in crosswalk)
    print(
        "OK: grunnskoli-sveitarfelag crosswalk passed validation "
        f"({len(crosswalk)} rows; exact={statuses['matched_exact']}, "
        f"fuzzy={statuses['matched_fuzzy_high_confidence']}, manual={statuses['matched_manual']}, "
        f"review={statuses['needs_manual_review']}, excluded={statuses['excluded_group_or_aggregate']})."
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
