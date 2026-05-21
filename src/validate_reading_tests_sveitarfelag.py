"""Lightweight validation for the public reading-test sveitarfelag dataset."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag.csv"

EXPECTED_ROW_COUNT = 1920
EXPECTED_MUNICIPALITIES = 48
EXPECTED_SCHOOL_YEARS = {
    "2020-2021",
    "2021-2022",
    "2022-2023",
    "2023-2024",
    "2024-2025",
}
EXPECTED_PERIODS = {"haust", "vor"}
EXPECTED_METRICS = {
    "1. vidmid",
    "2. vidmid",
    "3. vidmid",
    "naer_lagmarksvidmidi_2_3",
}
ROUNDING_TOLERANCE = 1.1


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def parse_pct(value: str, errors: list[str], row_number: int) -> float | None:
    if value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        errors.append(f"row {row_number}: value_pct is not numeric or blank: {value!r}")
        return None
    if not 0 <= parsed <= 100:
        errors.append(f"row {row_number}: value_pct outside 0-100: {parsed}")
    return parsed


def validate(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []

    if len(rows) != EXPECTED_ROW_COUNT:
        errors.append(f"expected {EXPECTED_ROW_COUNT} rows, found {len(rows)}")

    municipalities = {row["sveitarfelag"] for row in rows}
    if len(municipalities) != EXPECTED_MUNICIPALITIES:
        errors.append(
            f"expected {EXPECTED_MUNICIPALITIES} municipalities, found {len(municipalities)}"
        )

    school_years = {row["school_year"] for row in rows}
    if school_years != EXPECTED_SCHOOL_YEARS:
        errors.append(f"unexpected school years: {sorted(school_years)}")

    periods = {row["measurement_period"] for row in rows}
    if periods != EXPECTED_PERIODS:
        errors.append(f"unexpected measurement periods: {sorted(periods)}")

    metrics = {row["metric"] for row in rows}
    if metrics != EXPECTED_METRICS:
        errors.append(f"unexpected metrics: {sorted(metrics)}")

    grouped: dict[tuple[str, str, str], dict[str, float | None]] = defaultdict(dict)
    for row_number, row in enumerate(rows, start=2):
        value = parse_pct(row["value_pct"], errors, row_number)
        if row["number_of_students"] != "":
            errors.append(
                f"row {row_number}: number_of_students should be blank for this public source"
            )
        key = (row["sveitarfelag"], row["school_year"], row["measurement_period"])
        grouped[key][row["metric"]] = value

    for key, metric_values in grouped.items():
        missing_metrics = EXPECTED_METRICS - set(metric_values)
        if missing_metrics:
            errors.append(f"{key}: missing metrics {sorted(missing_metrics)}")
            continue

        if all(metric_values[metric] is not None for metric in EXPECTED_METRICS):
            first = metric_values["1. vidmid"]
            second = metric_values["2. vidmid"]
            third = metric_values["3. vidmid"]
            minimum = metric_values["naer_lagmarksvidmidi_2_3"]
            assert first is not None and second is not None and third is not None
            assert minimum is not None

            total = first + second + third
            if abs(total - 100) > ROUNDING_TOLERANCE:
                errors.append(f"{key}: 1+2+3 metrics sum to {total:.1f}, not about 100")

            minimum_sum = second + third
            if abs(minimum - minimum_sum) > ROUNDING_TOLERANCE:
                errors.append(
                    f"{key}: minimum metric {minimum:.1f} differs from 2+3 sum {minimum_sum:.1f}"
                )

    return errors


def main() -> int:
    if not DATA_PATH.exists():
        print(f"FAIL: missing dataset: {DATA_PATH}")
        return 1

    rows = read_rows(DATA_PATH)
    errors = validate(rows)
    if errors:
        print(f"FAIL: {len(errors)} validation issue(s) found in {DATA_PATH}")
        for error in errors[:25]:
            print(f"- {error}")
        if len(errors) > 25:
            print(f"- ... {len(errors) - 25} more")
        return 1

    print(
        "OK: reading_tests_sveitarfelag.csv passed validation "
        f"({len(rows)} rows, {EXPECTED_MUNICIPALITIES} municipalities, "
        "5 school years, 2 periods, 4 metrics)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
