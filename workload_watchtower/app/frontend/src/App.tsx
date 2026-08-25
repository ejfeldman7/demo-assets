import { useState } from "react";
import { Layout, type ViewKey } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { useTheme } from "./hooks";
import { Dashboard } from "./views/Dashboard";
import { TriageBoard } from "./views/TriageBoard";
import { Findings } from "./views/Findings";
import { Rules } from "./views/Rules";
import { Actions } from "./views/Actions";
import { Monitoring } from "./views/Monitoring";
import { Ask } from "./views/Ask";

const VIEWS: Record<ViewKey, () => JSX.Element> = {
  dashboard: Dashboard,
  board: TriageBoard,
  findings: Findings,
  rules: Rules,
  actions: Actions,
  monitoring: Monitoring,
  ask: Ask,
};

export default function App() {
  const [view, setView] = useState<ViewKey>("dashboard");
  const { dark, toggle } = useTheme();
  const ViewComponent = VIEWS[view];

  return (
    <ToastProvider>
      <Layout view={view} onNavigate={setView} dark={dark} onToggleTheme={toggle}>
        <ErrorBoundary key={view}>
          <ViewComponent />
        </ErrorBoundary>
      </Layout>
    </ToastProvider>
  );
}
