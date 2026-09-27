# Email Extraction Project

A data pipeline that extracts emails from Gmail using the Gmail API, loads them into Snowflake, transforms them with dbt, and visualizes the results in Power BI.

## Overview

This project automates the process of pulling email data out of a Gmail account and turning it into analytics-ready tables for reporting. The pipeline has four stages:

1. **Extract** — Pull emails from Gmail via the Gmail API (`gmail_extract.py`)
2. **Load** — Load the extracted data into Snowflake
3. **Transform** — Clean and model the data using dbt
4. **Visualize** — Connect Power BI to Snowflake for dashboards and reporting

```
Gmail API  →  CSV  →  Snowflake (raw)  →  dbt (transformed)  →  Power BI
```

## Prerequisites

- Python 3.8+
- A Google Cloud account with the Gmail API enabled
- A Snowflake account
- dbt (Core or Cloud)
- Power BI Desktop

## Setup

### 1. Gmail Extraction

Install the required Python packages:

```bash
pip install google-auth google-auth-oauthlib google-api-python-client tqdm
```

Set up Gmail API access:

1. Go to the [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project and enable the **Gmail API**
3. Create **OAuth 2.0 credentials** (Desktop app type)
4. Download the credentials file and rename it `credentials.json`
5. Place `credentials.json` in the same directory as `gmail_extract.py`

Run the extraction script:

```bash
python gmail_extract.py
```

On first run, a browser window will open for Google sign-in. Once authenticated, a `token.json` file is saved locally so future runs won't require signing in again.

> ⚠️ **Do not commit `credentials.json` or `token.json` to version control** — add them to your `.gitignore`.

### 2. Snowflake Setup

1. Open Snowflake and create the database and tables by running the DDL statements in [`snowflake_ddl.sql`](./snowflake_ddl.sql)
2. Upload the extracted email CSV (produced by `gmail_extract.py`) into the raw table in Snowflake

### 3. dbt Transformation

1. Connect your dbt project to Snowflake by configuring your `profiles.yml` with your Snowflake account credentials
2. Run the dbt models:

```bash
dbt run
```

### 4. Power BI Reporting

1. Open Power BI Desktop
2. Connect to your Snowflake instance using the Snowflake connector
3. Load the dbt-transformed tables/views to build your dashboards

## Project Structure

```
.
├── gmail_extract.py       # Extracts emails from Gmail via the Gmail API
├── snowflake_ddl.sql      # DDL to create the Snowflake database/tables
├── credentials.json       # Google OAuth credentials (not committed)
├── token.json             # Saved Gmail auth token (not committed)
├── dbt_project/           # dbt models for transforming raw email data
└── README.md
```

## Tech Stack

- **Python** — Gmail API extraction
- **Snowflake** — Data warehouse
- **dbt** — Data transformation
- **Power BI** — Reporting and visualization

## Notes / Roadmap

- [ ] Automate CSV upload to Snowflake (currently manual)
- [ ] Schedule extraction + dbt runs (e.g., via Airflow or a cron job)
- [ ] Add tests for dbt models


