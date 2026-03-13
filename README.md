# SDSS DR16 Quasar Spectra Pipeline

This repository is built around the spectroscopic quasar catalog `sdssdr16qso.main` in WSDB.

The reproducible workflow is:

1. create a reusable sample CSV
2. download that same CSV with one worker or many workers
3. plot the downloaded spectra from the manifest

The key point is that sampling is separate from downloading. Once a sample CSV exists, you can reuse it for `1`-worker, `4`-worker, dry-run, or plotting tests without changing the object set.

## Repository Layout

- `sample_sdssdr16qso.py`: create a reusable random sample from WSDB
- `download_sdssdr16qso_spectra.py`: single-process downloader for a local sample table or a direct WSDB query
- `parallel_download_sdssdr16qso.py`: multi-worker downloader that reads a prepared sample, stages worker-local downloads, and merges to one final tree
- `plot_sdssdr16qso_spectra.py`: read a download manifest and make PNG plots
- `sample_and_download_sdssdr16qso.py`: older convenience wrapper that combines sampling and download in one step
- `download_sdss_spectra.py`: separate DR19 `allspec`-based downloader
- `data/`: sample CSVs, manifests, worker manifests, and merge reports
- `plots/`: generated PNG plots
- `requirements.txt`: Python dependencies

Committed example data files live under `data/`:

- `data/sample_sdssdr16qso.csv`
- `data/sample_targets.csv`

Common generated files under `data/`:

- `data/random_qso_sample_100.csv`
- `data/random_qso_sample_manifest.csv`
- `data/random_qso_sample_merge_report.csv`
- `data/random_qso_sample_plot_manifest.csv`
- `data/workers/worker_XX.csv`
- `data/workers/worker_XX_manifest.csv`

## Dependencies

Create and populate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The WSDB-backed sampling script requires:

```bash
export PGUSER=your_wsdb_username
export PGHOST=your_wsdb_host
```

Authentication is expected through `~/.pgpass`.

## Main Workflow

### Step 1: Create a reusable sample

```bash
source .venv/bin/activate
python sample_sdssdr16qso.py \
  --sample-size 100 \
  --output data/random_qso_sample_100.csv
```

This script:

- runs a random WSDB query against `sdssdr16qso.main`
- writes exactly one reusable CSV sample
- does not download anything

You can optionally constrain the sample:

```bash
python sample_sdssdr16qso.py \
  --sample-size 100 \
  --where "z > 2.5" \
  --output data/random_qso_sample_highz_100.csv
```

### Step 2a: Download with one worker

```bash
source .venv/bin/activate
python download_sdssdr16qso_spectra.py \
  data/random_qso_sample_100.csv \
  --sas-root ~/data/sdss/spectra/qso/ \
  --manifest data/random_qso_sample_manifest.csv
```

This script:

- reads the prepared sample CSV
- resolves the public DR16 spectrum URLs from `plate`, `mjd`, and `fiberid`
- downloads the FITS files into the SAS-style cache rooted at `--sas-root`
- writes progress and final state to the manifest you specify

### Step 2b: Download with multiple workers and merge

```bash
source .venv/bin/activate
python parallel_download_sdssdr16qso.py \
  data/random_qso_sample_100.csv \
  --workers 4 \
  --worker-batch-size 10 \
  --output-root ~/data/sdss/spectra/qso/ \
  --data-dir data/
```

This script:

- reads the prepared sample CSV
- splits it into disjoint worker chunk CSVs under `data/workers/`
- launches one `download_sdssdr16qso_spectra.py` worker per chunk
- gives each worker its own staging root under `OUTPUT_ROOT/_workers/worker_XX/`
- merges all worker trees into the final unified tree under `OUTPUT_ROOT/`
- deletes each worker staging FITS tree after a successful merge, so spectra are not stored twice
- rewrites the combined manifest at `data/random_qso_sample_manifest.csv`
- writes a merge report at `data/random_qso_sample_merge_report.csv`

Because both runs consume the same input CSV, `--workers 1` and `--workers 4` are directly comparable.

### Step 2c: Reproducible `1` vs `4` worker benchmark

Use separate output and data directories for each run so the timings are not contaminated by cached files from a previous benchmark:

```bash
source .venv/bin/activate
python sample_sdssdr16qso.py \
  --sample-size 100 \
  --output data/random_qso_sample_100.csv

/usr/bin/time -p python parallel_download_sdssdr16qso.py \
  data/random_qso_sample_100.csv \
  --workers 1 \
  --worker-batch-size 10 \
  --output-root /tmp/sdss_benchmark_workers1/output \
  --data-dir /tmp/sdss_benchmark_workers1/data

/usr/bin/time -p python parallel_download_sdssdr16qso.py \
  data/random_qso_sample_100.csv \
  --workers 4 \
  --worker-batch-size 10 \
  --output-root /tmp/sdss_benchmark_workers4/output \
  --data-dir /tmp/sdss_benchmark_workers4/data
```

Then compare the `real` times reported by `/usr/bin/time -p`.

### Step 3: Plot the downloaded spectra

```bash
source .venv/bin/activate
python plot_sdssdr16qso_spectra.py \
  --manifest data/random_qso_sample_manifest.csv \
  --plot-dir plots/ \
  --plot-manifest data/random_qso_sample_plot_manifest.csv
```

This script:

- reads the download manifest
- checks which FITS files are available locally
- extracts `flux` and `loglam` from the spectrum table HDUs
- writes one PNG per spectrum under `plots/`
- writes plotting status information to `data/random_qso_sample_plot_manifest.csv`

## Deterministic Bookkeeping Locations

The scripts use fixed, identifiable file locations.

`sample_sdssdr16qso.py`

- sample CSV: whatever you pass via `--output`

`download_sdssdr16qso_spectra.py`

- download manifest: whatever you pass via `--manifest`
- if omitted, default is `sdssdr16qso_spectra_manifest.csv` in the current directory

`parallel_download_sdssdr16qso.py`

- combined manifest: `DATA_DIR/random_qso_sample_manifest.csv`
- merge report: `DATA_DIR/random_qso_sample_merge_report.csv`
- worker chunk CSVs: `DATA_DIR/workers/worker_XX.csv`
- worker manifests: `DATA_DIR/workers/worker_XX_manifest.csv`
- worker logs: `DATA_DIR/workers/worker_XX.log`
- worker stderr: `DATA_DIR/workers/worker_XX.err`
- worker staging FITS trees live temporarily under `OUTPUT_ROOT/_workers/worker_XX/` during the run and are removed after a successful merge

`plot_sdssdr16qso_spectra.py`

- input manifest default: `data/random_qso_sample_manifest.csv`
- plot manifest default: `data/random_qso_sample_plot_manifest.csv`

## How Spectrum Resolution Works

`sdssdr16qso.main` includes the classical spectroscopic identifiers:

- `plate`
- `mjd`
- `fiberid`

Those are enough to reconstruct DR16 spectrum paths via `sdss_access`.

## Bookkeeping Needed To Build Paths

The downloader only needs the bookkeeping fields that identify one SDSS spectrum product:

- `plate`: spectroscopic plate identifier
- `mjd`: observing Modified Julian Date
- `fiberid`: fiber number on that plate
- `run2d`: reduction version
- `product`: `spec-lite` or `spec`
- `release`: DR16 in this project

Where those values come from:

- `plate`, `mjd`, `fiberid` come directly from `sdssdr16qso.main`
- `product` comes from the command-line option, defaulting to `spec-lite`
- `release` comes from the command-line option, defaulting to `dr16`
- `run2d` is inferred by the script from `mjd`, unless you override it

The other columns pulled from WSDB are not needed to construct the path itself:

- `sdss_name`: used for readable progress logs and plot titles
- `ra`, `dec`: kept in the sample CSV and manifest for bookkeeping
- `z`: kept in the manifest and used in plot titles
- `thing_id`: kept in the sample CSV and manifest as an object identifier

So the path-building chain is:

1. read the prepared sample CSV
2. read `plate`, `mjd`, and `fiberid` from each row
3. infer `run2d` from `mjd`
4. pass `release`, `product`, `run2d`, `plateid`, `mjd`, and `fiberid` to `sdss_access`
5. let `sdss_access` resolve the SAS path and download it into the local SAS-style cache

For example, one sampled row might contribute:

- `plate=6197`
- `mjd=56191`
- `fiberid=962`
- inferred `run2d=v5_13_0`
- `product=spec-lite`
- `release=dr16`

That resolves to a SAS path like:

`dr16/boss/spectro/redux/v5_13_0/spectra/lite/6197/spec-6197-56191-0962.fits`

The catalog spans two reduction families, so the scripts infer `run2d` from `mjd`:

- `mjd < 55176` -> legacy SDSS, `run2d=26`
- `mjd >= 55176` -> BOSS/eBOSS, `run2d=v5_13_0`

Examples:

- legacy SDSS:
  `dr16/sdss/spectro/redux/26/spectra/lite/1887/spec-1887-53239-0253.fits`
- BOSS/eBOSS:
  `dr16/eboss/spectro/redux/v5_13_0/spectra/lite/3586/spec-3586-55181-0756.fits`

You can override the inference logic with:

- `--run2d`
- `--legacy-run2d`
- `--boss-run2d`
- `--boss-start-mjd`

## Download Behavior

By default the downloaders use `sdss_access.Access`, which selects `RsyncAccess` automatically on macOS and Linux. Downloads are queued in batches and committed with `follow_symlinks=False` so the local cache keeps the SAS directory structure.

The default product is `spec-lite`, which is usually the right choice for quick plotting and inspection.

## Script Options

Useful options for `sample_sdssdr16qso.py`:

- `--output data/random_qso_sample_100.csv`: output reusable sample CSV
- `--sample-size 100`: number of random quasars to draw
- `--where "z > 2.5"`: append a predicate to the default WSDB query
- `--query "select ..."`: replace the sampling query completely

The default WSDB sampling query is:

```sql
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
limit 100
```

Useful options for `download_sdssdr16qso_spectra.py`:

- `input_table`: local CSV/FITS/ECSV sample table
- `--manifest data/random_qso_sample_manifest.csv`: output manifest
- `--sas-root ~/data/sdss/spectra/qso/`: root of the local SAS-style cache
- `--download-method access`: use `sdss_access` batch downloads; default and fastest here
- `--download-method http`: sequential direct HTTP fallback
- `--batch-size 10`: number of spectra to queue per `sdss_access` batch
- `--product spec-lite`: small coadded spectra
- `--product spec`: larger full spectra
- `--download-timeout 120`: per-file timeout when `--download-method=http`

Useful options for `parallel_download_sdssdr16qso.py`:

- `input_sample`: reusable sample CSV to split and download
- `--workers 4`: number of worker processes
- `--worker-batch-size 10`: spectra queued per worker batch
- `--output-root ~/data/sdss/spectra/qso/`: final merged SAS tree
- `--data-dir data/`: location for worker chunk CSVs, manifests, logs, and merge report

Useful options for `plot_sdssdr16qso_spectra.py`:

- `--manifest data/random_qso_sample_manifest.csv`: input download manifest
- `--plot-dir plots/`: output directory for PNGs
- `--plot-manifest data/random_qso_sample_plot_manifest.csv`: plotting status table
- `--overwrite`: replace existing PNGs instead of skipping them

The plotting script looks for the first binary-table HDU with:

- `flux`
- `loglam`

It then computes wavelength as `10 ** loglam` and plots flux versus wavelength.

## Notes

- `sample_and_download_sdssdr16qso.py` is still present as a convenience wrapper, but it is not the recommended path for reproducible worker benchmarks because it combines sampling and download in one run.
- `download_sdss_spectra.py` is separate from the DR16 QSO pipeline. It follows the official DR19 `allspec` workflow and is useful for broader SDSS spectrum discovery from positions on the sky.
