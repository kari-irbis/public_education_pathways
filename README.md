# Public Education Pathways

Small public-data project for reusable education datasets.

This repository currently extracts public reading-test results by sveitarfelag
from the Ministry of Education and Children source page:

https://www.stjornarradid.is/verkefni/menntamal/adgerdir-i-menntamalum/birting-a-namsframvindu-barna/

Scope for this first pass is intentionally narrow:

- public reading-test data only
- sveitarfelag, school year, and measurement period grain
- no private Hljom2 data
- no Althingi PDF extraction
- no rankings, spending analysis, or dashboards

## Outputs

- `data/processed/reading_tests_sveitarfelag.csv`
- `outputs/tables/reading_tests_extraction_audit.csv`

## Dataset Grain

The processed CSV uses long format:

- `school_year`
- `measurement_period` (`haust` or `vor`)
- `sveitarfelag`
- `metric`
- `value_pct`
- `number_of_students`
- `source_url`
- `source_note`

`number_of_students` is left blank because the public source gives only
percentages. Suppressed or missing public values are represented as blank
`value_pct` values and noted in `source_note`.

## Local Setup

Create the local virtual environment:

```bash
python3 -m venv .venv
```

Install requirements:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Run the extractor:

```bash
.venv/bin/python -m src.extract_reading_tests_sveitarfelag
```

Run validation:

```bash
.venv/bin/python -m src.validate_reading_tests_sveitarfelag
```

Run the inspection notebook headlessly, if useful:

```bash
.venv/bin/python -m jupyter nbconvert --to notebook --execute notebooks/01_extract_reading_tests_sveitarfelag.ipynb --output 01_extract_reading_tests_sveitarfelag.executed.ipynb --output-dir /tmp
```

## Source Handling

The extractor attempts and caches the ministry page first. If that page does
not expose the full parseable sveitarfelag table, it uses the public Althingi
HTML answer as a fallback mirror of the same Lesfimi table. The Althingi PDF
has not been downloaded or parsed.

## Rebuild

After local setup, rebuild and validate with:

```bash
.venv/bin/python -m src.extract_reading_tests_sveitarfelag
.venv/bin/python -m src.validate_reading_tests_sveitarfelag
```

The notebook `notebooks/01_extract_reading_tests_sveitarfelag.ipynb` runs the
same extraction in an inspectable form.
