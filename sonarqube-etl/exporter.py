import os
import re
import threading
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

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

        self._lock = threading.Lock()
        self._last_error_message: Optional[str] = None

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
                "last_error": self._last_error_message,
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
                err_msg = str(exc)
            duration = time.time() - start

            with self._lock:
                self._last_refresh_duration_seconds.set(duration)
                if ok:
                    self._up.set(1)
                    self._last_error.set(0)
                    self._last_success_unixtime.set(time.time())
                    self._last_error_message = None
                else:
                    self._up.set(0)
                    self._last_error.set(1)
                    self._last_error_message = err_msg

            time.sleep(max(1, PULL_INTERVAL_SECONDS))

    def refresh_once(self) -> None:
        projects = list(self._list_projects())
        # Fetch measures for each project
        current_labels: Dict[str, Set[Tuple[str, str]]] = {k: set() for k in _METRIC_KEYS}

        for project in projects:
            key = project["key"]
            name = project["name"]
            labels = (key, name)

            measures = self._fetch_measures(key)
            for metric_key in _METRIC_KEYS:
                g = self._gauges[metric_key]
                value = measures.get(metric_key)
                if value is None:
                    # Represent missing value as NaN to avoid implying 0.
                    g.labels(project_key=key, project_name=name).set(float("nan"))
                else:
                    g.labels(project_key=key, project_name=name).set(value)
                current_labels[metric_key].add(labels)

        # Remove stale labelsets if projects disappeared or were renamed
        for metric_key in _METRIC_KEYS:
            stale = self._known_labelsets[metric_key] - current_labels[metric_key]
            for (project_key, project_name) in stale:
                try:
                    self._gauges[metric_key].remove(project_key, project_name)
                except KeyError:
                    pass
            self._known_labelsets[metric_key] = current_labels[metric_key]

    def _request_json(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{SONAR_URL}{path}"
        resp = self._session.get(url, params=params, timeout=15, verify=VERIFY_TLS)
        resp.raise_for_status()
        return resp.json()

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

    def _fetch_measures(self, project_key: str) -> Dict[str, float]:
        metric_keys = ",".join(_METRIC_KEYS)
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
