"""Validate Phase 7 analysis-ready datasets."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

READING_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag_harmonized.csv"
MUNICIPALITY_READING_SPENDING_PATH = (
    PROJECT_ROOT / "data" / "processed" / "analysis_municipality_reading_spending_2024.csv"
)
SCHOOL_OUTCOMES_PATH = PROJECT_ROOT / "data" / "processed" / "analysis_althingi_school_outcomes_mapped.csv"
MUNICIPALITY_ALTHINGI_PATH = PROJECT_ROOT / "data" / "processed" / "analysis_municipality_althingi_summary.csv"
COVERAGE_AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "analysis_dataset_coverage_audit.csv"
CROSSWALK_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_sveitarfelag_crosswalk.csv"

READING_METRIC = "naer_lagmarksvidmidi_2_3"
READING_PERIOD = "vor"


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
        for column in ["strict_latest_reading_value_pct", "latest_available_reading_value_pct", "latest_reading_value_pct"]:
            value = parse_float(row.get(column, ""))
            if value is None:
                continue
            if value < 0 or value > 100:
                errors.append(f"Reading value out of range in row {index}, {column}: {value}")


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


def check_grade_coverage(rows: list[dict[str, str]], errors: list[str]) -> None:
    for index, row in enumerate(rows, start=2):
        total = parse_float(row.get("total_matched_graduates", ""))
        covered = parse_float(row.get("graduates_with_grade_coverage", ""))
        share = parse_float(row.get("grade_coverage_share", ""))
        if total is None or total <= 0:
            continue
        if covered is None:
            errors.append(f"Missing graduates_with_grade_coverage in row {index}")
            continue
        if covered < 0 or covered > total:
            errors.append(f"graduates_with_grade_coverage outside 0-total range in row {index}: {covered} / {total}")
        if share is None or share < 0 or share > 1:
            errors.append(f"grade_coverage_share outside 0-1 range in row {index}: {share}")
        elif abs(share - (covered / total)) > 0.0002:
            errors.append(f"grade_coverage_share does not match covered/total in row {index}: {share} vs {covered / total}")


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


def reading_source_groups() -> tuple[dict[str, list[dict[str, str]]], str]:
    rows = [
        row
        for row in read_rows(READING_PATH)
        if row.get("measurement_period") == READING_PERIOD and row.get("metric") == READING_METRIC
    ]
    latest_year = max(row["school_year"] for row in rows)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["sveitarfelag_harmonized"]].append(row)
    return grouped, latest_year


def aggregate_expected(rows: list[dict[str, str]], selected_year: str) -> tuple[str, int, int]:
    selected = [row for row in rows if row.get("school_year") == selected_year]
    components = {row.get("sveitarfelag_source", "") for row in selected if row.get("sveitarfelag_source", "")}
    values = [parse_float(row.get("value_pct", "")) for row in selected]
    values = [value for value in values if value is not None]
    if not values:
        return "", len(components), 0
    return str(round(sum(values) / len(values), 2)), len(components), len(values)


def check_reading_aggregation(rows: list[dict[str, str]], errors: list[str]) -> None:
    output = {row.get("sveitarfelag_harmonized", ""): row for row in rows}
    grouped, global_latest_year = reading_source_groups()
    for municipality, source_rows in grouped.items():
        row = output.get(municipality)
        if not row:
            errors.append(f"Reading municipality missing from analysis output: {municipality}")
            continue
        source_components = {source_row.get("sveitarfelag_source", "") for source_row in source_rows}
        strict_value, strict_components, strict_nonmissing = aggregate_expected(source_rows, global_latest_year)
        if row.get("strict_latest_reading_school_year") != global_latest_year:
            errors.append(f"{municipality} strict latest year is not global latest {global_latest_year}")
        if int(float(row.get("strict_latest_reading_component_count", "0") or 0)) != strict_components:
            errors.append(f"{municipality} strict component count does not match source rows")
        if int(float(row.get("strict_latest_reading_nonmissing_component_count", "0") or 0)) != strict_nonmissing:
            errors.append(f"{municipality} strict nonmissing component count does not match source rows")
        output_strict = row.get("strict_latest_reading_value_pct", "")
        if strict_value and abs(float(output_strict) - float(strict_value)) > 0.01:
            errors.append(f"{municipality} strict latest value does not match source component average")
        if not strict_value and output_strict:
            errors.append(f"{municipality} strict latest value should be blank")

        years_with_values = sorted(
            {source_row["school_year"] for source_row in source_rows if parse_float(source_row.get("value_pct", "")) is not None}
        )
        if years_with_values:
            expected_year = years_with_values[-1]
            expected_value, expected_components, expected_nonmissing = aggregate_expected(source_rows, expected_year)
            if row.get("latest_available_reading_school_year") != expected_year:
                errors.append(f"{municipality} latest_available year should be {expected_year}")
            if expected_value and abs(float(row.get("latest_available_reading_value_pct", "")) - float(expected_value)) > 0.01:
                errors.append(f"{municipality} latest_available value does not match source component average")
            if int(float(row.get("latest_available_reading_component_count", "0") or 0)) != expected_components:
                errors.append(f"{municipality} latest_available component count does not match source rows")
            if int(float(row.get("latest_available_reading_nonmissing_component_count", "0") or 0)) != expected_nonmissing:
                errors.append(f"{municipality} latest_available nonmissing component count does not match source rows")
        if len(source_components) > 1:
            methods = {
                row.get("strict_latest_reading_aggregation_method", ""),
                row.get("latest_available_reading_aggregation_method", ""),
            }
            allowed = {"single_available_component", "unweighted_source_component_average", "no_nonmissing_component"}
            if not methods <= allowed:
                errors.append(f"{municipality} multi-component reading aggregation has unexpected methods: {methods}")
            if row.get("strict_latest_reading_component_count") in {"", "1"}:
                errors.append(f"{municipality} appears to have silently collapsed multiple reading source components")

    mula = output.get("Múlaþing")
    if mula and not mula.get("latest_available_reading_value_pct"):
        errors.append("Múlaþing should have a non-missing latest_available reading value when any component has one")


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
            "strict_latest_reading_school_year",
            "strict_latest_reading_value_pct",
            "strict_latest_reading_component_count",
            "strict_latest_reading_nonmissing_component_count",
            "strict_latest_reading_aggregation_method",
            "strict_latest_reading_note",
            "latest_available_reading_school_year",
            "latest_available_reading_value_pct",
            "latest_available_reading_component_count",
            "latest_available_reading_nonmissing_component_count",
            "latest_available_reading_aggregation_method",
            "latest_available_reading_note",
            "amount_isk",
            "spend_per_source_student",
            "spend_per_resident",
            "spend_per_resident_age_6_15",
            "population_total",
            "population_age_6_15",
            "has_latest_available_reading",
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
            "graduates_with_grade_coverage",
            "grade_coverage_share",
            "grade_coverage_note",
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
            "strict_latest_reading_value_pct",
            "latest_available_reading_value_pct",
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
            "graduates_with_grade_coverage",
            "grade_coverage_share",
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
    check_reading_aggregation(municipality_reading_spending, errors)
    check_grade_ranges(municipality_althingi, errors)
    check_grade_coverage(municipality_althingi, errors)
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
