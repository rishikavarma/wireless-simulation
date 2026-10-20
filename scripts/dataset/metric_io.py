"""Shared numeric CSV reader for raw metrics and aggregated feature tables."""

import csv
from pathlib import Path


def read_commented_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Read a numeric CSV after skipping comments and blank lines; preserve column order and
    represent empty cells as None.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(lines)
    rows = []
    for row in reader:
        parsed = {}
        for key, value in row.items():
            if key is None:
                continue
            text = (value or "").strip()
            if text == "":
                parsed[key] = None
            else:
                parsed[key] = float(text)
        rows.append(parsed)
    return list(reader.fieldnames or []), rows
