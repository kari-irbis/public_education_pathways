"""Extract municipality population denominators from Hagstofa MAN02005."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://px.hagstofa.is/pxis/pxweb/is/Ibuar/Ibuar__mannfjoldi__2_byggdir__sveitarfelog/MAN02005.px"
API_URL = "https://px.hagstofa.is/pxis/api/v1/is/Ibuar/mannfjoldi/2_byggdir/sveitarfelog/MAN02005.px"

RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TABLES_DIR = PROJECT_ROOT / "outputs" / "tables"

METADATA_PATH = RAW_DIR / "hagstofa_man02005_metadata.json"
QUERY_PATH = RAW_DIR / "hagstofa_man02005_query.json"
RAW_RESPONSE_PATH = RAW_DIR / "hagstofa_man02005_population_denominators_jsonstat2.json"
INTERIM_LONG_PATH = INTERIM_DIR / "hagstofa_man02005_population_denominators_long.csv"
MUNICIPALITY_CROSSWALK_PATH = PROJECT_ROOT / "data" / "manual" / "municipality_name_crosswalk.csv"
PROCESSED_PATH = PROCESSED_DIR / "municipality_population_denominators.csv"
AUDIT_PATH = TABLES_DIR / "population_denominators_audit.csv"

TOTAL_AGE_CODE = "-1"
SCHOOL_AGE_CODES = {str(age) for age in range(6, 16)}
SOURCE_NOTE = (
    "Hagstofa table MAN02005. Kyn=Alls. population_total uses Aldur=Alls; "
    "population_age_6_15 sums ages 6 through 15 as a school-age population proxy."
)
AGE_6_15_NOTE = "Population ages 6-15; proxy denominator only, not exact grunnskóli enrollment."


def ensure_dirs() -> None:
    for path in [RAW_DIR, INTERIM_DIR, PROCESSED_DIR, TABLES_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def fetch_json(url: str, path: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if path.exists() and path.stat().st_size > 0:
        return json.loads(path.read_text(encoding="utf-8"))
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"User-Agent": "andvari-public-education-pathways/0.6"}
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


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def normalized_key(value: str) -> str:
    text = value.casefold().strip().replace("–", "-").replace("—", "-").replace("‐", "-")
    normalized = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_municipality_crosswalk() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    exact: dict[str, dict[str, str]] = {}
    normalized: dict[str, dict[str, str]] = {}
    for row in read_csv_rows(MUNICIPALITY_CROSSWALK_PATH):
        source = row["sveitarfelag_source"].strip()
        exact[source] = row
        normalized.setdefault(normalized_key(source), row)
    return exact, normalized


def harmonize_municipality(source: str, exact: dict[str, dict[str, str]], normalized: dict[str, dict[str, str]]) -> tuple[str, str, str]:
    if source in exact:
        row = exact[source]
        return row["sveitarfelag_harmonized"], row["harmonization_status"], row.get("notes", "")
    key = normalized_key(source)
    if key in normalized:
        row = normalized[key]
        note = row.get("notes", "")
        if row["sveitarfelag_source"] != source:
            note = f"Matched by punctuation/spacing-insensitive municipality lookup. {note}".strip()
        return row["sveitarfelag_harmonized"], row["harmonization_status"], note
    return source, "unchanged", "No project-local harmonization needed; carried forward as current/source name."


def metadata_values(metadata: dict[str, Any], code: str) -> list[str]:
    for variable in metadata["variables"]:
        if variable["code"] == code:
            return variable["values"]
    raise RuntimeError(f"metadata variable not found: {code}")


def build_query(metadata: dict[str, Any]) -> dict[str, Any]:
    age_values = [TOTAL_AGE_CODE] + [str(age) for age in range(6, 16)]
    query = []
    for variable in metadata["variables"]:
        code = variable["code"]
        if code == "Aldur":
            values = age_values
        elif code == "Kyn":
            values = ["0"]
        else:
            values = variable["values"]
        query.append({"code": code, "selection": {"filter": "item", "values": values}})
    return {"query": query, "response": {"format": "JSON-stat2"}}


def ordered_labels(dataset: dict[str, Any], dimension_id: str) -> list[tuple[str, str]]:
    category = dataset["dimension"][dimension_id]["category"]
    labels = category["label"]
    index = category["index"]
    return [(code, labels[code]) for code, _ in sorted(index.items(), key=lambda item: item[1])]


def parse_jsonstat(dataset: dict[str, Any]) -> list[dict[str, object]]:
    ids = dataset["id"]
    if ids != ["Sveitarfélag", "Aldur", "Ár", "Kyn"]:
        raise RuntimeError(f"Unexpected MAN02005 dimension order: {ids}")
    municipality_labels = ordered_labels(dataset, "Sveitarfélag")
    age_labels = ordered_labels(dataset, "Aldur")
    year_labels = ordered_labels(dataset, "Ár")
    sex_labels = ordered_labels(dataset, "Kyn")
    values = dataset["value"]
    expected = len(municipality_labels) * len(age_labels) * len(year_labels) * len(sex_labels)
    if len(values) != expected:
        raise RuntimeError(f"Unexpected JSON-stat size: values={len(values)}, expected={expected}")
    rows = []
    position = 0
    for municipality_code, municipality in municipality_labels:
        for age_code, age in age_labels:
            for year_code, year in year_labels:
                for sex_code, sex in sex_labels:
                    value = values[position]
                    position += 1
                    rows.append(
                        {
                            "municipality_code": municipality_code,
                            "age_code": age_code,
                            "year_code": year_code,
                            "sex_code": sex_code,
                            "year": int(year),
                            "sveitarfelag_source": municipality,
                            "age": age,
                            "sex": sex,
                            "population": "" if value is None else int(value),
                        }
                    )
    return rows


def processed_rows(long_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    exact, normalized = load_municipality_crosswalk()
    grouped: dict[tuple[int, str], dict[str, object]] = defaultdict(lambda: {"total": None, "age_6_15": 0})
    for row in long_rows:
        municipality = str(row["sveitarfelag_source"])
        if municipality == "Alls":
            continue
        key = (int(row["year"]), municipality)
        value = row["population"]
        if value == "":
            continue
        if row["age_code"] == TOTAL_AGE_CODE:
            grouped[key]["total"] = int(value)
        elif str(row["age_code"]) in SCHOOL_AGE_CODES:
            grouped[key]["age_6_15"] = int(grouped[key]["age_6_15"]) + int(value)

    rows = []
    for (year, municipality), values in sorted(grouped.items()):
        harmonized, status, note = harmonize_municipality(municipality, exact, normalized)
        if values["total"] is None:
            continue
        rows.append(
            {
                "year": year,
                "sveitarfelag_source": municipality,
                "sveitarfelag_harmonized": harmonized,
                "sveitarfelag_harmonization_status": status,
                "sveitarfelag_harmonization_note": note,
                "population_total": int(values["total"]),
                "population_age_6_15": int(values["age_6_15"]),
                "population_age_6_15_note": AGE_6_15_NOTE,
                "source_url": SOURCE_URL,
                "source_note": SOURCE_NOTE,
            }
        )
    return rows


def build_audit(rows: list[dict[str, object]], long_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    years = sorted({str(row["year"]) for row in rows})
    municipalities = sorted({str(row["sveitarfelag_source"]) for row in rows})
    harmonized = sorted({str(row["sveitarfelag_harmonized"]) for row in rows})
    needs_review = sorted({str(row["sveitarfelag_source"]) for row in rows if row["sveitarfelag_harmonization_status"] == "needs_review"})
    return [
        {"audit_section": "source", "item": "source_url", "value": SOURCE_URL, "source_note": ""},
        {"audit_section": "source", "item": "api_url", "value": API_URL, "source_note": ""},
        {"audit_section": "source", "item": "extracted_at_utc", "value": datetime.now(timezone.utc).isoformat(), "source_note": ""},
        {"audit_section": "source", "item": "raw_metadata_file", "value": str(METADATA_PATH.relative_to(PROJECT_ROOT)), "source_note": ""},
        {"audit_section": "source", "item": "raw_response_file", "value": str(RAW_RESPONSE_PATH.relative_to(PROJECT_ROOT)), "source_note": ""},
        {"audit_section": "summary", "item": "long_rows_extracted", "value": len(long_rows), "source_note": ""},
        {"audit_section": "summary", "item": "processed_denominator_rows", "value": len(rows), "source_note": ""},
        {"audit_section": "summary", "item": "years_extracted", "value": "; ".join(years), "source_note": ""},
        {"audit_section": "summary", "item": "source_municipalities", "value": len(municipalities), "source_note": "; ".join(municipalities)},
        {"audit_section": "summary", "item": "harmonized_municipalities", "value": len(harmonized), "source_note": "; ".join(harmonized)},
        {"audit_section": "summary", "item": "municipalities_needing_harmonization_review", "value": len(needs_review), "source_note": "; ".join(needs_review)},
        {"audit_section": "summary", "item": "total_population_coverage", "value": sum(1 for row in rows if int(row["population_total"]) >= 0), "source_note": "Rows with non-missing Aldur=Alls population."},
        {"audit_section": "summary", "item": "age_6_15_population_coverage", "value": sum(1 for row in rows if int(row["population_age_6_15"]) >= 0), "source_note": "Rows with summed ages 6-15 population."},
        {"audit_section": "definition", "item": "population_total", "value": "Aldur=Alls, Kyn=Alls.", "source_note": SOURCE_NOTE},
        {"audit_section": "definition", "item": "population_age_6_15", "value": AGE_6_15_NOTE, "source_note": SOURCE_NOTE},
    ]


def main() -> None:
    ensure_dirs()
    metadata = fetch_json(API_URL, METADATA_PATH)
    query = build_query(metadata)
    QUERY_PATH.write_text(json.dumps(query, ensure_ascii=False, indent=2), encoding="utf-8")
    dataset = fetch_json(API_URL, RAW_RESPONSE_PATH, payload=query)
    long_rows = parse_jsonstat(dataset)
    rows = processed_rows(long_rows)
    write_csv(INTERIM_LONG_PATH, long_rows, ["municipality_code", "age_code", "year_code", "sex_code", "year", "sveitarfelag_source", "age", "sex", "population"])
    write_csv(
        PROCESSED_PATH,
        rows,
        [
            "year",
            "sveitarfelag_source",
            "sveitarfelag_harmonized",
            "sveitarfelag_harmonization_status",
            "sveitarfelag_harmonization_note",
            "population_total",
            "population_age_6_15",
            "population_age_6_15_note",
            "source_url",
            "source_note",
        ],
    )
    write_csv(AUDIT_PATH, build_audit(rows, long_rows), ["audit_section", "item", "value", "source_note"])
    print(f"Wrote {len(rows)} population denominator rows to {PROCESSED_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
