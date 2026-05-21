"""Build a pragmatic grunnskoli -> sveitarfelag crosswalk."""

from __future__ import annotations

import csv
import html.parser
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PAGE_URL = "https://www.samband.is/grunnskolar"
RAW_PAGE_PATH = PROJECT_ROOT / "data" / "raw" / "samband_grunnskolar_page.html"
RAW_EXCEL_PATH = PROJECT_ROOT / "data" / "raw" / "samband_netfangalisti_grunnskola_2026-05-11.xlsx"
CROSSWALK_PATH = PROJECT_ROOT / "data" / "processed" / "grunnskoli_sveitarfelag_crosswalk.csv"
MANUAL_PATH = PROJECT_ROOT / "data" / "manual" / "manual_school_crosswalk.csv"
REVIEW_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_review.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_audit.csv"

DATASETS = {
    "in_althingi_graduates": PROJECT_ROOT / "data" / "processed" / "althingi_graduates_by_grunnskoli.csv",
    "in_althingi_framhaldsskoli_grunnskoli": PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_framhaldsskoli_grunnskoli.csv",
    "in_althingi_grunnskoli_grades": PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_grunnskoli.csv",
    "in_hagstofa_student_counts": PROJECT_ROOT / "data" / "processed" / "grunnskoli_student_counts.csv",
    "in_hagstofa_10th_grade_counts": PROJECT_ROOT / "data" / "processed" / "grunnskoli_10th_grade_counts.csv",
}
PRESENCE_COLUMNS = list(DATASETS)
MANUAL_COLUMNS = ["source_school_name", "normalized_school_name", "sveitarfelag", "canonical_school_name", "manual_status", "notes"]
EXCLUDED_NAMES = {"heild", "alls", "faerri en 5 nemendur", "faerri en fimm nemendur"}
FUZZY_ACCEPT_THRESHOLD = 0.96
FUZZY_REVIEW_THRESHOLD = 0.78


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def ensure_dirs() -> None:
    for path in [RAW_PAGE_PATH.parent, CROSSWALK_PATH.parent, MANUAL_PATH.parent, REVIEW_PATH.parent]:
        path.mkdir(parents=True, exist_ok=True)


def fetch_url(url: str, path: Path) -> bytes:
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    request = Request(url, headers={"User-Agent": "andvari-public-education-pathways/0.4"})
    with urlopen(request, timeout=60) as response:
        body = response.read()
    path.write_bytes(body)
    return body


def discover_excel_url(page_html: str) -> str | None:
    parser = LinkParser()
    parser.feed(page_html)
    candidates = []
    for href in parser.links:
        url = urljoin(SOURCE_PAGE_URL, href)
        lower = url.lower()
        if lower.endswith(".xlsx") and "grunnsk" in lower and "netfang" in lower:
            candidates.append(url)
    return candidates[0] if candidates else None


def ensure_external_mapping_source() -> tuple[str, str]:
    page = fetch_url(SOURCE_PAGE_URL, RAW_PAGE_PATH).decode("utf-8", errors="replace")
    excel_url = discover_excel_url(page)
    if not excel_url:
        return "Samband grunnskólar page cached; Excel URL not found", ""
    fetch_url(excel_url, RAW_EXCEL_PATH)
    return f"Samband public grunnskólar Excel: {excel_url}", excel_url


def normalize_school_name(name: str) -> str:
    replacements = {"æ": "ae", "ð": "d", "þ": "th", "ö": "o"}
    lowered = name.casefold().strip()
    for source, target in replacements.items():
        lowered = lowered.replace(source, target)
    normalized = unicodedata.normalize("NFKD", lowered)
    text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    text = re.sub(r"\b(ib|ehf|ses)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def alias_normalized_name(normalized: str) -> str:
    alias = normalized
    alias = re.sub(r"^grunnskolinn i ", "grunnskoli ", alias)
    alias = re.sub(r"^grunnskolinn a ", "grunnskoli ", alias)
    alias = re.sub(r"^grunnskolinn ", "grunnskoli ", alias)
    alias = re.sub(r"^barnaskolinn a ", "barnaskoli ", alias)
    return re.sub(r"\s+", " ", alias).strip()


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


def load_source_universe() -> dict[str, dict[str, object]]:
    universe: dict[str, dict[str, object]] = {}
    for presence_col, path in DATASETS.items():
        for row in read_csv_rows(path):
            name = (row.get("grunnskoli") or "").strip()
            if not name:
                continue
            rec = universe.setdefault(
                name,
                {"source_school_name": name, "normalized_school_name": normalize_school_name(name), **{col: "false" for col in PRESENCE_COLUMNS}},
            )
            rec[presence_col] = "true"
    return universe


def xlsx_rows(path: Path) -> list[list[str]]:
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
        rows: list[list[str]] = []
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
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(text.text or "" for text in cell.findall(".//m:t", ns))
                values[col_index(ref)] = value.strip()
            if values:
                rows.append([values.get(i, "") for i in range(max(values) + 1)])
    return rows


def load_external_mapping() -> tuple[list[dict[str, str]], dict[str, list[dict[str, str]]]]:
    if not RAW_EXCEL_PATH.exists():
        return [], {}
    rows = xlsx_rows(RAW_EXCEL_PATH)
    if not rows:
        return [], {}
    header = rows[0]
    school_idx = header.index("Nafn skóla")
    municipality_idx = header.index("Sveitarfélag")
    mapping_rows: list[dict[str, str]] = []
    by_norm: dict[str, list[dict[str, str]]] = defaultdict(list)
    for raw in rows[1:]:
        if len(raw) <= max(school_idx, municipality_idx):
            continue
        school = raw[school_idx].strip()
        municipality = raw[municipality_idx].strip()
        if not school or not municipality:
            continue
        normalized = normalize_school_name(school)
        rec = {
            "canonical_school_name": school,
            "sveitarfelag": municipality,
            "normalized_school_name": normalized,
            "alias_normalized_school_name": alias_normalized_name(normalized),
        }
        mapping_rows.append(rec)
        by_norm[rec["normalized_school_name"]].append(rec)
        by_norm[rec["alias_normalized_school_name"]].append(rec)
    return mapping_rows, by_norm


def load_manual_crosswalk() -> dict[str, dict[str, str]]:
    if not MANUAL_PATH.exists():
        return {}
    manual = {}
    for row in read_csv_rows(MANUAL_PATH):
        name = row.get("source_school_name", "").strip()
        if not name:
            continue
        status = row.get("manual_status", "").strip().lower()
        if status in {"matched", "matched_manual", "ok"} and row.get("sveitarfelag", "").strip():
            manual[name] = row
    return manual


def unique_mapping(records: list[dict[str, str]]) -> dict[str, str] | None:
    if not records:
        return None
    pairs = {(row["canonical_school_name"], row["sveitarfelag"]) for row in records}
    if len(pairs) != 1:
        return None
    return records[0]


def fuzzy_candidates(normalized: str, mapping_rows: list[dict[str, str]]) -> list[dict[str, object]]:
    candidates = []
    source_alias = alias_normalized_name(normalized)
    for row in mapping_rows:
        score = max(
            SequenceMatcher(None, normalized, row["normalized_school_name"]).ratio(),
            SequenceMatcher(None, source_alias, row["alias_normalized_school_name"]).ratio(),
        )
        if score >= FUZZY_REVIEW_THRESHOLD:
            candidates.append({**row, "score": round(score, 3)})
    candidates.sort(key=lambda row: (-float(row["score"]), str(row["canonical_school_name"])))
    return candidates[:5]


def is_excluded(name: str, normalized: str) -> bool:
    return normalized in EXCLUDED_NAMES or name in {"Heild", "Alls", "Færri en 5 nemendur", "Færri en fimm nemendur"}


def review_row(base: dict[str, object], candidates: list[dict[str, object]]) -> dict[str, object]:
    row = {
        "source_school_name": base["source_school_name"],
        "normalized_school_name": base["normalized_school_name"],
        "possible_match_1": "",
        "possible_match_1_sveitarfelag": "",
        "possible_match_1_score": "",
        "possible_match_2": "",
        "possible_match_2_sveitarfelag": "",
        "possible_match_2_score": "",
        **{col: base[col] for col in PRESENCE_COLUMNS},
        "notes": base["notes"],
    }
    for idx, candidate in enumerate(candidates[:2], start=1):
        row[f"possible_match_{idx}"] = candidate["canonical_school_name"]
        row[f"possible_match_{idx}_sveitarfelag"] = candidate["sveitarfelag"]
        row[f"possible_match_{idx}_score"] = f"{float(candidate['score']):.3f}"
    return row


def build_manual_file_rows(crosswalk: list[dict[str, object]]) -> list[dict[str, object]]:
    existing = {row.get("source_school_name", ""): row for row in read_csv_rows(MANUAL_PATH)} if MANUAL_PATH.exists() else {}
    rows = []
    for row in crosswalk:
        if row["match_status"] != "needs_manual_review":
            continue
        name = str(row["source_school_name"])
        existing_row = existing.get(name, {})
        rows.append(
            {
                "source_school_name": name,
                "normalized_school_name": row["normalized_school_name"],
                "sveitarfelag": existing_row.get("sveitarfelag", ""),
                "canonical_school_name": existing_row.get("canonical_school_name", ""),
                "manual_status": existing_row.get("manual_status", ""),
                "notes": existing_row.get("notes", ""),
            }
        )
    rows.sort(key=lambda row: str(row["normalized_school_name"]))
    return rows


def build_crosswalk() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], str]:
    source_description, _ = ensure_external_mapping_source()
    mapping_rows, mapping_by_norm = load_external_mapping()
    manual = load_manual_crosswalk()
    universe = load_source_universe()
    crosswalk: list[dict[str, object]] = []
    review: list[dict[str, object]] = []

    for name, rec in sorted(universe.items(), key=lambda item: normalize_school_name(item[0])):
        normalized = str(rec["normalized_school_name"])
        base = {**rec, "canonical_school_name": "", "sveitarfelag": "", "match_status": "", "match_confidence": "", "match_source": "", "notes": ""}
        candidates = fuzzy_candidates(normalized, mapping_rows) if mapping_rows else []
        if is_excluded(name, normalized):
            base.update({"match_status": "excluded_group_or_aggregate", "match_confidence": "1.000", "match_source": "rule", "notes": "Grouped or aggregate source row; no sveitarfelag assigned."})
        elif name in manual:
            row = manual[name]
            base.update({"canonical_school_name": row.get("canonical_school_name", ""), "sveitarfelag": row.get("sveitarfelag", ""), "match_status": "matched_manual", "match_confidence": "1.000", "match_source": "data/manual/manual_school_crosswalk.csv", "notes": row.get("notes", "")})
        else:
            exact = unique_mapping(mapping_by_norm.get(normalized, []) + mapping_by_norm.get(alias_normalized_name(normalized), []))
            if exact:
                base.update({"canonical_school_name": exact["canonical_school_name"], "sveitarfelag": exact["sveitarfelag"], "match_status": "matched_exact", "match_confidence": "1.000", "match_source": "samband_grunnskolar_excel", "notes": "Exact normalized match."})
            elif candidates and float(candidates[0]["score"]) >= FUZZY_ACCEPT_THRESHOLD and (len(candidates) == 1 or float(candidates[0]["score"]) - float(candidates[1]["score"]) >= 0.04):
                best = candidates[0]
                base.update({"canonical_school_name": best["canonical_school_name"], "sveitarfelag": best["sveitarfelag"], "match_status": "matched_fuzzy_high_confidence", "match_confidence": f"{float(best['score']):.3f}", "match_source": "samband_grunnskolar_excel", "notes": "High-confidence fuzzy normalized match; review optional."})
            else:
                base.update({"match_status": "needs_manual_review", "match_confidence": f"{float(candidates[0]['score']):.3f}" if candidates else "", "match_source": "", "notes": "No exact or high-confidence fuzzy match accepted."})
                review.append(review_row(base, candidates))
        crosswalk.append(base)
    return crosswalk, review, build_manual_file_rows(crosswalk), source_description


def build_audit(crosswalk: list[dict[str, object]], review: list[dict[str, object]], source_description: str) -> list[dict[str, object]]:
    status_counts = Counter(str(row["match_status"]) for row in crosswalk)
    audit = [
        {"section": "source", "dataset": "", "item": "external_mapping_source_used", "value": source_description},
        {"section": "source", "dataset": "", "item": "external_mapping_page_cache", "value": str(RAW_PAGE_PATH.relative_to(PROJECT_ROOT))},
        {"section": "source", "dataset": "", "item": "external_mapping_excel_cache", "value": str(RAW_EXCEL_PATH.relative_to(PROJECT_ROOT)) if RAW_EXCEL_PATH.exists() else ""},
        {"section": "summary", "dataset": "", "item": "created_at_utc", "value": datetime.now(timezone.utc).isoformat()},
        {"section": "summary", "dataset": "", "item": "total_unique_source_school_names", "value": len(crosswalk)},
        {"section": "summary", "dataset": "", "item": "excluded_aggregate_group_rows", "value": status_counts["excluded_group_or_aggregate"]},
        {"section": "summary", "dataset": "", "item": "exact_matches", "value": status_counts["matched_exact"]},
        {"section": "summary", "dataset": "", "item": "fuzzy_high_confidence_matches", "value": status_counts["matched_fuzzy_high_confidence"]},
        {"section": "summary", "dataset": "", "item": "manual_matches_applied", "value": status_counts["matched_manual"]},
        {"section": "summary", "dataset": "", "item": "rows_needing_manual_review", "value": status_counts["needs_manual_review"]},
    ]
    matched_statuses = {"matched_exact", "matched_fuzzy_high_confidence", "matched_manual"}
    for col in PRESENCE_COLUMNS:
        rows = [row for row in crosswalk if row[col] == "true" and row["match_status"] != "excluded_group_or_aggregate"]
        matched = sum(1 for row in rows if row["match_status"] in matched_statuses)
        rate = matched / len(rows) if rows else 0
        audit.append({"section": "match_rate_by_source", "dataset": col, "item": "matched_non_excluded", "value": f"{matched}/{len(rows)} ({rate:.1%})"})
    patterns = Counter()
    for row in review:
        tokens = str(row["normalized_school_name"]).split()
        pattern = " ".join(tokens[:2]) if tokens else ""
        patterns[pattern] += 1
    audit.append({"section": "manual_review", "dataset": "", "item": "top_common_unmatched_patterns", "value": "; ".join(f"{k}: {v}" for k, v in patterns.most_common(10))})
    audit.append({"section": "manual_review", "dataset": "", "item": "notes", "value": "Weak fuzzy matches are not accepted automatically; fill data/manual/manual_school_crosswalk.csv and rerun."})
    return audit


def main() -> None:
    ensure_dirs()
    crosswalk, review, manual_rows, source_description = build_crosswalk()
    crosswalk_fields = ["source_school_name", "normalized_school_name", "canonical_school_name", "sveitarfelag", *PRESENCE_COLUMNS, "match_status", "match_confidence", "match_source", "notes"]
    review_fields = ["source_school_name", "normalized_school_name", "possible_match_1", "possible_match_1_sveitarfelag", "possible_match_1_score", "possible_match_2", "possible_match_2_sveitarfelag", "possible_match_2_score", *PRESENCE_COLUMNS, "notes"]
    write_csv(CROSSWALK_PATH, crosswalk, crosswalk_fields)
    write_csv(REVIEW_PATH, review, review_fields)
    write_csv(MANUAL_PATH, manual_rows, MANUAL_COLUMNS)
    write_csv(AUDIT_PATH, build_audit(crosswalk, review, source_description), ["section", "dataset", "item", "value"])
    print(f"Wrote {len(crosswalk)} rows to {CROSSWALK_PATH}")
    print(f"Wrote {len(review)} rows to {REVIEW_PATH}")
    print(f"Wrote {len(manual_rows)} rows to {MANUAL_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
