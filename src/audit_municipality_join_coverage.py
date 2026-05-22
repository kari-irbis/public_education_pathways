"""Audit harmonized municipality coverage across Phase 1-5 outputs."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
READING_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag_harmonized.csv"
SCHOOL_CROSSWALK_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_sveitarfelag_crosswalk.csv"
SPENDING_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_sveitarfelag.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "municipality_join_coverage_audit.csv"


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


def source_values(rows: list[dict[str, str]], harmonized: str) -> str:
    values = sorted({row.get("sveitarfelag_source", "") for row in rows if row.get("sveitarfelag_harmonized") == harmonized})
    return "; ".join(value for value in values if value)


def classify_gap(municipality: str, comparison: str, reading: set[str], school: set[str], spending: set[str]) -> tuple[str, str]:
    if municipality in {"Grindavíkurbær", "Skaftárhreppur"}:
        return (
            "likely_true_source_coverage_gap",
            "Municipality remains absent from the spending source after harmonization; this matches the observed Phase 5 source coverage.",
        )
    if comparison == "spending_not_reading":
        return (
            "likely_true_source_coverage_gap",
            "Municipality is present in the 2024 Samband spending source but not in the reading-test municipality dataset.",
        )
    if comparison == "spending_not_althingi_school_outcomes":
        return (
            "likely_true_source_coverage_gap",
            "Municipality is present in the 2024 Samband spending source but not among Alþingi-relevant school outcome municipalities.",
        )
    if municipality not in spending and (municipality in reading or municipality in school):
        return (
            "likely_true_source_coverage_gap",
            "Municipality appears in outcomes but not in the 2024 Samband spending extraction after current-name harmonization.",
        )
    return (
        "possible_harmonization_issue",
        "Unexpected coverage difference; inspect source and harmonized municipality names.",
    )


def audit_rows() -> list[dict[str, object]]:
    reading_rows = read_rows(READING_PATH)
    school_rows = [
        row
        for row in read_rows(SCHOOL_CROSSWALK_PATH)
        if row.get("mapping_priority") == "analysis_required" and row.get("match_status") != "excluded_group_or_aggregate"
    ]
    spending_rows = read_rows(SPENDING_PATH)

    reading = {row["sveitarfelag_harmonized"] for row in reading_rows if row.get("sveitarfelag_harmonized")}
    school = {row["sveitarfelag_harmonized"] for row in school_rows if row.get("sveitarfelag_harmonized")}
    spending = {row["sveitarfelag_harmonized"] for row in spending_rows if row.get("sveitarfelag_harmonized")}

    rows: list[dict[str, object]] = [
        {
            "audit_section": "summary",
            "comparison": "all_sources",
            "municipality": "",
            "in_reading_tests": "",
            "in_althingi_school_outcomes": "",
            "in_spending": "",
            "gap_type": "coverage_count",
            "assessment": "summary",
            "source_values": "",
            "note": f"reading_tests={len(reading)}; althingi_school_outcomes={len(school)}; spending={len(spending)}",
        }
    ]
    comparisons = [
        ("reading_not_spending", reading - spending),
        ("althingi_school_outcomes_not_spending", school - spending),
        ("spending_not_reading", spending - reading),
        ("spending_not_althingi_school_outcomes", spending - school),
    ]
    all_source_rows = {
        "reading_tests": reading_rows,
        "althingi_school_outcomes": school_rows,
        "spending": spending_rows,
    }
    for comparison, municipalities in comparisons:
        rows.append(
            {
                "audit_section": "summary",
                "comparison": comparison,
                "municipality": "",
                "in_reading_tests": "",
                "in_althingi_school_outcomes": "",
                "in_spending": "",
                "gap_type": "gap_count",
                "assessment": "summary",
                "source_values": "",
                "note": len(municipalities),
            }
        )
        for municipality in sorted(municipalities):
            gap_type, note = classify_gap(municipality, comparison, reading, school, spending)
            present_sources = [
                label
                for label, source_rows in all_source_rows.items()
                if any(row.get("sveitarfelag_harmonized") == municipality for row in source_rows)
            ]
            rows.append(
                {
                    "audit_section": "gap",
                    "comparison": comparison,
                    "municipality": municipality,
                    "in_reading_tests": str(municipality in reading).lower(),
                    "in_althingi_school_outcomes": str(municipality in school).lower(),
                    "in_spending": str(municipality in spending).lower(),
                    "gap_type": gap_type,
                    "assessment": "true_source_gap" if gap_type == "likely_true_source_coverage_gap" else "needs_review",
                    "source_values": " | ".join(
                        f"{label}: {source_values(source_rows, municipality)}"
                        for label, source_rows in all_source_rows.items()
                        if label in present_sources
                    ),
                    "note": note,
                }
            )
    return rows


def main() -> None:
    rows = audit_rows()
    write_csv(
        AUDIT_PATH,
        rows,
        [
            "audit_section",
            "comparison",
            "municipality",
            "in_reading_tests",
            "in_althingi_school_outcomes",
            "in_spending",
            "gap_type",
            "assessment",
            "source_values",
            "note",
        ],
    )
    print(f"Wrote {len(rows)} rows to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
