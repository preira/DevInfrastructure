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
cd observability
docker compose up -d --build
```

### If you already have Prometheus/Grafana (merge mode)

Bring up **only** the exporter (no Prometheus/Grafana containers from this folder):

```bash
cd observability
docker compose -f docker-compose.exporter.yml up -d --build
```

Then add this scrape target to your existing Prometheus:

- Preferred (if your Prometheus container is attached to `observability-net`): `sonarqube-etl:9119`
- Fallback (no shared Docker network needed): `host.docker.internal:9119`

To stop **without deleting data**:

```bash
docker compose down
```

Exporter-only stop:

```bash
docker compose -f docker-compose.exporter.yml down
```

Avoid:

- `docker compose down -v` (removes volumes)

## Access

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
