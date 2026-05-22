"""Extract public municipality-level grunnskoli spending data.

Phase 5 is extraction and audit only. The usable municipality-level dataset is
derived from Samband's public 2024 school-size Excel by summing school-level
grunnskoli operating rows to sveitarfelag level.
"""

from __future__ import annotations

import csv
import html
import json
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs" / "tables"

SAMBAND_MAELABORD_URL = "https://www.samband.is/maelabord"
SAMBAND_GRUNNSKOLAR_URL = "https://www.samband.is/grunnskolar"
SAMBAND_SCHOOL_SIZE_EXCEL_URL = (
    "https://samband-islenskra-sveitarfelaga.cdn.prismic.io/samband-islenskra-sveitarfelaga/"
    "aUpSiHNYClf9omZZ_Reksturgrunnsk%C3%B3laeftirst%C3%A6r%C3%B0sk%C3%B3la2024.xlsx"
)
POWERBI_LYKILTOLUR_URL = (
    "https://app.powerbi.com/view?r=eyJrIjoiZjM0YTlmMTYtZjcxYS00NTc1LWE3ODQtMTRlMTYwNzM3YmI5IiwidCI6"
    "IjU0ZmNlODg4LTY0MDYtNGQ3Yy04YjdlLThmYjBhNGY5MjgzMCIsImMiOjh9"
)
POWERBI_ISLANDSKORT_URL = (
    "https://app.powerbi.com/view?r=eyJrIjoiNTQwMTQ5OTEtNzA4Zi00OTZiLWEwMjAtMDgyZjk5NDU1NzNkIiwidCI6"
    "IjU0ZmNlODg4LTY0MDYtNGQ3Yy04YjdlLThmYjBhNGY5MjgzMCIsImMiOjh9"
)
POWERBI_LYKILTOLUR_MODELS_URL = (
    "https://wabi-europe-north-b-api.analysis.windows.net/public/reports/"
    "f34a9f16-f71a-4575-a784-14e160737bb9/modelsAndExploration?preferReadOnlySession=true"
)
POWERBI_ISLANDSKORT_MODELS_URL = (
    "https://wabi-europe-north-b-api.analysis.windows.net/public/reports/"
    "54014991-708f-496b-a020-082f9945573d/modelsAndExploration?preferReadOnlySession=true"
)
HAGSTOFA_CONTEXT_URL = (
    "https://px.hagstofa.is/pxis/pxweb/is/Efnahagur/"
    "Efnahagur__fjaropinber__fjarmal_fraedsla__1_utgjold_fraedsla/THJ05636B.px/"
)
HAGSTOFA_CONTEXT_API_URL = (
    "https://px.hagstofa.is/pxis/api/v1/is/Efnahagur/fjaropinber/fjarmal_fraedsla/1_utgjold_fraedsla/THJ05636B.px"
)

RAW_MAELABORD_HTML = RAW_DIR / "samband_maelabord.html"
RAW_GRUNNSKOLAR_HTML = RAW_DIR / "samband_grunnskolar_phase5.html"
RAW_SCHOOL_SIZE_EXCEL = RAW_DIR / "samband_rekstur_grunnskola_eftir_staerd_skola_2024.xlsx"
RAW_POWERBI_LYKILTOLUR_HTML = RAW_DIR / "samband_powerbi_lykiltolur_grunnskola_2024.html"
RAW_POWERBI_ISLANDSKORT_HTML = RAW_DIR / "samband_powerbi_rekstur_grunnskola_islandskort.html"
RAW_POWERBI_LYKILTOLUR_MODELS = RAW_DIR / "samband_powerbi_lykiltolur_modelsAndExploration.json"
RAW_POWERBI_ISLANDSKORT_MODELS = RAW_DIR / "samband_powerbi_islandskort_modelsAndExploration.json"
RAW_HAGSTOFA_CONTEXT_METADATA = RAW_DIR / "hagstofa_thj05636b_metadata.json"

MUNICIPALITY_CROSSWALK_PATH = PROJECT_ROOT / "data" / "manual" / "municipality_name_crosswalk.csv"
INTERIM_SCHOOL_ROWS_PATH = INTERIM_DIR / "education_spend_school_size_2024_school_rows.csv"
PROCESSED_SPENDING_PATH = PROCESSED_DIR / "education_spend_sveitarfelag.csv"
AUDIT_PATH = OUTPUTS_DIR / "education_spending_extraction_audit.csv"

SOURCE_NOTE = (
    "Samband public Excel 'Rekstur grunnskóla eftir stærð skóla 2024'. "
    "School-level rows aggregated to municipality level; per-student values are derived from "
    "summed municipality amounts divided by summed source student counts. No inhabitant counts "
    "were provided by this source."
)
SOURCE_URL = SAMBAND_SCHOOL_SIZE_EXCEL_URL

METRIC_COLUMNS = {
    "tekjur": "Tekjur",
    "laun_og_launtengd_gjold": "Laun og launtengd gjöld",
    "annar_kostnadur_samtals": "Annar kostnaður samtals",
    "annar_kostnadur_an_innri_leigu_og_skolaakstur": "Annar kostnaður án innri leigu og skólaakstur",
    "innri_husaleiga_eignasjodur": "Innri húsaleiga (Eignasjóður)",
    "skolaakstur": "Skólaakstur",
    "kostnadur_brutto": "Kostnaður brúttó",
    "kostnadur_netto": "Kostnaður nettó",
}


def ensure_dirs() -> None:
    for path in [RAW_DIR, INTERIM_DIR, PROCESSED_DIR, OUTPUTS_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def fetch_url(url: str, path: Path, headers: dict[str, str] | None = None) -> tuple[bool, str]:
    if path.exists() and path.stat().st_size > 0:
        return True, "cached"
    request = Request(url, headers={"User-Agent": "andvari-public-education-pathways/0.5", **(headers or {})})
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        return False, str(exc)
    path.write_bytes(body)
    return True, "downloaded"


def fetch_json(url: str, path: Path, headers: dict[str, str] | None = None) -> tuple[bool, str]:
    ok, note = fetch_url(url, path, headers=headers)
    if not ok:
        return ok, note
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"downloaded response is not JSON: {exc}"
    return True, note


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


def extract_embed_urls(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text)
    if not match:
        return []
    data = json.loads(html.unescape(match.group(1)))
    page = data.get("props", {}).get("pageProps", {}).get("page", {})
    found: list[dict[str, str]] = []

    def walk(value: Any, title: str = "") -> None:
        if isinstance(value, dict):
            current_title = str(value.get("title") or title or "")
            url = value.get("url")
            if isinstance(url, str) and ("powerbi.com" in url or "dwcdn.net" in url or url.endswith((".xlsx", ".xls"))):
                found.append({"title": current_title, "url": url})
            for child in value.values():
                walk(child, current_title)
        elif isinstance(value, list):
            for child in value:
                walk(child, title)

    walk(page)
    return found


def xlsx_rows(path: Path) -> list[dict[str, str]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

    def col_index(cell_ref: str) -> int:
        match = re.match(r"([A-Z]+)", cell_ref)
        if not match:
            return 0
        index = 0
        for char in match.group(1):
            index = index * 26 + ord(char) - 64
        return index - 1

    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                shared.append("".join(text.text or "" for text in item.findall(".//m:t", ns)))
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        raw_rows: list[list[str]] = []
        for row in sheet.findall(".//m:sheetData/m:row", ns):
            values: dict[int, str] = {}
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r", "A1")
                value = ""
                v = cell.find("m:v", ns)
                if v is not None:
                    value = v.text or ""
                    if cell.attrib.get("t") == "s":
                        value = shared[int(value)]
                values[col_index(ref)] = value.strip()
            if values:
                raw_rows.append([values.get(i, "") for i in range(max(values) + 1)])
    if not raw_rows:
        return []
    header = raw_rows[0]
    rows = []
    for raw in raw_rows[1:]:
        row = {header[i]: raw[i] if i < len(raw) else "" for i in range(len(header))}
        if row.get("Ár", "").isdigit() and row.get("Sveitarfélag", "").strip() and row.get("Skóli", "").strip():
            rows.append(row)
    return rows


def parse_number(value: str) -> float | None:
    value = str(value).strip()
    if value == "":
        return None
    return float(value.replace(",", "."))


def source_municipality(value: str) -> str:
    return re.sub(r"^\d{4}\s+", "", value).strip()


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


def school_interim_rows(rows: list[dict[str, str]], exact: dict[str, dict[str, str]], normalized: dict[str, dict[str, str]]) -> list[dict[str, object]]:
    output = []
    for row in rows:
        sveitarfelag = source_municipality(row["Sveitarfélag"])
        harmonized, status, note = harmonize_municipality(sveitarfelag, exact, normalized)
        out = {
            "year": int(row["Ár"]),
            "svnr": row["Svnr"],
            "sveitarfelag_source": sveitarfelag,
            "sveitarfelag_harmonized": harmonized,
            "sveitarfelag_harmonization_status": status,
            "sveitarfelag_harmonization_note": note,
            "grunnskoli": row["Skóli"].strip(),
            "grade_span": row["Bekkjardeild"],
            "school_size_group": row["Stærð skóla"],
            "student_count": int(float(row["Nemendur"])),
        }
        for metric, column in METRIC_COLUMNS.items():
            value = parse_number(row.get(column, ""))
            out[metric] = "" if value is None else int(round(value))
        output.append(out)
    output.sort(key=lambda r: (int(r["year"]), str(r["sveitarfelag_source"]), str(r["grunnskoli"])))
    return output


def municipality_spending_rows(school_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[int, str, str], dict[str, object]] = {}
    for row in school_rows:
        key = (int(row["year"]), str(row["sveitarfelag_source"]), str(row["sveitarfelag_harmonized"]))
        rec = grouped.setdefault(
            key,
            {
                "year": row["year"],
                "sveitarfelag_source": row["sveitarfelag_source"],
                "sveitarfelag_harmonized": row["sveitarfelag_harmonized"],
                "harmonization_statuses": set(),
                "harmonization_notes": set(),
                "student_count": 0,
                "metrics": defaultdict(int),
            },
        )
        rec["harmonization_statuses"].add(row["sveitarfelag_harmonization_status"])  # type: ignore[index]
        note = str(row["sveitarfelag_harmonization_note"])
        if note:
            rec["harmonization_notes"].add(note)  # type: ignore[index]
        rec["student_count"] = int(rec["student_count"]) + int(row["student_count"])
        for metric in METRIC_COLUMNS:
            value = row.get(metric, "")
            if value != "":
                rec["metrics"][metric] += int(value)  # type: ignore[index]

    output = []
    for (year, sveitarfelag_source, sveitarfelag_harmonized), rec in sorted(grouped.items()):
        student_count = int(rec["student_count"])
        statuses = sorted(rec["harmonization_statuses"])  # type: ignore[arg-type]
        status = "harmonized" if "harmonized" in statuses else statuses[0] if statuses else ""
        note = " | ".join(sorted(rec["harmonization_notes"]))  # type: ignore[arg-type]
        for metric in METRIC_COLUMNS:
            amount = int(rec["metrics"].get(metric, 0))  # type: ignore[union-attr]
            output.append(
                {
                    "year": year,
                    "sveitarfelag_source": sveitarfelag_source,
                    "sveitarfelag_harmonized": sveitarfelag_harmonized,
                    "sveitarfelag_harmonization_status": status,
                    "sveitarfelag_harmonization_note": note,
                    "spending_metric": metric,
                    "amount_isk": amount,
                    "student_count": student_count,
                    "spend_per_student": round(amount / student_count, 2) if student_count else "",
                    "spend_per_inhabitant": "",
                    "source_url": SOURCE_URL,
                    "source_note": SOURCE_NOTE,
                }
            )
    return output


def try_hagstofa_context_metadata(audit: list[dict[str, object]]) -> None:
    ok, note = fetch_json(HAGSTOFA_CONTEXT_API_URL, RAW_HAGSTOFA_CONTEXT_METADATA)
    audit.append({"audit_section": "source_attempt", "item": "hagstofa_national_context_metadata", "value": note, "source_note": HAGSTOFA_CONTEXT_API_URL})
    audit.append({"audit_section": "source_attempt", "item": "hagstofa_context_role", "value": "national_context_only_not_municipality_level", "source_note": HAGSTOFA_CONTEXT_URL})
    if ok:
        audit.append({"audit_section": "raw_cache", "item": "hagstofa_context_metadata", "value": str(RAW_HAGSTOFA_CONTEXT_METADATA.relative_to(PROJECT_ROOT)), "source_note": ""})


def build_audit(school_rows: list[dict[str, object]], spending_rows: list[dict[str, object]], source_attempts: list[dict[str, object]]) -> list[dict[str, object]]:
    years = sorted({str(row["year"]) for row in spending_rows})
    municipalities = sorted({str(row["sveitarfelag_source"]) for row in spending_rows})
    harmonized = sorted({str(row["sveitarfelag_harmonized"]) for row in spending_rows})
    metrics = sorted({str(row["spending_metric"]) for row in spending_rows})
    needing_review = sorted({str(row["sveitarfelag_source"]) for row in spending_rows if row["sveitarfelag_harmonization_status"] == "needs_review"})
    audit = [
        {"audit_section": "summary", "item": "usable_municipality_level_dataset_found", "value": "true", "source_note": "Derived by aggregating public school-level Samband Excel rows to municipality level."},
        {"audit_section": "summary", "item": "school_rows_extracted", "value": len(school_rows), "source_note": ""},
        {"audit_section": "summary", "item": "municipality_spending_rows", "value": len(spending_rows), "source_note": ""},
        {"audit_section": "summary", "item": "years_found", "value": "; ".join(years), "source_note": ""},
        {"audit_section": "summary", "item": "municipalities_found", "value": len(municipalities), "source_note": "; ".join(municipalities)},
        {"audit_section": "summary", "item": "harmonized_municipalities_found", "value": len(harmonized), "source_note": "; ".join(harmonized)},
        {"audit_section": "summary", "item": "metrics_found", "value": "; ".join(metrics), "source_note": ""},
        {"audit_section": "summary", "item": "amount_semantics", "value": "amount_isk contains total annual ISK by municipality and metric; spend_per_student is safely derived from summed source student counts.", "source_note": ""},
        {"audit_section": "summary", "item": "spend_per_inhabitant", "value": "not_available", "source_note": "No inhabitant counts in the source; left blank."},
        {"audit_section": "summary", "item": "value_status", "value": "public_excel_dashboard_derived_2024_values", "source_note": "Values are from Samband's public 2024 school-size Excel linked from the grunnskólar page."},
        {"audit_section": "summary", "item": "municipality_names_needing_harmonization_review", "value": len(needing_review), "source_note": "; ".join(needing_review)},
        {"audit_section": "limitation", "item": "scope", "value": "Extraction only; no outcome joins, rankings, dashboards, or analysis were created.", "source_note": ""},
        {"audit_section": "limitation", "item": "school_level_source_aggregated", "value": "Municipality totals are computed from school rows; municipalities with schools outside this file would need source review.", "source_note": ""},
    ]
    return source_attempts + audit


def main() -> None:
    ensure_dirs()
    source_attempts: list[dict[str, object]] = []
    for url, path, label in [
        (SAMBAND_MAELABORD_URL, RAW_MAELABORD_HTML, "samband_maelabord_page"),
        (SAMBAND_GRUNNSKOLAR_URL, RAW_GRUNNSKOLAR_HTML, "samband_grunnskolar_page"),
        (SAMBAND_SCHOOL_SIZE_EXCEL_URL, RAW_SCHOOL_SIZE_EXCEL, "samband_school_size_excel"),
        (POWERBI_LYKILTOLUR_URL, RAW_POWERBI_LYKILTOLUR_HTML, "powerbi_lykiltolur_embed"),
        (POWERBI_ISLANDSKORT_URL, RAW_POWERBI_ISLANDSKORT_HTML, "powerbi_islandskort_embed"),
    ]:
        ok, note = fetch_url(url, path)
        source_attempts.append({"audit_section": "source_attempt", "item": label, "value": note if ok else f"failed: {note}", "source_note": url})
        if ok:
            source_attempts.append({"audit_section": "raw_cache", "item": label, "value": str(path.relative_to(PROJECT_ROOT)), "source_note": ""})

    for path, label in [(RAW_MAELABORD_HTML, "samband_maelabord_embeds"), (RAW_GRUNNSKOLAR_HTML, "samband_grunnskolar_embeds")]:
        embeds = extract_embed_urls(path)
        source_attempts.append({"audit_section": "source_attempt", "item": label, "value": json.dumps(embeds, ensure_ascii=False), "source_note": str(path.relative_to(PROJECT_ROOT))})

    for url, path, key, label in [
        (POWERBI_LYKILTOLUR_MODELS_URL, RAW_POWERBI_LYKILTOLUR_MODELS, "f34a9f16-f71a-4575-a784-14e160737bb9", "powerbi_lykiltolur_modelsAndExploration"),
        (POWERBI_ISLANDSKORT_MODELS_URL, RAW_POWERBI_ISLANDSKORT_MODELS, "54014991-708f-496b-a020-082f9945573d", "powerbi_islandskort_modelsAndExploration"),
    ]:
        ok, note = fetch_json(
            url,
            path,
            headers={
                "Accept": "application/json",
                "ActivityId": "00000000-0000-0000-0000-000000000101",
                "RequestId": "00000000-0000-0000-0000-000000000102",
                "X-PowerBI-ResourceKey": key,
            },
        )
        source_attempts.append({"audit_section": "source_attempt", "item": label, "value": note if ok else f"failed: {note}", "source_note": url})
        if ok:
            source_attempts.append({"audit_section": "raw_cache", "item": label, "value": str(path.relative_to(PROJECT_ROOT)), "source_note": ""})

    try_hagstofa_context_metadata(source_attempts)

    exact_municipalities, normalized_municipalities = load_municipality_crosswalk()
    raw_school_rows = xlsx_rows(RAW_SCHOOL_SIZE_EXCEL)
    school_rows = school_interim_rows(raw_school_rows, exact_municipalities, normalized_municipalities)
    spending_rows = municipality_spending_rows(school_rows)

    write_csv(
        INTERIM_SCHOOL_ROWS_PATH,
        school_rows,
        [
            "year",
            "svnr",
            "sveitarfelag_source",
            "sveitarfelag_harmonized",
            "sveitarfelag_harmonization_status",
            "sveitarfelag_harmonization_note",
            "grunnskoli",
            "grade_span",
            "school_size_group",
            "student_count",
            *METRIC_COLUMNS.keys(),
        ],
    )
    write_csv(
        PROCESSED_SPENDING_PATH,
        spending_rows,
        [
            "year",
            "sveitarfelag_source",
            "sveitarfelag_harmonized",
            "sveitarfelag_harmonization_status",
            "sveitarfelag_harmonization_note",
            "spending_metric",
            "amount_isk",
            "student_count",
            "spend_per_student",
            "spend_per_inhabitant",
            "source_url",
            "source_note",
        ],
    )
    write_csv(AUDIT_PATH, build_audit(school_rows, spending_rows, source_attempts), ["audit_section", "item", "value", "source_note"])
    print(f"Wrote {len(school_rows)} school-level interim rows to {INTERIM_SCHOOL_ROWS_PATH}")
    print(f"Wrote {len(spending_rows)} municipality-level spending rows to {PROCESSED_SPENDING_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
