#!/usr/bin/env python3
"""Create a reusable random sample from sdssdr16qso.main."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sample_and_download_sdssdr16qso import (
    build_query,
    ensure_data_dir,
    fetch_rows,
    write_sample_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw a reusable random sample from sdssdr16qso.main and save it as CSV."
    )
    parser.add_argument(
        "--output",
        default="data/random_qso_sample_100.csv",
        help="Output CSV path. Default: data/random_qso_sample_100.csv",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=100,
        help="Number of quasars to sample. Default: 100",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Override the WSDB sampling SQL entirely.",
    )
    parser.add_argument(
        "--where",
        default=None,
        help="Additional SQL predicate appended to the default sample query.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    ensure_data_dir(output_path.parent)

    try:
        import psycopg2
        import psycopg2.extras
    except ModuleNotFoundError as exc:
        package = exc.name or "required package"
        raise SystemExit(
            "Missing dependency: "
            f"{package}. Install the project requirements first, for example "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    extras = psycopg2.extras
    query = build_query(args.sample_size, args.where, args.query)
    rows = fetch_rows(psycopg2, extras, query)
    if not rows:
        raise SystemExit("The sampling query returned no rows.")

    write_sample_csv(output_path, rows)
    print(f"Sample CSV: {output_path}", flush=True)
    print(f"Rows sampled: {len(rows)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
