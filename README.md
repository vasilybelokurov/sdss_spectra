# SDSS DR16 Quasar Spectra Pipeline

This repository builds a practical download pipeline for spectra from the spectroscopic quasar catalog `sdssdr16qso.main`.

The primary script:

- draws a random sample of quasars from WSDB
- resolves the SDSS DR16 spectrum path for each object
- downloads the FITS spectra into a user-chosen folder
- plots each downloaded spectrum as a PNG
- writes CSV manifests for the sampled objects and run status

The default output location is:

```text
~/data/sdss/spectra/qso/
```

That keeps the SDSS cache, sample table, manifest, and PNG plots together in one place outside the repository.

## Repository Layout

- `sample_and_plot_sdssdr16qso.py`: end-to-end workflow for random sampling, downloading, and plotting
- `download_sdssdr16qso_spectra.py`: lower-level downloader for any local export or direct WSDB query against `sdssdr16qso.main`
- `download_sdss_spectra.py`: separate DR19 `allspec`-based downloader kept for broader SDSS discovery workflows
- `sample_sdssdr16qso.csv`: tiny local example table for the DR16 downloader
- `sample_targets.csv`: tiny sky-position example for the generic DR19 script
- `requirements.txt`: Python dependencies

## Dependencies

Create and populate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The DR16 QSO workflow needs direct access to WSDB. These environment variables must be set:

```bash
export PGUSER=your_wsdb_username
export PGHOST=your_wsdb_host
```

Authentication is expected through `~/.pgpass`.

## Quick Start

Run the full workflow for a random sample of 100 quasars:

```bash
source .venv/bin/activate
python sample_and_plot_sdssdr16qso.py \
  --sample-size 100 \
  --output-root ~/data/sdss/spectra/qso/
```

This uses `spec-lite` by default, because those files are smaller and already contain the coadded spectrum needed for quick plotting.

Typical output under `~/data/sdss/spectra/qso/`:

- `random_qso_sample_100.csv`: the sampled rows from `sdssdr16qso.main`
- `random_qso_sample_manifest.csv`: per-object status, local FITS path, local PNG path, and any error message
- `plots/*.png`: one PNG per successfully plotted spectrum
- `dr16/...`: downloaded spectra stored in SDSS directory layout

## How The Main Script Works

### 1. Query a random sample from WSDB

By default the script runs:

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

You can modify the selection in two ways:

- `--where "z > 2.5"` appends an extra predicate to the default query
- `--query "select ..."` replaces the query entirely

### 2. Resolve the DR16 spectrum location

Each row in `sdssdr16qso.main` includes the classical spectroscopic identifiers:

- `plate`
- `mjd`
- `fiberid`

Those are enough to reconstruct DR16 spectrum paths with `sdss_access`.

The catalog spans two reduction families, so the scripts infer `run2d` from `mjd`:

- `mjd < 55176` -> legacy SDSS, `run2d=26`
- `mjd >= 55176` -> BOSS/eBOSS, `run2d=v5_13_0`

Examples:

- legacy SDSS:
  `dr16/sdss/spectro/redux/26/spectra/lite/1887/spec-1887-53239-0253.fits`
- BOSS/eBOSS:
  `dr16/eboss/spectro/redux/v5_13_0/spectra/lite/3586/spec-3586-55181-0756.fits`

If you need a different reduction choice, use:

- `--run2d`
- `--legacy-run2d`
- `--boss-run2d`
- `--boss-start-mjd`

### 3. Download the FITS files

The end-to-end script sets `SAS_BASE_DIR` to the chosen `--output-root`, so the local files are written directly inside your requested destination tree.

Downloads happen via the resolved public SDSS URLs. The timeout is configurable:

```bash
--download-timeout 120
```

If a download fails or times out, the script keeps going and records the failure in `random_qso_sample_manifest.csv`.

### 4. Plot each spectrum

For each downloaded FITS file, the script searches the binary table HDUs for columns named:

- `flux`
- `loglam`

It then computes:

- wavelength = `10 ** loglam`
- flux = `flux`

and writes a PNG plot for that spectrum to the `plots/` directory.

Plot failures are also recorded in the manifest so partial runs remain inspectable.

## Important Options

For `sample_and_plot_sdssdr16qso.py`:

- `--output-root ~/data/sdss/spectra/qso/`: choose where spectra, plots, and manifests are written
- `--sample-size 100`: choose the random sample size
- `--product spec-lite`: small coadded spectra for quick work
- `--product spec`: larger full spectra
- `--where "z > 2.5"`: restrict the sample
- `--query "select ..."`: replace the sampling query completely
- `--download-timeout 120`: per-file HTTP timeout in seconds
- `--skip-download`: only make PNGs for spectra already present locally

For `download_sdssdr16qso_spectra.py`:

- `sample_sdssdr16qso.csv`: use a local export as input
- `--from-wsdb`: query `sdssdr16qso.main` directly
- `--include-duplicates`: also try duplicate plate/mjd/fiber combinations
- `--manifest path.csv`: choose the downloader manifest path
- `--dry-run`: resolve paths without downloading files

## Lower-Level Downloader Examples

Download from a local CSV export:

```bash
source .venv/bin/activate
python download_sdssdr16qso_spectra.py sample_sdssdr16qso.csv
```

Download directly from WSDB:

```bash
source .venv/bin/activate
python download_sdssdr16qso_spectra.py \
  --from-wsdb \
  --where "z > 2.5" \
  --limit 100
```

Resolve paths only:

```bash
source .venv/bin/activate
python download_sdssdr16qso_spectra.py \
  --from-wsdb \
  --limit 10 \
  --dry-run
```

## Notes On The Generic DR19 Script

`download_sdss_spectra.py` is a separate path that follows the official DR19 `allspec` workflow. It is useful when you want broad SDSS spectrum discovery from positions on the sky, but it is not required for the DR16 quasar pipeline built around `sdssdr16qso.main`.
