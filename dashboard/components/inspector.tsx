"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Agent, EventRecord, api, duration } from "@/lib/types";
import { Trend } from "./charts";

export function Inspector({ runId, agentId }: { runId: string; agentId: string }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const [record, activity] = await Promise.all([
          api<Agent>(`/runs/${runId}/agents/${agentId}`),
          api<EventRecord[]>(`/runs/${runId}/events?agent_id=${agentId}`),
        ]);
        if (active) { setAgent(record); setEvents(activity); setError(""); }
      } catch (e) { if (active) setError((e as Error).message); }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => { active = false; clearInterval(timer); };
  }, [runId, agentId]);
  if (error) return <p role="alert" className="error">{error}</p>;
  if (!agent) return <p className="muted" role="status">Loading agent…</p>;
  return <div className="inspector">
    <div className="section-heading"><div><h2>{agent.name}</h2><span className="mono muted">{agent.id}</span></div>
      <span className={`state ${agent.state.toLowerCase()}`}>{agent.state}</span></div>
    <dl className="facts"><div><dt>Remaining</dt><dd>{duration(agent.lifetime_ms)}</dd></div>
      <div><dt>Model</dt><dd>{agent.model.model}</dd></div><div><dt>Context</dt><dd>{agent.context_profile}</dd></div>
      <div><dt>Task</dt><dd>{agent.task_id || "Unassigned"}</dd></div><div><dt>Processes</dt><dd>{agent.processes.join(", ") || "None"}</dd></div>
      <div><dt>Charged / queue</dt><dd>{duration(agent.total_charge_ms)} / {duration(agent.queue_ms)}</dd></div></dl>
    <Link className="text-link" href={`/runs/${runId}/agents/${agentId}`}>Open agent detail page ↗</Link>
    <details open><summary>Exact delivered context</summary><pre>{agent.context}</pre></details>
    <details><summary>Model settings and version</summary><pre>{JSON.stringify({ settings: agent.model, hash: agent.model_version }, null, 2)}</pre></details>
    <div className="chart-grid"><Trend title="Lifetime rewards and penalties" unit="minutes"
      points={(agent.lifetime_history || []).map(r => [r.sim_ms, r.balance_ms / 60000])} />
      <section><h3>Task history</h3><div className="table-scroll"><table><thead><tr><th>Task</th><th>Outcome</th><th>Time</th></tr></thead>
        <tbody>{agent.task_history?.map((t, i) => <tr key={i}><td>{t.task_id}</td><td>{t.result}</td><td>{duration(t.elapsed_ms || 0)}</td></tr>)}</tbody></table></div></section></div>
    <details><summary>Personal notebook</summary><pre>{agent.memory?.join("\n\n") || "No entries"}</pre></details>
    <details><summary>Changes to accessed workspace paths · latest {agent.workspace_events?.length || 0} of {agent.workspace_event_count || 0}</summary>
      <p className="muted">Paths recorded in this agent’s read and write calls. These records do not identify who changed the file.</p>
      {agent.workspace_events?.map(e => <details key={e.sequence}><summary>#{e.sequence} · {String(e.payload.path)}</summary><pre>{JSON.stringify(e.payload, null, 2)}</pre></details>)}
    </details>
    <details open><summary>Conversation and provider reasoning</summary>
      <div className="transcript">{agent.history?.map((m, i) => <article key={i}>
        <span className="eyebrow">{String(m.role)} {m.name ? `· ${m.name}` : ""}</span>
        {Boolean(m.thinking || m.reasoning_content) && <details><summary>Returned reasoning</summary><pre>{String(m.thinking || m.reasoning_content)}</pre></details>}
        {Boolean(m.content) && <pre>{String(m.content)}</pre>}
        {Boolean(m.tool_calls) && <pre>{JSON.stringify(m.tool_calls, null, 2)}</pre>}
      </article>)}</div></details>
    <details><summary>Tool and lifecycle events · first {events.length} records</summary>
      {events.filter(e => e.kind.startsWith("tool.") || e.kind.startsWith("agent.")).map(e =>
        <details id={`event-${e.sequence}`} key={e.sequence}><summary>#{e.sequence} · {duration(e.sim_ms)} · {e.kind}</summary><pre>{JSON.stringify(e.payload, null, 2)}</pre></details>)}
    </details>
  </div>;
}

export function AgentPage({ runId, agentId }: { runId: string; agentId: string }) {
  return <><header className="topbar"><Link href="/">Agent Utopia</Link><span>Agent inspector</span></header>
    <main className="detail-main"><Inspector runId={runId} agentId={agentId} /></main></>;
}
