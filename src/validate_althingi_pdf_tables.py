"""Validate candidate datasets extracted from Althingi PDF 157/s/1182."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATASETS = {
    "graduates": {
        "path": PROJECT_ROOT / "data" / "processed" / "althingi_graduates_by_grunnskoli.csv",
        "required_columns": {"grunnskoli", "number_of_students", "source_url", "source_note"},
        "numeric_columns": {"number_of_students": "int_positive"},
    },
    "framhald_grades": {
        "path": PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_framhaldsskoli_grunnskoli.csv",
        "required_columns": {
            "framhaldsskoli",
            "grunnskoli",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "average_framhaldsskoli_graduation_grade",
            "number_of_students",
            "is_fewer_than_5_group",
            "source_url",
            "source_note",
        },
        "numeric_columns": {
            "average_grunnskoli_icelandic_grade": "grade",
            "average_grunnskoli_math_grade": "grade",
            "average_framhaldsskoli_graduation_grade": "grade",
            "number_of_students": "int_positive",
        },
    },
    "grunnskoli_grades": {
        "path": PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_grunnskoli.csv",
        "required_columns": {
            "grunnskoli",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "row_type",
            "is_fewer_than_5_group",
            "source_url",
            "source_note",
        },
        "numeric_columns": {
            "average_grunnskoli_icelandic_grade": "grade",
            "average_grunnskoli_math_grade": "grade",
        },
    },
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def validate_numeric(value: str, kind: str) -> bool:
    if value == "":
        return False
    try:
        parsed = float(value)
    except ValueError:
        return False
    if kind == "int_positive":
        return parsed.is_integer() and parsed > 0
    if kind == "grade":
        return 0 <= parsed <= 10
    raise ValueError(f"Unknown numeric validation kind: {kind}")


def main() -> int:
    errors: list[str] = []
    row_counts: dict[str, int] = {}
    fewer_than_5_rows = 0

    for name, config in DATASETS.items():
        path = config["path"]
        if not path.exists():
            errors.append(f"{name}: missing output file {path}")
            continue
        rows = read_rows(path)
        row_counts[name] = len(rows)
        if not rows:
            errors.append(f"{name}: table is empty")
            continue
        columns = set(rows[0].keys())
        missing_columns = config["required_columns"] - columns
        if missing_columns:
            errors.append(f"{name}: missing required columns {sorted(missing_columns)}")
        for row_number, row in enumerate(rows, start=2):
            for column, kind in config["numeric_columns"].items():
                if column in row and not validate_numeric(row[column], kind):
                    errors.append(f"{name} row {row_number}: invalid {column}={row[column]!r}")
            if row.get("grunnskoli") == "Færri en 5 nemendur":
                fewer_than_5_rows += 1
                if name in {"framhald_grades", "grunnskoli_grades"} and row.get("is_fewer_than_5_group") != "true":
                    errors.append(f"{name} row {row_number}: Færri en 5 row not flagged")

    if fewer_than_5_rows == 0:
        errors.append("No Færri en 5 nemendur rows found; suppression rows were not preserved")

    if errors:
        print(f"FAIL: {len(errors)} validation issue(s) found")
        for error in errors[:30]:
            print(f"- {error}")
        if len(errors) > 30:
            print(f"- ... {len(errors) - 30} more")
        return 1

    print(
        "OK: Althingi PDF candidate tables passed validation "
        f"({row_counts.get('graduates', 0)} graduate rows, "
        f"{row_counts.get('framhald_grades', 0)} framhaldsskoli-grunnskoli rows, "
        f"{row_counts.get('grunnskoli_grades', 0)} grunnskoli grade rows; "
        f"{fewer_than_5_rows} Færri en 5 rows preserved)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
