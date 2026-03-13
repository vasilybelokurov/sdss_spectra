#!/usr/bin/env python3
"""Sample DR16 quasars from WSDB and download their spectra."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from download_sdssdr16qso_spectra import download_file, infer_run2d


DEFAULT_SQL = """
select
    sdss_name,
    ra,
    dec,
    plate,
    mjd,
    fiberid,
    z,
    thing_id
from sdssdr16qso.main
where is_qso_final = 1
order by random()
limit {limit}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Draw a random sample from sdssdr16qso.main and download the matching spectra."
        )
    )
    parser.add_argument(
        "--output-root",
        default="~/data/sdss/spectra/qso/",
        help="Root directory for downloaded spectra. Default: ~/data/sdss/spectra/qso/",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory for sampled catalogs and manifests. Default: ./data",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=100,
        help="Number of quasars to sample. Default: 100",
    )
    parser.add_argument(
        "--product",
        choices=["spec", "spec-lite"],
        default="spec-lite",
        help="Spectrum product to download. Default: spec-lite",
    )
    parser.add_argument(
        "--run2d",
        default=None,
        help="Override the reduction version for all rows. Default: infer from MJD.",
    )
    parser.add_argument(
        "--boss-run2d",
        default="v5_13_0",
        help="Reduction version for BOSS/eBOSS spectra. Default: v5_13_0",
    )
    parser.add_argument(
        "--legacy-run2d",
        default="26",
        help="Reduction version for legacy SDSS spectra. Default: 26",
    )
    parser.add_argument(
        "--boss-start-mjd",
        type=int,
        default=55176,
        help="MJD at which the catalog switches to BOSS/eBOSS-style spectra. Default: 55176",
    )
    parser.add_argument(
        "--release",
        default="dr16",
        help="SDSS release for sdss_access. Default: dr16",
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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose sdss_access output.",
    )
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds per spectrum download. Default: 120",
    )
    return parser.parse_args()


def import_runtime_dependencies():
    try:
        import psycopg2
        import psycopg2.extras
        from sdss_access.path import Path as SdssPath
    except ModuleNotFoundError as exc:
        package = exc.name or "required package"
        raise SystemExit(
            "Missing dependency: "
            f"{package}. Install the project requirements first, for example "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    return psycopg2, psycopg2.extras, SdssPath


def build_query(limit: int, where: str | None, override: str | None) -> str:
    if override:
        return override.strip().rstrip(";")
    query = DEFAULT_SQL.format(limit=limit).strip()
    if where:
        query = query.replace("where is_qso_final = 1", f"where is_qso_final = 1 and ({where})")
    return query


def fetch_rows(psycopg2, extras, query: str) -> list[dict]:
    pguser = os.environ.get("PGUSER")
    pghost = os.environ.get("PGHOST")
    if not pguser or not pghost:
        raise SystemExit("PGUSER and PGHOST must be set to query WSDB.")

    with psycopg2.connect(
        dbname="wsdb",
        user=pguser,
        host=pghost,
        port=5432,
        cursor_factory=extras.RealDictCursor,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return list(cur.fetchall())


def ensure_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)


def write_sample_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["sdss_name", "ra", "dec", "plate", "mjd", "fiberid", "z", "thing_id"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def resolve_local_path(sdss_path, product: str, row: dict, args: argparse.Namespace) -> Path:
    run2d = infer_run2d(args, row)
    return (
        Path(
            sdss_path.full(
                product,
                run2d=run2d,
                plateid=int(row["plate"]),
                mjd=int(row["mjd"]),
                fiberid=int(row["fiberid"]),
            )
        )
        .expanduser()
        .resolve()
    )


def resolve_remote_url(sdss_path, product: str, row: dict, args: argparse.Namespace) -> str:
    run2d = infer_run2d(args, row)
    return sdss_path.url(
        product,
        run2d=run2d,
        plateid=int(row["plate"]),
        mjd=int(row["mjd"]),
        fiberid=int(row["fiberid"]),
    )


def write_manifest(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sdss_name",
        "ra",
        "dec",
        "z",
        "thing_id",
        "plate",
        "mjd",
        "fiberid",
        "run2d",
        "product",
        "remote_url",
        "spectrum_path",
        "status",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    os.environ["SAS_BASE_DIR"] = str(output_root)

    psycopg2, extras, SdssPath = import_runtime_dependencies()
    ensure_data_dir(data_dir)

    query = build_query(args.sample_size, args.where, args.query)
    rows = fetch_rows(psycopg2, extras, query)
    if not rows:
        raise SystemExit("The sampling query returned no rows.")

    sample_csv = data_dir / f"random_qso_sample_{len(rows)}.csv"
    write_sample_csv(sample_csv, rows)
    manifest_path = data_dir / "random_qso_sample_manifest.csv"
    write_manifest(manifest_path, [])
    print(f"Sample written: {sample_csv}", flush=True)
    print(f"Progress manifest: {manifest_path}", flush=True)
    print(f"Starting downloads for {len(rows)} spectra", flush=True)

    sdss_path = SdssPath(release=args.release, verbose=args.verbose)

    manifest_rows: list[dict] = []
    queued: set[Path] = set()
    downloaded = 0
    failed = 0
    cached = 0

    for index, row in enumerate(rows, start=1):
        run2d = infer_run2d(args, row)
        spectrum_path = resolve_local_path(sdss_path, args.product, row, args)
        remote_url = resolve_remote_url(sdss_path, args.product, row, args)
        print(
            f"[{index}/{len(rows)}] starting {row['sdss_name']} "
            f"plate={row['plate']} mjd={row['mjd']} fiber={int(row['fiberid']):04d}",
            flush=True,
        )

        if spectrum_path.exists():
            status = "cached"
            error = ""
            cached += 1
        elif spectrum_path in queued:
            status = "queued_duplicate"
            error = ""
        else:
            try:
                download_file(remote_url, spectrum_path, timeout=args.download_timeout)
            except Exception as exc:
                status = "download_failed"
                error = str(exc)
                failed += 1
            else:
                status = "downloaded" if spectrum_path.exists() else "download_failed"
                error = ""
                if status == "downloaded":
                    downloaded += 1
                else:
                    failed += 1
            queued.add(spectrum_path)

        manifest_row = {
            "sdss_name": row["sdss_name"],
            "ra": row["ra"],
            "dec": row["dec"],
            "z": row["z"],
            "thing_id": row["thing_id"],
            "plate": row["plate"],
            "mjd": row["mjd"],
            "fiberid": row["fiberid"],
            "run2d": run2d,
            "product": args.product,
            "remote_url": remote_url,
            "spectrum_path": str(spectrum_path),
            "status": status,
            "error": error,
        }
        manifest_rows.append(manifest_row)

        write_manifest(manifest_path, manifest_rows)
        print(
            f"[{index}/{len(rows)}] {status} {row['sdss_name']} "
            f"plate={row['plate']} mjd={row['mjd']} fiber={int(row['fiberid']):04d}",
            flush=True,
        )

    print(f"Output root: {output_root}")
    print(f"Data dir: {data_dir}")
    print(f"Sample CSV: {sample_csv}")
    print(f"Manifest: {manifest_path}")
    print(f"Rows sampled: {len(rows)}")
    print(f"Downloaded this run: {downloaded}")
    print(f"Already cached: {cached}")
    print(f"Download failures: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
