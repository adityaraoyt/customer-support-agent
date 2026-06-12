import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, AlertTriangle, Bot, CheckCircle2, Clock, DollarSign, RefreshCw, Send, ShieldCheck, XCircle } from "lucide-react";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

type Decision = "approved" | "denied" | "escalated" | "needs_info";

type ToolCall = {
  name: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  latency_ms: number;
  status: "success" | "failed" | "retried";
};

type ReasoningStep = {
  title: string;
  detail: string;
  status: "ok" | "warning" | "failed";
};

type AgentTrace = {
  trace_id: string;
  session_id: string;
  started_at: string;
  latency_ms: number;
  estimated_prompt_tokens: number;
  estimated_completion_tokens: number;
  estimated_cost_usd: number;
  retries: number;
  decision: Decision;
  tool_calls: ToolCall[];
  reasoning: ReasoningStep[];
};

type ChatResponse = {
  reply: string;
  decision: Decision;
  trace: AgentTrace;
};

type Message = {
  id: string;
  role: "customer" | "agent";
  body: string;
  decision?: Decision;
};

function App() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: crypto.randomUUID(),
      role: "agent",
      body: "Hi, I can evaluate refund requests against the Northstar Retail policy. Send a customer name and order number."
    }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [traces, setTraces] = useState<AgentTrace[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  const selectedTrace = useMemo(
    () => traces.find((trace) => trace.trace_id === selectedTraceId) ?? traces[0],
    [selectedTraceId, traces]
  );

  useEffect(() => {
    fetchTraces();
  }, []);

  async function fetchTraces() {
    const response = await fetch(`${API_BASE}/api/traces`);
    if (response.ok) {
      const data = (await response.json()) as AgentTrace[];
      setTraces(data);
      if (!selectedTraceId && data[0]) setSelectedTraceId(data[0].trace_id);
    }
  }

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setLoading(true);
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "customer", body: text }]);

    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text })
      });
      const data = (await response.json()) as ChatResponse;
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "agent", body: data.reply, decision: data.decision }
      ]);
      setTraces((current) => [data.trace, ...current.filter((trace) => trace.trace_id !== data.trace.trace_id)].slice(0, 25));
      setSelectedTraceId(data.trace.trace_id);
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "agent",
          body: "The API is not reachable. Start the FastAPI server on port 8000 and try again.",
          decision: "needs_info"
        }
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="chat-pane" aria-label="Customer chat">
        <header className="topbar">
          <div>
            <p className="eyebrow">Northstar Retail</p>
            <h1>Refund Agent Console</h1>
          </div>
          <div className="status-pill">
            <ShieldCheck size={16} />
            Policy locked
          </div>
        </header>

        <div className="messages">
          {messages.map((message) => (
            <article key={message.id} className={`message ${message.role}`}>
              <div className="avatar">{message.role === "agent" ? <Bot size={18} /> : "C"}</div>
              <div className="bubble">
                {message.decision && <DecisionBadge decision={message.decision} />}
                <p>{message.body}</p>
              </div>
            </article>
          ))}
        </div>

        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            sendMessage();
          }}
        >
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Type a refund request..."
            rows={3}
          />
          <button type="submit" aria-label="Send message" title="Send message" disabled={loading}>
            {loading ? <RefreshCw className="spin" size={20} /> : <Send size={20} />}
          </button>
        </form>
      </section>

      <aside className="admin-pane" aria-label="Agent trace dashboard">
        <div className="admin-header">
          <div>
            <p className="eyebrow">Admin trace</p>
            <h2>Reasoning Logs</h2>
          </div>
          <button type="button" className="icon-button" onClick={fetchTraces} title="Refresh traces" aria-label="Refresh traces">
            <RefreshCw size={18} />
          </button>
        </div>

        {selectedTrace ? (
          <>
            <div className="trace-list">
              {traces.map((trace) => (
                <button
                  key={trace.trace_id}
                  type="button"
                  className={trace.trace_id === selectedTrace.trace_id ? "selected" : ""}
                  onClick={() => setSelectedTraceId(trace.trace_id)}
                >
                  <span>{trace.trace_id}</span>
                  <DecisionBadge decision={trace.decision} />
                </button>
              ))}
            </div>

            <div className="metric-grid">
              <Metric icon={<Clock size={17} />} label="Latency" value={`${selectedTrace.latency_ms} ms`} />
              <Metric icon={<RefreshCw size={17} />} label="Retries" value={String(selectedTrace.retries)} />
              <Metric icon={<DollarSign size={17} />} label="Token cost" value={`$${selectedTrace.estimated_cost_usd.toFixed(6)}`} />
              <Metric
                icon={<Activity size={17} />}
                label="Tokens"
                value={`${selectedTrace.estimated_prompt_tokens + selectedTrace.estimated_completion_tokens}`}
              />
            </div>

            <section className="trace-section">
              <h3>Reasoning</h3>
              {selectedTrace.reasoning.map((step, index) => (
                <div className="reasoning-row" key={`${step.title}-${index}`}>
                  {step.status === "ok" ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
                  <div>
                    <strong>{step.title}</strong>
                    <p>{step.detail}</p>
                  </div>
                </div>
              ))}
            </section>

            <section className="trace-section">
              <h3>Tool I/O</h3>
              {selectedTrace.tool_calls.map((call, index) => (
                <details key={`${call.name}-${index}`} open={index === selectedTrace.tool_calls.length - 1}>
                  <summary>
                    <span>{call.name}</span>
                    <span className={`call-status ${call.status}`}>{call.status}</span>
                  </summary>
                  <pre>{JSON.stringify({ input: call.input, output: call.output, latency_ms: call.latency_ms }, null, 2)}</pre>
                </details>
              ))}
            </section>
          </>
        ) : (
          <div className="empty-state">
            <XCircle size={28} />
            <p>No traces yet. Send a refund request to generate the first run.</p>
          </div>
        )}
      </aside>
    </main>
  );
}

function DecisionBadge({ decision }: { decision: Decision }) {
  return <span className={`decision ${decision}`}>{decision.replace("_", " ")}</span>;
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
