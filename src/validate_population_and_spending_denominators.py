"""Validate Phase 6 population denominators and standardized spending metrics."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POPULATION_PATH = PROJECT_ROOT / "data" / "processed" / "municipality_population_denominators.csv"
SPENDING_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_sveitarfelag.csv"
STANDARDIZED_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_sveitarfelag_standardized.csv"
POPULATION_AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "population_denominators_audit.csv"
SPENDING_AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "spending_denominator_audit.csv"

POPULATION_REQUIRED_COLUMNS = {
    "year",
    "sveitarfelag_source",
    "sveitarfelag_harmonized",
    "population_total",
    "population_age_6_15",
    "population_age_6_15_note",
    "source_url",
    "source_note",
}
STANDARDIZED_REQUIRED_COLUMNS = {
    "year",
    "sveitarfelag_source",
    "sveitarfelag_harmonized",
    "spending_metric",
    "amount_isk",
    "student_count",
    "spend_per_student",
    "denominator_source_student_count",
    "spend_per_source_student",
    "population_total",
    "spend_per_resident",
    "population_age_6_15",
    "spend_per_resident_age_6_15",
    "denominator_notes",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def parse_int(value: str, field: str, row_number: int, errors: list[str]) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"row {row_number}: {field} is not an integer: {value!r}")
        return None
    if parsed < 0:
        errors.append(f"row {row_number}: {field} is negative: {parsed}")
    return parsed


def parse_float(value: str, field: str, row_number: int, errors: list[str]) -> float | None:
    try:
        return float(value)
    except ValueError:
        errors.append(f"row {row_number}: {field} is not numeric: {value!r}")
        return None


def close_enough(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= 0.02


def validate_population(errors: list[str]) -> dict[tuple[str, str], dict[str, str]]:
    rows = read_rows(POPULATION_PATH)
    if not rows:
        errors.append("population denominator dataset is empty")
        return {}
    missing = POPULATION_REQUIRED_COLUMNS - set(rows[0])
    if missing:
        errors.append(f"population dataset missing columns: {sorted(missing)}")
        return {}
    lookup = {}
    for idx, row in enumerate(rows, start=2):
        if not row["year"].isdigit():
            errors.append(f"population row {idx}: year is not an integer: {row['year']!r}")
        if not row["sveitarfelag_harmonized"]:
            errors.append(f"population row {idx}: sveitarfelag_harmonized is blank")
        total = parse_int(row["population_total"], "population_total", idx, errors)
        age = parse_int(row["population_age_6_15"], "population_age_6_15", idx, errors)
        if total is not None and age is not None and age > total:
            errors.append(f"population row {idx}: age 6-15 population exceeds total population")
        lookup[(row["year"], row["sveitarfelag_harmonized"])] = row
    return lookup


def validate_standardized(errors: list[str], population_lookup: dict[tuple[str, str], dict[str, str]]) -> None:
    original = read_rows(SPENDING_PATH)
    standardized = read_rows(STANDARDIZED_PATH)
    if len(original) != len(standardized):
        errors.append(f"standardized row count differs from original spending: {len(standardized)} vs {len(original)}")
    if not standardized:
        errors.append("standardized spending output is empty")
        return
    missing = STANDARDIZED_REQUIRED_COLUMNS - set(standardized[0])
    if missing:
        errors.append(f"standardized spending output missing columns: {sorted(missing)}")
        return
    missing_population = set()
    for idx, (source_row, row) in enumerate(zip(original, standardized), start=2):
        for column in source_row:
            if row.get(column) != source_row[column]:
                errors.append(f"row {idx}: original spending column changed: {column}")
                break
        if row["year"] == "2024" and not row["population_total"]:
            missing_population.add(row["sveitarfelag_harmonized"])
        if (row["year"], row["sveitarfelag_harmonized"]) not in population_lookup:
            missing_population.add(row["sveitarfelag_harmonized"])
        amount = parse_float(row["amount_isk"], "amount_isk", idx, errors)
        source_students = parse_float(row["denominator_source_student_count"], "denominator_source_student_count", idx, errors)
        spend_source_student = parse_float(row["spend_per_source_student"], "spend_per_source_student", idx, errors)
        population_total = parse_float(row["population_total"], "population_total", idx, errors)
        spend_resident = parse_float(row["spend_per_resident"], "spend_per_resident", idx, errors)
        population_age = parse_float(row["population_age_6_15"], "population_age_6_15", idx, errors)
        spend_age = parse_float(row["spend_per_resident_age_6_15"], "spend_per_resident_age_6_15", idx, errors)
        if amount is None:
            continue
        if source_students and spend_source_student is not None and not close_enough(spend_source_student, amount / source_students):
            errors.append(f"row {idx}: spend_per_source_student does not equal amount/source student count")
        if population_total and spend_resident is not None and not close_enough(spend_resident, amount / population_total):
            errors.append(f"row {idx}: spend_per_resident does not equal amount/population_total")
        if population_age and spend_age is not None and not close_enough(spend_age, amount / population_age):
            errors.append(f"row {idx}: spend_per_resident_age_6_15 does not equal amount/population_age_6_15")
    if missing_population:
        errors.append(f"spending municipalities missing population denominators: {sorted(missing_population)}")


def validate_audits(errors: list[str]) -> None:
    for path in [POPULATION_AUDIT_PATH, SPENDING_AUDIT_PATH]:
        rows = read_rows(path)
        if not rows:
            errors.append(f"audit is empty: {path}")
        elif {"audit_section", "item", "value", "source_note"} - set(rows[0]):
            errors.append(f"audit missing required columns: {path}")


def main() -> int:
    errors: list[str] = []
    for path in [POPULATION_PATH, SPENDING_PATH, STANDARDIZED_PATH, POPULATION_AUDIT_PATH, SPENDING_AUDIT_PATH]:
        if not path.exists():
            errors.append(f"missing expected file: {path}")
    if errors:
        return fail(errors)
    population_lookup = validate_population(errors)
    validate_standardized(errors, population_lookup)
    validate_audits(errors)
    if errors:
        return fail(errors)
    population_rows = read_rows(POPULATION_PATH)
    standardized_rows = read_rows(STANDARDIZED_PATH)
    print(
        "OK: population and spending denominator outputs passed validation "
        f"({len(population_rows)} population rows, {len(standardized_rows)} standardized spending rows)."
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
