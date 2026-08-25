import { useState } from "react";
import { Sparkles, Send, MessageSquareText } from "lucide-react";
import { api, type AskAnswer } from "../api";
import { useToast } from "../components/Toast";
import { Card, PageHeader, Spinner, Button, EmptyState } from "../components/ui";
import { Markdown } from "../components/Markdown";

const EXAMPLES = [
  "What are the most costly open workloads?",
  "Who overrode STATEMENT_TIMEOUT at the session level?",
  "Who owns the most runaway workloads?",
  "Summarize the current triage backlog by severity.",
];

export function Ask() {
  const toast = useToast();
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskAnswer | null>(null);

  const submit = async (q?: string) => {
    const text = (q ?? question).trim();
    if (!text) {
      toast({ kind: "error", title: "Enter a question first" });
      return;
    }
    setQuestion(text);
    setLoading(true);
    try {
      const res = await api.ask(text);
      setResult(res);
    } catch (e) {
      toast({ kind: "error", title: "Ask unavailable", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Ask Watchtower"
        subtitle="Natural-language questions answered by a Foundation Model, grounded in current findings, cards, and rules."
      />

      <Card>
        <label className="mb-2 flex items-center gap-2 text-[12px] font-medium uppercase tracking-wide text-text-secondary">
          <Sparkles size={14} className="text-lava-warm" />
          Ask a question
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="e.g. Which open findings are the most expensive, and who owns them?"
            className="flex-1 rounded-[10px] border border-line bg-app px-3.5 py-2.5 text-sm text-text-primary outline-none transition-colors placeholder:text-text-disabled focus:border-brand"
          />
          <Button variant="primary" icon={Send} loading={loading} onClick={() => submit()} className="py-2.5">
            Ask
          </Button>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              disabled={loading}
              onClick={() => submit(ex)}
              className="rounded-full border border-line bg-surface px-3 py-1.5 text-[12px] text-text-secondary transition-colors hover:border-brand/40 hover:bg-hover hover:text-text-primary disabled:opacity-50"
            >
              {ex}
            </button>
          ))}
        </div>
      </Card>

      <div className="mt-4">
        {loading ? (
          <Card>
            <div className="flex items-center gap-3 py-8 text-[13px] text-text-secondary">
              <Spinner size={18} />
              Consulting the Foundation Model…
            </div>
          </Card>
        ) : result ? (
          <Card>
            <div className="mb-3 border-b border-line pb-3">
              <div className="text-[11px] font-medium uppercase tracking-wide text-text-disabled">Question</div>
              <div className="mt-1 text-sm font-medium text-text-primary">{result.question}</div>
            </div>
            <Markdown text={result.answer} />
            <div className="mt-4 flex items-center gap-1.5 border-t border-line pt-3 text-[11px] text-text-disabled">
              <Sparkles size={12} className="text-lava-warm" />
              answered by {result.model}
            </div>
          </Card>
        ) : (
          <Card>
            <EmptyState
              icon={MessageSquareText}
              title="Ask anything about your workloads"
              hint="Watchtower answers only from the current findings, cards, and rules — try an example above to get started."
            />
          </Card>
        )}
      </div>
    </div>
  );
}
