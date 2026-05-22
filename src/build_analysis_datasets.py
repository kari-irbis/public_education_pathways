"""Build Phase 7 analysis-ready joined datasets.

This script only prepares reusable joined datasets and coverage audits. It does
not rank, dashboard, or interpret municipality outcomes.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

READING_PATH = PROJECT_ROOT / "data" / "processed" / "reading_tests_sveitarfelag_harmonized.csv"
GRADUATES_PATH = PROJECT_ROOT / "data" / "processed" / "althingi_graduates_by_grunnskoli.csv"
GRADES_FG_PATH = PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_framhaldsskoli_grunnskoli.csv"
GRADES_G_PATH = PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_grunnskoli.csv"
CROSSWALK_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_sveitarfelag_crosswalk.csv"
SPENDING_PATH = PROJECT_ROOT / "data" / "processed" / "education_spend_sveitarfelag_standardized.csv"
POPULATION_PATH = PROJECT_ROOT / "data" / "processed" / "municipality_population_denominators.csv"

MUNICIPALITY_READING_SPENDING_OUTPUT = (
    PROJECT_ROOT / "data" / "processed" / "analysis_municipality_reading_spending_2024.csv"
)
SCHOOL_OUTCOMES_OUTPUT = PROJECT_ROOT / "data" / "processed" / "analysis_althingi_school_outcomes_mapped.csv"
MUNICIPALITY_ALTHINGI_OUTPUT = PROJECT_ROOT / "data" / "processed" / "analysis_municipality_althingi_summary.csv"
COVERAGE_AUDIT_OUTPUT = PROJECT_ROOT / "outputs" / "tables" / "analysis_dataset_coverage_audit.csv"

LATEST_READING_METRIC = "naer_lagmarksvidmidi_2_3"
LATEST_READING_PERIOD = "vor"
SPENDING_YEAR = "2024"
PRIMARY_SPENDING_METRIC = "kostnadur_netto"
SECONDARY_SPENDING_METRIC = "kostnadur_brutto"


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


def parse_float(value: object) -> float | None:
    text = "" if value is None else str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return float(text)


def parse_int(value: object) -> int | None:
    parsed = parse_float(value)
    if parsed is None:
        return None
    return int(parsed)


def fmt_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return ""
    return str(round(value, digits))


def bool_text(value: bool) -> str:
    return str(value).lower()


def latest_reading_rows() -> tuple[dict[str, dict[str, str]], str]:
    rows = [
        row
        for row in read_rows(READING_PATH)
        if row.get("measurement_period") == LATEST_READING_PERIOD and row.get("metric") == LATEST_READING_METRIC
    ]
    latest_year = max(row["school_year"] for row in rows)
    latest_rows = {
        row["sveitarfelag_harmonized"]: row
        for row in rows
        if row.get("school_year") == latest_year and row.get("sveitarfelag_harmonized")
    }
    return latest_rows, latest_year


def spending_metric_rows(metric: str) -> dict[str, dict[str, str]]:
    return {
        row["sveitarfelag_harmonized"]: row
        for row in read_rows(SPENDING_PATH)
        if row.get("year") == SPENDING_YEAR
        and row.get("spending_metric") == metric
        and row.get("sveitarfelag_harmonized")
    }


def population_rows() -> dict[str, dict[str, str]]:
    return {
        row["sveitarfelag_harmonized"]: row
        for row in read_rows(POPULATION_PATH)
        if row.get("year") == SPENDING_YEAR and row.get("sveitarfelag_harmonized")
    }


def school_crosswalk() -> dict[str, dict[str, str]]:
    return {row["source_school_name"]: row for row in read_rows(CROSSWALK_PATH)}


def build_municipality_reading_spending() -> tuple[list[dict[str, object]], set[str], set[str], set[str], str]:
    reading, latest_year = latest_reading_rows()
    spending_primary = spending_metric_rows(PRIMARY_SPENDING_METRIC)
    spending_secondary = spending_metric_rows(SECONDARY_SPENDING_METRIC)
    population = population_rows()
    municipalities = sorted(set(reading) | set(spending_primary) | set(spending_secondary))
    rows: list[dict[str, object]] = []
    for municipality in municipalities:
        reading_row = reading.get(municipality, {})
        primary = spending_primary.get(municipality, {})
        secondary = spending_secondary.get(municipality, {})
        pop = population.get(municipality, {})
        rows.append(
            {
                "sveitarfelag_harmonized": municipality,
                "latest_reading_school_year": reading_row.get("school_year", latest_year),
                "latest_reading_measurement_period": LATEST_READING_PERIOD,
                "latest_reading_metric": LATEST_READING_METRIC,
                "latest_reading_value_pct": reading_row.get("value_pct", ""),
                "spending_year": SPENDING_YEAR,
                "primary_spending_metric": PRIMARY_SPENDING_METRIC,
                "amount_isk": primary.get("amount_isk", ""),
                "spend_per_source_student": primary.get("spend_per_source_student", ""),
                "spend_per_resident": primary.get("spend_per_resident", ""),
                "spend_per_resident_age_6_15": primary.get("spend_per_resident_age_6_15", ""),
                "population_total": primary.get("population_total") or pop.get("population_total", ""),
                "population_age_6_15": primary.get("population_age_6_15") or pop.get("population_age_6_15", ""),
                "denominator_source_student_count": primary.get("denominator_source_student_count", ""),
                "kostnadur_netto_amount_isk": primary.get("amount_isk", ""),
                "kostnadur_netto_spend_per_source_student": primary.get("spend_per_source_student", ""),
                "kostnadur_netto_spend_per_resident": primary.get("spend_per_resident", ""),
                "kostnadur_netto_spend_per_resident_age_6_15": primary.get("spend_per_resident_age_6_15", ""),
                "kostnadur_brutto_amount_isk": secondary.get("amount_isk", ""),
                "kostnadur_brutto_spend_per_source_student": secondary.get("spend_per_source_student", ""),
                "kostnadur_brutto_spend_per_resident": secondary.get("spend_per_resident", ""),
                "kostnadur_brutto_spend_per_resident_age_6_15": secondary.get("spend_per_resident_age_6_15", ""),
                "has_latest_reading": bool_text(bool(reading_row.get("value_pct", ""))),
                "has_2024_spending_kostnadur_netto": bool_text(bool(primary)),
                "has_2024_spending_kostnadur_brutto": bool_text(bool(secondary)),
                "has_2024_population_denominator": bool_text(bool(pop) or bool(primary.get("population_total", ""))),
                "reading_source_url": reading_row.get("source_url", ""),
                "spending_source_url": primary.get("source_url", "") or secondary.get("source_url", ""),
                "source_note": (
                    "Latest vor reading metric is retained as a percentage. Spending is 2024 Samband "
                    "municipality-level grunnskóli spending; population_age_6_15 is a population proxy, "
                    "not exact school enrollment."
                ),
            }
        )
    return rows, set(reading), set(spending_primary) | set(spending_secondary), set(population), latest_year


def rows_by_school(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("grunnskoli", "")].append(row)
    return grouped


def sum_students(rows: list[dict[str, str]]) -> int | None:
    values = [parse_int(row.get("number_of_students", "")) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values)


def build_school_outcomes_mapped() -> list[dict[str, object]]:
    graduates = {row["grunnskoli"]: row for row in read_rows(GRADUATES_PATH)}
    grades_g = {row["grunnskoli"]: row for row in read_rows(GRADES_G_PATH)}
    grades_fg = rows_by_school(read_rows(GRADES_FG_PATH))
    crosswalk = school_crosswalk()
    school_names = sorted(set(graduates) | set(grades_g) | set(grades_fg))
    rows: list[dict[str, object]] = []
    for school in school_names:
        graduate_row = graduates.get(school, {})
        grade_row = grades_g.get(school, {})
        fg_rows = grades_fg.get(school, [])
        cw = crosswalk.get(school, {})
        rows.append(
            {
                "source_school_name": school,
                "canonical_school_name": cw.get("canonical_school_name", ""),
                "sveitarfelag_harmonized": cw.get("sveitarfelag_harmonized", ""),
                "sveitarfelag_source": cw.get("sveitarfelag_source", ""),
                "mapping_priority": cw.get("mapping_priority", ""),
                "match_status": cw.get("match_status", ""),
                "match_confidence": cw.get("match_confidence", ""),
                "match_source": cw.get("match_source", ""),
                "graduate_count": graduate_row.get("number_of_students", ""),
                "average_grunnskoli_icelandic_grade": grade_row.get("average_grunnskoli_icelandic_grade", ""),
                "average_grunnskoli_math_grade": grade_row.get("average_grunnskoli_math_grade", ""),
                "framhaldsskoli_grunnskoli_row_count": len(fg_rows),
                "framhaldsskoli_grunnskoli_student_count": "" if sum_students(fg_rows) is None else sum_students(fg_rows),
                "has_graduates_table_row": bool_text(school in graduates),
                "has_grades_by_grunnskoli_row": bool_text(school in grades_g),
                "has_framhaldsskoli_grunnskoli_rows": bool_text(school in grades_fg),
                "is_excluded_group_or_aggregate": bool_text(
                    cw.get("mapping_priority") == "excluded_group_or_aggregate"
                    or grade_row.get("row_type") == "aggregate"
                    or school in {"Heild", "Alls", "Færri en 5 nemendur"}
                ),
                "notes": "; ".join(
                    note
                    for note in [
                        cw.get("notes", ""),
                        grade_row.get("manual_review", ""),
                        graduate_row.get("manual_review", ""),
                    ]
                    if note
                ),
                "source_url": graduate_row.get("source_url", "") or grade_row.get("source_url", ""),
                "source_note": (
                    "Alþingi PDF candidate school-level output joined to the Phase 4 school crosswalk. "
                    "Rows are extraction candidates; do not treat graduate counts as true graduation rates."
                ),
            }
        )
    return rows


def weighted_average(values: list[tuple[float | None, int | None]]) -> float | None:
    complete = [(value, weight) for value, weight in values if value is not None and weight is not None and weight > 0]
    if not complete:
        return None
    return sum(value * weight for value, weight in complete) / sum(weight for _, weight in complete)


def simple_average(values: list[float | None]) -> float | None:
    complete = [value for value in values if value is not None]
    if not complete:
        return None
    return sum(complete) / len(complete)


def build_municipality_althingi_summary(school_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in school_rows:
        municipality = str(row.get("sveitarfelag_harmonized", ""))
        if not municipality or row.get("is_excluded_group_or_aggregate") == "true":
            continue
        if row.get("mapping_priority") != "analysis_required":
            continue
        grouped[municipality].append(row)

    rows: list[dict[str, object]] = []
    for municipality, municipality_rows in sorted(grouped.items()):
        graduate_counts = [parse_int(row.get("graduate_count", "")) for row in municipality_rows]
        graduate_counts = [value for value in graduate_counts if value is not None]
        icelandic_values = [parse_float(row.get("average_grunnskoli_icelandic_grade", "")) for row in municipality_rows]
        math_values = [parse_float(row.get("average_grunnskoli_math_grade", "")) for row in municipality_rows]
        weights = [parse_int(row.get("graduate_count", "")) for row in municipality_rows]
        weighted_ice = weighted_average(list(zip(icelandic_values, weights)))
        weighted_math = weighted_average(list(zip(math_values, weights)))
        unweighted_ice = simple_average(icelandic_values)
        unweighted_math = simple_average(math_values)
        rows.append(
            {
                "sveitarfelag_harmonized": municipality,
                "total_matched_graduates": sum(graduate_counts),
                "number_of_grunnskolar_represented": len({str(row["source_school_name"]) for row in municipality_rows}),
                "average_grunnskoli_icelandic_grade": fmt_number(weighted_ice if weighted_ice is not None else unweighted_ice),
                "average_grunnskoli_math_grade": fmt_number(weighted_math if weighted_math is not None else unweighted_math),
                "weighted_average_grunnskoli_icelandic_grade": fmt_number(weighted_ice),
                "weighted_average_grunnskoli_math_grade": fmt_number(weighted_math),
                "unweighted_average_grunnskoli_icelandic_grade": fmt_number(unweighted_ice),
                "unweighted_average_grunnskoli_math_grade": fmt_number(unweighted_math),
                "average_grade_weighting_method": (
                    "graduate_count_weighted_where_available"
                    if weighted_ice is not None or weighted_math is not None
                    else "unweighted_school_average"
                ),
                "schools_with_graduate_counts": sum(1 for row in municipality_rows if row.get("graduate_count") not in {"", None}),
                "schools_with_grade_rows": sum(
                    1
                    for row in municipality_rows
                    if row.get("average_grunnskoli_icelandic_grade") not in {"", None}
                    or row.get("average_grunnskoli_math_grade") not in {"", None}
                ),
                "has_graduates_table_coverage": bool_text(any(row.get("has_graduates_table_row") == "true" for row in municipality_rows)),
                "has_grades_by_grunnskoli_coverage": bool_text(
                    any(row.get("has_grades_by_grunnskoli_row") == "true" for row in municipality_rows)
                ),
                "has_framhaldsskoli_grunnskoli_coverage": bool_text(
                    any(row.get("has_framhaldsskoli_grunnskoli_rows") == "true" for row in municipality_rows)
                ),
                "source_note": (
                    "Municipality summary of Alþingi PDF candidate school rows. Weighted averages use "
                    "matched graduate_count where available; this is not a true graduation rate."
                ),
            }
        )
    return rows


def audit_rows(
    reading_municipalities: set[str],
    spending_municipalities: set[str],
    althingi_municipalities: set[str],
    municipality_reading_spending_rows: list[dict[str, object]],
    school_rows: list[dict[str, object]],
    municipality_althingi_rows: list[dict[str, object]],
    latest_year: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "audit_section": "summary",
            "item": "analysis_municipality_reading_spending_2024_rows",
            "value": len(municipality_reading_spending_rows),
            "details": "",
        },
        {"audit_section": "summary", "item": "analysis_althingi_school_outcomes_mapped_rows", "value": len(school_rows), "details": ""},
        {"audit_section": "summary", "item": "analysis_municipality_althingi_summary_rows", "value": len(municipality_althingi_rows), "details": ""},
        {"audit_section": "summary", "item": "latest_reading_school_year_used", "value": latest_year, "details": ""},
        {"audit_section": "coverage", "item": "reading_municipalities", "value": len(reading_municipalities), "details": "; ".join(sorted(reading_municipalities))},
        {"audit_section": "coverage", "item": "spending_municipalities", "value": len(spending_municipalities), "details": "; ".join(sorted(spending_municipalities))},
        {"audit_section": "coverage", "item": "althingi_summary_municipalities", "value": len(althingi_municipalities), "details": "; ".join(sorted(althingi_municipalities))},
    ]
    gap_specs = [
        ("reading_not_spending", reading_municipalities - spending_municipalities),
        ("spending_not_reading", spending_municipalities - reading_municipalities),
        ("althingi_not_spending", althingi_municipalities - spending_municipalities),
        ("spending_not_althingi", spending_municipalities - althingi_municipalities),
        ("reading_not_althingi", reading_municipalities - althingi_municipalities),
        ("althingi_not_reading", althingi_municipalities - reading_municipalities),
    ]
    for item, municipalities in gap_specs:
        rows.append({"audit_section": "join_gap_summary", "item": item, "value": len(municipalities), "details": "; ".join(sorted(municipalities))})
        for municipality in sorted(municipalities):
            rows.append(
                {
                    "audit_section": "join_gap",
                    "item": item,
                    "value": municipality,
                    "details": (
                        f"in_reading={bool_text(municipality in reading_municipalities)}; "
                        f"in_spending={bool_text(municipality in spending_municipalities)}; "
                        f"in_althingi_summary={bool_text(municipality in althingi_municipalities)}"
                    ),
                }
            )
    rows.extend(
        [
            {
                "audit_section": "caveat",
                "item": "spending_denominators",
                "value": "population_age_6_15 is a population proxy, not exact school enrollment",
                "details": "spend_per_source_student uses Samband source student counts; population ratios use Hagstofa MAN02005.",
            },
            {
                "audit_section": "caveat",
                "item": "althingi_grades",
                "value": "candidate extraction, non-standardized grades",
                "details": "Alþingi grade tables are preserved as extracted candidates and are not adjusted for school or cohort composition.",
            },
            {
                "audit_section": "caveat",
                "item": "graduates",
                "value": "not a true graduation rate",
                "details": "Alþingi graduate counts are not joined to a true cohort denominator in Phase 7.",
            },
        ]
    )
    return rows


def main() -> None:
    municipality_reading_spending, reading_municipalities, spending_municipalities, _, latest_year = (
        build_municipality_reading_spending()
    )
    school_rows = build_school_outcomes_mapped()
    municipality_althingi = build_municipality_althingi_summary(school_rows)
    althingi_municipalities = {str(row["sveitarfelag_harmonized"]) for row in municipality_althingi}

    write_csv(
        MUNICIPALITY_READING_SPENDING_OUTPUT,
        municipality_reading_spending,
        [
            "sveitarfelag_harmonized",
            "latest_reading_school_year",
            "latest_reading_measurement_period",
            "latest_reading_metric",
            "latest_reading_value_pct",
            "spending_year",
            "primary_spending_metric",
            "amount_isk",
            "spend_per_source_student",
            "spend_per_resident",
            "spend_per_resident_age_6_15",
            "population_total",
            "population_age_6_15",
            "denominator_source_student_count",
            "kostnadur_netto_amount_isk",
            "kostnadur_netto_spend_per_source_student",
            "kostnadur_netto_spend_per_resident",
            "kostnadur_netto_spend_per_resident_age_6_15",
            "kostnadur_brutto_amount_isk",
            "kostnadur_brutto_spend_per_source_student",
            "kostnadur_brutto_spend_per_resident",
            "kostnadur_brutto_spend_per_resident_age_6_15",
            "has_latest_reading",
            "has_2024_spending_kostnadur_netto",
            "has_2024_spending_kostnadur_brutto",
            "has_2024_population_denominator",
            "reading_source_url",
            "spending_source_url",
            "source_note",
        ],
    )
    write_csv(
        SCHOOL_OUTCOMES_OUTPUT,
        school_rows,
        [
            "source_school_name",
            "canonical_school_name",
            "sveitarfelag_harmonized",
            "sveitarfelag_source",
            "mapping_priority",
            "match_status",
            "match_confidence",
            "match_source",
            "graduate_count",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "framhaldsskoli_grunnskoli_row_count",
            "framhaldsskoli_grunnskoli_student_count",
            "has_graduates_table_row",
            "has_grades_by_grunnskoli_row",
            "has_framhaldsskoli_grunnskoli_rows",
            "is_excluded_group_or_aggregate",
            "notes",
            "source_url",
            "source_note",
        ],
    )
    write_csv(
        MUNICIPALITY_ALTHINGI_OUTPUT,
        municipality_althingi,
        [
            "sveitarfelag_harmonized",
            "total_matched_graduates",
            "number_of_grunnskolar_represented",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "weighted_average_grunnskoli_icelandic_grade",
            "weighted_average_grunnskoli_math_grade",
            "unweighted_average_grunnskoli_icelandic_grade",
            "unweighted_average_grunnskoli_math_grade",
            "average_grade_weighting_method",
            "schools_with_graduate_counts",
            "schools_with_grade_rows",
            "has_graduates_table_coverage",
            "has_grades_by_grunnskoli_coverage",
            "has_framhaldsskoli_grunnskoli_coverage",
            "source_note",
        ],
    )
    write_csv(
        COVERAGE_AUDIT_OUTPUT,
        audit_rows(
            reading_municipalities,
            spending_municipalities,
            althingi_municipalities,
            municipality_reading_spending,
            school_rows,
            municipality_althingi,
            latest_year,
        ),
        ["audit_section", "item", "value", "details"],
    )
    print(f"Wrote {len(municipality_reading_spending)} rows to {MUNICIPALITY_READING_SPENDING_OUTPUT}")
    print(f"Wrote {len(school_rows)} rows to {SCHOOL_OUTCOMES_OUTPUT}")
    print(f"Wrote {len(municipality_althingi)} rows to {MUNICIPALITY_ALTHINGI_OUTPUT}")
    print(f"Wrote audit to {COVERAGE_AUDIT_OUTPUT}")


if __name__ == "__main__":
    main()
