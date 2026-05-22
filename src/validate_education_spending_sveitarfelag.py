"""Validate Phase 5 public education spending extraction outputs."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPENDING_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_sveitarfelag.csv"
NATIONAL_CONTEXT_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_national_context.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "education_spending_extraction_audit.csv"
INTERIM_SCHOOL_ROWS_PATH = PROJECT_ROOT / "data" / "interim" / "education_spend_school_size_2024_school_rows.csv"
JOIN_COVERAGE_AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "municipality_join_coverage_audit.csv"

REQUIRED_SPENDING_COLUMNS = {
    "year",
    "sveitarfelag_source",
    "sveitarfelag_harmonized",
    "sveitarfelag_harmonization_status",
    "sveitarfelag_harmonization_note",
    "spending_metric",
    "amount_isk",
    "student_count",
    "spend_per_student",
    "spend_per_inhabitant",
    "source_url",
    "source_note",
}
EXPECTED_METRICS = {
    "tekjur",
    "laun_og_launtengd_gjold",
    "annar_kostnadur_samtals",
    "annar_kostnadur_an_innri_leigu_og_skolaakstur",
    "innri_husaleiga_eignasjodur",
    "skolaakstur",
    "kostnadur_brutto",
    "kostnadur_netto",
}
KNOWN_HARMONIZATIONS = {
    "Stykkishólmsbær": "Sveitarfélagið Stykkishólmur",
    "Sveitarfélagið Skagafjörður": "Skagafjörður",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def numeric(value: str, field: str, row_number: int, errors: list[str], allow_blank: bool = False) -> float | None:
    if value == "":
        if not allow_blank:
            errors.append(f"row {row_number}: {field} is blank")
        return None
    try:
        return float(value)
    except ValueError:
        errors.append(f"row {row_number}: {field} is not numeric: {value!r}")
        return None


def validate_municipality_output(errors: list[str]) -> None:
    rows = read_rows(SPENDING_PATH)
    if not rows:
        errors.append("municipality spending output is empty")
        return
    missing = REQUIRED_SPENDING_COLUMNS - set(rows[0])
    if missing:
        errors.append(f"spending output missing columns: {sorted(missing)}")
        return
    metrics = {row["spending_metric"] for row in rows}
    if metrics != EXPECTED_METRICS:
        errors.append(f"unexpected spending metrics: {sorted(metrics)}")
    years = {row["year"] for row in rows}
    if years != {"2024"}:
        errors.append(f"unexpected years: {sorted(years)}")
    municipalities = {row["sveitarfelag_source"] for row in rows}
    if len(municipalities) < 10:
        errors.append(f"too few municipalities for a municipality-level output: {len(municipalities)}")
    if len(rows) != len(municipalities) * len(EXPECTED_METRICS):
        errors.append(
            f"row count {len(rows)} does not equal municipalities x metrics "
            f"({len(municipalities)} x {len(EXPECTED_METRICS)})"
        )
    by_source = {row["sveitarfelag_source"]: row["sveitarfelag_harmonized"] for row in rows}
    for source, expected in KNOWN_HARMONIZATIONS.items():
        if source in by_source and by_source[source] != expected:
            errors.append(f"known spending harmonization not applied: {source} -> {by_source[source]!r}, expected {expected!r}")
    for idx, row in enumerate(rows, start=2):
        if not row["year"].isdigit():
            errors.append(f"row {idx}: year is not an integer: {row['year']!r}")
        if not row["sveitarfelag_harmonized"]:
            errors.append(f"row {idx}: sveitarfelag_harmonized is blank")
        if row["sveitarfelag_harmonization_status"] == "needs_review":
            errors.append(f"row {idx}: municipality needs harmonization review: {row['sveitarfelag_source']}")
        amount = numeric(row["amount_isk"], "amount_isk", idx, errors)
        students = numeric(row["student_count"], "student_count", idx, errors)
        per_student = numeric(row["spend_per_student"], "spend_per_student", idx, errors)
        numeric(row["spend_per_inhabitant"], "spend_per_inhabitant", idx, errors, allow_blank=True)
        if students is not None and students <= 0:
            errors.append(f"row {idx}: student_count is not positive")
        if amount is not None and students and per_student is not None:
            expected = amount / students
            if abs(expected - per_student) > 0.02:
                errors.append(f"row {idx}: spend_per_student {per_student} does not match amount/student_count {expected}")


def validate_audit(errors: list[str]) -> None:
    rows = read_rows(AUDIT_PATH)
    if not rows:
        errors.append("audit is empty")
        return
    required = {"audit_section", "item", "value", "source_note"}
    missing = required - set(rows[0])
    if missing:
        errors.append(f"audit missing columns: {sorted(missing)}")
    items = {row["item"]: row["value"] for row in rows}
    for item in [
        "usable_municipality_level_dataset_found",
        "school_rows_extracted",
        "municipality_spending_rows",
        "years_found",
        "municipalities_found",
        "harmonized_municipalities_found",
        "metrics_found",
        "amount_semantics",
        "value_status",
        "municipality_names_needing_harmonization_review",
    ]:
        if item not in items:
            errors.append(f"audit missing item: {item}")
    if items.get("usable_municipality_level_dataset_found") != "true":
        errors.append("audit does not mark municipality-level dataset as usable")
    if items.get("municipality_names_needing_harmonization_review") not in {"0", "0.0"}:
        errors.append("audit reports municipality names needing harmonization review")


def validate_join_coverage_audit(errors: list[str]) -> None:
    rows = read_rows(JOIN_COVERAGE_AUDIT_PATH)
    if not rows:
        errors.append("municipality join coverage audit is empty")
        return
    required = {"audit_section", "comparison", "municipality", "in_reading_tests", "in_althingi_school_outcomes", "in_spending", "gap_type", "assessment", "source_values", "note"}
    missing = required - set(rows[0])
    if missing:
        errors.append(f"municipality join coverage audit missing columns: {sorted(missing)}")


def main() -> int:
    errors: list[str] = []
    for path in [AUDIT_PATH, INTERIM_SCHOOL_ROWS_PATH, JOIN_COVERAGE_AUDIT_PATH]:
        if not path.exists():
            errors.append(f"missing expected file: {path}")
    if SPENDING_PATH.exists():
        validate_municipality_output(errors)
    elif NATIONAL_CONTEXT_PATH.exists():
        context_rows = read_rows(NATIONAL_CONTEXT_PATH)
        if not context_rows:
            errors.append("national context output exists but is empty")
    else:
        errors.append("neither municipality spending output nor national context output exists")
    if AUDIT_PATH.exists():
        validate_audit(errors)
    if JOIN_COVERAGE_AUDIT_PATH.exists():
        validate_join_coverage_audit(errors)
    if errors:
        print(f"FAIL: {len(errors)} validation issue(s) found")
        for error in errors[:30]:
            print(f"- {error}")
        if len(errors) > 30:
            print(f"- ... {len(errors) - 30} more")
        return 1
    rows = read_rows(SPENDING_PATH)
    print(
        "OK: education_spend_sveitarfelag.csv passed validation "
        f"({len(rows)} rows, {len({row['sveitarfelag_source'] for row in rows})} municipalities, "
        f"{len({row['spending_metric'] for row in rows})} metrics)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
