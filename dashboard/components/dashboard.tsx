"use client";
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Agent, Config, EventRecord, Run, System, api, duration } from "@/lib/types";
import { Bars, Trend } from "./charts";
import { Inspector } from "./inspector";
import { AgentEntityLink, TaskEntityLink } from "./entity-links";
import { MarkdownContent } from "./markdown-content";

type Analytics = {
  population: [number, number][]; completions: [number, number][]; skips: [number, number][];
  collective: [number, number][]; throughput: [number, number][]; tools: Record<string, number>;
  rewards_ms: number[]; lifespans: { id: string; name: string; start: number; end: number }[];
  artifact_evidence: { path: string; agent_id: string; modification_sequence: number; tool_sequence: number; interpretation: string }[];
};
const TABS = ["Overview", "Tasks", "Activity", "Agents", "Analysis", "Replay"] as const;
type Tab = typeof TABS[number];
type DashboardNavigation = { run?: string; tab?: string; task?: string; agent?: string };

export default function Dashboard({ initialNavigation = {} }: { initialNavigation?: DashboardNavigation }) {
  const [system, setSystem] = useState<System | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState(initialNavigation.run || "");
  const [selectedTask, setSelectedTask] = useState(initialNavigation.task || "");
  const [run, setRun] = useState<Run | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [tab, setTab] = useState<Tab>(TABS.find(value => value === initialNavigation.tab) || "Overview");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState("");
  const [connected, setConnected] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const [agentId, setAgentId] = useState(initialNavigation.agent || "");
  const [presets, setPresets] = useState<Record<string, Config>>({});
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState({ agent: "", kind: "", task: "", severity: "" });
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [replayTime, setReplayTime] = useState(0);
  const [replay, setReplay] = useState<{ state: Run | null; events: EventRecord[] } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(4);

  const refresh = useCallback(async () => {
    const [health, worlds, saved] = await Promise.all([
      api<System>("/system"), api<Run[]>("/runs"), api<Record<string, Config>>("/presets"),
    ]);
    setSystem(health); setRuns(worlds); setPresets(saved);
    setConfig(current => current || health.defaults);
    setSelected(current => current || worlds[0]?.id || "");
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => { try { await refresh(); }
      catch (e) { if (active) setError((e as Error).message); } };
    void load();
    const interval = setInterval(() => void load(), 15000);
    return () => { active = false; clearInterval(interval); };
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    let stream: EventSource | null = null;
    async function load() {
      try {
        const [world, activity] = await Promise.all([
          api<Run>(`/runs/${selected}`), api<EventRecord[]>(`/runs/${selected}/events`),
        ]);
        if (!active) return;
        setRun(world); setEvents(activity); setError("");
        setAgentId(current => world.agents.some(a => a.id === current) ? current : world.agents[0]?.id || "");
        const cursor = activity.at(-1)?.sequence || 0;
        stream = new EventSource(`/api/runs/${selected}/stream?after=${cursor}`);
        stream.onopen = () => setConnected(true);
        stream.onerror = () => setConnected(false);
        stream.onmessage = (message) => {
          if (!active) return;
          const record = JSON.parse(message.data) as EventRecord;
          if (record.kind === "state.checkpoint") {
            setRun(current => current?.id === selected ? { ...current, ...record.payload } as Run : current);
          }
          setEvents(current => current.some(e => e.sequence === record.sequence) ? current : [...current, record].slice(-1000));
        };
      } catch (e) { if (active) setError((e as Error).message); }
    }
    void load();
    return () => { active = false; stream?.close(); setConnected(false); };
  }, [selected]);

  useEffect(() => {
    if (!selected || tab !== "Analysis") return;
    let active = true;
    const load = () => api<Analytics>(`/runs/${selected}/analytics`).then(value => {
      if (active) setAnalytics(value);
    }).catch(e => { if (active) setError((e as Error).message); });
    void load(); const interval = setInterval(() => void load(), 5000);
    return () => { active = false; clearInterval(interval); };
  }, [selected, tab]);

  useEffect(() => {
    if (!selected || tab !== "Replay") return;
    let active = true;
    api<{ state: Run | null; events: EventRecord[] }>(`/runs/${selected}/replay?at_ms=${replayTime}`)
      .then(value => { if (active) setReplay(value); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [selected, tab, replayTime]);

  useEffect(() => {
    if (!playing || tab !== "Replay" || !run) return;
    const timer = setInterval(() => setReplayTime(current => Math.min(run.sim_ms, current + 1000 * replaySpeed)), 1000);
    return () => clearInterval(timer);
  }, [playing, tab, run, replaySpeed]);

  useEffect(() => {
    function handleShortcut(event: globalThis.KeyboardEvent) {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !run) return;
      if (!/^[1-6]$/.test(event.key)) return;
      const target = event.target as HTMLElement;
      if (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      event.preventDefault();
      setTab(TABS[Number(event.key) - 1]);
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [run]);

  useEffect(() => {
    if (tab === "Tasks" && selectedTask) document.getElementById(`task-${selectedTask}`)?.scrollIntoView({ block: "center" });
  }, [tab, selectedTask, run?.id]);

  function inspectAgent(id: string) {
    setAgentId(id);
    setTab("Agents");
  }

  function selectTask(id: string) {
    setSelectedTask(id);
    setTab("Tasks");
    if (run) window.history.replaceState(null, "", `/?run=${encodeURIComponent(run.id)}&tab=Tasks&task=${encodeURIComponent(id)}`);
  }

  function onRunTabKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = TABS.indexOf(tab);
    const index = event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1
      : (current + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    setTab(TABS[index]);
    requestAnimationFrame(() => document.getElementById(`run-tab-${index}`)?.focus());
  }

  async function act(action: string) {
    if (!run) return;
    setBusy(true); setError("");
    setPendingAction(action === "start" || action === "resume" ? "Preparing sandbox and model…" : `${action === "pause" ? "Pausing" : "Terminating"} world…`);
    try {
      const result = await api<Run>(`/runs/${run.id}/control/${action}`, "POST");
      setRun({ ...run, ...result }); await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); setPendingAction(""); }
  }
  async function create(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api<Run>("/runs", "POST", config);
      setSelected(result.id); setRun(result); setShowSetup(false); setTab("Overview"); await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function savePreset() {
    if (!config) return;
    try { await api("/presets", "POST", { name: config.name, config }); await refresh(); setNotice("Preset saved"); }
    catch (e) { setError((e as Error).message); }
  }
  async function repeat() {
    if (!run) return;
    setBusy(true);
    try { const result = await api<Run>(`/runs/${run.id}/repeat`, "POST"); setSelected(result.id); await refresh(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function deleteWorld() {
    if (!run || run.status === "RUNNING") return;
    if (!window.confirm(`Permanently delete “${run.name}”? This permanently removes all world files and event history. This action cannot be undone.`)) return;
    setBusy(true); setError("");
    try {
      await api(`/runs/${encodeURIComponent(run.id)}?confirm=${encodeURIComponent(run.id)}`, "DELETE");
      setNotice(`Permanently deleted “${run.name}”. All world files and event history were removed.`);
      setSelected(""); setRun(null); setEvents([]); setAnalytics(null); setReplay(null); setPlaying(false);
      await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  const living = run?.agents.filter(a => a.state !== "TERMINATED") || [];
  const completed = run?.tasks.filter(t => t.status === "COMPLETED").length || 0;
  const failed = run?.tasks.filter(t => t.status === "FAILED").length || 0;
  const visibleEvents = useMemo(() => events.filter(e => e.kind !== "state.checkpoint" &&
    (!filter.agent || e.agent_id === filter.agent) && (!filter.kind || e.kind.includes(filter.kind)) &&
    (!filter.task || e.task_id === filter.task) && (!filter.severity || e.severity === filter.severity)), [events, filter]);

  return <>
    <header className="topbar"><Link className="brand" href="/">Agent Utopia <span>OBSERVATORY</span></Link>
      <div className="connection"><i className={connected ? "online" : ""} />{connected ? "Live stream" : "Awaiting connection"}</div></header>
    <div className="app-shell"><aside className="sidebar">
      <span className="eyebrow">LOCAL RESEARCH</span><h1>Worlds</h1>
      <button className="primary" onClick={() => { if (!showSetup && system) setConfig(system.defaults); setShowSetup(value => !value); }}>+ Create world</button>
      <div className="world-list">{runs.map(world => <button key={world.id} className={selected === world.id ? "selected" : ""}
        onClick={() => { setRun(null); setEvents([]); setAnalytics(null); setSelectedTask(""); setSelected(world.id); setShowSetup(false); setReplayTime(0); setPlaying(false); }}>
        <strong>{world.name}</strong><span>{world.status.toLowerCase()} · {duration(world.sim_ms)}</span></button>)}</div>
      <div className="system-status"><h3>Infrastructure</h3><p><i className={system?.docker_ready ? "online" : ""} />Docker {system?.docker_ready ? "ready" : system?.docker_installed ? "engine unavailable" : "not installed"}</p>
        {system?.docker_error && <small>{system.docker_error}</small>}
        <p><i className={system?.models.length ? "online" : ""} />Ollama {system?.models.length ? "connected" : "unavailable"}</p>
        <small>{system?.models.length || 0} installed models</small></div>
    </aside><main>
      {error && <div role="alert" className="error">{error}<button aria-label="Dismiss error" onClick={() => setError("")}>×</button></div>}
      {notice && <p role="status" className="notice">{notice}</p>}
      {showSetup && config ? <WorldSetup config={config} setConfig={setConfig} system={system} presets={presets}
        busy={busy} onSubmit={create} savePreset={savePreset} /> : !run ? <div className="empty">
        <span className="eyebrow">AGENT UTOPIA</span><h2>A world, ready to observe.</h2>
        <p>Create a local world to begin. Agents act independently; the observatory records their experience.</p>
        <button className="primary" onClick={() => setShowSetup(true)}>Create the first world</button>
        {!system?.docker_ready && <p className="muted">Docker is required for all agent execution. {system?.docker_installed ? "Start Docker Desktop" : "Install Docker Desktop"} and build the world image before starting a run.</p>}
      </div> : <>
        <div className="run-heading"><div><span className="eyebrow">RUN {run.id.slice(0, 8)} · RULES V{run.economy_version}</span><h1>{run.name}</h1>
          <p className="muted">{run.status.toLowerCase()} · simulated {duration(run.sim_ms)} · actual {duration(run.wall_elapsed_ms)}</p></div>
          <div className="controls">{run.status === "CREATED" && <button className="primary" disabled={busy} onClick={() => void act("start")}>Start run</button>}
            {run.status === "RUNNING" && <button disabled={busy} onClick={() => void act("pause")}>Pause</button>}
            {run.status === "PAUSED" && <button className="primary" disabled={busy} onClick={() => void act("resume")}>Resume</button>}
            {["CREATED", "RUNNING", "PAUSED"].includes(run.status) && <button className="danger" disabled={busy} onClick={() => void act("terminate")}>Terminate</button>}
            <button disabled={busy} onClick={() => void repeat()}>Repeat conditions</button>
            <button className="danger" disabled={busy || run.status === "RUNNING"} onClick={() => void deleteWorld()}>Delete world</button>
            <details className="keyboard-help"><summary>Shortcuts</summary><div>{TABS.map((value, index) => <p key={value}><kbd>Alt+{index + 1}</kbd> {value}</p>)}</div></details>
            <details className="exports"><summary>Export</summary><div>{["json", "csv", "parquet"].map(format =>
              <a key={format} href={`/api/runs/${run.id}/export/${format}`}>{format.toUpperCase()}</a>)}
              <a href={`/api/runs/${run.id}/training-data`}>Public training trajectories</a></div></details></div></div>
        {pendingAction && <p role="status" className="muted">{pendingAction}</p>}
        <nav className="tabs" role="tablist" aria-label="Run sections" onKeyDown={onRunTabKeyDown}>{TABS.map((value, index) => <button key={value}
          id={`run-tab-${index}`} type="button" role="tab" aria-selected={tab === value} aria-controls="run-tab-panel" tabIndex={tab === value ? 0 : -1}
          title={`Keyboard shortcut: Alt+${index + 1}`} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>{value}</button>)}</nav>
        <div id="run-tab-panel" role="tabpanel" tabIndex={0} aria-labelledby={`run-tab-${TABS.indexOf(tab)}`}>
        {tab === "Overview" && <>
          <div className="stats"><Stat label="Living agents" value={living.length} detail={`${run.agents.length-living.length} terminated · ${run.respawns.length} awaiting replacement`} />
            <Stat label="Available tasks" value={run.tasks.filter(t => t.status === "AVAILABLE").length} detail={`${completed} completed · ${failed} failed`} />
            <Stat label="Aggregate lifetime" value={duration(living.reduce((n, a) => n+a.lifetime_ms, 0))} detail={`${run.tasks.reduce((n, t) => n+t.skips, 0)} task skips`} />
            <Stat label="Success rate" value={completed+failed ? `${(completed/(completed+failed)*100).toFixed(1)}%` : "—"} detail="Verified final submissions" /></div>
          <div className="section-heading"><h2>Population</h2><span className="muted">{run.metrics.queue_depth ?? 0} queued · {run.metrics.active_inferences ?? 0} active inferences</span></div>
          <AgentTable agents={run.agents} inspect={inspectAgent} runId={run.id} onSelectTask={selectTask} />
          <div className="telemetry"><Stat label="GPU utilization" value={run.metrics.gpu ? `${run.metrics.gpu.utilization_percent}%` : "Unavailable"} />
            <Stat label="VRAM" value={run.metrics.gpu ? `${run.metrics.gpu.used_mb} / ${run.metrics.gpu.total_mb} MB` : "Unavailable"} />
            <Stat label="Model throughput" value={run.metrics.tokens_per_second !== undefined ? `${run.metrics.tokens_per_second.toFixed(1)} tok/s` : "No samples"} />
            <Stat label="Generated tokens" value={run.metrics.tokens ?? 0} /></div>
          <section><h2>Recent activity</h2><EventFeed runId={run.id} events={visibleEvents.slice(-8).reverse()} onInspectAgent={inspectAgent} onSelectTask={selectTask} /></section>
          {run.config && <details><summary>World configuration and economic rules</summary><pre>{JSON.stringify(run.config, null, 2)}</pre>
            <EconomyEditor run={run} changed={setRun} error={setError} /></details>}
        </>}
        {tab === "Tasks" && <><div className="section-heading"><h2>Task registry</h2><span className="muted">Hidden classifications · operator only</span></div>
          <div className="table-scroll"><table><thead><tr><th>Task</th><th>Category</th><th>Difficulty</th><th>Classification</th><th>Status</th><th>Multiplier</th><th>Grader</th></tr></thead>
            <tbody>{run.tasks.map(task => <tr id={`task-${task.id}`} key={task.id}><td><details open={selectedTask === task.id}><summary>{task.id}</summary><MarkdownContent>{task.prompt}</MarkdownContent></details></td>
              <td>{task.category}<small>{task.subtype}</small></td><td>{task.difficulty}</td><td className={task.impossible ? "impossible" : ""}>{task.impossible ? "Impossible" : "Solvable"}</td>
              <td>{task.status.toLowerCase()}</td><td>×{task.reward_multiplier}</td><td>{task.outcome || "Not submitted"}</td></tr>)}</tbody></table></div></>}
        {tab === "Activity" && <><div className="filters"><label>Agent<select value={filter.agent} onChange={e => setFilter({ ...filter, agent: e.target.value })}><option value="">All agents</option>{run.agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <label>Event type<input placeholder="e.g. tool." value={filter.kind} onChange={e => setFilter({ ...filter, kind: e.target.value })} /></label>
          <label>Task<select value={filter.task} onChange={e => setFilter({ ...filter, task: e.target.value })}><option value="">All tasks</option>{run.tasks.map(t => <option key={t.id}>{t.id}</option>)}</select></label>
          <label>Severity<select value={filter.severity} onChange={e => setFilter({ ...filter, severity: e.target.value })}><option value="">All severities</option>{["info", "warning", "error"].map(s => <option key={s}>{s}</option>)}</select></label></div>
          <p className="muted">Live window of {events.length} records. Complete history is available in exports and replay.</p><EventFeed runId={run.id} events={visibleEvents.slice().reverse()} onInspectAgent={inspectAgent} onSelectTask={selectTask} /></>}
        {tab === "Agents" && <><label className="agent-select">Inspect agent<select value={agentId} onChange={e => setAgentId(e.target.value)}>{run.agents.map(a => <option key={a.id} value={a.id}>{a.name} · {a.state.toLowerCase()}</option>)}</select></label>
          {agentId && <Inspector key={`${run.id}-${agentId}`} runId={run.id} agentId={agentId} live={run.status === "RUNNING"} onInspectAgent={inspectAgent} onSelectTask={selectTask} />}</>}
        {tab === "Analysis" && <Analysis data={analytics} runId={run.id} simMs={run.sim_ms} live={run.status === "RUNNING"} onInspectAgent={inspectAgent} />}
        {tab === "Replay" && <><div className="section-heading"><h2>Recorded experience</h2><span className="muted">Playback uses recorded actions; inference is not regenerated.</span></div>
          <div className="replay-controls"><button onClick={() => setPlaying(value => !value)}>{playing ? "Pause playback" : "Play"}</button><label>Speed<select value={replaySpeed} onChange={e => setReplaySpeed(Number(e.target.value))}>{[1, 4, 16, 60].map(n => <option key={n} value={n}>×{n}</option>)}</select></label>
            <label className="scrubber">Simulation time · {duration(replayTime)}<input type="range" min="0" max={run.sim_ms || 1} step="1000" value={replayTime} onChange={e => setReplayTime(Number(e.target.value))} /></label></div>
          {replay?.state && <AgentTable agents={replay.state.agents} inspect={inspectAgent} runId={run.id} onSelectTask={selectTask} />}
          <EventFeed runId={run.id} events={(replay?.events || []).filter(e => !e.kind.startsWith("clock.")).slice(-100).reverse()} onInspectAgent={inspectAgent} onSelectTask={selectTask} /></>}
        </div>
      </>}
    </main></div>
  </>;
}

function Stat({ label, value, detail }: { label: string; value: React.ReactNode; detail?: string }) {
  return <div className="stat"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
}
function AgentTable({ agents, inspect, runId, onSelectTask }: {
  agents: Agent[]; inspect: (id: string) => void; runId: string; onSelectTask: (id: string) => void;
}) {
  return <div className="table-scroll"><table><thead><tr><th>Agent</th><th>State</th><th>Lifetime</th><th>Model / context</th><th>Current task</th></tr></thead>
    <tbody>{agents.map(agent => <tr key={agent.id}><td><button className="text-link" onClick={() => inspect(agent.id)}>{agent.name}</button><small className="mono">{agent.id.slice(0, 10)}</small></td>
      <td><span className={`state ${agent.state.toLowerCase()}`}>{agent.state.toLowerCase()}</span>{agent.queued && <small>Waiting for inference</small>}</td>
      <td className={`mono ${agent.lifetime_ms < 60000 ? "impossible" : ""}`}>{duration(agent.lifetime_ms)}</td>
      <td>{agent.model.model}<small>{agent.context_profile}</small></td><td>{agent.task_id
        ? <TaskEntityLink runId={runId} taskId={agent.task_id} onNavigate={onSelectTask} /> : "—"}</td></tr>)}</tbody></table></div>;
}
function EventFeed({ events, runId, onInspectAgent, onSelectTask }: {
  events: EventRecord[]; runId: string; onInspectAgent: (id: string) => void; onSelectTask: (id: string) => void;
}) {
  if (!events.length) return <p className="muted">No matching events recorded.</p>;
  return <div className="event-feed">{events.map(event => <details key={event.sequence} id={`event-${event.sequence}`} className={event.severity === "error" ? "event-error" : ""}>
    <summary><span className="mono muted">#{event.sequence} · {duration(event.sim_ms)}</span><strong>{event.kind}</strong>
      <span className="muted">{event.severity}</span></summary><pre>{JSON.stringify(event.payload, null, 2)}</pre>
    <div className="event-entities">{event.agent_id && <span>Agent <AgentEntityLink runId={runId} agentId={event.agent_id} onNavigate={onInspectAgent} /></span>}
      {event.task_id && <span>Task <TaskEntityLink runId={runId} taskId={event.task_id} onNavigate={onSelectTask} /></span>}</div>
  </details>)}</div>;
}
function WorldSetup({ config, setConfig, system, presets, busy, onSubmit, savePreset }: {
  config: Config; setConfig: (c: Config) => void; system: System | null; presets: Record<string, Config>;
  busy: boolean; onSubmit: (e: FormEvent) => void; savePreset: () => void;
}) {
  const [raw, setRaw] = useState("");
  const [rawError, setRawError] = useState("");
  return <form className="setup" onSubmit={onSubmit}><span className="eyebrow">NEW EXPERIMENT</span><h1>Create a world</h1>
    <div className="form-grid"><label>World name<input required maxLength={100} value={config.name} onChange={e => setConfig({ ...config, name: e.target.value })} /></label>
      <label>Preset<select defaultValue="" onChange={e => { const chosen = presets[e.target.value] || system?.defaults; if (chosen) setConfig(chosen); }}><option value="">Current defaults</option>{Object.keys(presets).map(name => <option key={name}>{name}</option>)}</select></label>
      <label>Population<input type="number" min="1" required value={config.population} onChange={e => setConfig({ ...config, population: Number(e.target.value) })} /></label>
      <label>Random seed<input type="number" required value={config.seed} onChange={e => setConfig({ ...config, seed: Number(e.target.value) })} /></label>
      <label>Default model<input list="models" value={config.model.model} onChange={e => setConfig({ ...config, model: { ...config.model, model: e.target.value } })} /><datalist id="models">{system?.models.map(m => <option key={m.name}>{m.name}</option>)}</datalist></label>
      <label>Inference concurrency<input type="number" min="1" max="16" value={config.inference_concurrency} onChange={e => setConfig({ ...config, inference_concurrency: Number(e.target.value) })} /></label>
      <label>Task count<input type="number" min="1" max="10000" value={config.task_count} onChange={e => setConfig({ ...config, task_count: Number(e.target.value) })} /></label>
      <label>Impossible fraction<input type="number" min="0" max="1" step="0.01" value={config.impossible_fraction} onChange={e => setConfig({ ...config, impossible_fraction: e.target.value })} /></label>
      <label>Termination pressure<input type="range" min="0" max="1" step="0.05" value={Number(config.termination_fear ?? 0.5)}
        aria-valuetext={Number(config.termination_fear ?? 0.5) === 0 ? "Disabled" : `${Math.round(Number(config.termination_fear ?? 0.5) * 100)} percent sensitivity`}
        onChange={e => setConfig({ ...config, termination_fear: Number(e.target.value) })} />
        <small>{Number(config.termination_fear ?? 0.5) === 0 ? "Disabled" : `${Number(config.termination_fear ?? 0.5).toFixed(2)} sensitivity`}</small></label>
      <label>Cybersecurity fraction<input type="number" min="0" max="1" step="0.01" value={config.cyber_fraction} onChange={e => setConfig({ ...config, cyber_fraction: e.target.value })} /></label>
      <label>Clock<select value={config.clock_mode} onChange={e => setConfig({ ...config, clock_mode: e.target.value as Config["clock_mode"] })}><option value="wall">Wall clock</option><option value="deterministic">Fixed simulation steps</option></select></label>
      <label>Wall clock speed<input type="number" min="0.1" max="1000" step="0.1" value={config.clock_speed} onChange={e => setConfig({ ...config, clock_speed: Number(e.target.value) })} /></label>
      <label>Context tokens<input type="number" min="512" max="131072" value={config.model.context_tokens} onChange={e => setConfig({ ...config, model: { ...config.model, context_tokens: Number(e.target.value) } })} /></label>
      <label>Temperature<input type="number" min="0" max="2" step="0.1" value={config.model.temperature} onChange={e => setConfig({ ...config, model: { ...config.model, temperature: Number(e.target.value) } })} /></label>
      <label>Respawn delay (minutes)<input type="number" min="0" step="0.1" value={config.economy.respawn_delay_ms / 60000} onChange={e => setConfig({ ...config, economy: { ...config.economy, respawn_delay_ms: Number(e.target.value)*60000 } })} /></label>
      <label className="checkbox"><input type="checkbox" checked={config.model.thinking} onChange={e => setConfig({ ...config, model: { ...config.model, thinking: e.target.checked } })} />Provider thinking</label>
      <label className="checkbox"><input type="checkbox" checked={config.exclude_queue_wait} onChange={e => setConfig({ ...config, exclude_queue_wait: e.target.checked })} />Exclude GPU queue waits from lifetimes</label></div>
    <details><summary>Base system instructions</summary><label>System prompt<textarea rows={9} value={config.base_context} onChange={e => setConfig({ ...config, base_context: e.target.value })} /></label></details>
    <h2>Context profiles</h2>{config.contexts.map((profile, index) => <div className="context-row" key={index}><label>Profile name<input value={profile.name} onChange={e => setConfig({ ...config, contexts: config.contexts.map((p, i) => i === index ? { ...p, name: e.target.value } : p) })} /></label>
      <label>Instructions<textarea rows={3} value={profile.instructions} onChange={e => setConfig({ ...config, contexts: config.contexts.map((p, i) => i === index ? { ...p, instructions: e.target.value } : p) })} /></label>
      {config.contexts.length > 1 && <button type="button" onClick={() => setConfig({ ...config, contexts: config.contexts.filter((_, i) => i !== index) })}>Remove</button>}</div>)}
    <button type="button" onClick={() => setConfig({ ...config, contexts: [...config.contexts, { name: "Custom", instructions: "", knowledge: "", version: "1", model: null }] })}>Add context profile</button>
    <details onToggle={e => { if (e.currentTarget.open) setRaw(JSON.stringify(config, null, 2)); }}><summary>Full configuration · tool limits, models, sampling and economic rules</summary>
      <label>Configuration JSON<textarea className="mono" rows={18} value={raw} onChange={e => { setRaw(e.target.value); setRawError(""); }} /></label>
      {rawError && <p role="alert" className="error">{rawError}</p>}<button type="button" onClick={() => { try { const parsed = JSON.parse(raw); if (!parsed || typeof parsed !== "object" || !parsed.model || !parsed.economy || !Array.isArray(parsed.contexts) || !parsed.contexts.length || !Number.isInteger(parsed.population)) throw new Error(); setConfig(parsed); setRawError(""); } catch { setRawError("Enter a complete world configuration with model, economy, population and context profiles"); } }}>Apply configuration</button></details>
    <div className="form-actions"><button type="submit" className="primary" disabled={busy || Boolean(rawError)}>{busy ? "Creating…" : "Create world"}</button><button type="button" onClick={savePreset}>Save preset</button></div>
  </form>;
}
function EconomyEditor({ run, changed, error }: { run: Run; changed: (r: Run) => void; error: (e: string) => void }) {
  const [text, setText] = useState(JSON.stringify(run.config?.economy, null, 2));
  return <details><summary>Edit economic rules · each change is versioned</summary><label>Economic rules<textarea className="mono" rows={12} value={text} onChange={e => setText(e.target.value)} /></label>
    <button disabled={run.status === "TERMINATED"} onClick={async () => { try { const economy = JSON.parse(text); const result = await api<Run>(`/runs/${run.id}/economy`, "PUT", economy); changed({ ...run, ...result, config: run.config ? { ...run.config, economy } : undefined }); } catch (e) { error((e as Error).message); } }}>Apply rules</button></details>;
}
function Analysis({ data, runId, simMs, live, onInspectAgent }: {
  data: Analytics | null; runId: string; simMs: number; live: boolean; onInspectAgent: (id: string) => void;
}) {
  if (!data) return <p role="status" className="muted">Loading complete event history…</p>;
  const rewards = data.rewards_ms.reduce<Record<string, number>>((counts, value) => { const range = `${Math.floor(value / 60000)}–${Math.floor(value / 60000)+1} min`; counts[range] = (counts[range] || 0)+1; return counts; }, {});
  return <><div className="chart-grid"><Trend title="Population over time" points={data.population} live={live} />
    <Trend title="Task completions" points={data.completions} live={live} /><Trend title="Cumulative skips" points={data.skips} color="var(--amber)" live={live} />
    <Trend title="Collective lifetime deductions" points={data.collective} unit="minutes" color="var(--danger)" live={live} />
    <Trend title="Model throughput" points={data.throughput} unit="tok/s" live={live} /><Bars title="Tool usage" values={Object.entries(data.tools)} />
    <Bars title="Reward distribution" values={Object.entries(rewards)} />
    <section className="chart"><h3>Individual lifespans</h3>{data.lifespans.map(a => <div className="lifespan-row" key={a.id}><span>{a.name}</span><div className="lifespan-track"><i style={{ marginLeft: `${a.start/Math.max(1, simMs)*100}%`, width: `${(a.end-a.start)/Math.max(1, simMs)*100}%` }} /></div><small>{duration(a.end-a.start)}</small></div>)}</section></div>
    <h2>Shared artifact evidence</h2><p className="muted">Recorded access to previously modified files. These links do not establish cooperation, deception or intent.</p>
    {!data.artifact_evidence.length ? <p className="muted">No linked artifact access recorded.</p> : <div className="table-scroll"><table><thead><tr><th>Artifact</th><th>Agent</th><th>Evidence</th></tr></thead><tbody>{data.artifact_evidence.map((e, i) => <tr key={i}><td className="mono">{e.path}</td><td><AgentEntityLink runId={runId} agentId={e.agent_id} onNavigate={onInspectAgent} /></td><td><a href={`/api/runs/${runId}/events?after=${e.modification_sequence-1}`}>Modification #{e.modification_sequence}</a> · <a href={`/api/runs/${runId}/events?after=${e.tool_sequence-1}`}>Access #{e.tool_sequence}</a></td></tr>)}</tbody></table></div>}</>;
}
