# SDSS DR16 Quasar Spectra Pipeline

This repository is built around the spectroscopic quasar catalog `sdssdr16qso.main` in WSDB.

The workflow is intentionally split into two scripts:

1. `sample_and_download_sdssdr16qso.py`
   Draw a random sample from `sdssdr16qso.main`, save the sampled catalog under `data/`, and download the spectra to a user-defined external folder.
2. `plot_sdssdr16qso_spectra.py`
   Read the download manifest from `data/` and render the downloaded spectra to PNGs under `plots/`.

This keeps the large FITS files out of the repository while keeping the catalog products and quick-look figures in the working tree.

## Repository Layout

- `sample_and_download_sdssdr16qso.py`: sample 100 quasars from WSDB and download spectra
- `plot_sdssdr16qso_spectra.py`: read a download manifest and make PNG plots
- `download_sdssdr16qso_spectra.py`: lower-level DR16 QSO downloader for local tables or direct WSDB queries
- `download_sdss_spectra.py`: separate DR19 `allspec`-based downloader
- `data/`: small example catalogs and generated sample/manifests
- `plots/`: generated PNG plots
- `requirements.txt`: Python dependencies

Committed example data files live under `data/`:

- `data/sample_sdssdr16qso.csv`
- `data/sample_targets.csv`

Generated files also default to `data/`:

- `data/random_qso_sample_100.csv`
- `data/random_qso_sample_manifest.csv`
- `data/random_qso_sample_plot_manifest.csv`

## Dependencies

Create and populate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The WSDB-backed scripts require:

```bash
export PGUSER=your_wsdb_username
export PGHOST=your_wsdb_host
```

Authentication is expected through `~/.pgpass`.

## Main Workflow

### Step 1: Sample 100 quasars and download spectra

```bash
source .venv/bin/activate
python sample_and_download_sdssdr16qso.py \
  --sample-size 100 \
  --output-root ~/data/sdss/spectra/qso/ \
  --data-dir data/
```

What this does:

- runs a random WSDB query against `sdssdr16qso.main`
- writes the sampled rows to `data/random_qso_sample_100.csv`
- resolves the public DR16 spectrum URLs from `plate`, `mjd`, and `fiberid`
- downloads the FITS files to `~/data/sdss/spectra/qso/`
- writes a per-object download manifest to `data/random_qso_sample_manifest.csv`

By default the downloader uses `sdss_access.Access`, which selects `RsyncAccess` automatically on macOS and Linux. Downloads are queued in batches and committed with `follow_symlinks=False` so the local cache keeps the SAS directory structure.

The default product is `spec-lite`, which is usually the right choice for quick plotting and inspection.

### Step 2: Plot the downloaded spectra

```bash
source .venv/bin/activate
python plot_sdssdr16qso_spectra.py \
  --manifest data/random_qso_sample_manifest.csv \
  --plot-dir plots/ \
  --plot-manifest data/random_qso_sample_plot_manifest.csv
```

What this does:

- reads the download manifest
- checks which FITS files are available locally
- extracts `flux` and `loglam` from the spectrum table HDUs
- writes one PNG per spectrum under `plots/`
- writes plotting status information to `data/random_qso_sample_plot_manifest.csv`

## How Spectrum Resolution Works

`sdssdr16qso.main` includes the classical spectroscopic identifiers:

- `plate`
- `mjd`
- `fiberid`

Those are enough to reconstruct DR16 spectrum paths via `sdss_access`.

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

## Download Script Options

Useful options for `sample_and_download_sdssdr16qso.py`:

- `--output-root ~/data/sdss/spectra/qso/`: where the FITS spectra are stored
- `--data-dir data/`: where sampled catalogs and manifests are written
- `--sample-size 100`: number of random quasars to draw
- `--download-method access`: use `sdss_access` batch downloads; this is the default and fastest option here
- `--download-method http`: fall back to sequential direct HTTP downloads
- `--batch-size 10`: number of spectra to queue per `sdss_access` batch
- `--product spec-lite`: small coadded spectra
- `--product spec`: larger full spectra
- `--where "z > 2.5"`: append a predicate to the default WSDB query
- `--query "select ..."`: replace the sampling query completely
- `--download-timeout 120`: per-file timeout when `--download-method=http`

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

## Plot Script Options

Useful options for `plot_sdssdr16qso_spectra.py`:

- `--manifest data/random_qso_sample_manifest.csv`: input download manifest
- `--plot-dir plots/`: output directory for PNGs
- `--plot-manifest data/random_qso_sample_plot_manifest.csv`: plotting status table
- `--overwrite`: replace existing PNGs instead of skipping them

The plotting script looks for the first binary-table HDU with:

- `flux`
- `loglam`

It then computes wavelength as `10 ** loglam` and plots flux versus wavelength.

## Lower-Level Downloader

`download_sdssdr16qso_spectra.py` remains available when you want to work from a local export of `sdssdr16qso.main` or run a direct WSDB query without the random-sampling wrapper.

Example with the committed sample CSV:

```bash
source .venv/bin/activate
python download_sdssdr16qso_spectra.py data/sample_sdssdr16qso.csv
```

Direct WSDB example:

```bash
source .venv/bin/activate
python download_sdssdr16qso_spectra.py \
  --from-wsdb \
  --where "z > 2.5" \
  --limit 100
```

## DR19 Script

`download_sdss_spectra.py` is separate from the DR16 QSO pipeline. It follows the official DR19 `allspec` workflow and is useful for broader SDSS spectrum discovery from positions on the sky.
