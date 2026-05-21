"""Extract candidate tables from Althingi PDF 157/s/1182.

The goal is extraction and audit only. The script preserves source values and
flags rows that deserve manual review instead of silently normalizing them.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pdfplumber


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.althingi.is/altext/pdf/157/s/1182.pdf"
PDF_PATH = PROJECT_ROOT / "data" / "raw" / "althingi_157_s_1182.pdf"

RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TABLES_DIR = PROJECT_ROOT / "outputs" / "tables"

GRADUATES_PATH = PROCESSED_DIR / "althingi_graduates_by_grunnskoli.csv"
FRAMHALD_GRADES_PATH = PROCESSED_DIR / "althingi_grades_by_framhaldsskoli_grunnskoli.csv"
GRUNNSKOLI_GRADES_PATH = PROCESSED_DIR / "althingi_grades_by_grunnskoli.csv"
AUDIT_PATH = TABLES_DIR / "althingi_pdf_extraction_audit.csv"
MANUAL_REVIEW_PATH = INTERIM_DIR / "althingi_pdf_manual_review_rows.csv"
RAW_TEXT_PATH = INTERIM_DIR / "althingi_157_s_1182_pages_text.txt"
RAW_LINES_PATH = INTERIM_DIR / "althingi_157_s_1182_extracted_table_lines.csv"

TABLE2_ROW_RE = re.compile(
    r"^(?P<school>.+?)\s+"
    r"(?P<icelandic>\d+(?:\.\d+)?)\s+"
    r"(?P<math>\d+(?:\.\d+)?)\s+"
    r"(?P<graduation>\d+(?:\.\d+)?)\s+"
    r"(?P<count>\d+)$"
)


@dataclass
class ExtractionResult:
    graduates: list[dict[str, object]]
    framhald_grades: list[dict[str, object]]
    grunnskoli_grades: list[dict[str, object]]
    manual_review_rows: list[dict[str, object]]
    raw_lines: list[dict[str, object]]
    issues: list[str]


def ensure_dirs() -> None:
    for path in [RAW_DIR, INTERIM_DIR, PROCESSED_DIR, TABLES_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def fetch_pdf() -> None:
    if PDF_PATH.exists() and PDF_PATH.stat().st_size > 0:
        return
    request = Request(SOURCE_URL, headers={"User-Agent": "andvari-public-education-pathways/0.2"})
    try:
        with urlopen(request, timeout=30) as response:
            PDF_PATH.write_bytes(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {SOURCE_URL}") from exc
    except URLError as exc:
        raise RuntimeError(f"URL fetch failed for {SOURCE_URL}: {exc.reason}") from exc


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def page_text_lines(page: pdfplumber.page.Page) -> list[str]:
    text = page.extract_text() or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def grouped_words_by_line(page: pdfplumber.page.Page) -> dict[float, list[dict[str, object]]]:
    groups: dict[float, list[dict[str, object]]] = defaultdict(list)
    words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
    for word in words:
        top = round(float(word["top"]), 1)
        groups[top].append(word)
    return groups


def words_to_text(words: Iterable[dict[str, object]]) -> str:
    return " ".join(str(word["text"]) for word in sorted(words, key=lambda word: float(word["x0"])))


def parse_count_pair_words(words: list[dict[str, object]], page_number: int, table: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not words:
        return rows
    count_word = max(words, key=lambda word: float(word["x0"]))
    if not str(count_word["text"]).isdigit():
        return rows
    name_words = [word for word in words if word is not count_word]
    name = words_to_text(name_words)
    if not name or name in {"Grunnskóli", "Fjöldi"}:
        return rows
    rows.append(
        {
            "table": table,
            "page": page_number,
            "grunnskoli": name,
            "number_of_students": int(str(count_word["text"])),
            "source_url": SOURCE_URL,
            "source_note": "Parsed from PDF table; candidate extraction for audit.",
            "manual_review": "",
        }
    )
    return rows


def parse_grade_pair_words(words: list[dict[str, object]], page_number: int, table: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if len(words) < 3:
        return rows
    grade_words = [word for word in words if re.fullmatch(r"\d+\.\d+", str(word["text"]))]
    if len(grade_words) < 2:
        return rows
    grade_words = sorted(grade_words, key=lambda word: float(word["x0"]))
    name_words = [word for word in words if float(word["x0"]) < float(grade_words[0]["x0"])]
    name = words_to_text(name_words)
    if not name or name == "Grunnskólar":
        return rows
    row_type = "aggregate" if name == "Heild" else "grunnskoli"
    rows.append(
        {
            "table": table,
            "page": page_number,
            "grunnskoli": name,
            "average_grunnskoli_icelandic_grade": str(grade_words[0]["text"]),
            "average_grunnskoli_math_grade": str(grade_words[1]["text"]),
            "row_type": row_type,
            "is_fewer_than_5_group": str(name == "Færri en 5 nemendur").lower(),
            "source_url": SOURCE_URL,
            "source_note": "Parsed from PDF table; row_type identifies the retained Heild aggregate row.",
            "manual_review": "",
        }
    )
    return rows


def parse_table1(page: pdfplumber.page.Page, raw_lines: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for top, words in sorted(grouped_words_by_line(page).items()):
        if top < 90:
            continue
        left = [word for word in words if float(word["x0"]) < 240]
        right = [word for word in words if 250 < float(word["x0"]) < 450]
        for side, side_words in [("left", left), ("right", right)]:
            text = words_to_text(side_words)
            if text:
                raw_lines.append({"table": "table_1", "page": page.page_number, "side": side, "text": text})
            rows.extend(parse_count_pair_words(side_words, page.page_number, "table_1"))
    rows.sort(key=lambda row: str(row["grunnskoli"]))
    return rows


def parse_table3(page: pdfplumber.page.Page, raw_lines: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for top, words in sorted(grouped_words_by_line(page).items()):
        if top < 110:
            continue
        left = [word for word in words if float(word["x0"]) < 265]
        right = [word for word in words if 270 < float(word["x0"]) < 470]
        for side, side_words in [("left", left), ("right", right)]:
            text = words_to_text(side_words)
            if text:
                raw_lines.append({"table": "table_3", "page": page.page_number, "side": side, "text": text})
            rows.extend(parse_grade_pair_words(side_words, page.page_number, "table_3"))
    rows.sort(key=lambda row: (str(row["row_type"]), str(row["grunnskoli"])))
    return rows


def is_table2_header_or_page_line(line: str) -> bool:
    if line.isdigit():
        return True
    return line.startswith(
        (
            "Tafla 2",
            "skólaárið 2023",
            "Meðaleinkunn úr",
            "grunnskóla í",
            "Skólar íslensku",
        )
    )


def parse_table2_page(
    lines: list[str],
    page_number: int,
    raw_lines: list[dict[str, object]],
    manual_review_rows: list[dict[str, object]],
    issues: list[str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    current_framhaldsskoli = ""
    for line in lines:
        if is_table2_header_or_page_line(line):
            continue
        raw_lines.append({"table": "table_2", "page": page_number, "side": "", "text": line})
        match = TABLE2_ROW_RE.match(line)
        if not match:
            current_framhaldsskoli = line
            continue
        if not current_framhaldsskoli:
            issues.append(f"Table 2 row without framhaldsskoli on page {page_number}: {line}")
            continue
        grunnskoli = match.group("school")
        manual_review = ""
        if match.group("math") == "2.51":
            manual_review = "Unusual two-decimal math grade appears in source; preserved as extracted."
        is_fewer_than_5_group = (
            grunnskoli == "Færri en 5 nemendur"
            or current_framhaldsskoli == "Færri en fimm nemendur"
        )
        row = {
            "table": "table_2",
            "page": page_number,
            "framhaldsskoli": current_framhaldsskoli,
            "grunnskoli": grunnskoli,
            "average_grunnskoli_icelandic_grade": match.group("icelandic"),
            "average_grunnskoli_math_grade": match.group("math"),
            "average_framhaldsskoli_graduation_grade": match.group("graduation"),
            "number_of_students": int(match.group("count")),
            "is_fewer_than_5_group": str(is_fewer_than_5_group).lower(),
            "source_url": SOURCE_URL,
            "source_note": (
                "Færri en 5 nemendur rows preserve the source suppression/grouping label."
                if is_fewer_than_5_group
                else "Parsed from PDF table; candidate extraction for audit."
            ),
            "manual_review": manual_review,
        }
        if manual_review:
            manual_review_rows.append(
                {
                    "table": "table_2",
                    "page": page_number,
                    "row_identifier": f"{current_framhaldsskoli} | {grunnskoli}",
                    "issue": manual_review,
                    "raw_text": line,
                }
            )
        rows.append(row)
    return rows


def extract() -> ExtractionResult:
    ensure_dirs()
    fetch_pdf()
    raw_lines: list[dict[str, object]] = []
    manual_review_rows: list[dict[str, object]] = []
    issues: list[str] = []
    pages_text: list[str] = []

    graduates: list[dict[str, object]] = []
    framhald_grades: list[dict[str, object]] = []
    grunnskoli_grades: list[dict[str, object]] = []

    with pdfplumber.open(PDF_PATH) as pdf:
        for page in pdf.pages:
            lines = page_text_lines(page)
            pages_text.append(f"===== PAGE {page.page_number} =====\n" + "\n".join(lines))
            if page.page_number == 3:
                graduates = parse_table1(page, raw_lines)
            elif 6 <= page.page_number <= 11:
                framhald_grades.extend(
                    parse_table2_page(lines, page.page_number, raw_lines, manual_review_rows, issues)
                )
            elif page.page_number == 13:
                grunnskoli_grades = parse_table3(page, raw_lines)

    RAW_TEXT_PATH.write_text("\n\n".join(pages_text), encoding="utf-8")
    return ExtractionResult(
        graduates=graduates,
        framhald_grades=framhald_grades,
        grunnskoli_grades=grunnskoli_grades,
        manual_review_rows=manual_review_rows,
        raw_lines=raw_lines,
        issues=issues,
    )


def build_audit(result: ExtractionResult) -> list[dict[str, object]]:
    table_columns = {
        "althingi_graduates_by_grunnskoli": [
            "grunnskoli",
            "number_of_students",
            "source_url",
            "source_note",
            "page",
            "manual_review",
        ],
        "althingi_grades_by_framhaldsskoli_grunnskoli": [
            "framhaldsskoli",
            "grunnskoli",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "average_framhaldsskoli_graduation_grade",
            "number_of_students",
            "is_fewer_than_5_group",
            "source_url",
            "source_note",
            "page",
            "manual_review",
        ],
        "althingi_grades_by_grunnskoli": [
            "grunnskoli",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "row_type",
            "is_fewer_than_5_group",
            "source_url",
            "source_note",
            "page",
            "manual_review",
        ],
    }
    issues = list(result.issues)
    issues.append("Table 1 and Table 3 were parsed from two-column word positions.")
    issues.append("Table 2 was parsed from line endings across pages 6-11.")
    issues.append("One source value has two decimals (2.51) and is preserved for manual review.")

    return [
        {"item": "source_url", "value": SOURCE_URL},
        {"item": "extraction_timestamp_utc", "value": datetime.now(timezone.utc).isoformat()},
        {"item": "pdf_filename", "value": str(PDF_PATH.relative_to(PROJECT_ROOT))},
        {"item": "tables_attempted", "value": "Tafla 1; Tafla 2; Tafla 3"},
        {"item": "graduates_rows_extracted", "value": len(result.graduates)},
        {"item": "graduates_columns_extracted", "value": "; ".join(table_columns["althingi_graduates_by_grunnskoli"])},
        {"item": "framhald_grades_rows_extracted", "value": len(result.framhald_grades)},
        {
            "item": "framhald_grades_columns_extracted",
            "value": "; ".join(table_columns["althingi_grades_by_framhaldsskoli_grunnskoli"]),
        },
        {"item": "grunnskoli_grades_rows_extracted", "value": len(result.grunnskoli_grades)},
        {
            "item": "grunnskoli_grades_columns_extracted",
            "value": "; ".join(table_columns["althingi_grades_by_grunnskoli"]),
        },
        {"item": "rows_requiring_manual_review", "value": len(result.manual_review_rows)},
        {"item": "known_parsing_issues", "value": " | ".join(issues)},
        {
            "item": "fewer_than_5_handling",
            "value": (
                "Rows labeled Færri en 5 nemendur are preserved, flagged with "
                "is_fewer_than_5_group=true where applicable, and are not expanded."
            ),
        },
        {
            "item": "analysis_readiness",
            "value": (
                "Candidate extraction outputs are structurally valid but should receive "
                "manual review before analysis because PDF-derived tables can contain "
                "line-wrap or layout edge cases."
            ),
        },
    ]


def main() -> None:
    result = extract()
    write_csv(
        GRADUATES_PATH,
        result.graduates,
        ["grunnskoli", "number_of_students", "source_url", "source_note", "page", "manual_review"],
    )
    write_csv(
        FRAMHALD_GRADES_PATH,
        result.framhald_grades,
        [
            "framhaldsskoli",
            "grunnskoli",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "average_framhaldsskoli_graduation_grade",
            "number_of_students",
            "is_fewer_than_5_group",
            "source_url",
            "source_note",
            "page",
            "manual_review",
        ],
    )
    write_csv(
        GRUNNSKOLI_GRADES_PATH,
        result.grunnskoli_grades,
        [
            "grunnskoli",
            "average_grunnskoli_icelandic_grade",
            "average_grunnskoli_math_grade",
            "row_type",
            "is_fewer_than_5_group",
            "source_url",
            "source_note",
            "page",
            "manual_review",
        ],
    )
    write_csv(RAW_LINES_PATH, result.raw_lines, ["table", "page", "side", "text"])
    write_csv(MANUAL_REVIEW_PATH, result.manual_review_rows, ["table", "page", "row_identifier", "issue", "raw_text"])
    write_csv(AUDIT_PATH, build_audit(result), ["item", "value"])
    print(f"Wrote {len(result.graduates)} rows to {GRADUATES_PATH}")
    print(f"Wrote {len(result.framhald_grades)} rows to {FRAMHALD_GRADES_PATH}")
    print(f"Wrote {len(result.grunnskoli_grades)} rows to {GRUNNSKOLI_GRADES_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
