"""Validate Hagstofa SKO02102 grunnskoli student count outputs."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COUNTS_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_student_counts.csv"
TENTH_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_10th_grade_counts.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "grunnskoli_student_counts_audit.csv"
SOURCE_URL = "https://px.hagstofa.is/pxis/pxweb/is/Samfelag/Samfelag__skolamal__2_grunnskolastig__0_gsNemendur/SKO02102.px/"
EXPECTED_YEARS = {str(year) for year in range(2001, 2025)}
EXPECTED_GRADES = {f"{grade}. bekkur" for grade in range(1, 11)}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def non_negative_int(value: str) -> bool:
    if value == "":
        return False
    try:
        parsed = int(value)
    except ValueError:
        return False
    return parsed >= 0


def validate() -> list[str]:
    errors: list[str] = []
    for path in [COUNTS_PATH, TENTH_PATH, AUDIT_PATH]:
        if not path.exists():
            errors.append(f"missing expected file: {path}")

    if errors:
        return errors

    counts = read_rows(COUNTS_PATH)
    tenth = read_rows(TENTH_PATH)
    audit = read_rows(AUDIT_PATH)
    if not counts:
        errors.append("student-count table is empty")
    if not tenth:
        errors.append("10th-grade table is empty")
    if not audit:
        errors.append("audit table is empty")

    count_required = {"year", "school_year", "grunnskoli", "normalized_school_name", "grade", "student_count", "source_url", "source_note"}
    tenth_required = {"year", "school_year", "grunnskoli", "normalized_school_name", "student_count_10th_grade", "source_url", "source_note"}
    audit_required = {"audit_section", "year", "grade", "item", "value", "source_note"}
    if counts and count_required - set(counts[0]):
        errors.append(f"student-count table missing columns: {sorted(count_required - set(counts[0]))}")
    if tenth and tenth_required - set(tenth[0]):
        errors.append(f"10th-grade table missing columns: {sorted(tenth_required - set(tenth[0]))}")
    if audit and audit_required - set(audit[0]):
        errors.append(f"audit table missing columns: {sorted(audit_required - set(audit[0]))}")

    years = {row["year"] for row in counts}
    grades = {row["grade"] for row in counts}
    if years != EXPECTED_YEARS:
        errors.append(f"unexpected years in student-count table: {sorted(years)}")
    if grades != EXPECTED_GRADES:
        errors.append(f"unexpected grades in student-count table: {sorted(grades)}")

    count_keys = set()
    for row_number, row in enumerate(counts, start=2):
        if not non_negative_int(row["student_count"]):
            errors.append(f"student-count row {row_number}: invalid student_count={row['student_count']!r}")
        if row["source_url"] != SOURCE_URL:
            errors.append(f"student-count row {row_number}: source_url missing or unexpected")
        if row["grunnskoli"] == "Alls":
            errors.append(f"student-count row {row_number}: aggregate Alls school should not be in processed output")
        count_keys.add((row["year"], row["school_year"], row["grunnskoli"], row["grade"]))

    tenth_keys = set()
    for row_number, row in enumerate(tenth, start=2):
        if not non_negative_int(row["student_count_10th_grade"]):
            errors.append(f"10th-grade row {row_number}: invalid student_count_10th_grade={row['student_count_10th_grade']!r}")
        if row["source_url"] != SOURCE_URL:
            errors.append(f"10th-grade row {row_number}: source_url missing or unexpected")
        if row["grunnskoli"] == "Alls":
            errors.append(f"10th-grade row {row_number}: aggregate Alls school should not be in processed output")
        key = (row["year"], row["school_year"], row["grunnskoli"], "10. bekkur")
        tenth_keys.add(key)
        if key not in count_keys:
            errors.append(f"10th-grade row {row_number}: no matching 10. bekkur row in main output")

    expected_tenth_keys = {key for key in count_keys if key[3] == "10. bekkur"}
    if tenth_keys != expected_tenth_keys:
        errors.append("10th-grade output does not exactly match main-output 10. bekkur rows")

    audit_items = {row["item"] for row in audit}
    for expected_item in ["years_extracted", "grades_extracted", "missing_source_cells_not_written", "hagstofa_source_caveats"]:
        if expected_item not in audit_items:
            errors.append(f"audit missing item: {expected_item}")
    if not any(row["item"] == "source_url" and row["value"] == SOURCE_URL for row in audit):
        errors.append("audit source_url missing or unexpected")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(f"FAIL: {len(errors)} validation issue(s) found")
        for error in errors[:30]:
            print(f"- {error}")
        if len(errors) > 30:
            print(f"- ... {len(errors) - 30} more")
        return 1

    counts = read_rows(COUNTS_PATH)
    tenth = read_rows(TENTH_PATH)
    years = sorted({row["year"] for row in counts})
    grades = sorted({row["grade"] for row in counts}, key=lambda text: int(text.split(".")[0]))
    print(
        "OK: grunnskoli student counts passed validation "
        f"({len(counts)} grade rows, {len(tenth)} 10th-grade rows, "
        f"years {years[0]}-{years[-1]}, grades {grades[0]}-{grades[-1]})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
