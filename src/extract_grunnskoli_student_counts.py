"""Extract grunnskoli student counts from Hagstofa table SKO02102.

This phase creates reusable denominator/proxy datasets only. It does not perform
school matching, rankings, dashboards, or rate calculations.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://px.hagstofa.is/pxis/pxweb/is/Samfelag/Samfelag__skolamal__2_grunnskolastig__0_gsNemendur/SKO02102.px/"
API_URL = "https://px.hagstofa.is/pxis/api/v1/is/Samfelag/skolamal/2_grunnskolastig/0_gsNemendur/SKO02102.px"

RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TABLES_DIR = PROJECT_ROOT / "outputs" / "tables"

METADATA_PATH = RAW_DIR / "hagstofa_sko02102_metadata.json"
QUERY_PATH = RAW_DIR / "hagstofa_sko02102_query.json"
RAW_RESPONSE_PATH = RAW_DIR / "hagstofa_sko02102_all_jsonstat2.json"
INTERIM_LONG_PATH = INTERIM_DIR / "hagstofa_sko02102_long_all_categories.csv"
PROCESSED_COUNTS_PATH = PROCESSED_DIR / "grunnskoli_student_counts.csv"
PROCESSED_10TH_PATH = PROCESSED_DIR / "grunnskoli_10th_grade_counts.csv"
AUDIT_PATH = TABLES_DIR / "grunnskoli_student_counts_audit.csv"

GRADE_LABELS = {f"{i}. bekkur" for i in range(1, 11)}
PROCESSED_SOURCE_NOTE = (
    "Hagstofa table SKO02102. Grade rows 1. bekkur through 10. bekkur; "
    "raw table also includes Alls, Drengir, and Stúlkur categories. "
    "Use as denominator/proxy context, not a true framhaldsskóli graduation-rate denominator."
)

SOURCE_CAVEATS = (
    "Hagstofa table SKO02102. Nemendum sem ekki er raðað í bekki af skólum eru "
    "flokkaðir í bekki eftir aldri. Nemendur í fimm ára bekk eru ekki meðtaldir "
    "en eru sýndir í sérstakri töflu til ársins 2016; eftir það teljast þeir með "
    "leikskólabörnum. Notes in the source also flag possible registration issues "
    "for Einholtsskóli in 2001-2002, Dalbrautarskóli in 2001, and that pupils in "
    "Kárahnjúkaskóli 2004-2006 are not included. This is a denominator/proxy "
    "source, not a true framhaldsskóli graduation-rate denominator."
)


def ensure_dirs() -> None:
    for path in [RAW_DIR, INTERIM_DIR, PROCESSED_DIR, TABLES_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def fetch_json(url: str, path: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if path.exists() and path.stat().st_size > 0:
        return json.loads(path.read_text(encoding="utf-8"))
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"User-Agent": "andvari-public-education-pathways/0.3"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"URL fetch failed for {url}: {exc.reason}") from exc
    path.write_bytes(body)
    return json.loads(body.decode("utf-8"))


def build_all_values_query(metadata: dict[str, Any]) -> dict[str, Any]:
    query = []
    for variable in metadata["variables"]:
        query.append(
            {
                "code": variable["code"],
                "selection": {"filter": "item", "values": variable["values"]},
            }
        )
    return {"query": query, "response": {"format": "JSON-stat2"}}


def ordered_labels(dataset: dict[str, Any], dimension_id: str) -> list[tuple[str, str]]:
    category = dataset["dimension"][dimension_id]["category"]
    labels = category["label"]
    index = category["index"]
    return [(code, labels[code]) for code, _ in sorted(index.items(), key=lambda item: item[1])]


def normalize_school_name(name: str) -> str:
    replacements = {
        "æ": "ae",
        "ð": "d",
        "þ": "th",
        "ö": "o",
    }
    lowered = name.casefold()
    for source, target in replacements.items():
        lowered = lowered.replace(source, target)
    normalized = unicodedata.normalize("NFKD", lowered)
    asciiish = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    asciiish = re.sub(r"[^a-z0-9]+", " ", asciiish)
    return re.sub(r"\s+", " ", asciiish).strip()


def grade_sort_key(grade: object) -> int:
    return int(str(grade).split(".")[0])


def source_year_to_school_year(year: int) -> str:
    return f"{year}-{year + 1}"


def parse_jsonstat(dataset: dict[str, Any]) -> list[dict[str, object]]:
    ids = dataset["id"]
    if ids != ["Skóli", "Ár", "Bekkur"]:
        raise RuntimeError(f"Unexpected dimension order: {ids}")
    school_labels = ordered_labels(dataset, "Skóli")
    year_labels = ordered_labels(dataset, "Ár")
    grade_labels = ordered_labels(dataset, "Bekkur")
    values = dataset["value"]
    size_school, size_year, size_grade = dataset["size"]
    rows: list[dict[str, object]] = []
    position = 0
    for school_code, school in school_labels:
        for year_code, year_text in year_labels:
            year = int(year_text)
            for grade_code, grade in grade_labels:
                value = values[position] if position < len(values) else None
                position += 1
                rows.append(
                    {
                        "school_code": school_code,
                        "year_code": year_code,
                        "grade_code": grade_code,
                        "year": year,
                        "school_year": source_year_to_school_year(year),
                        "grunnskoli": school,
                        "normalized_school_name": normalize_school_name(school),
                        "grade": grade,
                        "student_count": value,
                        "source_url": SOURCE_URL,
                        "source_note": PROCESSED_SOURCE_NOTE,
                    }
                )
    expected = size_school * size_year * size_grade
    if position != expected or len(values) != expected:
        raise RuntimeError(f"Unexpected JSON-stat size: iterated {position}, values {len(values)}, expected {expected}")
    return rows


def is_int_like(value: object) -> bool:
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def processed_grade_rows(all_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for row in all_rows:
        value = row["student_count"]
        if row["grunnskoli"] == "Alls" or row["grade"] not in GRADE_LABELS or value is None:
            continue
        if not is_int_like(value):
            raise RuntimeError(f"Non-integer student count: {value!r} in {row}")
        rows.append(
            {
                "year": row["year"],
                "school_year": row["school_year"],
                "grunnskoli": row["grunnskoli"],
                "normalized_school_name": row["normalized_school_name"],
                "grade": row["grade"],
                "student_count": int(value),
                "source_url": row["source_url"],
                "source_note": row["source_note"],
            }
        )
    rows.sort(key=lambda row: (int(row["year"]), str(row["grunnskoli"]), grade_sort_key(row["grade"])))
    return rows


def tenth_grade_rows(grade_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for row in grade_rows:
        if row["grade"] != "10. bekkur":
            continue
        rows.append(
            {
                "year": row["year"],
                "school_year": row["school_year"],
                "grunnskoli": row["grunnskoli"],
                "normalized_school_name": row["normalized_school_name"],
                "student_count_10th_grade": row["student_count"],
                "source_url": row["source_url"],
                "source_note": row["source_note"],
            }
        )
    rows.sort(key=lambda row: (int(row["year"]), str(row["grunnskoli"])))
    return rows


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_audit(all_rows: list[dict[str, object]], grade_rows: list[dict[str, object]], tenth_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    audit: list[dict[str, object]] = [
        {"audit_section": "source", "year": "", "grade": "", "item": "source_url", "value": SOURCE_URL, "source_note": ""},
        {"audit_section": "source", "year": "", "grade": "", "item": "api_url", "value": API_URL, "source_note": ""},
        {"audit_section": "source", "year": "", "grade": "", "item": "extracted_at_utc", "value": datetime.now(timezone.utc).isoformat(), "source_note": ""},
        {"audit_section": "source", "year": "", "grade": "", "item": "raw_metadata_file", "value": str(METADATA_PATH.relative_to(PROJECT_ROOT)), "source_note": ""},
        {"audit_section": "source", "year": "", "grade": "", "item": "raw_response_file", "value": str(RAW_RESPONSE_PATH.relative_to(PROJECT_ROOT)), "source_note": ""},
        {"audit_section": "summary", "year": "", "grade": "", "item": "processed_grade_rows", "value": len(grade_rows), "source_note": ""},
        {"audit_section": "summary", "year": "", "grade": "", "item": "processed_10th_grade_rows", "value": len(tenth_rows), "source_note": ""},
        {"audit_section": "summary", "year": "", "grade": "", "item": "years_extracted", "value": "; ".join(str(year) for year in sorted({row["year"] for row in grade_rows})), "source_note": ""},
        {"audit_section": "summary", "year": "", "grade": "", "item": "grades_extracted", "value": "; ".join(sorted({str(row["grade"]) for row in grade_rows}, key=lambda text: int(text.split(".")[0]))), "source_note": ""},
        {"audit_section": "summary", "year": "", "grade": "", "item": "missing_source_cells_not_written", "value": sum(1 for row in all_rows if row["student_count"] is None), "source_note": "Null/missing cells in the raw JSON-stat response are omitted from processed outputs."},
        {"audit_section": "summary", "year": "", "grade": "", "item": "hagstofa_source_caveats", "value": SOURCE_CAVEATS, "source_note": ""},
    ]

    by_year_schools: dict[int, set[str]] = {}
    for row in grade_rows:
        by_year_schools.setdefault(int(row["year"]), set()).add(str(row["grunnskoli"]))

    lookup = {(row["grunnskoli"], row["year"], row["grade"]): row["student_count"] for row in all_rows}
    for year in sorted(by_year_schools):
        audit.append({"audit_section": "by_year", "year": year, "grade": "", "item": "schools_with_grade_rows", "value": len(by_year_schools[year]), "source_note": "Schools excluding the aggregate Alls row and excluding null grade cells."})
        audit.append({"audit_section": "by_year", "year": year, "grade": "Alls", "item": "total_students_source_all_schools", "value": lookup.get(("Alls", year, "Alls"), ""), "source_note": "Taken from Hagstofa Skóli=Alls, Bekkur=Alls."})
        audit.append({"audit_section": "by_year", "year": year, "grade": "10. bekkur", "item": "total_10th_grade_students_source_all_schools", "value": lookup.get(("Alls", year, "10. bekkur"), ""), "source_note": "Taken from Hagstofa Skóli=Alls, Bekkur=10. bekkur."})

    for grade in sorted(GRADE_LABELS, key=lambda text: int(text.split(".")[0])):
        audit.append({"audit_section": "by_grade", "year": "", "grade": grade, "item": "rows_extracted", "value": sum(1 for row in grade_rows if row["grade"] == grade), "source_note": ""})
    return audit


def main() -> None:
    ensure_dirs()
    metadata = fetch_json(API_URL, METADATA_PATH)
    query = build_all_values_query(metadata)
    QUERY_PATH.write_text(json.dumps(query, ensure_ascii=False, indent=2), encoding="utf-8")
    dataset = fetch_json(API_URL, RAW_RESPONSE_PATH, payload=query)
    all_rows = parse_jsonstat(dataset)
    grade_rows = processed_grade_rows(all_rows)
    grade_10_rows = tenth_grade_rows(grade_rows)

    write_csv(
        INTERIM_LONG_PATH,
        all_rows,
        ["school_code", "year_code", "grade_code", "year", "school_year", "grunnskoli", "normalized_school_name", "grade", "student_count", "source_url", "source_note"],
    )
    write_csv(
        PROCESSED_COUNTS_PATH,
        grade_rows,
        ["year", "school_year", "grunnskoli", "normalized_school_name", "grade", "student_count", "source_url", "source_note"],
    )
    write_csv(
        PROCESSED_10TH_PATH,
        grade_10_rows,
        ["year", "school_year", "grunnskoli", "normalized_school_name", "student_count_10th_grade", "source_url", "source_note"],
    )
    write_csv(AUDIT_PATH, build_audit(all_rows, grade_rows, grade_10_rows), ["audit_section", "year", "grade", "item", "value", "source_note"])
    print(f"Wrote {len(grade_rows)} rows to {PROCESSED_COUNTS_PATH}")
    print(f"Wrote {len(grade_10_rows)} rows to {PROCESSED_10TH_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
