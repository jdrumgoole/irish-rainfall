# Irish Rainfall

Analysis tools and interactive dashboard for the Long-term Island of Ireland Precipitation (IIP) network dataset (1850-2010).

## Dataset

The dataset contains monthly rainfall measurements from 25 weather stations across Ireland,
spanning 161 years from 1850 to 2010. Data is sourced from [Met Éireann](https://www.met.ie/).

## Reference

If you use this dataset, please cite:

> Mateus, C.; Potito, A.; Curley, M. 2020. Reconstruction of a long-term historical daily maximum and minimum air temperature network dataset for Ireland (1831-1968). *Geoscience Data Journal*. http://dx.doi.org/10.1002/gdj3.92

## Installation

```bash
uv sync
```

## Quick Start

Import the rainfall data (downloads automatically from Met Éireann):

```bash
invoke import-data
```

Start the dashboard:

```bash
invoke start
```

Then open http://127.0.0.1:8000 in your browser.

## Usage

### Import Data

Download and import data from Met Éireann (default):

```bash
invoke import-data
```

Or import from a local directory:

```bash
invoke import-data --source /path/to/csv/directory
```

Force re-import (overwrites existing database):

```bash
invoke import-data --force
```

### Dashboard Server

Start the server:

```bash
invoke start
```

Start on a different port:

```bash
invoke start --port 8080
```

Check server status:

```bash
invoke status
```

Stop the server:

```bash
invoke stop
```

### Database Info

View database statistics:

```bash
invoke db-info
```

## Dashboard Features

- **Interactive Map**: 25 stations on a Leaflet map, color-coded by average rainfall
- **Time Series Chart**: Annual rainfall trends with configurable moving averages
- **Monthly Climatology Heatmap**: Average rainfall by station and month
- **Period Comparison**: Compare rainfall between any two time periods
- **Seasonal Analysis**: Winter/Spring/Summer/Autumn breakdown by station

## API Endpoints

The dashboard includes a REST API:

- `GET /api/stations` - All station metadata
- `GET /api/rainfall/annual` - Annual totals
- `GET /api/rainfall/monthly` - Monthly data
- `GET /api/rainfall/seasonal` - Seasonal averages
- `GET /api/rainfall/climatology` - Monthly climatology
- `GET /api/rainfall/station-summary` - Summary stats per station
- `GET /api/rainfall/trends` - National trends with moving average
- `GET /api/rainfall/anomalies` - Departures from baseline
- `GET /api/rainfall/comparison` - Compare two periods

## Data Directory Structure

After running `invoke import-data`, the data directory contains:

```
data/
├── rainfall.db          # SQLite database with imported data
└── raw/                  # Raw data files from Met Éireann
    ├── *.csv             # 25 station CSV files + national series
    ├── IIP_station_metadata.pdf  # Station metadata documentation
    └── readme.txt        # Original dataset readme
```

## Data Source

Data is downloaded from Met Éireann:
https://www.met.ie/cms/assets/uploads/2018/01/Long-Term-IIP-network-1.zip
