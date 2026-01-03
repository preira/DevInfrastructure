# Observability Stack (Prometheus + Grafana + SonarQube Exporter)

This folder deploys an autonomous observability stack for SonarQube project metrics:

- `sonarqube-etl` (exporter): pulls SonarQube Web API metrics and exposes Prometheus metrics on `:9119/metrics`
- `prometheus`: scrapes the exporter (pull model)
- `grafana`: queries Prometheus for dashboards and drill-down

This deployment is **separate** from the SonarQube deployment and connects only via:

- an **external Docker network** (`observability-net`) for internal service discovery
- SonarQube URL + token (no direct DB access)

## Prerequisites

1) Create (or reuse) the external Docker network (idempotent):

```bash
docker network create observability-net || true
```

PowerShell alternative:

```powershell
if (-not (docker network inspect observability-net *> $null)) { docker network create observability-net }
```

2) Ensure SonarQube is running and reachable on the host.

With the port update, SonarQube is expected at:

- http://localhost:9000

SonarQube is an external dependency for this stack:

- Do not rebuild SonarQube.
- Do not destroy or recreate SonarQube volumes.

## Configure

This stack requires a SonarQube token:

- Create a token in SonarQube: **My Account → Security → Generate Tokens**
- Export it in your shell as `SONAR_TOKEN`

Optional environment variables:

- `SONAR_URL` (default `http://host.docker.internal:9000`)
- `PROJECT_KEY_REGEX` (default `.*`)
- `PULL_INTERVAL_SECONDS` (default `300`)
- `VERIFY_TLS` (default `true`)
- `GRAFANA_ADMIN_USER` (default `admin`)
- `GRAFANA_ADMIN_PASSWORD` (default `admin`)

## Run

From the base folder:

```bash
cd Sonarqube  # on Windows, `cd sonarqube` also works (case-insensitive)
docker compose up -d
```

Then:

```bash
cd observability
docker compose up -d --build
```

To stop **without deleting data**:

```bash
docker compose down
```

Avoid:

- `docker compose down -v` (removes volumes)

## Grafana plugins (treemap)

The **SonarQube – Global Quality** dashboard uses the Treemap panel plugin (`marcusolsson-treemap-panel`) for the technical debt visualization.

To keep this deterministic across rebuilds, plugins are vendored in this repo and bind-mounted into Grafana:

- `./grafana/provisioning/plugins` → `/var/lib/grafana/plugins:ro`

The Treemap panel plugin lives under:

- `./grafana/provisioning/plugins/marcusolsson-treemap-panel`

Notes:

- If you run `docker compose down -v`, Grafana’s volume is removed (dashboards/users/config state resets).
- With the bind-mount in place, the treemap plugin itself will still be available after a rebuild, even if volumes are wiped.

CRITICAL: Never destroy SonarQube volumes. SonarQube already contains projects and historical data.

## Access

- SonarQube: http://localhost:9000
- Prometheus UI: http://localhost:9090
- Grafana UI: http://localhost:3000
- Exporter metrics: http://localhost:9119/metrics
- Exporter health: http://localhost:9119/health

## Verify

1) In Prometheus (http://localhost:9090), check **Status → Targets**:

- `sonarqube-etl` should be **UP**

2) Run a query like:

- `sonar_project_bugs`

If you see time series, scraping is working.
