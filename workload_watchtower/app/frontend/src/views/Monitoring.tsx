import { BarChart3, ExternalLink } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { Card, EmptyState, PageHeader, Spinner, Button, Chip } from "../components/ui";

const PAGES = [
  "Unified Cost Analysis",
  "Job Operations & Cost",
  "DBSQL Cost & Query Performance",
  "Data Lineage & Catalog Utilization",
  "AI & ML Infrastructure Cost",
  "Genie Usage Cost Tracking",
];

export function Monitoring() {
  const cfg = useApi(() => api.config());
  const url = cfg.data?.dashboard_url ?? null;
  const embedUrl = cfg.data?.dashboard_embed_url ?? url; // /embed path is frameable; /published isn't

  return (
    <div>
      <PageHeader
        title="Monitoring"
        subtitle="Historical cost, usage & performance analytics over system tables — the analytical companion to real-time triage."
        actions={
          url ? (
            <Button icon={ExternalLink} variant="primary" onClick={() => window.open(url, "_blank", "noopener")}>
              Open in Databricks
            </Button>
          ) : undefined
        }
      />

      {cfg.loading && !cfg.data ? (
        <div className="flex items-center justify-center py-16">
          <Spinner />
        </div>
      ) : !url ? (
        <Card>
          <EmptyState
            icon={BarChart3}
            title="Monitoring dashboard not configured"
            hint="Set WT_DASHBOARD_URL (the published Lakeview dashboard) in the app config to enable this page."
          />
        </Card>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap gap-1.5">
            {PAGES.map((p) => (
              <Chip key={p}>{p}</Chip>
            ))}
          </div>
          <Card padded={false} className="overflow-hidden">
            <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5 text-[12px] text-text-secondary">
              <span>AI/BI Cost &amp; Usage Suite (6 pages)</span>
              <span className="text-text-disabled">
                Blank? The embed only renders in a direct browser tab (not the Workspace app preview) with
                third-party cookies enabled — otherwise use “Open in Databricks”.
              </span>
            </div>
            <iframe
              title="Cost & Usage Suite"
              src={embedUrl ?? undefined}
              className="h-[calc(100vh-260px)] min-h-[520px] w-full border-0 bg-app"
            />
          </Card>
        </>
      )}
    </div>
  );
}
