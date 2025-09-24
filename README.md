# Oracle Metadata Connector

This project provides a Python service and companion notebook that connect to an Oracle 19c database using Kerberos authentication and expose REST APIs for fetching schema metadata that conforms to the [WrenMDL manifest schema](https://docs.getwren.ai/oss/engine/concept/what_is_mdl).

The service can:

* Connect to Oracle using Kerberos / external authentication (no database password required).
* Inspect one or more tables within a schema.
* Filter out empty tables or tables without activity in the last N months (defaults to six months).
* Generate WrenMDL-compatible metadata for individual tables, selected groups, or the entire filtered result set.

The repository also includes a Jupyter notebook that demonstrates basic interactions with the metadata service for local experiments.

## Project structure

```
.
├── app/
│   ├── config.py            # Environment-driven settings
│   ├── db.py                # Oracle client wrapper with Kerberos support
│   ├── main.py              # FastAPI application exposing REST endpoints
│   └── services/
│       ├── metadata.py      # Core metadata/usage filtering logic
│       └── models.py        # Dataclasses shared by the service & API
├── notebooks/
│   └── metadata_demo.ipynb  # Notebook for interactive testing
├── tests/
│   └── test_metadata_service.py
├── requirements.txt
└── README.md
```

## Prerequisites

* Python 3.10+
* Access to an Oracle 19c database
* Kerberos infrastructure configured for the Oracle database (keytabs or ticket cache, `sqlnet.ora`, `krb5.conf`)
* [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client/downloads.html) libraries available locally (required for Kerberos connections when using python-oracledb in thick mode)
* `kinit` credentials for the service principal or user running the process

### Kerberos and Oracle client configuration

Kerberos authentication relies on Oracle's thick client. Export the following environment variables before starting the API server or running the notebook:

```bash
export ORACLE_DSN="//db-host.example.com:1521/ORCLPDB1"
export ORACLE_CONFIG_DIR="/opt/oracle/kerberos"    # directory containing sqlnet.ora & krb5.conf
export ORACLE_LIB_DIR="/opt/oracle/instantclient_19_8"  # location of Instant Client libraries
export ORACLE_KRB5_CONFIG="/etc/krb5.conf"             # optional explicit Kerberos configuration file
export ORACLE_KRB5_CREDENTIALS_CACHE="/tmp/krb5cc_$(id -u)"  # optional credential cache location
export ORACLE_USE_THICK=true
export ORACLE_USE_POOL=true
export ORACLE_POOL_MIN=1
export ORACLE_POOL_MAX=5
export ORACLE_POOL_INCREMENT=1
export METADATA_RECENT_MONTHS=6
export METADATA_SCHEMA="HR"                               # optional default schema for metadata APIs
export METADATA_TABLES="EMPLOYEES,DEPARTMENTS"            # optional comma-separated table list
```

The `ORACLE_CONFIG_DIR` directory should contain the Kerberos configuration (`sqlnet.ora`, `krb5.conf`). Ensure that Kerberos tickets are available (e.g., via `kinit`) for the process identity. The Oracle server must be configured to trust Kerberos-authenticated clients.

When `ORACLE_KRB5_CONFIG` or `ORACLE_KRB5_CREDENTIALS_CACHE` are provided, the service sets the corresponding `KRB5_CONFIG` and `KRB5CCNAME` environment variables before initializing the Oracle client. This makes it easy to point the connector at alternate Kerberos configuration files or ticket caches without modifying the host environment.

If you regularly work with a specific schema, `METADATA_SCHEMA` and `METADATA_TABLES` allow you to define the default schema and a comma-separated list of tables to inspect. When these variables are set, the metadata service automatically scopes table discovery and manifest generation to the configured tables whenever you request that schema.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you need to override configuration values, create a `.env` file at the project root or set environment variables directly. Supported options are documented in `app/config.py`.

## Running the API server

1. Obtain a Kerberos ticket (e.g., `kinit user@REALM`).
2. Export the environment variables described above.
3. Start the FastAPI server:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Available endpoints

* `GET /health` — health check.
* `GET /schemas/{schema}/tables` — list tables within a schema that satisfy the usage filter. Optional query parameters:
  * `months` — override the recency window (defaults to six).
  * `include_empty` — if `true`, retain empty tables in the response.
  * `table` — repeatable parameter to scope the check to specific tables.
* `POST /schemas/{schema}/metadata` — generate WrenMDL metadata. Request body fields:
  * `catalog` — catalog name for the manifest.
  * `tables` — optional list of table names. When omitted and `apply_usage_filter` is `true`, the service uses the filtered list from the previous endpoint.
  * `apply_usage_filter` — whether to exclude inactive/empty tables before generating metadata (`true` by default).
  * `months` — optional override for the recency window.
  * `include_empty` — retain empty tables when applying the filter.

Example `POST /schemas/HR/metadata` request body:

```json
{
  "catalog": "Analytics",
  "tables": ["EMPLOYEES", "DEPARTMENTS"],
  "apply_usage_filter": true,
  "months": 6,
  "include_empty": false
}
```

The response is a WrenMDL-compatible JSON document containing the requested models.

## Notebook usage

Launch Jupyter and open `notebooks/metadata_demo.ipynb` to interactively query the service or call the metadata generator directly from Python. The notebook demonstrates:

* Loading configuration from environment variables.
* Connecting to Oracle via Kerberos.
* Listing filtered tables.
* Generating metadata for a subset of tables.

## Testing

The repository includes unit tests that validate the metadata filtering logic with a simulated Oracle client. Run the tests with:

```bash
pytest
```

## Troubleshooting

* **Missing Oracle client libraries** – ensure the `ORACLE_LIB_DIR` path matches your Instant Client installation and that the libraries are discoverable (`LD_LIBRARY_PATH` on Linux/macOS).
* **Kerberos ticket issues** – confirm that `klist` shows a valid ticket for the Oracle service principal and that the key distribution center is reachable.
* **Insufficient privileges** – the Oracle account authenticated via Kerberos must be able to read `ALL_TABLES`, `ALL_TAB_COLUMNS`, `ALL_TAB_STATISTICS`, and `ALL_TAB_MODIFICATIONS`, and (optionally) the actual tables to check for empty status.

## License

This project is provided under the MIT License.
