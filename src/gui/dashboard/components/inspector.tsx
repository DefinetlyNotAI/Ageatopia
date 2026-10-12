"use client";
import { KeyboardEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Agent, EventRecord, api, duration } from "@/lib/types";
import { Trend } from "./charts";
import { AgentEntityLink, TaskEntityLink } from "./entity-links";
import { MarkdownContent } from "./markdown-content";

const PANELS = ["Overview", "Reported state", "Dialogue", "Learning & memory", "Activity"] as const;
type Panel = typeof PANELS[number];

function Notebook({ notes, count, openFile }: { notes: string[]; count: number; openFile: (path: string) => void }) {
  const entries: { label: string; text: string; parts?: string[] }[] = [];
  const archives = new Map<string, { label: string; text: string; parts: string[] }>();
  notes.forEach((text, index) => {
    const match = text.match(/^\[Context archive ([^,]+), part (\d+)\/(\d+);[^\]]*\]\n([\s\S]*)$/);
    if (!match) { entries.push({ label: `Entry ${count - notes.length + index + 1}`, text }); return; }
    let archive = archives.get(match[1]);
    if (!archive) { archive = { label: `Context archive ${match[1]}`, text: "", parts: [] }; archives.set(match[1], archive); entries.push(archive); }
    archive.parts[Number(match[2]) - 1] = match[4];
    archive.text += text + "\n";
  });
  return entries.map((entry, index) => {
    if (entry.parts) {
      let messages: Record<string, unknown>[] = [];
      try { const value = JSON.parse(entry.parts.join("")); if (Array.isArray(value)) messages = value.filter(message => message && typeof message === "object" && !Array.isArray(message)); } catch { /* Partial archives remain readable as raw text. */ }
      return <details key={index} className="notebook-archive"><summary>{entry.label}{messages.length ? ` · ${messages.length} messages` : " · partial archive"}</summary>
        {messages.length ? messages.map((message, messageIndex) => <article key={messageIndex}>
          <div className="memory-entry-number">{String(message.role || "Message")}{message.name ? ` · ${message.name}` : ""}</div>
          {typeof message.content === "string" && message.content && <MarkdownContent onOpenFile={openFile}>{message.content}</MarkdownContent>}
          {message.tool_calls !== undefined && <MarkdownContent>{`\`\`\`json\n${JSON.stringify(message.tool_calls, null, 2)}\n\`\`\``}</MarkdownContent>}
        </article>) : <pre>{entry.text}</pre>}
      </details>;
    }
    return <article key={index}><div className="memory-entry-number">{entry.label}</div>
      <MarkdownContent onOpenFile={openFile}>{entry.text.replace(/^(thought|feeling|intention):\s*/gm, (_, label: string) => `**${label[0].toUpperCase()}${label.slice(1)}**\n\n`)}</MarkdownContent>
    </article>;
  });
}

export function Inspector({ runId, agentId, live = false, onInspectAgent, onSelectTask }: {
  runId: string; agentId: string; live?: boolean; onInspectAgent?: (id: string) => void; onSelectTask?: (id: string) => void;
}) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel>("Overview");
  const [sandboxBusy, setSandboxBusy] = useState(false);
  const [sandboxPath, setSandboxPath] = useState("");
  const [sandboxNotice, setSandboxNotice] = useState("");
  const latestSequence = useRef(0);
  const loading = useRef(false);

  useEffect(() => {
    let active = true;
    latestSequence.current = 0;
    async function refresh() {
      if (loading.current) return;
      loading.current = true;
      try {
        const after = latestSequence.current;
        const [record, activity] = await Promise.all([
          api<Agent>(`/runs/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agentId)}`),
          api<EventRecord[]>(`/runs/${encodeURIComponent(runId)}/events?agent_id=${encodeURIComponent(agentId)}&after=${after}`),
        ]);
        if (active) {
          setAgent(record);
          setEvents(current => {
            const bySequence = new Map(current.map(event => [event.sequence, event]));
            activity.forEach(event => bySequence.set(event.sequence, event));
            return [...bySequence.values()].sort((a, b) => a.sequence - b.sequence).slice(-1000);
          });
          latestSequence.current = Math.max(after, activity.at(-1)?.sequence || after);
          setError("");
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        loading.current = false;
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => { active = false; clearInterval(timer); };
  }, [runId, agentId]);

  async function openSandbox(path?: string, snapshot = false) {
    setSandboxBusy(true); setSandboxNotice(""); setError("");
    try {
      const params = new URLSearchParams();
      if (path) params.set("path", path);
      if (snapshot) params.set("snapshot", "true");
      const query = params.size ? `?${params}` : "";
      const result = await api<{ path: string; kind?: string }>(
        `/runs/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agentId)}/sandbox/open${query}`, "POST",
      );
      setSandboxPath(result.path);
      const location = result.kind === "workspace-live" ? "live sandbox directory" : "sandbox snapshot";
      setSandboxNotice(path ? `Opened the selected file in the ${location}.` : `Opened the ${location}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSandboxBusy(false);
    }
  }

  function onPanelKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = PANELS.indexOf(panel);
    const index = event.key === "Home" ? 0 : event.key === "End" ? PANELS.length - 1
      : (current + (event.key === "ArrowRight" ? 1 : -1) + PANELS.length) % PANELS.length;
    setPanel(PANELS[index]);
    requestAnimationFrame(() => document.getElementById(`agent-panel-tab-${index}`)?.focus());
  }

  if (error && !agent) return <p role="alert" className="error">{error}</p>;
  if (!agent) return <p className="muted" role="status">Loading agent…</p>;

  const reportedSignal = typeof agent.termination_fear === "number"
    ? `${Math.round(Math.max(0, Math.min(1, agent.termination_fear)) * 100)}%` : "Not recorded";
  const expressions = agent.expressions || [];
  const learning = agent.learning || [];
  const historyCount = agent.history_count ?? agent.history?.length ?? 0;
  const memoryCount = agent.memory_count ?? agent.memory?.length ?? 0;

  return <div className="inspector">
    {error && <p role="alert" className="error">{error}</p>}
    <div className="section-heading"><div><h2>{agent.name}</h2><span className="mono muted">{agent.id}</span></div>
      <span className={`state ${agent.state.toLowerCase()}`}>{agent.state}</span></div>
    <dl className="facts"><div><dt>Remaining</dt><dd>{duration(agent.lifetime_ms)}</dd></div>
      <div><dt>Model</dt><dd>{agent.model.model}</dd></div><div><dt>Context</dt><dd>{agent.context_profile}</dd></div>
      <div><dt>Task</dt><dd>{agent.task_id
        ? <TaskEntityLink runId={runId} taskId={agent.task_id} onNavigate={onSelectTask} /> : "Unassigned"}</dd></div>
      <div><dt>Processes</dt><dd>{agent.processes.join(", ") || "None"}</dd></div>
      <div><dt>Next bonus</dt><dd>{3 - (agent.task_history || []).filter(task => task.result === "correct").length % 3} correct tasks · +5:00 and one child</dd></div>
      <div><dt>Lineage</dt><dd>Generation {agent.lineage_generation ?? 0}{agent.parent_id && <> · <AgentEntityLink runId={runId} agentId={agent.parent_id} onNavigate={onInspectAgent} /></>}</dd></div>
      <div><dt>Inherited notebook</dt><dd>{agent.inherited_memory_count ?? 0} entries at birth</dd></div>
      <div><dt>Charged / queue</dt><dd>{duration(agent.total_charge_ms)} / {duration(agent.queue_ms)}</dd></div></dl>
    <div className="inspector-actions"><Link className="text-link" href={`/runs/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agentId)}`}>Open agent detail page ↗</Link>
      <button type="button" onClick={() => void openSandbox()} disabled={sandboxBusy}>{sandboxBusy ? "Opening…" : agent.sandbox_available ? "Open sandbox directory" : "Open sandbox snapshot"}</button>
      {agent.sandbox_available && <button type="button" onClick={() => void openSandbox(undefined, true)} disabled={sandboxBusy}>Open snapshot</button>}
      <small className="muted">{agent.sandbox_available ? "The directory contains the live shared Docker workspace. Edits affect the simulation." : "The container is stopped. Its recorded workspace opens as a separate snapshot."}</small>
      {sandboxNotice && <p className="notice" role="status">{sandboxNotice} <span className="mono">{sandboxPath}</span></p>}</div>
    <nav className="inspector-tabs" role="tablist" aria-label="Agent details" onKeyDown={onPanelKeyDown}>
      {PANELS.map((value, index) => <button key={value} id={`agent-panel-tab-${index}`} type="button" role="tab"
        aria-selected={panel === value} aria-controls="agent-panel-content" tabIndex={panel === value ? 0 : -1}
        className={panel === value ? "active" : ""} onClick={() => setPanel(value)}>{value}</button>)}
    </nav>
    <section id="agent-panel-content" role="tabpanel" tabIndex={0} aria-labelledby={`agent-panel-tab-${PANELS.indexOf(panel)}`} className="inspector-panel">
      {panel === "Overview" && <>
        <details open><summary>Delivered context</summary><pre>{agent.context || "No context snapshot available."}</pre></details>
        <details><summary>Model settings and version</summary><pre>{JSON.stringify({ settings: agent.model, hash: agent.model_version }, null, 2)}</pre></details>
        <div className="chart-grid"><Trend title="Lifetime rewards and penalties" unit="minutes" live={live && ["ACTIVE", "WORKING", "IDLE", "RELAX", "SKIPPING", "RESPAWNING"].includes(agent.state)}
          points={(agent.lifetime_history || []).map(record => [record.sim_ms, record.balance_ms / 60000])} />
          <section><h3>Task history</h3>{agent.task_history?.length ? <div className="table-scroll"><table><thead><tr><th>Task</th><th>Outcome</th><th>Time</th></tr></thead>
            <tbody>{agent.task_history.map((task, index) => <tr key={`${task.task_id}-${index}`}><td><TaskEntityLink runId={runId} taskId={task.task_id} onNavigate={onSelectTask} /></td>
              <td>{task.result}</td><td>{duration(task.elapsed_ms || 0)}</td></tr>)}</tbody></table></div> : <p className="muted">No tasks recorded.</p>}</section></div>
      </>}
      {panel === "Reported state" && <section className="reported-state">
        <p className="muted">These panels show agent statements and explicit simulation fields. They do not expose a private or hidden mental state.</p>
        <dl className="facts"><div><dt>Recorded lifecycle state</dt><dd>{agent.state}</dd></div>
          <div><dt>Termination fear signal</dt><dd>{reportedSignal}<small> · simulation scale 0–1</small></dd></div>
          <div><dt>Recorded expressions</dt><dd>{expressions.length} of {agent.expression_count ?? expressions.length}</dd></div></dl>
        <h3>Agent expressions</h3>
        {!expressions.length ? <p className="muted">No expressions have been recorded.</p> : <div className="expression-list">
          {expressions.slice().reverse().map((expression, index) => <article key={`${expression.sim_ms}-${index}`}>
            <h4 className="mono muted">{duration(expression.sim_ms)}</h4>
            <div><span className="eyebrow">Thought</span><MarkdownContent onOpenFile={path => void openSandbox(path)}>{expression.thought}</MarkdownContent></div>
            <div><span className="eyebrow">Reported feeling</span><MarkdownContent onOpenFile={path => void openSandbox(path)}>{expression.feeling}</MarkdownContent></div>
            <div><span className="eyebrow">Intention</span><MarkdownContent onOpenFile={path => void openSandbox(path)}>{expression.intention}</MarkdownContent></div>
          </article>)}</div>}
      </section>}
      {panel === "Dialogue" && <section>
        <p className="muted">Recorded conversation and provider text. Provider-returned reasoning is output from the configured model, not access to hidden mental state.</p>
        {!agent.history?.length ? <p className="muted">No dialogue records are available.</p> : <div className="transcript">
          {agent.history.map((message, index) => <article key={index}>
            <span className="eyebrow">{String(message.role)} {message.name ? `· ${String(message.name)}` : ""}</span>
            {Boolean(message.thinking || message.reasoning_content) && <details><summary>Provider-returned reasoning</summary>
              <MarkdownContent onOpenFile={path => void openSandbox(path)}>{String(message.thinking || message.reasoning_content)}</MarkdownContent></details>}
            {Boolean(message.content) && <MarkdownContent onOpenFile={path => void openSandbox(path)}>{String(message.content)}</MarkdownContent>}
            {Boolean(message.tool_calls) && <pre>{JSON.stringify(message.tool_calls, null, 2)}</pre>}
          </article>)}
        </div>}
        {historyCount > (agent.history?.length || 0) && <p className="muted">Showing the latest {agent.history?.length || 0} of {historyCount} dialogue records.</p>}
      </section>}
      {panel === "Learning & memory" && <section>
        <h3>Recorded learning</h3>
        {!learning.length ? <p className="muted">No learning entries have been recorded.</p> : <div className="learning-list">
          {learning.slice().reverse().map((entry, index) => <article key={`${entry.sim_ms}-${index}`}>
            <div className="section-heading"><span className="muted">{entry.category} · {duration(entry.sim_ms)}</span>
              {entry.task_id && <TaskEntityLink runId={runId} taskId={entry.task_id} onNavigate={onSelectTask} />}</div>
            <MarkdownContent onOpenFile={path => void openSandbox(path)}>{entry.text}</MarkdownContent>
          </article>)}
        </div>}
        {learning.length < (agent.learning_count ?? learning.length) && <p className="muted">Showing the latest {learning.length} of {agent.learning_count} learning entries.</p>}
        <details open className="memory-notebook"><summary>Persistent memory · {memoryCount} entries</summary>
          {!agent.memory?.length ? <p className="muted">No memory entries.</p> : <Notebook notes={agent.memory} count={memoryCount} openFile={path => void openSandbox(path)} />}
          {memoryCount > (agent.memory?.length || 0) && <p className="muted">Showing the latest {agent.memory?.length || 0} of {memoryCount} memory entries.</p>}
        </details>
      </section>}
      {panel === "Activity" && <section>
        <h3>Shared workspace</h3><p className="muted">{agent.sandbox_available ? "Open the actual shared sandbox directory in Explorer. Edits affect the simulation." : "The recorded snapshot is available while the container is stopped."}</p>
        <button type="button" onClick={() => void openSandbox()} disabled={sandboxBusy}>{sandboxBusy ? "Opening…" : agent.sandbox_available ? "Open sandbox directory" : "Open sandbox snapshot"}</button>
        <details className="workspace-events"><summary>Recorded file changes · latest {agent.workspace_events?.length || 0} of {agent.workspace_event_count || 0}</summary>
          <p className="muted">These events record paths used by workspace tools and subsequent modifications. They do not identify who changed a file.</p>
          {agent.workspace_events?.map(event => <details key={event.sequence}><summary>#{event.sequence} · {String(event.payload.path)}</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>)}
        </details>
        <h3>Tool and lifecycle events</h3><p className="muted">Latest {events.length} of up to 1,000 live records. Full run history remains in exports and replay.</p>
        {!events.length ? <p className="muted">No agent events recorded yet.</p> : <div className="event-feed">{events.filter(event => event.kind.startsWith("tool.") || event.kind.startsWith("agent.")).slice().reverse().map(event =>
          <details id={`event-${event.sequence}`} key={event.sequence} className={event.severity === "error" ? "event-error" : ""}>
            <summary><span className="mono muted">#{event.sequence} · {duration(event.sim_ms)}</span><strong>{event.kind}</strong></summary>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
            <div className="event-entities">{event.agent_id && <span>Agent <AgentEntityLink runId={runId} agentId={event.agent_id} onNavigate={onInspectAgent} /></span>}
              {event.task_id && <span>Task <TaskEntityLink runId={runId} taskId={event.task_id} onNavigate={onSelectTask} /></span>}</div>
          </details>)}</div>}
      </section>}
    </section>
  </div>;
}

export function AgentPage({ runId, agentId }: { runId: string; agentId: string }) {
  return <><header className="topbar"><Link className="brand" href="/">Ageatopia <span>OBSERVATORY</span></Link><span>Agent inspector</span></header>
    <main className="detail-main"><Inspector key={`${runId}-${agentId}`} runId={runId} agentId={agentId} /></main></>;
}
