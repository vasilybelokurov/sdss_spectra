#!/usr/bin/env python3
"""Download SDSS spectra for a list of sky positions using DR19 allspec."""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Match a table of targets against the SDSS DR19 allspec catalog and "
            "download the matching spectra with sdss_access."
        )
    )
    parser.add_argument("input_table", help="Input table with RA/Dec columns.")
    parser.add_argument(
        "--input-format",
        default=None,
        help="Optional astropy table format override, for example csv, ascii.tab, ecsv, or fits.",
    )
    parser.add_argument("--ra-column", default="ra", help="RA column name in degrees.")
    parser.add_argument("--dec-column", default="dec", help="Dec column name in degrees.")
    parser.add_argument(
        "--name-column",
        default=None,
        help="Optional object name column. Falls back to target_<row_number>.",
    )
    parser.add_argument(
        "--match-radius",
        type=float,
        default=2.0,
        help="Cross-match radius in arcsec. Default: 2.0",
    )
    parser.add_argument(
        "--release",
        default="dr19",
        help="SDSS public release used by sdss_access. Default: dr19",
    )
    parser.add_argument(
        "--allspec-version",
        default="1.0.1",
        help="allspec catalog version. Default: 1.0.1",
    )
    parser.add_argument(
        "--allspec-file",
        default=None,
        help="Use an existing local allspec FITS file instead of downloading it.",
    )
    parser.add_argument(
        "--sas-root",
        default=None,
        help="Override SAS_BASE_DIR for local caching. Default: let sdss_access choose.",
    )
    parser.add_argument(
        "--file-spec",
        action="append",
        default=[],
        help="Restrict to one or more allspec file_spec values, for example specLite.",
    )
    parser.add_argument(
        "--instrument",
        action="append",
        default=[],
        help="Restrict to one or more allspec instrument values, for example BOSS or APOGEE.",
    )
    parser.add_argument(
        "--coadd",
        action="append",
        default=[],
        help="Restrict to one or more allspec coadd values, for example daily or epoch.",
    )
    parser.add_argument(
        "--manifest",
        default="sdss_spectra_manifest.csv",
        help="Output CSV manifest path. Default: sdss_spectra_manifest.csv",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve matches and write the manifest without downloading spectra.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose sdss_access output.",
    )
    return parser.parse_args()


def import_runtime_dependencies():
    try:
        import numpy as np
        import astropy.units as u
        from astropy.coordinates import SkyCoord
        from astropy.io import fits
        from astropy.table import Table
        from sdss_access import Access
        from sdss_access.path import Path as SdssPath
    except ModuleNotFoundError as exc:
        package = exc.name or "required package"
        raise SystemExit(
            "Missing dependency: "
            f"{package}. Install the project requirements first, for example "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    return np, u, SkyCoord, fits, Table, Access, SdssPath


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


def sas_url_to_local_path(sas_url: str, sas_root: Path) -> Path:
    parsed = urlparse(sas_url)
    marker = "/sas/"
    if marker not in parsed.path:
        raise ValueError(f"Could not map SAS url to a local path: {sas_url}")
    relative_path = parsed.path.split(marker, 1)[1].lstrip("/")
    return sas_root / relative_path


def ensure_allspec_file(
    args: argparse.Namespace,
    access,
    sdss_path,
    local_path_class,
) -> Path:
    if args.allspec_file:
        allspec_file = local_path_class(args.allspec_file).expanduser().resolve()
        if not allspec_file.exists():
            raise SystemExit(f"allspec file not found: {allspec_file}")
        return allspec_file

    allspec_file = local_path_class(
        sdss_path.full("allspec", vers=args.allspec_version, release=args.release)
    ).expanduser()
    if allspec_file.exists():
        return allspec_file

    if args.dry_run:
        raise SystemExit(
            "The allspec catalog is not cached locally. Remove --dry-run or pass "
            "--allspec-file to an existing FITS file."
        )

    access.add("allspec", vers=args.allspec_version, release=args.release)
    access.set_stream()
    access.commit()

    if not allspec_file.exists():
        raise SystemExit(f"allspec download did not produce {allspec_file}")

    return allspec_file


def infer_sas_root(allspec_file: Path, release: str) -> Path:
    env_value = os.environ.get("SAS_BASE_DIR")
    if env_value:
        return Path(env_value).expanduser()

    parts = allspec_file.parts
    if release not in parts:
        raise SystemExit(
            "Could not infer the SAS cache root from the allspec path. Pass --sas-root "
            "or set SAS_BASE_DIR explicitly."
        )

    release_index = parts.index(release)
    return Path(*parts[:release_index])


def filter_row(record, dtype_names: set[str], args: argparse.Namespace) -> bool:
    if "file_spec" in dtype_names and args.file_spec:
        if normalize_string(record["file_spec"]) not in args.file_spec:
            return False
    elif args.file_spec:
        return False
    if "instrument" in dtype_names and args.instrument:
        if normalize_string(record["instrument"]) not in args.instrument:
            return False
    elif args.instrument:
        return False
    if "coadd" in dtype_names and args.coadd:
        if normalize_string(record["coadd"]) not in args.coadd:
            return False
    elif args.coadd:
        return False
    return True


def record_value(record, dtype_names: set[str], column: str, numeric: bool = False):
    if column not in dtype_names:
        return ""
    value = record[column]
    return normalize_number(value) if numeric else normalize_string(value)


def write_manifest(manifest_path: Path, rows: list[dict]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "input_index",
        "object_name",
        "input_ra",
        "input_dec",
        "status",
        "match_sep_arcsec",
        "sdss_id",
        "instrument",
        "telescope",
        "file_spec",
        "coadd",
        "run2d",
        "mjd",
        "plate",
        "fieldid",
        "catalogid",
        "sas_file",
        "sas_url",
        "local_path",
    ]
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()

    if args.sas_root:
        os.environ["SAS_BASE_DIR"] = str(Path(args.sas_root).expanduser())
    elif "SAS_BASE_DIR" not in os.environ:
        os.environ["SAS_BASE_DIR"] = str(
            (Path(tempfile.gettempdir()) / "sdss_spectra_sas_cache").resolve()
        )

    np, u, SkyCoord, fits, Table, Access, SdssPath = import_runtime_dependencies()

    input_table = Path(args.input_table).expanduser().resolve()
    if not input_table.exists():
        raise SystemExit(f"Input table not found: {input_table}")

    targets = (
        Table.read(input_table, format=args.input_format)
        if args.input_format
        else Table.read(input_table)
    )

    missing_columns = [
        column
        for column in (args.ra_column, args.dec_column)
        if column not in targets.colnames
    ]
    if missing_columns:
        raise SystemExit(
            f"Input table is missing required columns: {', '.join(missing_columns)}"
        )
    if args.name_column and args.name_column not in targets.colnames:
        raise SystemExit(f"Input table is missing name column: {args.name_column}")

    access = Access(release=args.release, verbose=args.verbose)
    access.remote()
    sdss_path = SdssPath(release=args.release, verbose=args.verbose)
    allspec_file = ensure_allspec_file(args, access, sdss_path, Path)
    sas_root = infer_sas_root(allspec_file, args.release)

    allspec = fits.getdata(allspec_file, ext=1, memmap=True)
    dtype_names = set(allspec.dtype.names or [])

    if "ra" not in dtype_names or "dec" not in dtype_names or "sdss_id" not in dtype_names:
        raise SystemExit("allspec file does not contain the expected RA/Dec/sdss_id columns.")

    allspec_ra = np.asarray(allspec["ra"], dtype=float)
    allspec_dec = np.asarray(allspec["dec"], dtype=float)
    good = np.isfinite(allspec_ra) & np.isfinite(allspec_dec)
    filtered_allspec = allspec[good]

    if len(filtered_allspec) == 0:
        raise SystemExit("allspec did not contain any rows with finite coordinates.")

    _, unique_index = np.unique(filtered_allspec["sdss_id"], return_index=True)
    unique_rows = filtered_allspec[unique_index]
    catalog_coords = SkyCoord(
        ra=np.asarray(unique_rows["ra"], dtype=float) * u.deg,
        dec=np.asarray(unique_rows["dec"], dtype=float) * u.deg,
    )

    target_coords = SkyCoord(
        ra=np.asarray(targets[args.ra_column], dtype=float) * u.deg,
        dec=np.asarray(targets[args.dec_column], dtype=float) * u.deg,
    )

    match_index, separation, _ = target_coords.match_to_catalog_sky(catalog_coords)
    matched = separation <= (args.match_radius * u.arcsec)

    manifest_rows: list[dict] = []
    queued_files: set[Path] = set()

    for input_index, is_match in enumerate(matched):
        object_name = (
            normalize_string(targets[args.name_column][input_index])
            if args.name_column
            else f"target_{input_index}"
        )
        input_ra = normalize_number(targets[args.ra_column][input_index])
        input_dec = normalize_number(targets[args.dec_column][input_index])
        base_row = {
            "input_index": input_index,
            "object_name": object_name,
            "input_ra": input_ra,
            "input_dec": input_dec,
            "match_sep_arcsec": (
                round(separation[input_index].arcsec, 5) if is_match else ""
            ),
        }

        if not is_match:
            manifest_rows.append(
                {
                    **base_row,
                    "status": "no_match_within_radius",
                    "sdss_id": "",
                    "instrument": "",
                    "telescope": "",
                    "file_spec": "",
                    "coadd": "",
                    "run2d": "",
                    "mjd": "",
                    "plate": "",
                    "fieldid": "",
                    "catalogid": "",
                    "sas_file": "",
                    "sas_url": "",
                    "local_path": "",
                }
            )
            continue

        matched_row = unique_rows[match_index[input_index]]
        matched_sdss_id = matched_row["sdss_id"]
        object_rows = filtered_allspec[filtered_allspec["sdss_id"] == matched_sdss_id]
        object_rows = [record for record in object_rows if filter_row(record, dtype_names, args)]

        if not object_rows:
            manifest_rows.append(
                {
                    **base_row,
                    "status": "no_spectra_after_filters",
                    "sdss_id": normalize_string(matched_sdss_id),
                    "instrument": "",
                    "telescope": "",
                    "file_spec": "",
                    "coadd": "",
                    "run2d": "",
                    "mjd": "",
                    "plate": "",
                    "fieldid": "",
                    "catalogid": "",
                    "sas_file": "",
                    "sas_url": "",
                    "local_path": "",
                }
            )
            continue

        for record in object_rows:
            sas_url = record_value(record, dtype_names, "sas_url")
            if not sas_url:
                continue
            local_path = sas_url_to_local_path(sas_url, sas_root)
            status = "cached" if local_path.exists() else "pending_download"

            if not args.dry_run and not local_path.exists() and local_path not in queued_files:
                access.add_file(str(local_path), input_type="filepath")
                queued_files.add(local_path)

            manifest_rows.append(
                {
                    **base_row,
                    "status": status if args.dry_run else "resolved",
                    "sdss_id": normalize_string(matched_sdss_id),
                    "instrument": record_value(record, dtype_names, "instrument"),
                    "telescope": record_value(record, dtype_names, "telescope"),
                    "file_spec": record_value(record, dtype_names, "file_spec"),
                    "coadd": record_value(record, dtype_names, "coadd"),
                    "run2d": record_value(record, dtype_names, "run2d"),
                    "mjd": record_value(record, dtype_names, "mjd", numeric=True),
                    "plate": record_value(record, dtype_names, "plate", numeric=True),
                    "fieldid": record_value(record, dtype_names, "fieldid", numeric=True),
                    "catalogid": record_value(record, dtype_names, "catalogid"),
                    "sas_file": record_value(record, dtype_names, "sas_file"),
                    "sas_url": sas_url,
                    "local_path": str(local_path),
                }
            )

    if queued_files:
        access.set_stream()
        access.commit()

    for row in manifest_rows:
        local_path = row["local_path"]
        if not local_path:
            continue
        file_exists = Path(local_path).exists()
        if args.dry_run:
            continue
        row["status"] = "downloaded_or_cached" if file_exists else "download_failed"

    manifest_path = Path(args.manifest).expanduser().resolve()
    write_manifest(manifest_path, manifest_rows)

    matched_targets = int(np.sum(matched))
    targets_with_spectra = len(
        {
            row["input_index"]
            for row in manifest_rows
            if row["status"] not in {"no_match_within_radius", "no_spectra_after_filters"}
        }
    )
    downloaded_files = sum(1 for row in manifest_rows if row["status"] == "downloaded_or_cached")

    print(f"allspec file: {allspec_file}")
    print(f"SAS cache: {sas_root}")
    print(f"Manifest: {manifest_path}")
    print(f"Targets processed: {len(targets)}")
    print(f"Targets with an SDSS match: {matched_targets}")
    print(f"Targets with spectra after filters: {targets_with_spectra}")
    if args.dry_run:
        pending_files = sum(1 for row in manifest_rows if row["status"] == "pending_download")
        print(f"Matched spectra already cached: {sum(1 for row in manifest_rows if row['status'] == 'cached')}")
        print(f"Matched spectra needing download: {pending_files}")
    else:
        print(f"Spectra available locally after run: {downloaded_files}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
