import os
import re
import threading
import time
from typing import Dict, Iterable, Optional, Set, Tuple

import requests
from flask import Flask, Response, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


SONAR_URL = os.getenv("SONAR_URL", "http://host.docker.internal:9000").rstrip("/")
SONAR_TOKEN = os.getenv("SONAR_TOKEN")
PROJECT_KEY_REGEX = os.getenv("PROJECT_KEY_REGEX", ".*")
PULL_INTERVAL_SECONDS = int(os.getenv("PULL_INTERVAL_SECONDS", "300"))
VERIFY_TLS = _env_bool("VERIFY_TLS", True)

_METRIC_KEYS = [
    "ncloc",
    # SonarQube Measures → Coverage
    # (Sonar uses line_coverage and branch_coverage; branch_coverage is often referred to as condition coverage.)
    "line_coverage",
    "branch_coverage",

    # SonarQube Measures → Size
    "lines",
    "statements",
    "functions",
    "classes",
    "files",
    "directories",
    "comment_lines",
    "comment_lines_density",

    # SonarQube Measures → Complexity
    "complexity",
    "cognitive_complexity",

    # SonarQube Measures → Technical debt
    # (sqale_index is technical debt in minutes)
    "sqale_index",

    # SonarQube Measures → Issue severities (legacy metric keys)
    "blocker_violations",
    "critical_violations",
    "major_violations",
    "minor_violations",
    "info_violations",

    "bugs",
    "vulnerabilities",
    "code_smells",
    "coverage",
    "duplicated_lines_density",
    "security_hotspots",
    "reliability_rating",
    "security_rating",
    "sqale_rating",
]


class SonarExporter:
    def __init__(self) -> None:
        if not SONAR_TOKEN:
            raise RuntimeError("SONAR_TOKEN environment variable is required")

        self._session = requests.Session()
        self._session.auth = (SONAR_TOKEN, "")
        self._session.headers.update({"User-Agent": "sonarqube-etl-exporter/1.0"})

        self._project_key_re = re.compile(PROJECT_KEY_REGEX)

        self._registry = CollectorRegistry()

        # Operational metrics
        self._up = Gauge(
            "sonar_exporter_up",
            "1 if last refresh succeeded, 0 otherwise",
            registry=self._registry,
        )
        self._last_success_unixtime = Gauge(
            "sonar_exporter_last_success_unixtime",
            "Unix timestamp of last successful refresh",
            registry=self._registry,
        )
        self._last_refresh_duration_seconds = Gauge(
            "sonar_exporter_last_refresh_duration_seconds",
            "Duration of last refresh in seconds",
            registry=self._registry,
        )
        self._last_error = Gauge(
            "sonar_exporter_last_error",
            "1 if last refresh ended in error, 0 otherwise",
            registry=self._registry,
        )

        # Global aggregate metrics (no labels to keep cardinality low)
        self._global_projects_total = Gauge(
            "sonar_global_projects_total",
            "Total number of projects scraped",
            registry=self._registry,
        )
        self._global_ncloc_total = Gauge(
            "sonar_global_ncloc_total",
            "Sum of NCLOC across scraped projects",
            registry=self._registry,
        )
        self._global_bugs_total = Gauge(
            "sonar_global_bugs_total",
            "Sum of bugs across scraped projects",
            registry=self._registry,
        )
        self._global_vulnerabilities_total = Gauge(
            "sonar_global_vulnerabilities_total",
            "Sum of vulnerabilities across scraped projects",
            registry=self._registry,
        )
        self._global_code_smells_total = Gauge(
            "sonar_global_code_smells_total",
            "Sum of code smells across scraped projects",
            registry=self._registry,
        )
        self._global_projects_with_bugs_gt0_total = Gauge(
            "sonar_global_projects_with_bugs_gt0_total",
            "Count of projects where bugs > 0",
            registry=self._registry,
        )
        self._global_projects_with_vulnerabilities_gt0_total = Gauge(
            "sonar_global_projects_with_vulnerabilities_gt0_total",
            "Count of projects where vulnerabilities > 0",
            registry=self._registry,
        )

        # Global Size aggregates
        self._global_lines_total = Gauge(
            "sonar_global_lines_total",
            "Sum of lines across scraped projects",
            registry=self._registry,
        )
        self._global_statements_total = Gauge(
            "sonar_global_statements_total",
            "Sum of statements across scraped projects",
            registry=self._registry,
        )
        self._global_functions_total = Gauge(
            "sonar_global_functions_total",
            "Sum of functions across scraped projects",
            registry=self._registry,
        )
        self._global_classes_total = Gauge(
            "sonar_global_classes_total",
            "Sum of classes across scraped projects",
            registry=self._registry,
        )
        self._global_files_total = Gauge(
            "sonar_global_files_total",
            "Sum of files across scraped projects",
            registry=self._registry,
        )
        self._global_comment_lines_total = Gauge(
            "sonar_global_comment_lines_total",
            "Sum of comment lines across scraped projects",
            registry=self._registry,
        )

        # Project metrics (controlled label cardinality)
        self._gauges: Dict[str, Gauge] = {}
        for key in _METRIC_KEYS:
            prom_name = f"sonar_project_{key}"
            self._gauges[key] = Gauge(
                prom_name,
                f"SonarQube project metric: {key}",
                labelnames=("project_key", "project_name"),
                registry=self._registry,
            )

        self._known_labelsets: Dict[str, Set[Tuple[str, str]]] = {k: set() for k in _METRIC_KEYS}

        # Track latest known project_name per project_key to remove stale labelsets on rename.
        self._project_key_to_name: Dict[str, str] = {}

        self._lock = threading.Lock()
        self._last_error_message: Optional[str] = None
        self._last_success_time: Optional[float] = None

        # Cache SonarQube-supported metric keys to avoid hard failures when a key isn't available.
        self._supported_metric_keys: Optional[Set[str]] = None
        self._supported_metric_keys_fetched_at: Optional[float] = None

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    def health(self) -> Dict[str, object]:
        with self._lock:
            return {
                "sonar_url": SONAR_URL,
                "project_key_regex": PROJECT_KEY_REGEX,
                "pull_interval_seconds": PULL_INTERVAL_SECONDS,
                "verify_tls": VERIFY_TLS,
                # Do not include secrets; keep error payload intentionally minimal.
                "last_error": self._last_error_message,
                "last_success_unixtime": self._last_success_time,
            }

    def refresh_forever(self) -> None:
        # Initial refresh immediately, then sleep-loop
        while True:
            start = time.time()
            ok = False
            err_msg: Optional[str] = None
            try:
                self.refresh_once()
                ok = True
            except Exception as exc:  # defensive: exporter must not crash
                err_msg = self._format_error(exc)
            duration = time.time() - start

            with self._lock:
                self._last_refresh_duration_seconds.set(duration)
                if ok:
                    self._up.set(1)
                    self._last_error.set(0)
                    now = time.time()
                    self._last_success_unixtime.set(now)
                    self._last_success_time = now
                    self._last_error_message = None
                else:
                    self._up.set(0)
                    self._last_error.set(1)
                    self._last_error_message = err_msg

            time.sleep(max(1, PULL_INTERVAL_SECONDS))

    def _format_error(self, exc: Exception) -> str:
        # Keep errors terse and avoid leaking secrets. Include HTTP status when available.
        if isinstance(exc, requests.HTTPError):
            try:
                status = exc.response.status_code if exc.response is not None else None
            except Exception:
                status = None
            if status is not None:
                return f"HTTPError {status}"
            return "HTTPError"
        return type(exc).__name__

    def refresh_once(self) -> None:
        projects = list(self._list_projects())

        # Discover supported metric keys from SonarQube (cached) so we don't fail hard if a metric
        # doesn't exist on this SonarQube instance.
        supported_keys = self._get_supported_metric_keys()

        # Fetch measures for each project
        current_labels: Dict[str, Set[Tuple[str, str]]] = {k: set() for k in _METRIC_KEYS}

        # Aggregate counters
        total_projects = 0
        total_ncloc = 0.0
        total_bugs = 0.0
        total_vulns = 0.0
        total_code_smells = 0.0
        projects_with_bugs_gt0 = 0
        projects_with_vulns_gt0 = 0

        total_lines = 0.0
        total_statements = 0.0
        total_functions = 0.0
        total_classes = 0.0
        total_files = 0.0
        total_comment_lines = 0.0

        for project in projects:
            key = project["key"]
            name = project["name"]
            labels = (key, name)

            # If the project was renamed, remove the old labelset(s) for this key.
            prev_name = self._project_key_to_name.get(key)
            if prev_name is not None and prev_name != name:
                for metric_key in _METRIC_KEYS:
                    try:
                        self._gauges[metric_key].remove(key, prev_name)
                    except KeyError:
                        pass
            self._project_key_to_name[key] = name

            measures = self._fetch_measures(key, supported_keys)
            for metric_key in _METRIC_KEYS:
                g = self._gauges[metric_key]
                value = measures.get(metric_key)
                if value is None:
                    # Represent missing value as NaN to avoid implying 0.
                    g.labels(project_key=key, project_name=name).set(float("nan"))
                else:
                    g.labels(project_key=key, project_name=name).set(value)
                current_labels[metric_key].add(labels)

            # Update aggregates (missing values are treated as 0 for totals)
            total_projects += 1
            ncloc_v = float(measures.get("ncloc") or 0.0)
            lines_v = float(measures.get("lines") or 0.0)
            statements_v = float(measures.get("statements") or 0.0)
            functions_v = float(measures.get("functions") or 0.0)
            classes_v = float(measures.get("classes") or 0.0)
            files_v = float(measures.get("files") or 0.0)
            comment_lines_v = float(measures.get("comment_lines") or 0.0)
            bugs_v = float(measures.get("bugs") or 0.0)
            vulns_v = float(measures.get("vulnerabilities") or 0.0)
            code_smells_v = float(measures.get("code_smells") or 0.0)
            total_ncloc += ncloc_v
            total_lines += lines_v
            total_statements += statements_v
            total_functions += functions_v
            total_classes += classes_v
            total_files += files_v
            total_comment_lines += comment_lines_v
            total_bugs += bugs_v
            total_vulns += vulns_v
            total_code_smells += code_smells_v
            if bugs_v > 0:
                projects_with_bugs_gt0 += 1
            if vulns_v > 0:
                projects_with_vulns_gt0 += 1

        # Publish global aggregates
        self._global_projects_total.set(total_projects)
        self._global_ncloc_total.set(total_ncloc)
        self._global_lines_total.set(total_lines)
        self._global_statements_total.set(total_statements)
        self._global_functions_total.set(total_functions)
        self._global_classes_total.set(total_classes)
        self._global_files_total.set(total_files)
        self._global_comment_lines_total.set(total_comment_lines)
        self._global_bugs_total.set(total_bugs)
        self._global_vulnerabilities_total.set(total_vulns)
        self._global_code_smells_total.set(total_code_smells)
        self._global_projects_with_bugs_gt0_total.set(projects_with_bugs_gt0)
        self._global_projects_with_vulnerabilities_gt0_total.set(projects_with_vulns_gt0)

        # Remove stale labelsets if projects disappeared or were renamed
        for metric_key in _METRIC_KEYS:
            stale = self._known_labelsets[metric_key] - current_labels[metric_key]
            for (project_key, project_name) in stale:
                try:
                    self._gauges[metric_key].remove(project_key, project_name)
                except KeyError:
                    pass
            self._known_labelsets[metric_key] = current_labels[metric_key]

        # Remove project_key->name mappings for projects no longer present.
        current_keys = {p["key"] for p in projects}
        for known_key in list(self._project_key_to_name.keys()):
            if known_key not in current_keys:
                del self._project_key_to_name[known_key]

    def _request_json(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{SONAR_URL}{path}"
        resp = self._session.get(url, params=params, timeout=15, verify=VERIFY_TLS)
        resp.raise_for_status()
        return resp.json()

    def _get_supported_metric_keys(self) -> Set[str]:
        # Refresh the cache at most every 60 minutes.
        now = time.time()
        if (
            self._supported_metric_keys is not None
            and self._supported_metric_keys_fetched_at is not None
            and (now - self._supported_metric_keys_fetched_at) < 3600
        ):
            return self._supported_metric_keys

        keys: Set[str] = set()
        page = 1
        page_size = 500
        while True:
            data = self._request_json(
                "/api/metrics/search",
                params={"p": page, "ps": page_size},
            )
            metrics = data.get("metrics") or []
            if not metrics:
                break
            for m in metrics:
                k = m.get("key")
                if isinstance(k, str) and k:
                    keys.add(k)
            page += 1

        # Never return an empty set (fallback to configured keys) to avoid disabling metrics.
        if not keys:
            keys = set(_METRIC_KEYS)

        self._supported_metric_keys = keys
        self._supported_metric_keys_fetched_at = now
        return keys

    def _list_projects(self) -> Iterable[dict]:
        page = 1
        page_size = 200
        while True:
            data = self._request_json(
                "/api/projects/search",
                params={"p": page, "ps": page_size},
            )
            components = data.get("components") or []
            if not components:
                return
            for c in components:
                key = c.get("key")
                name = c.get("name")
                if not key or not name:
                    continue
                if self._project_key_re.match(key):
                    yield {"key": key, "name": name}
            page += 1

    def _fetch_measures(self, project_key: str, supported_keys: Set[str]) -> Dict[str, float]:
        # Only request keys known by SonarQube to avoid hard 4xx responses.
        requested = [k for k in _METRIC_KEYS if k in supported_keys]
        if not requested:
            return {}

        metric_keys = ",".join(requested)
        data = self._request_json(
            "/api/measures/component",
            params={"component": project_key, "metricKeys": metric_keys},
        )
        measures = (
            (data.get("component") or {}).get("measures")
            or []
        )
        out: Dict[str, float] = {}
        for m in measures:
            key = m.get("metric")
            raw = m.get("value")
            if key in _METRIC_KEYS and raw is not None:
                try:
                    out[key] = float(raw)
                except (TypeError, ValueError):
                    # ignore unparsable values
                    pass
        return out


app = Flask(__name__)
exporter: Optional[SonarExporter] = None


@app.get("/health")
def health() -> Response:
    assert exporter is not None
    return jsonify(exporter.health())


@app.get("/metrics")
def metrics() -> Response:
    assert exporter is not None
    payload = generate_latest(exporter.registry)
    return Response(payload, mimetype=CONTENT_TYPE_LATEST)


def main() -> None:
    global exporter
    exporter = SonarExporter()

    t = threading.Thread(target=exporter.refresh_forever, daemon=True)
    t.start()

    # Must listen on 0.0.0.0:9119
    app.run(host="0.0.0.0", port=9119, threaded=True)


if __name__ == "__main__":
    main()
