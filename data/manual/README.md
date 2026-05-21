# Manual Data Fallback

Use this folder only if the public source pages change shape or are no longer
machine-parseable.

Suggested fallback path:

1. Save the relevant public table as CSV or text in this folder.
2. Keep the original public URL and access date in the filename or a sidecar note.
3. Update `src/extract_reading_tests_sveitarfelag.py` to parse that file explicitly.
4. Regenerate `data/processed/reading_tests_sveitarfelag.csv` and
   `outputs/tables/reading_tests_extraction_audit.csv`.

Do not place private Hljom2 data here.
