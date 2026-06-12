let traces = [];
let selectedTraceId = null;

const messagesEl = document.querySelector("#messages");
const inputEl = document.querySelector("#messageInput");
const composerEl = document.querySelector("#composer");
const traceListEl = document.querySelector("#traceList");
const traceDetailEl = document.querySelector("#traceDetail");
const refreshButton = document.querySelector("#refreshButton");

appendMessage("agent", "Hi, I can evaluate refund requests against the Retail policy. Send a customer name and order number.");

composerEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  appendMessage("customer", text);
  composerEl.querySelector("button").disabled = true;

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "API error");
    appendMessage("agent", data.reply, data.decision);
    traces = [data.trace, ...traces.filter((trace) => trace.trace_id !== data.trace.trace_id)].slice(0, 25);
    selectedTraceId = data.trace.trace_id;
    renderTraces();
  } catch (error) {
    appendMessage("agent", `The API returned an error: ${error.message}`, "needs_info");
  } finally {
    composerEl.querySelector("button").disabled = false;
  }
});

refreshButton.addEventListener("click", fetchTraces);
fetchTraces();

async function fetchTraces() {
  const response = await fetch("/api/traces");
  if (!response.ok) return;
  traces = await response.json();
  if (!selectedTraceId && traces[0]) selectedTraceId = traces[0].trace_id;
  renderTraces();
}

function appendMessage(role, body, decision) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  article.innerHTML = `
    <div class="avatar">${role === "agent" ? "AI" : "C"}</div>
    <div class="bubble">
      ${decision ? badge(decision) : ""}
      <p></p>
    </div>
  `;
  article.querySelector("p").textContent = body;
  messagesEl.appendChild(article);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderTraces() {
  traceListEl.innerHTML = "";
  traces.forEach((trace) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = trace.trace_id === selectedTraceId ? "selected" : "";
    button.innerHTML = `<span>${trace.trace_id}</span>${badge(trace.decision)}`;
    button.addEventListener("click", () => {
      selectedTraceId = trace.trace_id;
      renderTraces();
    });
    traceListEl.appendChild(button);
  });

  const trace = traces.find((item) => item.trace_id === selectedTraceId) || traces[0];
  if (!trace) {
    traceDetailEl.className = "empty-state";
    traceDetailEl.textContent = "No traces yet. Send a refund request to generate the first run.";
    return;
  }

  traceDetailEl.className = "";
  traceDetailEl.innerHTML = `
    <div class="metric-grid">
      ${metric("Latency", `${trace.latency_ms} ms`)}
      ${metric("Retries", String(trace.retries))}
      ${metric("Token cost", `$${trace.estimated_cost_usd.toFixed(6)}`)}
      ${metric("Tokens", String(trace.estimated_prompt_tokens + trace.estimated_completion_tokens))}
    </div>
    <section class="trace-section">
      <h3>Reasoning</h3>
      ${trace.reasoning
        .map(
          (step) => `
            <div class="reasoning-row">
              <span>${step.status === "ok" ? "OK" : "!"}</span>
              <div><strong>${escapeHtml(step.title)}</strong><p>${escapeHtml(step.detail)}</p></div>
            </div>
          `
        )
        .join("")}
    </section>
    <section class="trace-section">
      <h3>Tool I/O</h3>
      ${trace.tool_calls
        .map(
          (call, index) => `
            <details ${index === trace.tool_calls.length - 1 ? "open" : ""}>
              <summary><span>${escapeHtml(call.name)}</span><span class="call-status ${call.status}">${call.status}</span></summary>
              <pre>${escapeHtml(JSON.stringify({ input: call.input, output: call.output, latency_ms: call.latency_ms }, null, 2))}</pre>
            </details>
          `
        )
        .join("")}
    </section>
  `;
}

function badge(decision) {
  return `<span class="decision ${decision}">${decision.replace("_", " ")}</span>`;
}

function metric(label, value) {
  return `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
