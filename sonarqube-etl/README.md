# SonarQube ETL Exporter (SonarQube Web API → Prometheus)

This service periodically polls SonarQube project measures using the SonarQube Web API and exposes them as Prometheus metrics.

- Metrics endpoint: `GET /metrics`
- Health endpoint: `GET /health`
- Default listen address: `0.0.0.0:9119`

## How it works

Every `PULL_INTERVAL_SECONDS` (default 300 seconds), the exporter:

1) Lists projects via `GET /api/projects/search` (with pagination)
2) Filters project keys by `PROJECT_KEY_REGEX`
3) Fetches measures per project via `GET /api/measures/component?component=<key>&metricKeys=...`
4) Updates Prometheus gauges and removes stale labelsets if projects disappear or are renamed

The exporter is defensive:

- timeouts on all HTTP calls
- does not crash if SonarQube is temporarily down
- does not log secrets (token is never printed)

## Environment variables

Required:

- `SONAR_TOKEN` – SonarQube token (used as HTTP Basic auth username)

Optional:

- `SONAR_URL` (default `http://host.docker.internal:9000`)
- `PROJECT_KEY_REGEX` (default `.*`)
- `PULL_INTERVAL_SECONDS` (default `300`)
- `VERIFY_TLS` (default `true`)

## Exported SonarQube measures

Each measure is exported as a gauge named `sonar_project_<metric>` with labels:

- `project_key`
- `project_name`

Measures:

- `ncloc`
- `bugs`
- `vulnerabilities`
- `code_smells`
- `coverage`
- `duplicated_lines_density`
- `security_hotspots`
- `reliability_rating`
- `security_rating`
- `sqale_rating`

Operational metrics:

- `sonar_exporter_up`
- `sonar_exporter_last_success_unixtime`
- `sonar_exporter_last_refresh_duration_seconds`
- `sonar_exporter_last_error`

## Prometheus scrape contract

Prometheus must scrape:

- `http://sonarqube-etl:9119/metrics` (Docker networking)

The exporter does not push metrics.

## Run

Run this exporter as part of the full observability stack in [observability/docker-compose.yml](observability/docker-compose.yml).

The exporter container is built and launched by the observability deployment; this folder only contains the exporter implementation and its Docker image definition.

## Safe operations

- Use `docker compose up -d` to apply changes.
- Avoid `docker compose down -v` (removes volumes).
