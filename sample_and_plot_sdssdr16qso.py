#!/usr/bin/env python3
"""Sample DR16 quasars from WSDB, download spectra, and plot them."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
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
            "Draw a random sample from sdssdr16qso.main, download the spectra, "
            "and render each spectrum to a PNG."
        )
    )
    parser.add_argument(
        "--output-root",
        default="~/data/sdss/spectra/qso/",
        help="Root directory for sampled rows, downloaded spectra, manifests, and plots.",
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
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip the download step and only plot already-cached spectra.",
    )
    return parser.parse_args()


def import_runtime_dependencies():
    try:
        import matplotlib.pyplot as plt
        import psycopg2
        import psycopg2.extras
        from astropy.io import fits
        from sdss_access.path import Path as SdssPath
    except ModuleNotFoundError as exc:
        package = exc.name or "required package"
        raise SystemExit(
            "Missing dependency: "
            f"{package}. Install the project requirements first, for example "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    return plt, psycopg2, psycopg2.extras, fits, SdssPath


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


def ensure_output_dirs(root: Path) -> dict[str, Path]:
    paths = {
        "root": root,
        "plots": root / "plots",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


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
        "spectrum_path",
        "plot_path",
        "status",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def plot_spectrum(plt, fits, spectrum_path: Path, plot_path: Path, title: str) -> None:
    with fits.open(spectrum_path) as hdul:
        coadd = None
        for hdu in hdul[1:]:
            if getattr(hdu.data, "dtype", None) is None:
                continue
            names = {name.lower() for name in (hdu.data.dtype.names or [])}
            if {"flux", "loglam"}.issubset(names):
                coadd = hdu.data
                break
        if coadd is None:
            raise ValueError(f"Could not find a flux/loglam table in {spectrum_path}")
        wavelength = 10 ** coadd["loglam"]
        flux = coadd["flux"]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(wavelength, flux, color="black", linewidth=0.8)
    ax.set_xlabel("Wavelength [Angstrom]")
    ax.set_ylabel("Flux")
    ax.set_title(title)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


def main() -> int:
    args = parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    os.environ["SAS_BASE_DIR"] = str(output_root)
    os.environ.setdefault(
        "XDG_CACHE_HOME",
        str((Path(tempfile.gettempdir()) / "sdss_spectra_xdg_cache").resolve()),
    )
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault(
        "MPLCONFIGDIR",
        str((Path(tempfile.gettempdir()) / "sdss_spectra_mpl_cache").resolve()),
    )

    plt, psycopg2, extras, fits, SdssPath = import_runtime_dependencies()
    dirs = ensure_output_dirs(output_root)

    query = build_query(args.sample_size, args.where, args.query)
    rows = fetch_rows(psycopg2, extras, query)
    if not rows:
        raise SystemExit("The sampling query returned no rows.")

    sample_csv = dirs["root"] / f"random_qso_sample_{len(rows)}.csv"
    write_sample_csv(sample_csv, rows)

    sdss_path = SdssPath(release=args.release, verbose=args.verbose)

    manifest_rows: list[dict] = []
    queued: set[Path] = set()
    for row in rows:
        run2d = infer_run2d(args, row)
        spectrum_path = resolve_local_path(sdss_path, args.product, row, args)
        remote_url = resolve_remote_url(sdss_path, args.product, row, args)
        plot_path = dirs["plots"] / f"{row['plate']}-{row['mjd']}-{int(row['fiberid']):04d}.png"

        if not args.skip_download and not spectrum_path.exists() and spectrum_path not in queued:
            try:
                download_file(remote_url, spectrum_path, timeout=args.download_timeout)
            except Exception as exc:
                error = str(exc)
            else:
                error = ""
            queued.add(spectrum_path)
        else:
            error = ""

        manifest_rows.append(
            {
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
                "spectrum_path": str(spectrum_path),
                "plot_path": str(plot_path),
                "status": "resolved",
                "error": error,
            }
        )

    downloaded = 0
    plotted = 0
    for row in manifest_rows:
        spectrum_path = Path(row["spectrum_path"])
        plot_path = Path(row["plot_path"])
        if not spectrum_path.exists():
            row["status"] = "download_failed"
            continue
        downloaded += 1
        try:
            plot_spectrum(
                plt,
                fits,
                spectrum_path,
                plot_path,
                title=f"{row['sdss_name']}  z={row['z']:.4f}",
            )
        except Exception as exc:
            row["status"] = "plot_failed"
            row["error"] = str(exc)
            continue
        plotted += 1
        row["status"] = "downloaded_and_plotted"

    manifest_path = dirs["root"] / "random_qso_sample_manifest.csv"
    write_manifest(manifest_path, manifest_rows)

    print(f"Output root: {dirs['root']}")
    print(f"Sample CSV: {sample_csv}")
    print(f"Manifest: {manifest_path}")
    print(f"Rows sampled: {len(rows)}")
    print(f"Spectra available locally: {downloaded}")
    print(f"PNG plots written: {plotted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
