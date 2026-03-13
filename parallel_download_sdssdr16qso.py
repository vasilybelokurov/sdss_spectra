#!/usr/bin/env python3
"""Download a prepared DR16 QSO sample with worker-local roots, then merge."""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sample_and_download_sdssdr16qso import ensure_data_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a prepared DR16 QSO sample, split it into chunks, download with "
            "multiple worker-local SAS roots, and merge to one final tree."
        )
    )
    parser.add_argument("input_sample", help="CSV sample file to download.")
    parser.add_argument(
        "--output-root",
        default="~/data/sdss/spectra/qso/",
        help="Final merged root directory for downloaded spectra. Default: ~/data/sdss/spectra/qso/",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory for sampled catalogs, manifests, and worker state. Default: ./data",
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--worker-batch-size", type=int, default=10)
    parser.add_argument("--product", choices=["spec", "spec-lite"], default="spec-lite")
    parser.add_argument("--run2d", default=None)
    parser.add_argument("--boss-run2d", default="v5_13_0")
    parser.add_argument("--legacy-run2d", default="26")
    parser.add_argument("--boss-start-mjd", type=int, default=55176)
    parser.add_argument("--release", default="dr16")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--download-method",
        choices=["access", "http"],
        default="access",
        help="Download backend used inside each worker. Default: access",
    )
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds when --download-method=http. Default: 120",
    )
    return parser.parse_args()


def chunk_rows(rows: list[dict], workers: int) -> list[list[dict]]:
    return [rows[index::workers] for index in range(workers)]


def read_sample_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_chunk_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["sdss_name", "ra", "dec", "plate", "mjd", "fiberid", "z", "thing_id"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def read_manifest(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
        "download_status",
        "download_error",
        "worker_id",
        "worker_root",
        "merge_status",
        "merge_note",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_merge_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["worker_id", "worker_root", "merged", "skipped_existing", "conflicts"],
        )
        writer.writeheader()
        writer.writerows(rows)


def build_worker_command(
    args: argparse.Namespace,
    worker_input: Path,
    worker_manifest: Path,
    worker_output_root: Path,
) -> list[str]:
    cmd = [
        sys.executable,
        "download_sdssdr16qso_spectra.py",
        str(worker_input),
        "--manifest",
        str(worker_manifest),
        "--sas-root",
        str(worker_output_root),
        "--release",
        args.release,
        "--product",
        args.product,
        "--download-method",
        args.download_method,
        "--batch-size",
        str(args.worker_batch_size),
        "--download-timeout",
        str(args.download_timeout),
        "--boss-run2d",
        args.boss_run2d,
        "--legacy-run2d",
        args.legacy_run2d,
        "--boss-start-mjd",
        str(args.boss_start_mjd),
    ]
    if args.run2d:
        cmd.extend(["--run2d", args.run2d])
    if args.verbose:
        cmd.append("--verbose")
    return cmd


def merge_worker_tree(worker_root: Path, final_root: Path) -> dict[str, int]:
    merged = 0
    skipped_existing = 0
    conflicts = 0

    for source in sorted(path for path in worker_root.rglob("*") if path.is_file()):
        relative_path = source.relative_to(worker_root)
        destination = final_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.stat().st_size == source.stat().st_size:
                skipped_existing += 1
                source.unlink()
            else:
                conflicts += 1
            continue
        shutil.move(str(source), str(destination))
        merged += 1

    shutil.rmtree(worker_root, ignore_errors=True)
    return {
        "merged": merged,
        "skipped_existing": skipped_existing,
        "conflicts": conflicts,
    }


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")

    data_dir = Path(args.data_dir).expanduser().resolve()
    workers_dir = data_dir / "workers"
    output_root = Path(args.output_root).expanduser().resolve()
    worker_roots_dir = output_root / "_workers"
    os.environ["SAS_BASE_DIR"] = str(output_root)
    ensure_data_dir(data_dir)
    ensure_data_dir(workers_dir)
    ensure_data_dir(output_root)
    ensure_data_dir(worker_roots_dir)
    combined_manifest = data_dir / "random_qso_sample_manifest.csv"
    merge_report = data_dir / "random_qso_sample_merge_report.csv"
    write_manifest(combined_manifest, [])
    write_merge_report(merge_report, [])

    input_sample = Path(args.input_sample).expanduser().resolve()
    if not input_sample.exists():
        raise SystemExit(f"Input sample not found: {input_sample}")

    rows = read_sample_csv(input_sample)
    if not rows:
        raise SystemExit(f"The input sample is empty: {input_sample}")

    sample_copy = data_dir / input_sample.name
    write_chunk_csv(sample_copy, rows)
    print(f"Input sample: {input_sample}", flush=True)
    print(f"Working sample copy: {sample_copy}", flush=True)
    print(f"Final output root: {output_root}", flush=True)
    print(f"Worker staging root: {worker_roots_dir}", flush=True)
    print(f"Combined manifest: {combined_manifest}", flush=True)
    print(f"Merge report: {merge_report}", flush=True)

    chunks = [chunk for chunk in chunk_rows(rows, args.workers) if chunk]
    print(f"Launching {len(chunks)} worker(s)", flush=True)

    processes: list[tuple[int, subprocess.Popen, Path, Path, Path, Path]] = []
    for worker_number, chunk in enumerate(chunks, start=1):
        worker_input = workers_dir / f"worker_{worker_number:02d}.csv"
        worker_manifest = workers_dir / f"worker_{worker_number:02d}_manifest.csv"
        worker_log = workers_dir / f"worker_{worker_number:02d}.log"
        worker_err = workers_dir / f"worker_{worker_number:02d}.err"
        worker_tmp = workers_dir / f"worker_{worker_number:02d}_tmp"
        worker_output_root = worker_roots_dir / f"worker_{worker_number:02d}"
        write_chunk_csv(worker_input, chunk)
        worker_tmp.mkdir(parents=True, exist_ok=True)
        worker_output_root.mkdir(parents=True, exist_ok=True)
        cmd = build_worker_command(args, worker_input, worker_manifest, worker_output_root)
        worker_env = os.environ.copy()
        worker_env["TMPDIR"] = str(worker_tmp)
        with worker_log.open("w", encoding="utf-8") as log_handle, worker_err.open(
            "w", encoding="utf-8"
        ) as err_handle:
            process = subprocess.Popen(
                cmd,
                cwd=Path(__file__).resolve().parent,
                stdout=log_handle,
                stderr=err_handle,
                env=worker_env,
            )
        print(
            f"Worker {worker_number}: {len(chunk)} rows, output={worker_output_root}, "
            f"manifest={worker_manifest}, log={worker_log}, tmp={worker_tmp}",
            flush=True,
        )
        processes.append(
            (worker_number, process, worker_manifest, worker_log, worker_err, worker_output_root)
        )

    failed_workers: list[int] = []
    combined_rows: list[dict] = []
    for worker_number, process, worker_manifest, worker_log, worker_err, worker_output_root in processes:
        return_code = process.wait()
        if return_code != 0:
            failed_workers.append(worker_number)
        if worker_manifest.exists():
            worker_rows = read_manifest(worker_manifest)
            for row in worker_rows:
                combined_rows.append(
                    {
                        "sdss_name": row.get("object_name", ""),
                        "ra": row.get("ra", ""),
                        "dec": row.get("dec", ""),
                        "z": row.get("z", ""),
                        "thing_id": "",
                        "plate": row.get("plate", ""),
                        "mjd": row.get("mjd", ""),
                        "fiberid": row.get("fiberid", ""),
                        "run2d": row.get("run2d", ""),
                        "product": row.get("product", ""),
                        "remote_url": row.get("remote_url", ""),
                        "spectrum_path": row.get("local_path", ""),
                        "download_status": row.get("status", ""),
                        "download_error": row.get("error", ""),
                        "worker_id": worker_number,
                        "worker_root": str(worker_output_root),
                        "merge_status": "pending",
                        "merge_note": "",
                    }
                )
        print(
            f"Worker {worker_number} finished with return code {return_code}. "
            f"log={worker_log} err={worker_err}",
            flush=True,
        )

    if failed_workers:
        write_manifest(combined_manifest, combined_rows)
        raise SystemExit(
            f"Worker failures detected: {', '.join(str(worker) for worker in failed_workers)}"
        )

    merge_report_rows: list[dict] = []
    for worker_number, _, _, _, _, worker_output_root in processes:
        merge_result = merge_worker_tree(worker_output_root, output_root)
        merge_report_rows.append(
            {
                "worker_id": worker_number,
                "worker_root": str(worker_output_root),
                **merge_result,
            }
        )
        print(
            f"Merged worker {worker_number}: merged={merge_result['merged']} "
            f"skipped_existing={merge_result['skipped_existing']} conflicts={merge_result['conflicts']}",
            flush=True,
        )

    for row in combined_rows:
        worker_root = Path(row["worker_root"])
        spectrum_path = Path(row["spectrum_path"])
        if row["download_status"] not in {"downloaded_or_cached", "downloaded", "cached"}:
            row["merge_status"] = "not_downloaded"
            row["merge_note"] = row["download_error"]
            continue
        relative_path = spectrum_path.relative_to(worker_root)
        final_path = output_root / relative_path
        row["spectrum_path"] = str(final_path)
        if final_path.exists():
            row["merge_status"] = "merged"
            row["merge_note"] = ""
        else:
            row["merge_status"] = "missing_after_merge"
            row["merge_note"] = "expected merged file not found"

    write_manifest(combined_manifest, combined_rows)
    print(f"Combined manifest: {combined_manifest}", flush=True)

    write_merge_report(merge_report, merge_report_rows)
    print(f"Merge report: {merge_report}", flush=True)

    print("All workers completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
