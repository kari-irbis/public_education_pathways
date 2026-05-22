"""Build a pragmatic grunnskoli -> sveitarfelag crosswalk.

This revision narrows the main manual review queue to schools that appear in
Althingi outcome datasets. Hagstofa-only schools are retained as reference rows
but do not block the current analysis workflow.
"""

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
REFERENCE_REVIEW_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_reference_only_review.csv"
AUDIT_PATH = PROJECT_ROOT / "outputs" / "tables" / "school_mapping_audit.csv"
MUNICIPALITY_PATH = PROJECT_ROOT / "data" / "manual" / "municipality_name_crosswalk.csv"

DATASETS = {
    "in_althingi_graduates": PROJECT_ROOT / "data" / "processed" / "althingi_graduates_by_grunnskoli.csv",
    "in_althingi_framhaldsskoli_grunnskoli": PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_framhaldsskoli_grunnskoli.csv",
    "in_althingi_grunnskoli_grades": PROJECT_ROOT / "data" / "processed" / "althingi_grades_by_grunnskoli.csv",
    "in_hagstofa_student_counts": PROJECT_ROOT / "data" / "processed" / "grunnskoli_student_counts.csv",
    "in_hagstofa_10th_grade_counts": PROJECT_ROOT / "data" / "processed" / "grunnskoli_10th_grade_counts.csv",
}
ALTHINGI_COLUMNS = ["in_althingi_graduates", "in_althingi_framhaldsskoli_grunnskoli", "in_althingi_grunnskoli_grades"]
PRESENCE_COLUMNS = list(DATASETS)
MANUAL_COLUMNS = ["source_school_name", "normalized_school_name", "sveitarfelag", "canonical_school_name", "manual_status", "notes"]
MUNICIPALITY_COLUMNS = ["sveitarfelag_source", "sveitarfelag_harmonized", "harmonization_status", "notes"]
EXCLUDED_NAMES = {"heild", "alls", "faerri en 5 nemendur", "faerri en fimm nemendur"}
FUZZY_ACCEPT_THRESHOLD = 0.95
FUZZY_REVIEW_THRESHOLD = 0.78
FUZZY_ACCEPT_GAP = 0.04

MUNICIPALITY_HINTS = [
    ("reykjanesbae", "Reykjanesbær"),
    ("akureyri", "Akureyrarkaupstaður"),
    ("hvalfjardarsveit", "Hvalfjarðarsveit"),
    ("vik i myrdal", "Mýrdalshreppur"),
    ("grindavikur", "Grindavíkurbær"),
    ("seltjarnarness", "Seltjarnarneskaupstaður"),
    ("vesturbyggdar", "Vesturbyggð"),
    ("alftamyri", "Reykjavíkurborg"),
    ("nordurfelli", "Reykjavíkurborg"),
]

MANUAL_SCHOOL_MAPPINGS = {
    "Auðarskóli, grunnskóladeild Búðardal": ("Auðarskóli", "Dalabyggð", "Búðardalur school / Auðarskóli."),
    "Bláskógaskóli Reykholti": ("Reykholtsskóli / Bláskógaskóli Reykholti", "Bláskógabyggð", "Reykholt location of Bláskógaskóli / current Reykholtsskóli naming."),
    "Blönduskóli": ("Blönduskóli / Húnaskóli", "Húnabyggð", "Current municipality harmonized to Húnabyggð."),
    "Eskifjarðarskóli": ("Grunnskóli Eskifjarðar", "Fjarðabyggð", "Same school/name variant."),
    "Framsýn menntun ehf.": ("NÚ / Framsýn menntun", "Hafnarfjarðarkaupstaður", "Same entity as NÚ - framsýn menntun."),
    "Grunnskóli Djúpavogs": ("Djúpavogsskóli", "Múlaþing", "Current municipality harmonized to Múlaþing."),
    "Grunnskólinn á Eskifirði": ("Grunnskóli Eskifjarðar", "Fjarðabyggð", "Same school/name variant."),
    "Hafralækjarskóli": ("Hafralækjarskóli / Þingeyjarskóli", "Þingeyjarsveit", "Historical/renamed-school case; current municipality harmonized to Þingeyjarsveit."),
    "Húnavallaskóli": ("Húnavallaskóli / Húnaskóli", "Húnabyggð", "Current municipality harmonized to Húnabyggð."),
    "Kelduskóli - Vík": ("Kelduskóli - Vík", "Reykjavíkurborg", "Reykjavík school site."),
    "Laugalandsskóli í Holtum": ("Grunnskólinn á Laugalandi / Laugalandsskóli", "Rangárþing ytra", "Same school/name variant."),
    "NÚ - framsýn menntun": ("NÚ / Framsýn menntun", "Hafnarfjarðarkaupstaður", "Same entity as Framsýn menntun ehf."),
    "Sandgerðisskóli": ("Grunnskólinn í Sandgerði / Sandgerðisskóli", "Suðurnesjabær", "Current municipality harmonized to Suðurnesjabær."),
    "Stöðvarfjarðarskóli": ("Breiðdals- og Stöðvarfjarðarskóli", "Fjarðabyggð", "Stöðvarfjörður school/site."),
    "Vættaskóli - Engi": ("Vættaskóli - Engi", "Reykjavíkurborg", "Reykjavík school site."),
    "Víkurskóli": ("Víkurskóli", "Reykjavíkurborg", "The PDF has a separate “Víkurskóli, Vík í Mýrdal” row, so the plain Víkurskóli row should map to Reykjavíkurborg."),
}

DEFAULT_MUNICIPALITY_CROSSWALK = [
    ("Sandgerðisbær", "Suðurnesjabær", "harmonized", "Former municipality; now Suðurnesjabær."),
    ("Sveitarfélagið Garður", "Suðurnesjabær", "harmonized", "Former municipality; now Suðurnesjabær."),
    ("Blönduósbær", "Húnabyggð", "harmonized", "Former municipality; now Húnabyggð."),
    ("Húnavatnshreppur", "Húnabyggð", "harmonized", "Former municipality; now Húnabyggð."),
    ("Skútustaðahreppur", "Þingeyjarsveit", "harmonized", "Former municipality; now Þingeyjarsveit."),
    ("Tálknafjarðarhreppur", "Vesturbyggð", "harmonized", "Merged into Vesturbyggð."),
    ("Akureyrarkaupstaður", "Akureyrarbær", "harmonized", "Current official municipality name."),
    ("Seltjarnarneskaupstaður", "Seltjarnarnesbær", "harmonized", "Current official municipality name."),
    ("Bolungarvík", "Bolungarvíkurkaupstaður", "harmonized", "Current official municipality name."),
    ("Djúpavogshreppur", "Múlaþing", "harmonized", "Former municipality; now Múlaþing."),
    ("Fljótsdalshérað", "Múlaþing", "harmonized", "Former municipality; now Múlaþing."),
    ("Seyðisfjarðarkaupstaður", "Múlaþing", "harmonized", "Former municipality; now Múlaþing."),
    ("Borgarfjarðarhreppur", "Múlaþing", "harmonized", "Former municipality; now Múlaþing."),
    ("Reykhólahreppur", "Reykhólahreppur", "unchanged", "Already current for project joins."),
    ("Ísafjarðarbær", "Ísafjarðarbær", "unchanged", "Already current for project joins."),
    ("Reykjavíkurborg", "Reykjavíkurborg", "unchanged", "Already current for project joins."),
    ("Kópavogsbær", "Kópavogsbær", "unchanged", "Already current for project joins."),
    ("Garðabær", "Garðabær", "unchanged", "Already current for project joins."),
    ("Hafnarfjarðarkaupstaður", "Hafnarfjarðarkaupstaður", "unchanged", "Already current for project joins."),
    ("Mosfellsbær", "Mosfellsbær", "unchanged", "Already current for project joins."),
    ("Fjarðabyggð", "Fjarðabyggð", "unchanged", "Already current for project joins."),
    ("Rangárþing ytra", "Rangárþing ytra", "unchanged", "Already current for project joins."),
    ("Bláskógabyggð", "Bláskógabyggð", "unchanged", "Already current for project joins."),
    ("Dalabyggð", "Dalabyggð", "unchanged", "Already current for project joins."),
    ("Vesturbyggð", "Vesturbyggð", "unchanged", "Already current for project joins."),
    ("Þingeyjarsveit", "Þingeyjarsveit", "unchanged", "Already current for project joins."),
    ("Húnabyggð", "Húnabyggð", "unchanged", "Already current for project joins."),
    ("Suðurnesjabær", "Suðurnesjabær", "unchanged", "Already current for project joins."),
    ("Múlaþing", "Múlaþing", "unchanged", "Already current for project joins."),
    ("Akraneskaupstaður", "Akraneskaupstaður", "unchanged", "Already current for project joins."),
    ("Borgarbyggð", "Borgarbyggð", "unchanged", "Already current for project joins."),
    ("Dalvíkurbyggð", "Dalvíkurbyggð", "unchanged", "Already current for project joins."),
    ("Eyjafjarðarsveit", "Eyjafjarðarsveit", "unchanged", "Already current for project joins."),
    ("Fjallabyggð", "Fjallabyggð", "unchanged", "Already current for project joins."),
    ("Flóahreppur", "Flóahreppur", "unchanged", "Already current for project joins."),
    ("Grindavíkurbær", "Grindavíkurbær", "unchanged", "Already current for project joins."),
    ("Grundarfjarðarbær", "Grundarfjarðarbær", "unchanged", "Already current for project joins."),
    ("Grímsnes- og Grafningsher.", "Grímsnes- og Grafningshreppur", "harmonized", "Samband school list abbreviation expanded to current municipality name."),
    ("Grýtubakkahreppur", "Grýtubakkahreppur", "unchanged", "Already current for project joins."),
    ("Hrunamannahreppur", "Hrunamannahreppur", "unchanged", "Already current for project joins."),
    ("Hvalfjarðarsveit", "Hvalfjarðarsveit", "unchanged", "Already current for project joins."),
    ("Hveragerðisbær", "Hveragerðisbær", "unchanged", "Already current for project joins."),
    ("Hörgársveit", "Hörgársveit", "unchanged", "Already current for project joins."),
    ("Húnaþing vestra", "Húnaþing vestra", "unchanged", "Already current for project joins."),
    ("Kaldrananeshreppur", "Kaldrananeshreppur", "unchanged", "Already current for project joins."),
    ("Langanesbyggð", "Langanesbyggð", "unchanged", "Already current for project joins."),
    ("Múlaþing (Borgarfjarðarhreppur)", "Múlaþing", "harmonized", "Samband school list parenthetical source area collapsed to current municipality."),
    ("Múlaþing (Djúpavogshreppur)", "Múlaþing", "harmonized", "Samband school list parenthetical source area collapsed to current municipality."),
    ("Múlaþing (Fljótsdalshérað)", "Múlaþing", "harmonized", "Samband school list parenthetical source area collapsed to current municipality."),
    ("Mýrdalshreppur", "Mýrdalshreppur", "unchanged", "Already current for project joins."),
    ("Norðurþing", "Norðurþing", "unchanged", "Already current for project joins."),
    ("Rangárþing eystra", "Rangárþing eystra", "unchanged", "Already current for project joins."),
    ("Reykjanesbær", "Reykjanesbær", "unchanged", "Already current for project joins."),
    ("Skaftárhreppur", "Skaftárhreppur", "unchanged", "Already current for project joins."),
    ("Skeiða- og Gnúpverjahreppur", "Skeiða- og Gnúpverjahreppur", "unchanged", "Already current for project joins."),
    ("Snæfellsbær", "Snæfellsbær", "unchanged", "Already current for project joins."),
    ("Strandabyggð", "Strandabyggð", "unchanged", "Already current for project joins."),
    ("Stykkishólmsbær", "Stykkishólmsbær", "unchanged", "Already current for project joins."),
    ("Svalbarðsstrandarhreppur", "Svalbarðsstrandarhreppur", "unchanged", "Already current for project joins."),
    ("Sveitarfélagið Hornafjörður", "Sveitarfélagið Hornafjörður", "unchanged", "Already current for project joins."),
    ("Sveitarfélagið Skagafjörður", "Sveitarfélagið Skagafjörður", "unchanged", "Already current for project joins."),
    ("Sveitarfélagið Skagaströnd", "Sveitarfélagið Skagaströnd", "unchanged", "Already current for project joins."),
    ("Sveitarfélagið Vogar", "Sveitarfélagið Vogar", "unchanged", "Already current for project joins."),
    ("Sveitarfélagið Árborg", "Sveitarfélagið Árborg", "unchanged", "Already current for project joins."),
    ("Sveitarfélagið Ölfus", "Sveitarfélagið Ölfus", "unchanged", "Already current for project joins."),
    ("Súðavík", "Súðavíkurhreppur", "harmonized", "Source place-name form expanded to current municipality name."),
    ("Vestmannaeyjabær", "Vestmannaeyjabær", "unchanged", "Already current for project joins."),
    ("Vopnafjarðarhreppur", "Vopnafjarðarhreppur", "unchanged", "Already current for project joins."),
]



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
    for path in [RAW_PAGE_PATH.parent, CROSSWALK_PATH.parent, MANUAL_PATH.parent, REVIEW_PATH.parent, MUNICIPALITY_PATH.parent]:
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
    alias = re.sub(r"^barnaskolinn ", "barnaskoli ", alias)
    return re.sub(r"\s+", " ", alias).strip()


def normalized_variants(name: str) -> set[str]:
    cleaned = re.sub(r"\([^)]*\)", " ", name)
    variants = {normalize_school_name(name), normalize_school_name(cleaned)}
    for value in list(variants):
        variants.add(alias_normalized_name(value))
    if "/" in cleaned:
        parts = [part.strip() for part in cleaned.split("/") if part.strip()]
        for part in parts:
            variants.add(normalize_school_name(part))
            variants.add(alias_normalized_name(normalize_school_name(part)))
        first_norm = normalize_school_name(parts[0]) if parts else ""
        place = ""
        match = re.match(r"grunnskoli(?:nn)? (.+)", first_norm)
        if match:
            place = match.group(1)
        for part in parts[1:]:
            part_norm = normalize_school_name(part)
            if place and part_norm in {"barnaskolinn", "barnaskoli"}:
                variants.add(f"barnaskoli {place}")
    return {variant for variant in variants if variant}


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

def default_municipality_rows() -> list[dict[str, str]]:
    return [
        {
            "sveitarfelag_source": source,
            "sveitarfelag_harmonized": harmonized,
            "harmonization_status": status,
            "notes": notes,
        }
        for source, harmonized, status, notes in DEFAULT_MUNICIPALITY_CROSSWALK
    ]


def ensure_municipality_crosswalk_file() -> list[dict[str, str]]:
    rows_by_source = {row["sveitarfelag_source"]: row for row in default_municipality_rows()}
    if MUNICIPALITY_PATH.exists():
        for row in read_csv_rows(MUNICIPALITY_PATH):
            source = row.get("sveitarfelag_source", "").strip()
            if not source:
                continue
            rows_by_source[source] = {col: row.get(col, "") for col in MUNICIPALITY_COLUMNS}
    rows = sorted(rows_by_source.values(), key=lambda row: row["sveitarfelag_source"])
    write_csv(MUNICIPALITY_PATH, rows, MUNICIPALITY_COLUMNS)
    return rows


def municipality_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["sveitarfelag_source"]: row for row in rows if row.get("sveitarfelag_source")}


def harmonize_municipality(source: str, lookup: dict[str, dict[str, str]]) -> tuple[str, str, str]:
    source = (source or "").strip()
    if not source:
        return "", "", ""
    if source in lookup:
        row = lookup[source]
        return row.get("sveitarfelag_harmonized", source).strip() or source, row.get("harmonization_status", "unchanged"), row.get("notes", "")
    return source, "unchanged", "No project-local harmonization needed; carried forward as current/source name."



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
    by_variant: dict[str, list[dict[str, str]]] = defaultdict(list)
    for raw in rows[1:]:
        if len(raw) <= max(school_idx, municipality_idx):
            continue
        school = raw[school_idx].strip()
        municipality = raw[municipality_idx].strip()
        if not school or not municipality:
            continue
        variants = normalized_variants(school)
        canonical_norm = normalize_school_name(school)
        rec = {
            "canonical_school_name": school,
            "sveitarfelag": municipality,
            "normalized_school_name": canonical_norm,
            "alias_normalized_school_name": alias_normalized_name(canonical_norm),
            "variants": "|".join(sorted(variants)),
        }
        mapping_rows.append(rec)
        for variant in variants:
            by_variant[variant].append(rec)
    return mapping_rows, by_variant


def load_manual_crosswalk() -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    rows_by_name: dict[str, dict[str, str]] = {}
    if MANUAL_PATH.exists():
        for row in read_csv_rows(MANUAL_PATH):
            name = row.get("source_school_name", "").strip()
            if name:
                rows_by_name[name] = {col: row.get(col, "") for col in MANUAL_COLUMNS}
    for name, (canonical, municipality, notes) in MANUAL_SCHOOL_MAPPINGS.items():
        existing = rows_by_name.get(name, {})
        rows_by_name[name] = {
            "source_school_name": name,
            "normalized_school_name": existing.get("normalized_school_name") or normalize_school_name(name),
            "sveitarfelag": existing.get("sveitarfelag") or municipality,
            "canonical_school_name": existing.get("canonical_school_name") or canonical,
            "manual_status": existing.get("manual_status") or "matched",
            "notes": existing.get("notes") or notes,
        }
    rows = sorted(rows_by_name.values(), key=lambda row: normalize_school_name(row["source_school_name"]))
    write_csv(MANUAL_PATH, rows, MANUAL_COLUMNS)

    manual = {}
    non_empty_rows = []
    for row in rows:
        name = row.get("source_school_name", "").strip()
        if not name:
            continue
        if any(row.get(col, "").strip() for col in ["sveitarfelag", "canonical_school_name", "manual_status", "notes"]):
            non_empty_rows.append(row)
        status = row.get("manual_status", "").strip().lower()
        if status in {"matched", "matched_manual", "ok"} and row.get("sveitarfelag", "").strip():
            manual[name] = row
    return manual, non_empty_rows

def load_althingi_graduate_counts() -> dict[str, int]:
    counts = {}
    for row in read_csv_rows(DATASETS["in_althingi_graduates"]):
        value = row.get("number_of_students", "")
        if value.isdigit():
            counts[row["grunnskoli"]] = int(value)
    return counts


def load_recent_10th_counts() -> dict[str, int]:
    latest_by_norm: dict[str, tuple[int, int]] = {}
    path = PROJECT_ROOT / "data" / "processed" / "grunnskoli_10th_grade_counts.csv"
    for row in read_csv_rows(path):
        count = row.get("student_count_10th_grade", "")
        year = row.get("year", "")
        if not count.isdigit() or not year.isdigit():
            continue
        variants = normalized_variants(row["grunnskoli"])
        for variant in variants:
            current = latest_by_norm.get(variant)
            if current is None or int(year) > current[0]:
                latest_by_norm[variant] = (int(year), int(count))
    return {key: value[1] for key, value in latest_by_norm.items()}


def unique_mapping(records: list[dict[str, str]]) -> dict[str, str] | None:
    if not records:
        return None
    pairs = {(row["canonical_school_name"], row["sveitarfelag"]) for row in records}
    if len(pairs) != 1:
        return None
    return records[0]


def candidate_recent_count(candidate: dict[str, object], recent_counts: dict[str, int]) -> str:
    canonical = str(candidate.get("canonical_school_name", ""))
    municipality = str(candidate.get("sveitarfelag", ""))
    special_keys = []
    if normalize_school_name(canonical) == "vikurskoli":
        if municipality == "Reykjavíkurborg":
            special_keys.append("vikurskoli reykjavik")
        elif municipality == "Mýrdalshreppur":
            special_keys.append("vikurskoli vik i myrdal")
    for key in special_keys:
        if key in recent_counts:
            return str(recent_counts[key])
    for variant in str(candidate.get("variants", "")).split("|"):
        if variant in recent_counts:
            return str(recent_counts[variant])
    return ""


def fuzzy_candidates(normalized: str, mapping_rows: list[dict[str, str]], recent_counts: dict[str, int]) -> list[dict[str, object]]:
    candidates = []
    source_variants = {normalized, alias_normalized_name(normalized)}
    for row in mapping_rows:
        row_variants = set(str(row["variants"]).split("|"))
        score = max(SequenceMatcher(None, source, candidate).ratio() for source in source_variants for candidate in row_variants)
        if score >= FUZZY_REVIEW_THRESHOLD:
            candidate = {**row, "score": round(score, 3)}
            candidate["recent_10th_grade_count"] = candidate_recent_count(candidate, recent_counts)
            candidates.append(candidate)
    candidates.sort(key=lambda row: (-float(row["score"]), str(row["canonical_school_name"]), str(row["sveitarfelag"])))
    return candidates[:5]


def is_excluded(name: str, normalized: str) -> bool:
    return normalized in EXCLUDED_NAMES or name in {"Heild", "Alls", "Færri en 5 nemendur", "Færri en fimm nemendur"}


def mapping_priority(row: dict[str, object]) -> str:
    if row["match_status"] == "excluded_group_or_aggregate":
        return "excluded_group_or_aggregate"
    if any(row[col] == "true" for col in ALTHINGI_COLUMNS):
        return "analysis_required"
    return "reference_only"


def municipality_hint(normalized: str) -> str:
    for token, municipality in MUNICIPALITY_HINTS:
        if token in normalized:
            return municipality
    return ""


def high_confidence_fuzzy_allowed(candidates: list[dict[str, object]], hint: str) -> bool:
    if not candidates or float(candidates[0]["score"]) < FUZZY_ACCEPT_THRESHOLD:
        return False
    if len(candidates) > 1 and float(candidates[0]["score"]) - float(candidates[1]["score"]) < FUZZY_ACCEPT_GAP:
        return False
    if hint and candidates[0]["sveitarfelag"] != hint:
        return False
    return True


def vikurskoli_rule(name: str, candidates: list[dict[str, object]], graduate_count: str) -> tuple[dict[str, object] | None, str]:
    if normalize_school_name(name) != "vikurskoli" or not graduate_count.isdigit():
        return None, ""
    exact = [c for c in candidates if normalize_school_name(str(c["canonical_school_name"])) == "vikurskoli"]
    if len(exact) < 2:
        return None, ""
    with_counts = [c for c in exact if str(c.get("recent_10th_grade_count", "")).isdigit()]
    if len(with_counts) < 2:
        return None, ""
    grad = int(graduate_count)
    ordered = sorted(with_counts, key=lambda c: abs(int(str(c["recent_10th_grade_count"])) - grad))
    best, second = ordered[0], ordered[1]
    best_gap = abs(int(str(best["recent_10th_grade_count"])) - grad)
    second_gap = abs(int(str(second["recent_10th_grade_count"])) - grad)
    if best["sveitarfelag"] == "Reykjavíkurborg" and second_gap - best_gap >= 15:
        note = (
            "Ambiguous Víkurskóli; exact-name candidates exist in Reykjavíkurborg and Mýrdalshreppur. "
            f"Althingi graduate count {grad} is closer to recent 10th-grade count "
            f"{best['recent_10th_grade_count']} for Reykjavík than {second['recent_10th_grade_count']} for the next candidate."
        )
        return best, note
    return None, ""


def review_row(base: dict[str, object], candidates: list[dict[str, object]]) -> dict[str, object]:
    row = {
        "source_school_name": base["source_school_name"],
        "normalized_school_name": base["normalized_school_name"],
        "possible_match_1": "",
        "possible_match_1_sveitarfelag": "",
        "possible_match_1_score": "",
        "possible_match_1_recent_10th_grade_count": "",
        "possible_match_2": "",
        "possible_match_2_sveitarfelag": "",
        "possible_match_2_score": "",
        "possible_match_2_recent_10th_grade_count": "",
        "althingi_graduate_count": base.get("althingi_graduate_count", ""),
        "size_evidence_note": base.get("size_evidence_note", ""),
        **{col: base[col] for col in PRESENCE_COLUMNS},
        "notes": base["notes"],
    }
    for idx, candidate in enumerate(candidates[:2], start=1):
        row[f"possible_match_{idx}"] = candidate["canonical_school_name"]
        row[f"possible_match_{idx}_sveitarfelag"] = candidate["sveitarfelag"]
        row[f"possible_match_{idx}_score"] = f"{float(candidate['score']):.3f}"
        row[f"possible_match_{idx}_recent_10th_grade_count"] = candidate.get("recent_10th_grade_count", "")
    if (
        not row["size_evidence_note"]
        and str(row["althingi_graduate_count"]).isdigit()
        and row["possible_match_1_recent_10th_grade_count"]
        and row["possible_match_2_recent_10th_grade_count"]
    ):
        row["size_evidence_note"] = (
            "Althingi graduate count and recent 10th-grade candidate counts are shown for context; "
            "size evidence alone was not treated as decisive."
        )
    return row


def build_manual_file_rows(crosswalk: list[dict[str, object]], existing_non_empty: list[dict[str, str]]) -> list[dict[str, object]]:
    existing_by_name = {row.get("source_school_name", ""): row for row in existing_non_empty}
    rows: dict[str, dict[str, object]] = {}
    for row in existing_non_empty:
        name = row.get("source_school_name", "")
        if name:
            rows[name] = {col: row.get(col, "") for col in MANUAL_COLUMNS}
    for row in crosswalk:
        if row["mapping_priority"] != "analysis_required" or row["match_status"] != "needs_manual_review":
            continue
        name = str(row["source_school_name"])
        existing_row = existing_by_name.get(name, {})
        rows[name] = {
            "source_school_name": name,
            "normalized_school_name": row["normalized_school_name"],
            "sveitarfelag": existing_row.get("sveitarfelag", ""),
            "canonical_school_name": existing_row.get("canonical_school_name", ""),
            "manual_status": existing_row.get("manual_status", ""),
            "notes": existing_row.get("notes", ""),
        }
    return sorted(rows.values(), key=lambda row: str(row["normalized_school_name"]))


def build_crosswalk() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], str, list[dict[str, str]]]:
    source_description, _ = ensure_external_mapping_source()
    municipality_rows = ensure_municipality_crosswalk_file()
    municipality_by_source = municipality_lookup(municipality_rows)
    mapping_rows, mapping_by_variant = load_external_mapping()
    manual, existing_non_empty_manual = load_manual_crosswalk()
    graduate_counts = load_althingi_graduate_counts()
    recent_counts = load_recent_10th_counts()
    universe = load_source_universe()
    crosswalk: list[dict[str, object]] = []
    analysis_review: list[dict[str, object]] = []
    reference_review: list[dict[str, object]] = []

    for name, rec in sorted(universe.items(), key=lambda item: normalize_school_name(item[0])):
        normalized = str(rec["normalized_school_name"])
        base = {
            **rec,
            "mapping_priority": "",
            "canonical_school_name": "",
            "sveitarfelag": "",
            "sveitarfelag_source": "",
            "sveitarfelag_harmonized": "",
            "sveitarfelag_harmonization_status": "",
            "sveitarfelag_harmonization_note": "",
            "match_status": "",
            "match_confidence": "",
            "match_source": "",
            "althingi_graduate_count": str(graduate_counts.get(name, "")),
            "size_evidence_note": "",
            "notes": "",
        }
        candidates = fuzzy_candidates(normalized, mapping_rows, recent_counts) if mapping_rows else []
        hint = municipality_hint(normalized)
        if is_excluded(name, normalized):
            base.update({"match_status": "excluded_group_or_aggregate", "match_confidence": "1.000", "match_source": "rule", "notes": "Grouped or aggregate source row; no sveitarfelag assigned."})
        elif name in manual:
            row = manual[name]
            base.update({"canonical_school_name": row.get("canonical_school_name", ""), "sveitarfelag": row.get("sveitarfelag", ""), "match_status": "matched_manual", "match_confidence": "1.000", "match_source": "data/manual/manual_school_crosswalk.csv", "notes": row.get("notes", "")})
        else:
            direct = unique_mapping(mapping_by_variant.get(normalized, []))
            alias = unique_mapping([m for variant in normalized_variants(name) for m in mapping_by_variant.get(variant, [])])
            vik_rule, vik_note = vikurskoli_rule(name, candidates, str(base["althingi_graduate_count"]))
            if direct:
                base.update({"canonical_school_name": direct["canonical_school_name"], "sveitarfelag": direct["sveitarfelag"], "match_status": "matched_exact", "match_confidence": "1.000", "match_source": "samband_grunnskolar_excel", "notes": "Exact normalized match."})
            elif alias:
                base.update({"canonical_school_name": alias["canonical_school_name"], "sveitarfelag": alias["sveitarfelag"], "match_status": "matched_rule", "match_confidence": "1.000", "match_source": "samband_grunnskolar_excel_alias", "notes": "Matched by conservative alias rule: slash, parenthetical, or Grunnskólinn/Grunnskóli variant."})
            elif vik_rule:
                base.update({"canonical_school_name": vik_rule["canonical_school_name"], "sveitarfelag": vik_rule["sveitarfelag"], "match_status": "matched_rule", "match_confidence": "0.950", "match_source": "name_plus_size_rule", "size_evidence_note": vik_note, "notes": vik_note})
            elif high_confidence_fuzzy_allowed(candidates, hint):
                best = candidates[0]
                base.update({"canonical_school_name": best["canonical_school_name"], "sveitarfelag": best["sveitarfelag"], "match_status": "matched_fuzzy_high_confidence", "match_confidence": f"{float(best['score']):.3f}", "match_source": "samband_grunnskolar_excel", "notes": "High-confidence fuzzy normalized match; review optional."})
            elif hint:
                best = candidates[0] if candidates else {}
                base.update({"canonical_school_name": "", "sveitarfelag": hint, "match_status": "matched_municipality_rule_canonical_uncertain", "match_confidence": f"{float(best.get('score', 0)):.3f}" if best else "", "match_source": "municipality_location_hint", "notes": "Sveitarfelag assigned from explicit location signal in source name; canonical school remains uncertain."})
            else:
                base.update({"match_status": "needs_manual_review", "match_confidence": f"{float(candidates[0]['score']):.3f}" if candidates else "", "match_source": "", "notes": "No exact, rule, municipality, or high-confidence fuzzy match accepted."})
        base["mapping_priority"] = mapping_priority(base)
        base["sveitarfelag_source"] = base.get("sveitarfelag", "")
        harmonized, harmonization_status, harmonization_note = harmonize_municipality(str(base["sveitarfelag_source"]), municipality_by_source)
        base["sveitarfelag_harmonized"] = harmonized
        base["sveitarfelag_harmonization_status"] = harmonization_status
        base["sveitarfelag_harmonization_note"] = harmonization_note
        if base["match_status"] == "needs_manual_review":
            review = review_row(base, candidates)
            if base["mapping_priority"] == "analysis_required":
                analysis_review.append(review)
            elif base["mapping_priority"] == "reference_only":
                review["notes"] = review["notes"] + " Reference-only Hagstofa row; not needed for current analysis."
                reference_review.append(review)
        crosswalk.append(base)
    return crosswalk, analysis_review, reference_review, build_manual_file_rows(crosswalk, existing_non_empty_manual), source_description, municipality_rows


def build_audit(crosswalk: list[dict[str, object]], analysis_review: list[dict[str, object]], reference_review: list[dict[str, object]], source_description: str) -> list[dict[str, object]]:
    analysis_rows = [r for r in crosswalk if r["mapping_priority"] == "analysis_required"]
    reference_rows = [r for r in crosswalk if r["mapping_priority"] == "reference_only"]
    excluded_rows = [r for r in crosswalk if r["mapping_priority"] == "excluded_group_or_aggregate"]
    analysis_counts = Counter(str(row["match_status"]) for row in analysis_rows)
    reference_counts = Counter(str(row["match_status"]) for row in reference_rows)
    audit = [
        {"section": "source", "dataset": "", "item": "external_mapping_source_used", "value": source_description},
        {"section": "source", "dataset": "", "item": "external_mapping_page_cache", "value": str(RAW_PAGE_PATH.relative_to(PROJECT_ROOT))},
        {"section": "source", "dataset": "", "item": "external_mapping_excel_cache", "value": str(RAW_EXCEL_PATH.relative_to(PROJECT_ROOT)) if RAW_EXCEL_PATH.exists() else ""},
        {"section": "summary", "dataset": "", "item": "created_at_utc", "value": datetime.now(timezone.utc).isoformat()},
        {"section": "summary", "dataset": "", "item": "total_unique_source_school_names", "value": len(crosswalk)},
        {"section": "summary", "dataset": "", "item": "althingi_relevant_total_including_excluded", "value": len(analysis_rows) + len([r for r in excluded_rows if any(r[col] == "true" for col in ALTHINGI_COLUMNS)])},
        {"section": "summary", "dataset": "", "item": "analysis_required_total", "value": len(analysis_rows)},
        {"section": "summary", "dataset": "", "item": "analysis_required_matched_exact", "value": analysis_counts["matched_exact"]},
        {"section": "summary", "dataset": "", "item": "analysis_required_matched_fuzzy", "value": analysis_counts["matched_fuzzy_high_confidence"]},
        {"section": "summary", "dataset": "", "item": "analysis_required_matched_by_rule", "value": analysis_counts["matched_rule"]},
        {"section": "summary", "dataset": "", "item": "analysis_required_matched_by_municipality_only_rule", "value": analysis_counts["matched_municipality_rule_canonical_uncertain"]},
        {"section": "summary", "dataset": "", "item": "analysis_required_matched_manual", "value": analysis_counts["matched_manual"]},
        {"section": "summary", "dataset": "", "item": "analysis_required_needing_manual_review", "value": analysis_counts["needs_manual_review"]},
        {"section": "summary", "dataset": "", "item": "reference_only_total", "value": len(reference_rows)},
        {"section": "summary", "dataset": "", "item": "reference_only_unresolved", "value": reference_counts["needs_manual_review"]},
        {"section": "summary", "dataset": "", "item": "excluded_aggregate_group_rows", "value": len(excluded_rows)},
        {"section": "summary", "dataset": "", "item": "rows_with_sveitarfelag_harmonized_populated", "value": sum(1 for row in crosswalk if row["sveitarfelag_harmonized"])},
        {"section": "summary", "dataset": "", "item": "municipality_harmonizations_applied", "value": sum(1 for row in crosswalk if row["sveitarfelag_harmonization_status"] == "harmonized")},
        {"section": "summary", "dataset": "", "item": "municipality_names_needing_review", "value": sum(1 for row in crosswalk if row["sveitarfelag_harmonization_status"] == "needs_review")},
        {"section": "source", "dataset": "", "item": "municipality_harmonization_source", "value": "Project-local municipality_name_crosswalk.csv seeded from agreed Phase 4 harmonization mappings."},
    ]
    for col in PRESENCE_COLUMNS:
        rows = [row for row in crosswalk if row[col] == "true" and row["mapping_priority"] != "excluded_group_or_aggregate"]
        matched = sum(1 for row in rows if row["sveitarfelag_harmonized"])
        rate = matched / len(rows) if rows else 0
        audit.append({"section": "match_rate_by_source", "dataset": col, "item": "matched_non_excluded", "value": f"{matched}/{len(rows)} ({rate:.1%})"})
    audit.append({"section": "manual_review", "dataset": "analysis_required", "item": "notes", "value": "Main review file includes only unresolved analysis_required rows. Reference-only unresolved rows are in school_mapping_reference_only_review.csv."})
    for row in sorted(analysis_review, key=lambda r: int(r["althingi_graduate_count"] or 0), reverse=True):
        audit.append({"section": "unresolved_analysis_required", "dataset": "", "item": row["source_school_name"], "value": f"althingi_graduate_count={row['althingi_graduate_count']}; possible_match_1={row['possible_match_1']} ({row['possible_match_1_sveitarfelag']}, score={row['possible_match_1_score']})"})
    if reference_review:
        patterns = Counter(" ".join(str(row["normalized_school_name"]).split()[:2]) for row in reference_review)
        audit.append({"section": "reference_only", "dataset": "", "item": "top_common_unmatched_patterns", "value": "; ".join(f"{k}: {v}" for k, v in patterns.most_common(10))})
    return audit


def main() -> None:
    ensure_dirs()
    crosswalk, analysis_review, reference_review, manual_rows, source_description, municipality_rows = build_crosswalk()
    crosswalk_fields = [
        "source_school_name", "normalized_school_name", "mapping_priority", "canonical_school_name", "sveitarfelag",
        "sveitarfelag_source", "sveitarfelag_harmonized", "sveitarfelag_harmonization_status", "sveitarfelag_harmonization_note", *PRESENCE_COLUMNS,
        "match_status", "match_confidence", "match_source", "althingi_graduate_count", "size_evidence_note", "notes",
    ]
    review_fields = [
        "source_school_name", "normalized_school_name", "possible_match_1", "possible_match_1_sveitarfelag", "possible_match_1_score", "possible_match_1_recent_10th_grade_count",
        "possible_match_2", "possible_match_2_sveitarfelag", "possible_match_2_score", "possible_match_2_recent_10th_grade_count", "althingi_graduate_count", "size_evidence_note", *PRESENCE_COLUMNS, "notes",
    ]
    write_csv(CROSSWALK_PATH, crosswalk, crosswalk_fields)
    write_csv(REVIEW_PATH, analysis_review, review_fields)
    write_csv(REFERENCE_REVIEW_PATH, reference_review, review_fields)
    write_csv(MANUAL_PATH, manual_rows, MANUAL_COLUMNS)
    write_csv(MUNICIPALITY_PATH, municipality_rows, MUNICIPALITY_COLUMNS)
    write_csv(AUDIT_PATH, build_audit(crosswalk, analysis_review, reference_review, source_description), ["section", "dataset", "item", "value"])
    print(f"Wrote {len(crosswalk)} rows to {CROSSWALK_PATH}")
    print(f"Wrote {len(analysis_review)} analysis-required review rows to {REVIEW_PATH}")
    print(f"Wrote {len(reference_review)} reference-only review rows to {REFERENCE_REVIEW_PATH}")
    print(f"Wrote {len(manual_rows)} rows to {MANUAL_PATH}")
    print(f"Wrote {len(municipality_rows)} rows to {MUNICIPALITY_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
