"""Validate the Phase 4 grunnskoli -> sveitarfelag crosswalk outputs."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CROSSWALK_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_sveitarfelag_crosswalk.csv"
MANUAL_PATH = PROJECT_ROOT / "data" / "manual" / "manual_school_crosswalk.csv"
REVIEW_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_review.csv"
REFERENCE_REVIEW_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_reference_only_review.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_audit.csv"
PRESENCE_COLUMNS = [
    "in_althingi_graduates",
    "in_althingi_framhaldsskoli_grunnskoli",
    "in_althingi_grunnskoli_grades",
    "in_hagstofa_student_counts",
    "in_hagstofa_10th_grade_counts",
]
ALTHINGI_COLUMNS = PRESENCE_COLUMNS[:3]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    errors: list[str] = []
    for path in [CROSSWALK_PATH, MANUAL_PATH, REVIEW_PATH, REFERENCE_REVIEW_PATH, AUDIT_PATH]:
        if not path.exists():
            errors.append(f"missing expected file: {path}")
    if errors:
        return fail(errors)

    crosswalk = read_rows(CROSSWALK_PATH)
    manual = read_rows(MANUAL_PATH)
    review = read_rows(REVIEW_PATH)
    reference_review = read_rows(REFERENCE_REVIEW_PATH)
    audit = read_rows(AUDIT_PATH)
    if not crosswalk:
        errors.append("crosswalk is empty")
    if not audit:
        errors.append("audit is empty")

    required_crosswalk = {
        "source_school_name",
        "normalized_school_name",
        "mapping_priority",
        "canonical_school_name",
        "sveitarfelag",
        *PRESENCE_COLUMNS,
        "match_status",
        "match_confidence",
        "match_source",
        "althingi_graduate_count",
        "size_evidence_note",
        "notes",
    }
    required_review = {
        "source_school_name",
        "normalized_school_name",
        "possible_match_1",
        "possible_match_1_sveitarfelag",
        "possible_match_1_score",
        "possible_match_1_recent_10th_grade_count",
        "possible_match_2",
        "possible_match_2_sveitarfelag",
        "possible_match_2_score",
        "possible_match_2_recent_10th_grade_count",
        "althingi_graduate_count",
        "size_evidence_note",
        *PRESENCE_COLUMNS,
        "notes",
    }
    required_manual = {"source_school_name", "normalized_school_name", "sveitarfelag", "canonical_school_name", "manual_status", "notes"}
    required_audit = {"section", "dataset", "item", "value"}
    if crosswalk and required_crosswalk - set(crosswalk[0]):
        errors.append(f"crosswalk missing columns: {sorted(required_crosswalk - set(crosswalk[0]))}")
    if review and required_review - set(review[0]):
        errors.append(f"review missing columns: {sorted(required_review - set(review[0]))}")
    if reference_review and required_review - set(reference_review[0]):
        errors.append(f"reference review missing columns: {sorted(required_review - set(reference_review[0]))}")
    if manual and required_manual - set(manual[0]):
        errors.append(f"manual file missing columns: {sorted(required_manual - set(manual[0]))}")
    if audit and required_audit - set(audit[0]):
        errors.append(f"audit missing columns: {sorted(required_audit - set(audit[0]))}")

    names = [row["source_school_name"] for row in crosswalk]
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    if duplicates:
        errors.append(f"duplicate source_school_name rows: {duplicates[:20]}")

    expected_analysis_review = set()
    expected_reference_review = set()
    for row in crosswalk:
        status = row["match_status"]
        priority = row["mapping_priority"]
        analysis_presence = any(row[col] == "true" for col in ALTHINGI_COLUMNS)
        if status == "excluded_group_or_aggregate":
            if priority != "excluded_group_or_aggregate":
                errors.append(f"excluded row has wrong priority: {row['source_school_name']}")
            if row["sveitarfelag"]:
                errors.append(f"excluded row has sveitarfelag assigned: {row['source_school_name']}")
            continue
        if analysis_presence and priority != "analysis_required":
            errors.append(f"Althingi row is not marked analysis_required: {row['source_school_name']}")
        if not analysis_presence and priority == "analysis_required":
            errors.append(f"non-Althingi row marked analysis_required: {row['source_school_name']}")
        if priority == "analysis_required":
            if not row["sveitarfelag"] and status != "needs_manual_review":
                errors.append(f"analysis_required row lacks sveitarfelag without review status: {row['source_school_name']}")
            if status == "needs_manual_review":
                expected_analysis_review.add(row["source_school_name"])
        elif priority == "reference_only" and status == "needs_manual_review":
            expected_reference_review.add(row["source_school_name"])

    review_names = {row["source_school_name"] for row in review}
    reference_review_names = {row["source_school_name"] for row in reference_review}
    if review_names != expected_analysis_review:
        errors.append(
            "main review rows do not exactly match unresolved analysis_required rows: "
            f"missing={sorted(expected_analysis_review - review_names)[:10]}, extra={sorted(review_names - expected_analysis_review)[:10]}"
        )
    if reference_review_names != expected_reference_review:
        errors.append(
            "reference-only review rows do not exactly match unresolved reference_only rows: "
            f"missing={sorted(expected_reference_review - reference_review_names)[:10]}, extra={sorted(reference_review_names - expected_reference_review)[:10]}"
        )
    for row in review:
        if row.get("in_althingi_graduates") != "true" and row.get("in_althingi_framhaldsskoli_grunnskoli") != "true" and row.get("in_althingi_grunnskoli_grades") != "true":
            errors.append(f"main review contains non-analysis row: {row['source_school_name']}")

    audit_items = {row["item"] for row in audit}
    for item in [
        "total_unique_source_school_names",
        "analysis_required_total",
        "analysis_required_matched_exact",
        "analysis_required_matched_fuzzy",
        "analysis_required_matched_by_rule",
        "analysis_required_matched_by_municipality_only_rule",
        "analysis_required_needing_manual_review",
        "reference_only_total",
        "reference_only_unresolved",
        "excluded_aggregate_group_rows",
        "external_mapping_source_used",
    ]:
        if item not in audit_items:
            errors.append(f"audit missing item: {item}")

    if errors:
        return fail(errors)

    statuses = Counter(row["match_status"] for row in crosswalk)
    priorities = Counter(row["mapping_priority"] for row in crosswalk)
    print(
        "OK: grunnskoli-sveitarfelag crosswalk passed validation "
        f"({len(crosswalk)} rows; analysis_required={priorities['analysis_required']}, "
        f"reference_only={priorities['reference_only']}, exact={statuses['matched_exact']}, "
        f"fuzzy={statuses['matched_fuzzy_high_confidence']}, rule={statuses['matched_rule']}, "
        f"municipality_rule={statuses['matched_municipality_rule_canonical_uncertain']}, "
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
