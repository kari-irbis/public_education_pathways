"""Validate Phase 7 analysis-ready datasets."""

from __future__ import annotations

import csv
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

MUNICIPALITY_READING_SPENDING_PATH = (
    PROJECT_ROOT / "data" / "processed" / "analysis_municipality_reading_spending_2024.csv"
)
SCHOOL_OUTCOMES_PATH = PROJECT_ROOT / "data" / "processed" / "analysis_althingi_school_outcomes_mapped.csv"
MUNICIPALITY_ALTHINGI_PATH = PROJECT_ROOT / "data" / "processed" / "analysis_municipality_althingi_summary.csv"
COVERAGE_AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "analysis_dataset_coverage_audit.csv"
CROSSWALK_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_sveitarfelag_crosswalk.csv"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def parse_float(value: object) -> float | None:
    text = "" if value is None else str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return float(text)


def require_file(path: Path, errors: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        errors.append(f"Missing expected file: {path}")
        return []
    rows = read_rows(path)
    if not rows:
        errors.append(f"Expected non-empty file: {path}")
    return rows


def require_columns(path: Path, rows: list[dict[str, str]], required: set[str], errors: list[str]) -> None:
    if not rows:
        return
    columns = set(rows[0])
    missing = sorted(required - columns)
    if missing:
        errors.append(f"{path} is missing required columns: {', '.join(missing)}")


def check_no_duplicate_municipalities(path: Path, rows: list[dict[str, str]], errors: list[str]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        municipality = row.get("sveitarfelag_harmonized", "")
        if municipality in seen:
            duplicates.add(municipality)
        seen.add(municipality)
    if duplicates:
        errors.append(f"{path} has duplicate municipality rows: {', '.join(sorted(duplicates))}")


def check_numeric_columns(rows: list[dict[str, str]], columns: list[str], label: str, errors: list[str]) -> None:
    for index, row in enumerate(rows, start=2):
        for column in columns:
            value = row.get(column, "")
            if value == "":
                continue
            try:
                float(value)
            except ValueError:
                errors.append(f"{label} row {index} column {column} is not numeric: {value!r}")


def check_reading_values(rows: list[dict[str, str]], errors: list[str]) -> None:
    for index, row in enumerate(rows, start=2):
        value = parse_float(row.get("latest_reading_value_pct", ""))
        if value is None:
            continue
        if value < 0 or value > 100:
            errors.append(f"Reading value out of range in row {index}: {value}")


def check_grade_ranges(rows: list[dict[str, str]], errors: list[str]) -> None:
    columns = [
        "average_grunnskoli_icelandic_grade",
        "average_grunnskoli_math_grade",
        "weighted_average_grunnskoli_icelandic_grade",
        "weighted_average_grunnskoli_math_grade",
        "unweighted_average_grunnskoli_icelandic_grade",
        "unweighted_average_grunnskoli_math_grade",
    ]
    for index, row in enumerate(rows, start=2):
        for column in columns:
            value = parse_float(row.get(column, ""))
            if value is None:
                continue
            if value < 0 or value > 10:
                errors.append(f"Grade value outside plausible 0-10 range in row {index}, {column}: {value}")


def check_school_mapping_retained(school_rows: list[dict[str, str]], errors: list[str]) -> None:
    crosswalk_rows = [
        row
        for row in read_rows(CROSSWALK_PATH)
        if row.get("mapping_priority") == "analysis_required"
        and row.get("match_status") != "excluded_group_or_aggregate"
    ]
    output_by_school = {row.get("source_school_name", ""): row for row in school_rows}
    missing_output = []
    missing_municipality = []
    for row in crosswalk_rows:
        school = row.get("source_school_name", "")
        output = output_by_school.get(school)
        if not output:
            missing_output.append(school)
            continue
        if not output.get("sveitarfelag_harmonized"):
            missing_municipality.append(school)
    if missing_output:
        errors.append(f"Analysis-required schools missing from mapped output: {', '.join(sorted(missing_output))}")
    if missing_municipality:
        errors.append(
            "Analysis-required schools lost sveitarfelag_harmonized in mapped output: "
            + ", ".join(sorted(missing_municipality))
        )


def validate() -> list[str]:
    errors: list[str] = []
    municipality_reading_spending = require_file(MUNICIPALITY_READING_SPENDING_PATH, errors)
    school_outcomes = require_file(SCHOOL_OUTCOMES_PATH, errors)
    municipality_althingi = require_file(MUNICIPALITY_ALTHINGI_PATH, errors)
    coverage_audit = require_file(COVERAGE_AUDIT_PATH, errors)

    require_columns(
        MUNICIPALITY_READING_SPENDING_PATH,
        municipality_reading_spending,
        {
            "sveitarfelag_harmonized",
            "latest_reading_value_pct",
            "amount_isk",
            "spend_per_source_student",
            "spend_per_resident",
            "spend_per_resident_age_6_15",
            "population_total",
            "population_age_6_15",
            "has_latest_reading",
            "has_2024_spending_kostnadur_netto",
        },
        errors,
    )
    require_columns(
        SCHOOL_OUTCOMES_PATH,
        school_outcomes,
        {
            "source_school_name",
            "canonical_school_name",
            "sveitarfelag_harmonized",
            "graduate_count",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "has_graduates_table_row",
            "has_grades_by_grunnskoli_row",
            "match_status",
        },
        errors,
    )
    require_columns(
        MUNICIPALITY_ALTHINGI_PATH,
        municipality_althingi,
        {
            "sveitarfelag_harmonized",
            "total_matched_graduates",
            "number_of_grunnskolar_represented",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "weighted_average_grunnskoli_icelandic_grade",
            "weighted_average_grunnskoli_math_grade",
        },
        errors,
    )
    require_columns(COVERAGE_AUDIT_PATH, coverage_audit, {"audit_section", "item", "value", "details"}, errors)

    check_no_duplicate_municipalities(MUNICIPALITY_READING_SPENDING_PATH, municipality_reading_spending, errors)
    check_no_duplicate_municipalities(MUNICIPALITY_ALTHINGI_PATH, municipality_althingi, errors)
    check_school_mapping_retained(school_outcomes, errors)
    check_numeric_columns(
        municipality_reading_spending,
        [
            "amount_isk",
            "spend_per_source_student",
            "spend_per_resident",
            "spend_per_resident_age_6_15",
            "population_total",
            "population_age_6_15",
            "kostnadur_netto_amount_isk",
            "kostnadur_brutto_amount_isk",
        ],
        "municipality reading/spending",
        errors,
    )
    check_numeric_columns(
        school_outcomes,
        [
            "graduate_count",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "framhaldsskoli_grunnskoli_student_count",
        ],
        "school outcomes",
        errors,
    )
    check_numeric_columns(
        municipality_althingi,
        [
            "total_matched_graduates",
            "number_of_grunnskolar_represented",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "weighted_average_grunnskoli_icelandic_grade",
            "weighted_average_grunnskoli_math_grade",
            "unweighted_average_grunnskoli_icelandic_grade",
            "unweighted_average_grunnskoli_math_grade",
        ],
        "municipality Alþingi summary",
        errors,
    )
    check_reading_values(municipality_reading_spending, errors)
    check_grade_ranges(municipality_althingi, errors)
    return errors


def main() -> None:
    errors = validate()
    if errors:
        print("FAIL: analysis dataset validation found issues:")
        for error in errors:
            print(f"- {error}")
        sys.exit(1)
    print("OK: analysis datasets passed validation.")


if __name__ == "__main__":
    main()
