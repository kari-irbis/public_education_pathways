"""Extract public reading-test results by sveitarfelag.

This module keeps the extraction intentionally small and inspectable. It first
tries the Ministry source page requested for the project. If that page cannot
be parsed into the complete sveitarfelag table, it falls back to the public
Althingi HTML answer that reproduces the same ministry table. It does not use
or download the Althingi PDF.
"""

from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]

STJORNARRADID_URL = (
    "https://www.stjornarradid.is/verkefni/menntamal/"
    "adgerdir-i-menntamalum/birting-a-namsframvindu-barna/"
)
ALTHINGI_HTML_FALLBACK_URL = "https://www.althingi.is/altext/157/s/0837.html"

RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TABLES_DIR = PROJECT_ROOT / "outputs" / "tables"

PROCESSED_PATH = PROCESSED_DIR / "reading_tests_sveitarfelag.csv"
AUDIT_PATH = TABLES_DIR / "reading_tests_extraction_audit.csv"

METRICS = [
    "1. vidmid",
    "2. vidmid",
    "3. vidmid",
    "naer_lagmarksvidmidi_2_3",
]

PERIODS = {
    "Septembermælingar": "haust",
    "Septembermaelingar": "haust",
    "Maímælingar": "vor",
    "Maimælingar": "vor",
    "Maimaelingar": "vor",
}

SCHOOL_YEAR_RE = re.compile(r"^(20\d{2})[–-](20\d{2})(?:\s+(.*))?$")
VALUE_RE = re.compile(r"^(?P<number>\d+(?:[,.]\d+)?)%$")


@dataclass
class ParseResult:
    rows: list[dict[str, object]]
    issues: list[str]


def ensure_dirs() -> None:
    for path in [RAW_DIR, INTERIM_DIR, PROCESSED_DIR, TABLES_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def fetch_url(url: str, cache_path: Path) -> str:
    """Fetch a URL and cache the raw HTML/text response."""
    headers = {
        "User-Agent": (
            "andvari-public-education-pathways/0.1 "
            "(public data extraction; contact: local project)"
        )
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            encoding = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"URL fetch failed for {url}: {exc.reason}") from exc

    text = body.decode(encoding, errors="replace")
    cache_path.write_text(text, encoding="utf-8")
    return text


def html_to_lines(raw_html: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "\n", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|td|th|li|h[1-6]|section|article|table)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\u2013", "–")
    lines = []
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if clean:
            lines.append(clean)
    return lines


def parse_value(value: str) -> tuple[float | None, bool]:
    value = value.strip()
    if value in {"–", "-", ""}:
        return None, True
    match = VALUE_RE.match(value)
    if not match:
        raise ValueError(f"Unexpected percent value: {value!r}")
    return float(match.group("number").replace(",", ".")), False


def is_metadata_line(line: str) -> bool:
    return (
        line.startswith("Skólaár ")
        or line.startswith("157. löggjafarþing")
        or line.startswith("Þingskjal ")
        or line in {"Svar", "mennta- og barnamálaráðherra"}
    )


def maybe_municipality_before_table(line: str) -> bool:
    return line == "Akraneskaupstaður" or line.endswith(
        ("bær", "byggð", "hreppur", "kaupstaður")
    )


def parse_reading_table(lines: Iterable[str], source_url: str) -> ParseResult:
    rows: list[dict[str, object]] = []
    issues: list[str] = []
    current_municipality: str | None = None
    current_period: str | None = None
    pending_municipality: str | None = None
    pending_year: str | None = None
    pending_values: list[str] = []
    seen_table = False

    def flush_pending_values() -> None:
        nonlocal pending_year, pending_values
        if pending_year is None:
            return
        if not current_municipality or not current_period:
            issues.append(f"Data row without municipality/period: {pending_year} {pending_values}")
            pending_year = None
            pending_values = []
            return
        if len(pending_values) != len(METRICS):
            issues.append(
                "Unexpected value count "
                f"for {current_municipality} {current_period} {pending_year}: {pending_values}"
            )
            pending_year = None
            pending_values = []
            return

        for metric, value_text in zip(METRICS, pending_values):
            try:
                value_pct, suppressed = parse_value(value_text)
            except ValueError as exc:
                issues.append(str(exc))
                value_pct, suppressed = None, False

            if suppressed:
                note = (
                    "Suppressed or not published in public source; source says "
                    "results are not given when groups include fewer than 10 students. "
                    "Number of students not provided."
                )
            else:
                note = "Public Lesfimi percentage table. Number of students not provided."

            rows.append(
                {
                    "school_year": pending_year,
                    "measurement_period": current_period,
                    "sveitarfelag": current_municipality,
                    "metric": metric,
                    "value_pct": value_pct,
                    "number_of_students": None,
                    "source_url": source_url,
                    "source_note": note,
                }
            )
        pending_year = None
        pending_values = []

    for raw_line in lines:
        line = raw_line.strip()

        if line == "2." or (line.startswith("2.") and "Hversu mörg sveitarfélög" in line):
            flush_pending_values()
            break
        if is_metadata_line(line):
            continue
        if line in {"Skólaár", "1. viðmið", "2. viðmið", "3. viðmið", "Nær lágmarksviðmiði (2+3)"}:
            continue

        if line in PERIODS:
            flush_pending_values()
            current_period = PERIODS[line]
            if pending_municipality:
                current_municipality = pending_municipality
                pending_municipality = None
            if not current_municipality:
                issues.append(f"Measurement period without municipality: {line}")
            seen_table = True
            continue

        match = SCHOOL_YEAR_RE.match(line)
        if match:
            flush_pending_values()
            pending_year = f"{match.group(1)}-{match.group(2)}"
            trailing_values = (match.group(3) or "").split()
            pending_values = trailing_values if trailing_values else []
            if len(pending_values) == len(METRICS):
                flush_pending_values()
            continue

        if pending_year is not None and (VALUE_RE.match(line) or line in {"–", "-"}):
            pending_values.append(line)
            if len(pending_values) == len(METRICS):
                flush_pending_values()
            continue

        flush_pending_values()

        if not seen_table:
            if maybe_municipality_before_table(line):
                pending_municipality = line
            continue

        if line.startswith(("1.", "3.", "4.")):
            continue

        pending_municipality = line
        current_period = None

    flush_pending_values()
    return ParseResult(rows=rows, issues=issues)


def read_cache_or_fetch(url: str, cache_path: Path) -> str:
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path.read_text(encoding="utf-8", errors="replace")
    return fetch_url(url, cache_path)


def choose_source() -> tuple[str, str, list[str]]:
    """Return source URL, raw HTML, and source-selection issues."""
    issues: list[str] = []

    ministry_cache = RAW_DIR / "stjornarradid_birting_namsframvindu_barna.html"
    fallback_cache = RAW_DIR / "althingi_157_s_0837_public_html_fallback.html"

    try:
        ministry_html = read_cache_or_fetch(STJORNARRADID_URL, ministry_cache)
        ministry_result = parse_reading_table(html_to_lines(ministry_html), STJORNARRADID_URL)
        municipalities = {row["sveitarfelag"] for row in ministry_result.rows}
        if len(municipalities) >= 50:
            return STJORNARRADID_URL, ministry_html, ministry_result.issues
        issues.append(
            "Ministry page was attempted/cached but did not expose a complete parseable "
            f"sveitarfelag table; parsed {len(municipalities)} municipalities."
        )
    except Exception as exc:  # Audit should capture fetch/parse failures.
        issues.append(f"Ministry source fetch/parse failed: {exc}")

    fallback_html = read_cache_or_fetch(ALTHINGI_HTML_FALLBACK_URL, fallback_cache)
    issues.append(
        "Used public Althingi HTML answer as fallback mirror of the ministry "
        "Lesfimi table; no Althingi PDF extraction has been started."
    )
    return ALTHINGI_HTML_FALLBACK_URL, fallback_html, issues

def unique_sorted(rows: list[dict[str, object]], column: str) -> list[str]:
    return sorted({str(row[column]) for row in rows if row.get(column) not in {None, ""}})


def make_audit(rows: list[dict[str, object]], issues: list[str], source_url: str) -> list[dict[str, object]]:
    non_null_count = sum(1 for row in rows if row.get("value_pct") is not None)
    suppressed_count = sum(1 for row in rows if row.get("value_pct") is None)

    return [
        {"item": "source_url", "value": source_url},
        {"item": "extracted_at_utc", "value": datetime.now(timezone.utc).isoformat()},
        {"item": "rows_extracted", "value": len(rows)},
        {"item": "non_null_value_rows", "value": non_null_count},
        {"item": "suppressed_or_missing_value_rows", "value": suppressed_count},
        {"item": "school_years_found", "value": "; ".join(unique_sorted(rows, "school_year"))},
        {"item": "measurement_periods_found", "value": "; ".join(unique_sorted(rows, "measurement_period"))},
        {"item": "municipalities_found", "value": len(unique_sorted(rows, "sveitarfelag"))},
        {"item": "municipality_names_found", "value": "; ".join(unique_sorted(rows, "sveitarfelag"))},
        {"item": "metrics_found", "value": "; ".join(unique_sorted(rows, "metric"))},
        {
            "item": "missing_suppressed_value_handling",
            "value": (
                "Suppressed/missing public values are left blank in value_pct. The public "
                "source states values are not given for groups with fewer than 10 students."
            ),
        },
        {
            "item": "number_of_students_handling",
            "value": "number_of_students is unavailable in this source and is left blank because the public table provides percentages only.",
        },
        {
            "item": "parsing_issues_manual_review",
            "value": " | ".join(issues) if issues else "None detected.",
        },
    ]


def write_csv(rows: list[dict[str, object]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    ensure_dirs()
    source_url, raw_html, source_issues = choose_source()
    parsed = parse_reading_table(html_to_lines(raw_html), source_url)
    issues = source_issues + parsed.issues

    expected_columns = [
        "school_year",
        "measurement_period",
        "sveitarfelag",
        "metric",
        "value_pct",
        "number_of_students",
        "source_url",
        "source_note",
    ]
    rows = sorted(
        parsed.rows,
        key=lambda row: (
            str(row.get("sveitarfelag")),
            str(row.get("school_year")),
            str(row.get("measurement_period")),
            str(row.get("metric")),
        ),
    )

    write_csv(rows, PROCESSED_PATH, expected_columns)
    audit_rows = make_audit(rows, issues, source_url)
    write_csv(audit_rows, AUDIT_PATH, ["item", "value"])

    interim_text = "\n".join(html_to_lines(raw_html))
    (INTERIM_DIR / "reading_tests_source_text.txt").write_text(interim_text, encoding="utf-8")

    print(f"Wrote {len(rows)} rows to {PROCESSED_PATH}")
    print(f"Wrote audit to {AUDIT_PATH}")


if __name__ == "__main__":
    main()
