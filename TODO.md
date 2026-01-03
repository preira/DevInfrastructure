# TODO:
- [ ] Add sonarqube dashboard to grafana with aggregated metrics.
- [ ] Add alarmistic with grafana.
- [ ] Add Redpanda dashboards
- [ ] Study the possibility of adding Tempo to the observability stack. !?!?!?

- [ ] OpenTelemetry???? Jaeger????

- [ ] Add alerting with Prometheus Alertmanager and Grafana notifications. ?!?!?!?

- [ ] What about service network visualization with Weave Scope or similar tools? !?!?!?

- [ ] Consider adding Backstage for exploring the tool.

# PROMPT:

## DASHBOARD: SonarQube - Size
remove project key label and select.
remove:
- Selected Project – NCLOC
- Selected Project – Lines
- Selected Project – Statements
- Selected Project – Functions
- Selected Project – Classes
- Selected Project – Files
- Selected Project – Directories
- Selected Project – Comment Lines
- Selected Project – Comment Lines Density
- Top Project by NCLOC

add:
  totals:
    - Total Projects (count of all projects)
    - Total NCLOC (sum of all projects)
    - Total Files (sum of all projects)
    - Total Functions (sum of all projects)
  graphs:
    - Total Projects over Time (count of all projects) 
    - NCLOC Distribution (all projects)
    - NCLOC over Time (sum of all projects)
    - Files over Time (sum of all projects)
    - Functions over Time (sum of all projects)

## DASHBOARD: SonarQube - Global Quality
add:
  graphs:
    graph 1:
        toggle:
        - Line Coverage % over Time (average of all projects)
        - Each Project Line Coverage % over Time 
        - Condition Coverage % over Time (average of all projects)
        - Each Project Condition Coverage % over Time 
    graph 2:
        toggle:
        - Duplicated Lines Density % over Time (average of all projects)
        - Each Project Duplicated Lines Density % over Time
    graph 3:
        two vertical scales with toggle:
        - Total cyclomatic complexity over Time (sum of all projects)
        - Total cognitive complexity over Time (sum of all projects)
        - Average cyclomatic complexity over Time (average of all projects)
        - Average cognitive complexity over Time (average of all projects)
    graph 5:
        pie chart:
        - Cyclomatic Complexity Distribution (all projects)
    graph 5:
        pie chart:
        - Cognitive Complexity Distribution (all projects)
    graph 4:
        two vertical scales:
        - Total Technical Debt (in days) over Time (sum of all projects)
        - Average Technical Debt (in days) over Time (average of all projects)
    graph 6:
        Heatmap:
        - Technical Debt Distribution (all projects)
    graph 7:
        toggle:
        - Blocker Issues over Time (sum of all projects)
        - High Issues over Time (sum of all projects)
        - Medium Issues over Time (sum of all projects)
        - Low Issues over Time (sum of all projects)
        - Info Issues over Time (sum of all projects)
    graph 8:
        two vertical scales bubble chart:
        - tech debt (in days) vs sum of medium to blocker issues (all projects, per project)
        - bubble size: ncloc