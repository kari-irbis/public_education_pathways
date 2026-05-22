"""Add explicit student and population denominators to education spending rows."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPENDING_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_sveitarfelag.csv"
POPULATION_PATH = PROJECT_ROOT / "data" / "processed" / "municipality_population_denominators.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_sveitarfelag_standardized.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "spending_denominator_audit.csv"


DENOMINATOR_NOTE = (
    "denominator_source_student_count and spend_per_source_student use the Samband school-row "
    "student_count source. population_age_6_15 is a municipality population proxy from Hagstofa "
    "MAN02005, not exact school enrollment."
)


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


def parse_float(value: str) -> float | None:
    if value == "":
        return None
    return float(value)


def ratio(amount: float | None, denominator: float | None) -> str:
    if amount is None or denominator is None or denominator == 0:
        return ""
    return str(round(amount / denominator, 2))


def population_lookup() -> dict[tuple[str, str], dict[str, str]]:
    rows = read_rows(POPULATION_PATH)
    return {(row["year"], row["sveitarfelag_harmonized"]): row for row in rows}


def standardized_rows() -> tuple[list[dict[str, object]], list[str]]:
    spending = read_rows(SPENDING_PATH)
    population = population_lookup()
    output = []
    missing_population: set[str] = set()
    for row in spending:
        pop = population.get((row["year"], row["sveitarfelag_harmonized"]))
        amount = parse_float(row["amount_isk"])
        source_students = parse_float(row["student_count"])
        population_total = parse_float(pop["population_total"]) if pop else None
        population_age_6_15 = parse_float(pop["population_age_6_15"]) if pop else None
        if not pop:
            missing_population.add(row["sveitarfelag_harmonized"])
        output.append(
            {
                **row,
                "denominator_source_student_count": row["student_count"],
                "spend_per_source_student": ratio(amount, source_students),
                "population_total": "" if population_total is None else int(population_total),
                "spend_per_resident": ratio(amount, population_total),
                "population_age_6_15": "" if population_age_6_15 is None else int(population_age_6_15),
                "spend_per_resident_age_6_15": ratio(amount, population_age_6_15),
                "denominator_notes": DENOMINATOR_NOTE,
            }
        )
    return output, sorted(missing_population)


def build_audit(rows: list[dict[str, object]], missing_population: list[str]) -> list[dict[str, object]]:
    spending_municipalities = sorted({str(row["sveitarfelag_harmonized"]) for row in rows})
    matched = sorted({str(row["sveitarfelag_harmonized"]) for row in rows if str(row["population_total"]) != ""})
    status_counts = Counter(str(row["sveitarfelag_harmonization_status"]) for row in rows)
    return [
        {"audit_section": "summary", "item": "standardized_spending_rows", "value": len(rows), "source_note": ""},
        {"audit_section": "summary", "item": "spending_harmonized_municipalities", "value": len(spending_municipalities), "source_note": "; ".join(spending_municipalities)},
        {"audit_section": "summary", "item": "spending_municipalities_matched_to_population_denominators", "value": len(matched), "source_note": "; ".join(matched)},
        {"audit_section": "summary", "item": "spending_municipalities_missing_population_denominators", "value": len(missing_population), "source_note": "; ".join(missing_population)},
        {"audit_section": "summary", "item": "municipality_harmonization_status_counts", "value": "; ".join(f"{key}: {value}" for key, value in sorted(status_counts.items())), "source_note": ""},
        {"audit_section": "definition", "item": "denominator_source_student_count", "value": "Copied from Samband spending source student_count.", "source_note": DENOMINATOR_NOTE},
        {"audit_section": "definition", "item": "spend_per_source_student", "value": "amount_isk / denominator_source_student_count.", "source_note": DENOMINATOR_NOTE},
        {"audit_section": "definition", "item": "spend_per_resident", "value": "amount_isk / Hagstofa MAN02005 population_total.", "source_note": DENOMINATOR_NOTE},
        {"audit_section": "definition", "item": "spend_per_resident_age_6_15", "value": "amount_isk / Hagstofa MAN02005 population_age_6_15.", "source_note": DENOMINATOR_NOTE},
        {"audit_section": "caveat", "item": "population_age_6_15", "value": "Population proxy only; not exact school enrollment.", "source_note": DENOMINATOR_NOTE},
    ]


def main() -> None:
    rows, missing_population = standardized_rows()
    original_fields = list(read_rows(SPENDING_PATH)[0].keys())
    added_fields = [
        "denominator_source_student_count",
        "spend_per_source_student",
        "population_total",
        "spend_per_resident",
        "population_age_6_15",
        "spend_per_resident_age_6_15",
        "denominator_notes",
    ]
    write_csv(OUTPUT_PATH, rows, original_fields + added_fields)
    write_csv(AUDIT_PATH, build_audit(rows, missing_population), ["audit_section", "item", "value", "source_note"])
    print(f"Wrote {len(rows)} standardized spending rows to {OUTPUT_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
