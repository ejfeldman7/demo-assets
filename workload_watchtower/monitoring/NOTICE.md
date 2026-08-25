# Attribution

`cost-usage-suite.lvdash.json` — the 6-page "System Tables — Databricks Cost & Usage Suite"
Lakeview dashboard — is adapted from **mohanab89/databricks-dashboard-suite**
(https://github.com/mohanab89/databricks-dashboard-suite), used with the author's published
"clone and use" instructions. The upstream repo carries no explicit license; included here for
the Workload Watchtower demo with attribution to the original author.

`deploy_monitoring.py` re-implements the upstream `create_dashboards.py` deploy logic (reference
views/functions + Lakeview create/publish) as a standalone local script targeting the
`<catalog>.<schema>` configured in `setup/config.env`.
