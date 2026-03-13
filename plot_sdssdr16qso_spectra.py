#!/usr/bin/env python3
"""Plot downloaded DR16 QSO spectra listed in a manifest."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot spectra listed in a DR16 QSO download manifest."
    )
    parser.add_argument(
        "--manifest",
        default="data/random_qso_sample_manifest.csv",
        help="Download manifest created by sample_and_download_sdssdr16qso.py.",
    )
    parser.add_argument(
        "--plot-dir",
        default="plots",
        help="Directory for PNG plots. Default: ./plots",
    )
    parser.add_argument(
        "--plot-manifest",
        default="data/random_qso_sample_plot_manifest.csv",
        help="Output CSV manifest for the plotting pass.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing PNGs instead of skipping them.",
    )
    return parser.parse_args()


def import_runtime_dependencies():
    try:
        import matplotlib.pyplot as plt
        from astropy.io import fits
    except ModuleNotFoundError as exc:
        package = exc.name or "required package"
        raise SystemExit(
            "Missing dependency: "
            f"{package}. Install the project requirements first, for example "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    return plt, fits


def read_manifest(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


def format_title(row: dict) -> str:
    z_value = row.get("z", "")
    try:
        z_text = f"{float(z_value):.4f}"
    except (TypeError, ValueError):
        z_text = str(z_value)
    return f"{row.get('sdss_name', 'unknown')}  z={z_text}"


def write_plot_manifest(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    plot_dir = Path(args.plot_dir).expanduser().resolve()
    plot_manifest_path = Path(args.plot_manifest).expanduser().resolve()

    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    plot_dir.mkdir(parents=True, exist_ok=True)
    plt, fits = import_runtime_dependencies()
    rows = read_manifest(manifest_path)
    write_plot_manifest(plot_manifest_path, [])
    print(f"Input manifest: {manifest_path}", flush=True)
    print(f"Progress manifest: {plot_manifest_path}", flush=True)
    print(f"Starting plots for {len(rows)} spectra", flush=True)

    plot_rows: list[dict] = []
    plotted = 0
    missing = 0
    failed = 0
    skipped = 0

    for index, row in enumerate(rows, start=1):
        spectrum_path = Path(row["spectrum_path"]).expanduser().resolve()
        plot_path = plot_dir / f"{int(row['plate'])}-{int(row['mjd'])}-{int(row['fiberid']):04d}.png"
        print(
            f"[{index}/{len(rows)}] starting {row.get('sdss_name', 'unknown')}",
            flush=True,
        )

        plot_row = dict(row)
        plot_row["plot_path"] = str(plot_path)
        plot_row["plot_status"] = ""
        plot_row["plot_error"] = ""

        if not spectrum_path.exists():
            plot_row["plot_status"] = "missing_spectrum"
            missing += 1
            plot_rows.append(plot_row)
            write_plot_manifest(plot_manifest_path, plot_rows)
            print(
                f"[{index}/{len(rows)}] missing_spectrum {row.get('sdss_name', 'unknown')}",
                flush=True,
            )
            continue

        if plot_path.exists() and not args.overwrite:
            plot_row["plot_status"] = "skipped_existing"
            skipped += 1
            plot_rows.append(plot_row)
            write_plot_manifest(plot_manifest_path, plot_rows)
            print(
                f"[{index}/{len(rows)}] skipped_existing {row.get('sdss_name', 'unknown')}",
                flush=True,
            )
            continue

        try:
            plot_spectrum(plt, fits, spectrum_path, plot_path, format_title(row))
        except Exception as exc:
            plot_row["plot_status"] = "plot_failed"
            plot_row["plot_error"] = str(exc)
            failed += 1
        else:
            plot_row["plot_status"] = "plotted"
            plotted += 1

        plot_rows.append(plot_row)
        write_plot_manifest(plot_manifest_path, plot_rows)
        print(
            f"[{index}/{len(rows)}] {plot_row['plot_status']} {row.get('sdss_name', 'unknown')}",
            flush=True,
        )

    print(f"Input manifest: {manifest_path}")
    print(f"Plot dir: {plot_dir}")
    print(f"Plot manifest: {plot_manifest_path}")
    print(f"Rows read: {len(rows)}")
    print(f"Plotted this run: {plotted}")
    print(f"Skipped existing plots: {skipped}")
    print(f"Missing spectra: {missing}")
    print(f"Plot failures: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
