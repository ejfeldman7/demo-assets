const j = (r) => { if (!r.ok) return r.json().then((e) => Promise.reject(e)); return r.json(); };

export const api = {
  summary: () => fetch("/api/summary").then(j),
  roles: () => fetch("/api/roles").then(j),
  correlations: () => fetch("/api/correlations").then(j),
  cases: (state, qtext) => {
    const p = new URLSearchParams();
    if (state) p.set("state", state);
    if (qtext) p.set("qtext", qtext);
    return fetch("/api/cases?" + p.toString()).then(j);
  },
  case: (id) => fetch(`/api/cases/${id}`).then(j),
  recommend: (id) => fetch(`/api/cases/${id}/recommend`, { method: "POST" }).then(j),
  approve: (id, body) =>
    fetch(`/api/cases/${id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(j),
  dispose: (id, body) =>
    fetch(`/api/cases/${id}/dispose`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(j),
  wafermap: (id) => fetch(`/api/cases/${id}/wafermap`).then(j),
  narrative: (id) => fetch(`/api/cases/${id}/narrative`, { method: "POST" }).then(j),
  reset: () => fetch(`/api/reset`, { method: "POST" }).then(j),
  whatifBaseline: (id) => fetch(`/api/cases/${id}/whatif`).then(j),
  whatif: (id, overrides) =>
    fetch(`/api/cases/${id}/whatif`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ overrides }),
    }).then(j),
  genieAsk: (question, conversationId) =>
    fetch(`/api/genie/ask`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: conversationId || null }),
    }).then(j),
};
