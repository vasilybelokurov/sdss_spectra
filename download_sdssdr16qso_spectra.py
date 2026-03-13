#!/usr/bin/env python3
"""Download SDSS DR16 QSO spectra using rows from sdssdr16qso.main."""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import tempfile
import urllib.request
from pathlib import Path


DEFAULT_BOSS_RUN2D = "v5_13_0"
DEFAULT_LEGACY_RUN2D = "26"
DEFAULT_BOSS_START_MJD = 55176
DEFAULT_WSDB_QUERY = """
select
    sdss_name,
    ra,
    dec,
    plate,
    mjd,
    fiberid,
    z,
    thing_id,
    plate_duplicate,
    mjd_duplicate,
    fiberid_duplicate
from sdssdr16qso.main
where is_qso_final = 1
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download DR16 quasar spectra using plate/mjd/fiber identifiers from "
            "sdssdr16qso.main or a local export of that table."
        )
    )
    parser.add_argument(
        "input_table",
        nargs="?",
        help="Optional local table export with plate/mjd/fiberid columns.",
    )
    parser.add_argument(
        "--input-format",
        default=None,
        help="Optional astropy table format override, for example csv, ecsv, fits.",
    )
    parser.add_argument("--name-column", default="sdss_name", help="Object name column.")
    parser.add_argument("--plate-column", default="plate", help="Plate column name.")
    parser.add_argument("--mjd-column", default="mjd", help="MJD column name.")
    parser.add_argument("--fiber-column", default="fiberid", help="Fiber column name.")
    parser.add_argument("--ra-column", default="ra", help="Optional RA column name.")
    parser.add_argument("--dec-column", default="dec", help="Optional Dec column name.")
    parser.add_argument("--z-column", default="z", help="Optional redshift column name.")
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
        default=DEFAULT_BOSS_RUN2D,
        help=f"Reduction version for BOSS/eBOSS spectra. Default: {DEFAULT_BOSS_RUN2D}",
    )
    parser.add_argument(
        "--legacy-run2d",
        default=DEFAULT_LEGACY_RUN2D,
        help=f"Reduction version for legacy SDSS spectra. Default: {DEFAULT_LEGACY_RUN2D}",
    )
    parser.add_argument(
        "--boss-start-mjd",
        type=int,
        default=DEFAULT_BOSS_START_MJD,
        help=f"MJD at which the catalog switches to BOSS/eBOSS-style spectra. Default: {DEFAULT_BOSS_START_MJD}",
    )
    parser.add_argument(
        "--release",
        default="dr16",
        help="SDSS public release used by sdss_access. Default: dr16",
    )
    parser.add_argument(
        "--sas-root",
        default=None,
        help="Override SAS_BASE_DIR. Default: use a local .sas_cache directory.",
    )
    parser.add_argument(
        "--manifest",
        default="sdssdr16qso_spectra_manifest.csv",
        help="Output CSV manifest path. Default: sdssdr16qso_spectra_manifest.csv",
    )
    parser.add_argument(
        "--include-duplicates",
        action="store_true",
        help="Also download spectra listed in plate/mjd/fiberid duplicate arrays when available.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve file paths and write the manifest without downloading files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose sdss_access output.",
    )
    parser.add_argument(
        "--download-method",
        choices=["access", "http"],
        default="access",
        help="Download backend. Default: access",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of spectra to queue per sdss_access batch. Default: 10",
    )
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds per spectrum download when --download-method=http. Default: 120",
    )

    wsdb_group = parser.add_argument_group("WSDB source")
    wsdb_group.add_argument(
        "--from-wsdb",
        action="store_true",
        help="Query wsdb.sdssdr16qso.main directly instead of reading a local table.",
    )
    wsdb_group.add_argument(
        "--where",
        default=None,
        help="SQL predicate appended to the default sdssdr16qso.main query.",
    )
    wsdb_group.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional LIMIT for the WSDB query.",
    )
    wsdb_group.add_argument(
        "--wsdb-query",
        default=None,
        help=(
            "Full SQL query to execute against WSDB. The result must include plate, mjd, and fiberid. "
            "If omitted, the script queries sdssdr16qso.main."
        ),
    )

    return parser.parse_args()


def import_runtime_dependencies():
    try:
        from astropy.table import Table
        from sdss_access.path import Path as SdssPath
    except ModuleNotFoundError as exc:
        package = exc.name or "required package"
        raise SystemExit(
            "Missing dependency: "
            f"{package}. Install the project requirements first, for example "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    return Table, SdssPath


def normalize_string(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "item"):
        try:
            value = value.item()
        except ValueError:
            pass
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def normalize_number(value):
    if value is None:
        return ""
    if hasattr(value, "item"):
        try:
            value = value.item()
        except ValueError:
            pass
    try:
        if math.isnan(value):
            return ""
    except TypeError:
        return value
    return value


def ensure_sas_root(args: argparse.Namespace) -> None:
    if args.sas_root:
        os.environ["SAS_BASE_DIR"] = str(Path(args.sas_root).expanduser())
    elif "SAS_BASE_DIR" not in os.environ:
        os.environ["SAS_BASE_DIR"] = str(
            (Path(tempfile.gettempdir()) / "sdss_spectra_sas_cache").resolve()
        )


def read_local_table(args: argparse.Namespace, table_class):
    input_table = Path(args.input_table).expanduser().resolve()
    if not input_table.exists():
        raise SystemExit(f"Input table not found: {input_table}")

    table = (
        table_class.read(input_table, format=args.input_format)
        if args.input_format
        else table_class.read(input_table)
    )

    required = [args.plate_column, args.mjd_column, args.fiber_column]
    missing = [name for name in required if name not in table.colnames]
    if missing:
        raise SystemExit(
            f"Input table is missing required columns: {', '.join(missing)}"
        )

    return table, str(input_table)


def build_wsdb_query(args: argparse.Namespace) -> str:
    if args.wsdb_query:
        return args.wsdb_query.strip().rstrip(";")

    query = DEFAULT_WSDB_QUERY.strip()
    if args.where:
        query = f"{query}\nand ({args.where})"
    if args.limit is not None:
        query = f"{query}\nlimit {args.limit}"
    return query


def query_wsdb(args: argparse.Namespace):
    try:
        import psycopg2
        import psycopg2.extras
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: psycopg2. Install the project requirements first."
        ) from exc

    pguser = os.environ.get("PGUSER")
    pghost = os.environ.get("PGHOST")
    if not pguser or not pghost:
        raise SystemExit("PGUSER and PGHOST must be set to query WSDB directly.")

    query = build_wsdb_query(args)
    with psycopg2.connect(
        dbname="wsdb",
        user=pguser,
        host=pghost,
        port=5432,
        cursor_factory=psycopg2.extras.RealDictCursor,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

    return rows, "wsdb"


def to_list(value):
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return value
    return [value]


def row_value(row, key: str, default=""):
    if isinstance(row, dict):
        return row.get(key, default)
    return row[key] if key in row.colnames else default


def expand_rows(args: argparse.Namespace, rows) -> list[dict]:
    expanded: list[dict] = []
    for index, row in enumerate(rows):
        object_name = normalize_string(row_value(row, args.name_column, f"target_{index}"))
        base = {
            "input_index": index,
            "object_name": object_name,
            "ra": normalize_number(row_value(row, args.ra_column, "")),
            "dec": normalize_number(row_value(row, args.dec_column, "")),
            "z": normalize_number(row_value(row, args.z_column, "")),
        }

        primary = {
            **base,
            "spectrum_role": "primary",
            "plate": int(row_value(row, args.plate_column)),
            "mjd": int(row_value(row, args.mjd_column)),
            "fiberid": int(row_value(row, args.fiber_column)),
        }
        expanded.append(primary)

        if not args.include_duplicates:
            continue

        plates = to_list(row_value(row, "plate_duplicate", []))
        mjds = to_list(row_value(row, "mjd_duplicate", []))
        fibers = to_list(row_value(row, "fiberid_duplicate", []))
        for plate, mjd, fiberid in zip(plates, mjds, fibers):
            if plate is None or mjd is None or fiberid is None:
                continue
            if (
                int(plate) == primary["plate"]
                and int(mjd) == primary["mjd"]
                and int(fiberid) == primary["fiberid"]
            ):
                continue
            expanded.append(
                {
                    **base,
                    "spectrum_role": "duplicate",
                    "plate": int(plate),
                    "mjd": int(mjd),
                    "fiberid": int(fiberid),
                }
            )

    return expanded


def infer_run2d(args: argparse.Namespace, row: dict) -> str:
    if args.run2d:
        return args.run2d
    if row["mjd"] >= args.boss_start_mjd:
        return args.boss_run2d
    return args.legacy_run2d


def resolve_local_path(sdss_path, product: str, run2d: str, row: dict) -> Path:
    return Path(
        sdss_path.full(
            product,
            run2d=run2d,
            plateid=row["plate"],
            mjd=row["mjd"],
            fiberid=row["fiberid"],
        )
    ).expanduser()


def resolve_remote_url(sdss_path, product: str, run2d: str, row: dict) -> str:
    return sdss_path.url(
        product,
        run2d=run2d,
        plateid=row["plate"],
        mjd=row["mjd"],
        fiberid=row["fiberid"],
    )


def download_file(url: str, local_path: Path, timeout: int = 120) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as response, local_path.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def download_batch_with_access(args: argparse.Namespace, batch_rows: list[dict]) -> int | None:
    from sdss_access import Access

    access = Access(release=args.release, verbose=args.verbose)
    access.remote()
    for row in batch_rows:
        access.add(
            row["product"],
            run2d=row["run2d"],
            plateid=row["plate"],
            mjd=row["mjd"],
            fiberid=row["fiberid"],
        )
    access.set_stream()
    return access.commit(follow_symlinks=False)


def write_manifest(manifest_path: Path, rows: list[dict]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "input_index",
        "object_name",
        "ra",
        "dec",
        "z",
        "spectrum_role",
        "plate",
        "mjd",
        "fiberid",
        "product",
        "run2d",
        "status",
        "error",
        "local_path",
        "remote_url",
    ]
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    ensure_sas_root(args)
    Table, SdssPath = import_runtime_dependencies()

    using_wsdb = args.from_wsdb or args.wsdb_query is not None or args.where is not None
    if using_wsdb:
        source_rows, source_label = query_wsdb(args)
    else:
        if not args.input_table:
            raise SystemExit("Pass an input table or use --from-wsdb.")
        source_rows, source_label = read_local_table(args, Table)

    expanded_rows = expand_rows(args, source_rows)
    if not expanded_rows:
        raise SystemExit("No spectra were resolved from the input rows.")

    sdss_path = SdssPath(release=args.release, verbose=args.verbose)

    manifest_rows: list[dict] = []
    queued_files: set[Path] = set()
    pending_indices: list[int] = []

    for index, row in enumerate(expanded_rows, start=1):
        row_run2d = infer_run2d(args, row)
        local_path = resolve_local_path(sdss_path, args.product, row_run2d, row)
        status = "cached" if local_path.exists() else "pending_download"
        error = ""
        remote_url = resolve_remote_url(sdss_path, args.product, row_run2d, row)
        if not args.dry_run and local_path.exists():
            status = "downloaded_or_cached"
        elif not args.dry_run and local_path in queued_files:
            status = "queued_duplicate"
        elif not args.dry_run:
            status = "queued"
            queued_files.add(local_path)

        manifest_rows.append(
            {
                "input_index": row["input_index"],
                "object_name": row["object_name"],
                "ra": row["ra"],
                "dec": row["dec"],
                "z": row["z"],
                "spectrum_role": row["spectrum_role"],
                "plate": row["plate"],
                "mjd": row["mjd"],
                "fiberid": row["fiberid"],
                "product": args.product,
                "run2d": row_run2d,
                "status": status if args.dry_run else status,
                "error": error,
                "local_path": str(local_path),
                "remote_url": remote_url,
            }
        )
        if status == "queued":
            pending_indices.append(len(manifest_rows) - 1)
        if not args.dry_run:
            print(
                f"[{index}/{len(expanded_rows)}] prepared {row['object_name']} "
                f"status={status} plate={row['plate']} mjd={row['mjd']} fiber={int(row['fiberid']):04d}",
                flush=True,
            )

    manifest_path = Path(args.manifest).expanduser().resolve()
    write_manifest(manifest_path, manifest_rows)

    if not args.dry_run and pending_indices:
        for batch_start in range(0, len(pending_indices), args.batch_size):
            batch_chunk = pending_indices[batch_start : batch_start + args.batch_size]
            batch_rows = [manifest_rows[idx] for idx in batch_chunk]
            batch_number = (batch_start // args.batch_size) + 1
            batch_total = math.ceil(len(pending_indices) / args.batch_size)
            row_labels = ", ".join(
                f"{row['plate']}-{row['mjd']}-{int(row['fiberid']):04d}" for row in batch_rows
            )
            print(
                f"Starting batch {batch_number}/{batch_total} with {len(batch_rows)} spectra: {row_labels}",
                flush=True,
            )
            batch_error = ""
            if args.download_method == "access":
                try:
                    download_batch_with_access(args, batch_rows)
                except Exception as exc:
                    batch_error = str(exc)
            else:
                for row in batch_rows:
                    try:
                        download_file(
                            row["remote_url"],
                            Path(row["local_path"]),
                            timeout=args.download_timeout,
                        )
                    except Exception as exc:
                        batch_error = str(exc)
                        row["error"] = str(exc)
            for idx in batch_chunk:
                row = manifest_rows[idx]
                local_path = Path(row["local_path"])
                if local_path.exists():
                    row["status"] = "downloaded_or_cached"
                    row["error"] = ""
                else:
                    row["status"] = "download_failed"
                    row["error"] = row["error"] or batch_error or "download failed"
                print(
                    f"Completed {row['object_name']} status={row['status']} "
                    f"plate={row['plate']} mjd={row['mjd']} fiber={int(row['fiberid']):04d}",
                    flush=True,
                )
            write_manifest(manifest_path, manifest_rows)

    print(f"Source: {source_label}")
    print(f"SAS cache: {Path(os.environ['SAS_BASE_DIR']).expanduser()}")
    print(f"Manifest: {manifest_path}")
    print(f"Input rows processed: {len(source_rows)}")
    print(f"Spectra resolved: {len(manifest_rows)}")
    if args.dry_run:
        cached = sum(1 for row in manifest_rows if row["status"] == "cached")
        pending = sum(1 for row in manifest_rows if row["status"] == "pending_download")
        print(f"Already cached: {cached}")
        print(f"Needing download: {pending}")
    else:
        available = sum(1 for row in manifest_rows if row["status"] == "downloaded_or_cached")
        print(f"Available locally after run: {available}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
