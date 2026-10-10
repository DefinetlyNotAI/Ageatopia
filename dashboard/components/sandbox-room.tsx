"use client";

import { type CSSProperties, useEffect, useMemo, useState } from "react";
import { Agent, EventRecord, Run, api, duration } from "@/lib/types";

type FileEntry = { path: string; size?: number; mode?: number; sha256?: string; symlink?: string };
type RoomData = { sim_ms: number; sandbox_available: boolean; image: string; files: FileEntry[];
  limits: { workspace_mb: number; memory_mb: number; cpu_count: number; pid_limit: number; timeout_seconds: number } };
type Mood = "idle" | "thinking" | "queued" | "reading" | "writing" | "running" | "remembering" | "reflecting" | "relaxing" | "choosing" | "celebrating" | "error" | "sleeping";
type Zone = "files" | "terminal" | "memory" | "lounge" | "tasks";
const ZONES: { id: Zone; name: string; caption: string; x: number; y: number }[] = [
  { id: "files", name: "File library", caption: "Shared /workspace", x: 19, y: 18 },
  { id: "tasks", name: "Task board", caption: "Choose · solve · submit", x: 49, y: 12 },
  { id: "terminal", name: "Workbench", caption: "Code & processes", x: 80, y: 20 },
  { id: "lounge", name: "Quiet corner", caption: "Rest & reflection", x: 16, y: 55 },
  { id: "memory", name: "Notebook desk", caption: "Personal memory", x: 82, y: 55 },
];
const COLORS = ["#91c9a0", "#e9b27f", "#9dbbd7", "#e1ca75", "#c7add1", "#e5a5ad"];
const CAPTIONS: Record<Mood, string> = { idle: "Taking a breath", thinking: "Thinking", queued: "Waiting for model", reading: "Reading files",
  writing: "Writing files", running: "Running a tool", remembering: "Using memory", reflecting: "Reflecting", relaxing: "On a break",
  choosing: "At the task board", celebrating: "Task complete!", error: "Tool needs attention", sleeping: "Terminated" };

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function bytes(value: number) { return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`; }
function signature(text: string) { return [...text].reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 0); }
function toolLabel(event: EventRecord) {
  const operation = object(event.payload.arguments).operation;
  return `${String(event.payload.name || event.kind)}${operation ? ` · ${operation}` : ""}`;
}
function toolActivity(event: EventRecord): { mood: Mood; zone: Zone; event: EventRecord } {
  const name = event.payload.name;
  const operation = object(event.payload.arguments).operation;
  if (name === "workspace") {
    if (["read", "list", "search"].includes(String(operation))) return { mood: "reading", zone: "files", event };
    if (operation === "write") return { mood: "writing", zone: "files", event };
    return { mood: "running", zone: "terminal", event };
  }
  if (name === "memory") return { mood: "remembering", zone: "memory", event };
  if (name === "reflect") return { mood: "reflecting", zone: "lounge", event };
  if (name === "relax") return { mood: "relaxing", zone: "lounge", event };
  if (name === "task") return { mood: "choosing", zone: "tasks", event };
  return { mood: "running", zone: "terminal", event };
}
function toolResult(call: EventRecord, events: EventRecord[]) {
  const following = events.find(event => event.sequence > call.sequence && event.agent_id === call.agent_id &&
    (event.kind === "tool.invoked" || event.kind === "tool.result" && event.payload.name === call.payload.name));
  return following?.kind === "tool.result" ? following : undefined;
}
function activity(agent: Agent, records: EventRecord[], live: boolean, now: number): { mood: Mood; zone: Zone; event?: EventRecord; recent?: boolean } {
  const own = records.filter(event => event.agent_id === agent.id);
  const latest = own.findLast(event => event.kind === "tool.invoked" || event.kind.startsWith("inference.") || event.kind === "agent.state");
  const invoked = own.findLast(event => event.kind === "tool.invoked");
  if (agent.state === "TERMINATED") return { mood: "sleeping", zone: "lounge" };
  if (!live) return invoked ? toolActivity(invoked) : { mood: "idle", zone: agent.task_id ? "tasks" : "lounge" };
  const recent = own.filter(event => now - event.wall_ms >= 0 && now - event.wall_ms < 6500);
  if (recent.some(event => event.kind === "task.completed")) return { mood: "celebrating", zone: "tasks" };
  if (recent.some(event => event.severity === "error" || event.kind === "tool.result" && object(event.payload.result).error)) return { mood: "error", zone: "terminal" };
  if (agent.state === "RELAX") return { mood: "relaxing", zone: "lounge", event: invoked };
  if (invoked && now - invoked.wall_ms >= 0 && now - invoked.wall_ms < 3000 && toolResult(invoked, own)) return { ...toolActivity(invoked), recent: true };
  if (agent.queued) return { mood: "queued", zone: "tasks" };
  if (latest?.kind === "inference.started") return { mood: "thinking", zone: "tasks" };
  if (invoked && (latest === invoked) && (now - invoked.wall_ms < 15000 || !own.some(event => event.kind === "tool.result" && event.sequence > invoked.sequence))) {
    return toolActivity(invoked);
  }
  return { mood: "idle", zone: agent.task_id ? "tasks" : "lounge" };
}

function Mascot({ seed, mood }: { seed: number; mood: Mood }) {
  const variant = seed % 6;
  return <svg className={`room-mascot mood-${mood}`} viewBox="0 0 110 110" aria-hidden="true" style={{ "--pet-color": COLORS[variant] } as CSSProperties}>
    <ellipse className="pet-shadow" cx="55" cy="99" rx="28" ry="5" fill="#334d3920" />
    <g className="pet-body">
      <g className="pet-feet" fill="#44634d"><ellipse cx="39" cy="94" rx="9" ry="5" /><ellipse cx="71" cy="94" rx="9" ry="5" /></g>
      {variant === 0 && <><path d="M54 23Q38 4 26 13Q28 29 54 25M54 23Q62 3 79 9Q82 26 54 25" fill="#508e62" /><path d="M55 28V18" stroke="#44634d" strokeWidth="3" /></>}
      {variant === 1 && <path d="M24 39L20 11L43 28M67 28L90 11L86 39" fill="var(--pet-color)" stroke="#694e38" strokeWidth="2" />}
      {variant === 2 && <path d="M23 47Q4 29 23 25Q29 8 43 22Q52 7 65 23Q89 9 88 37" fill="var(--pet-color)" stroke="#46657e" strokeWidth="2" />}
      {variant === 3 && <path d="M36 34L55 7L73 34L102 40L84 64L88 90L59 85L30 96L27 70L9 45Z" fill="var(--pet-color)" stroke="#78652f" strokeWidth="2" />}
      {variant === 4 && <><path d="M25 40Q18 13 54 11Q92 11 88 41" fill="#8e6c9e" stroke="#624570" strokeWidth="2" /><circle cx="40" cy="22" r="4" fill="#eee0e9" /><circle cx="67" cy="19" r="5" fill="#eee0e9" /></>}
      {variant === 5 && <><path d="M55 29V16" stroke="#765159" strokeWidth="3" /><circle cx="55" cy="12" r="5" fill="#be737e" /><rect x="12" y="47" width="10" height="22" rx="4" fill="#765159" /><rect x="88" y="47" width="10" height="22" rx="4" fill="#765159" /></>}
      <path d={variant === 3 ? "M29 46Q30 29 55 29Q80 29 81 46L82 72Q83 92 55 93Q27 92 28 72Z" : variant === 5 ? "M31 29H79Q91 29 91 44V78Q91 94 75 94H35Q19 94 19 78V44Q19 29 31 29Z" : "M21 59Q19 29 55 28Q91 29 89 59L88 76Q85 97 55 95Q25 97 22 76Z"}
        fill="var(--pet-color)" stroke="#3c5145" strokeWidth="2" />
      <g className="pet-hand" stroke="#3c5145" strokeWidth="2" fill="var(--pet-color)"><path d="M22 64Q6 56 12 74Q17 80 24 76" /><path d="M88 64Q104 56 98 74Q93 80 86 76" /></g>
      <g className="pet-eyes"><ellipse cx="40" cy="56" rx="9" ry="12" fill="#fffdf7" /><ellipse cx="70" cy="56" rx="9" ry="12" fill="#fffdf7" />
        <g className="pet-pupils" fill="#253d33"><ellipse cx="42" cy="57" rx="4" ry="6" /><ellipse cx="68" cy="57" rx="4" ry="6" /></g></g>
      <ellipse cx="30" cy="71" rx="6" ry="3" fill="#da8a85" opacity=".65" /><ellipse cx="80" cy="71" rx="6" ry="3" fill="#da8a85" opacity=".65" />
      <path className="pet-smile" d="M48 73Q55 81 62 73" stroke="#3c5145" strokeWidth="2" strokeLinecap="round" fill="none" />
      {["reading", "writing", "remembering", "choosing"].includes(mood) && <g className="pet-book"><path d="M28 80L55 84L81 80V97L55 102L28 97Z" fill={mood === "remembering" ? "#a68bb7" : "#f9f1ce"} stroke="#746744" strokeWidth="2" /><path d="M55 84V102M34 87L49 90M62 90L75 87" stroke="#b9a877" />{mood === "writing" && <path className="pet-pen" d="M76 71L64 93" stroke="#386d54" strokeWidth="4" />}</g>}
      {mood === "relaxing" && <g className="pet-cup"><path d="M44 81H65V97H44ZM65 84Q79 81 72 92H65" fill="#fff9e7" stroke="#746744" strokeWidth="2" /><path className="pet-steam" d="M51 77Q45 70 51 64M60 77Q54 70 60 64" fill="none" stroke="#a08b6b" strokeWidth="2" /></g>}
    </g>
    {mood === "thinking" && <g className="pet-orbit" fill="#508a65"><circle cx="28" cy="18" r="3" /><circle cx="40" cy="13" r="3" /><circle cx="53" cy="11" r="3" /></g>}
    {mood === "queued" && <g className="pet-clock"><circle cx="87" cy="21" r="12" fill="#fff9e7" stroke="#8a794d" strokeWidth="2" /><path d="M87 13V21L93 25" stroke="#8a794d" strokeWidth="2" fill="none" /></g>}
    {mood === "running" && <g className="pet-gear" stroke="#52786a" strokeWidth="4"><circle cx="85" cy="22" r="10" fill="#f4f7ef" /><path d="M85 7V14M85 30V37M70 22H77M93 22H100" /><circle cx="85" cy="22" r="3" fill="#52786a" /></g>}
    {mood === "reflecting" && <g className="pet-thought"><path d="M67 9Q80 1 91 11Q105 25 88 30H71Z" fill="#fff9e7" stroke="#9682a3" strokeWidth="2" /><circle cx="66" cy="33" r="3" fill="#9682a3" /><path d="M78 13Q84 5 89 14Q94 20 84 25Q74 20 78 13" fill="#ca93a6" /></g>}
    {mood === "celebrating" && <g className="pet-spark" fill="#b89236"><path d="M21 7L24 16L33 19L24 22L21 31L18 22L9 19L18 16ZM91 46L94 54L103 57L94 60L91 68L88 60L80 57L88 54Z" /></g>}
    {mood === "error" && <g className="pet-alert"><path d="M85 7L102 34H68Z" fill="#edc18e" stroke="#976039" strokeWidth="2" /><path d="M85 15V24" stroke="#764b30" strokeWidth="3" /><circle cx="85" cy="29" r="1.5" fill="#764b30" /></g>}
    {mood === "sleeping" && <text x="78" y="24" fill="#66716b" fontSize="16">z z</text>}
  </svg>;
}

function RoomArtwork() {
  return <svg className="room-art" viewBox="0 0 1000 560" preserveAspectRatio="none" aria-hidden="true">
    <defs><pattern id="room-floor" width="130" height="48" patternUnits="userSpaceOnUse"><path d="M0 48H130M65 0V48" stroke="#c8bea0" strokeWidth="1" /></pattern></defs>
    <path d="M0 0H1000V260L500 340L0 260Z" fill="#e8ecdf" /><path d="M0 260L500 340L1000 260V560H0Z" fill="#e6d9b9" />
    <path d="M0 260L500 340L1000 260V560H0Z" fill="url(#room-floor)" /><path d="M0 258L500 338L1000 258" fill="none" stroke="#859879" strokeWidth="7" />
    <path d="M500 0V338" stroke="#c0cdb4" strokeWidth="2" />
    <rect x="76" y="55" width="235" height="169" rx="4" fill="#738a69" /><rect x="87" y="65" width="213" height="144" fill="#c6d4bb" />
    {[107, 155, 200].map(y => <g key={y}><path d={`M87 ${y + 6}H300`} stroke="#738a69" strokeWidth="6" />{[0, 1, 2, 3, 4, 5, 6].map(i => <rect key={i} x={100 + i * 27} y={y - 25} width="19" height="30" rx="2" fill={["#d29e73", "#8dab91", "#c6b478", "#93a9bb"][i % 4]} />)}</g>)}
    <rect x="389" y="52" width="213" height="130" rx="3" fill="#b99767" stroke="#7c7654" strokeWidth="6" />
    {[0, 1, 2, 3, 4, 5].map(i => <g key={i}><rect x={407 + i % 3 * 61} y={69 + Math.floor(i / 3) * 54} width="43" height="39" fill={i % 2 ? "#e6eccb" : "#f8edb6"} /><path d={`M${414 + i % 3 * 61} ${83 + Math.floor(i / 3) * 54}h28m-28 8h18`} stroke="#9eab7e" strokeWidth="2" /></g>)}
    <path d="M678 204H917V222H678ZM696 222V304M899 222V304" fill="#a3845e" stroke="#7c6b51" strokeWidth="6" />
    <rect x="741" y="106" width="119" height="81" rx="5" fill="#42644f" /><rect x="749" y="114" width="103" height="63" rx="2" fill="#213c30" />
    <path d="M763 132L773 140L763 148M786 149H810M797 187V203M780 203H815" fill="none" stroke="#a4d1a5" strokeWidth="3" /><path d="M730 210H871" stroke="#42644f" strokeWidth="5" />
    <rect x="866" y="162" width="36" height="42" rx="4" fill="#547764" /><circle cx="884" cy="175" r="3" fill="#bae0a9" /><path d="M876 187H892M876 193H892" stroke="#b0c8aa" strokeWidth="2" />
    <ellipse cx="174" cy="421" rx="123" ry="37" fill="#9fb6a0" /><rect x="65" y="330" width="188" height="71" rx="18" fill="#698f78" /><rect x="56" y="369" width="204" height="49" rx="12" fill="#80a38a" /><path d="M91 418V435M229 418V435" stroke="#627d62" strokeWidth="6" /><rect x="91" y="346" width="57" height="42" rx="10" fill="#c9d6b1" /><rect x="169" y="346" width="58" height="42" rx="10" fill="#e1c38d" />
    <path d="M733 371H918V386H733ZM748 386V449M905 386V449" fill="#ae9169" stroke="#7c6b51" strokeWidth="5" /><path d="M755 349L807 355L858 349V370L807 378L755 370Z" fill="#f7edce" stroke="#977c56" strokeWidth="2" /><path d="M807 355V378M767 356L798 361M816 361L846 356" stroke="#bea783" strokeWidth="2" />
    <path d="M881 359V306Q881 296 867 296H849M849 296L833 318H865Z" fill="#738a69" stroke="#536b52" strokeWidth="4" /><ellipse cx="880" cy="368" rx="16" ry="4" fill="#536b52" />
    <path d="M341 300H376L370 341H347Z" fill="#b99767" /><path d="M358 300V252M358 286Q319 279 328 258Q352 251 358 286M358 276Q388 267 386 246Q359 245 358 276" stroke="#577e54" strokeWidth="4" fill="#88a574" />
  </svg>;
}

function FileTree({ files, selected, choose, expanded = false }: { files: FileEntry[]; selected: string; choose: (path: string) => void; expanded?: boolean }) {
  const folders = new Map<string, FileEntry[]>();
  const root: FileEntry[] = [];
  for (const file of files) {
    const slash = file.path.indexOf("/");
    if (slash < 0) root.push(file);
    else { const name = file.path.slice(0, slash); const list = folders.get(name) || []; list.push({ ...file, path: file.path.slice(slash + 1) }); folders.set(name, list); }
  }
  return <ul className="room-file-tree">{[...folders].map(([name, children]) => <li key={`${name}/`}><details open={expanded || Boolean(selected.startsWith(`${name}/`))}>
    <summary title={`${children.length} files in ${name}`}>▸ {name}<small>{children.length}</small></summary>
    <FileTree files={children} selected={selected.startsWith(`${name}/`) ? selected.slice(name.length + 1) : ""} choose={path => choose(`${name}/${path}`)} expanded={expanded} />
  </details></li>)}{root.map(file => <li key={file.path}><button className={selected === file.path ? "selected" : ""} onClick={() => choose(file.path)}
    title={`${file.path}\n${file.symlink ? `Symbolic link → ${file.symlink}` : `${bytes(file.size || 0)} · mode ${(file.mode || 0).toString(8)}\nSHA-256 ${file.sha256 || "Unavailable"}`}`}> <span>{file.symlink ? "↗" : "▤"} {file.path}</span><small>{file.size !== undefined ? bytes(file.size) : "link"}</small></button></li>)}</ul>;
}

export function SandboxRoom({ run, events, connected, inspectAgent, selectTask }: {
  run: Run; events: EventRecord[]; connected: boolean; inspectAgent: (id: string) => void; selectTask: (id: string) => void;
}) {
  const [room, setRoom] = useState<RoomData | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState("");
  const [model, setModel] = useState("");
  const [page, setPage] = useState(0);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [file, setFile] = useState("");
  const [zone, setZone] = useState<Zone>("files");
  const [eventId, setEventId] = useState(0);
  const [motion, setMotion] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const live = run.status === "RUNNING" && connected;
  useEffect(() => {
    let active = true;
    const load = async () => { try { const value = await api<RoomData>(`/runs/${run.id}/room`); if (active) { setRoom(value); setError(""); } }
      catch (e) { if (active) setError((e as Error).message); } };
    void load(); const timer = live ? setInterval(() => void load(), 5000) : undefined;
    return () => { active = false; clearInterval(timer); };
  }, [run.id, live, refresh]);
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);
  const agents = run.agents.filter(agent => !model || `${agent.model.provider}/${agent.model.model}` === model);
  const models = [...new Set(run.agents.map(agent => `${agent.model.provider}/${agent.model.model}`))];
  const residentPage = Math.min(page, Math.max(0, Math.ceil(agents.length / 6) - 1));
  const activities = agents.slice(residentPage * 6, residentPage * 6 + 6).map(agent => ({ agent, ...activity(agent, events, live, now) }));
  const calls = useMemo(() => events.filter(event => event.kind === "tool.invoked" && (!selectedAgent || event.agent_id === selectedAgent) &&
    (!model || run.agents.some(agent => agent.id === event.agent_id && `${agent.model.provider}/${agent.model.model}` === model))).slice(-60).reverse(), [events, selectedAgent, model, run.agents]);
  const chosen = run.agents.find(agent => agent.id === selectedAgent);
  const entry = room?.files.find(item => item.path === file);
  const selectedCall = events.find(event => event.sequence === eventId);
  const result = selectedCall && toolResult(selectedCall, events);
  const touched = file && events.findLast(event => event.kind === "workspace.modified" && [file, `/workspace/${file}`].includes(String(event.payload.path)));
  const files = (room?.files || []).filter(item => item.path.toLowerCase().includes(search.toLowerCase()));
  const used = room?.files.reduce((sum, item) => sum + (item.size || 0), 0) || 0;
  const shelf = new Map<string, { folder: boolean; count: number; size: number }>();
  for (const item of room?.files || []) {
    const name = item.path.split("/")[0];
    const group = shelf.get(name) || { folder: item.path.includes("/"), count: 0, size: 0 };
    group.count++; group.size += item.size || 0; shelf.set(name, group);
  }
  const mascotSeed = (agent: Agent) => signature(agent.model.model) + run.agents.indexOf(agent);
  const pickAgent = (id: string) => { setSelectedAgent(id); setEventId(0); setPage(Math.floor(agents.findIndex(agent => agent.id === id) / 6)); };
  return <section className={`sandbox-room ${!live || !motion ? "room-still" : ""}`} aria-label="Sandbox room">
    <div className="room-toolbar"><div><h2>Inside the sandbox</h2><p className="muted">{live ? "Live activity" : run.status === "RUNNING" ? "Stream disconnected · last known state" : `${run.status.toLowerCase()} · recorded state`} · one mascot per agent</p></div>
      <div className="room-options"><label>Model<select value={model} onChange={e => { setModel(e.target.value); setPage(0); setSelectedAgent(""); setEventId(0); }}><option value="">All models</option>{models.map(name => <option key={name}>{name}</option>)}</select></label>
        <button aria-pressed={!motion} onClick={() => setMotion(value => !value)}>{motion ? "Pause animations" : "Enable animations"}</button></div></div>
    <div className={`room-scene ${!live || !motion ? "room-still" : ""}`} aria-label="Shared sandbox room. Select a station or an agent to inspect it.">
      <RoomArtwork />
      <span className="room-address">/workspace <span>{room?.sandbox_available ? "Docker connected" : "Saved snapshot"}</span></span>
      {ZONES.map(station => <button key={station.id} className={`room-station ${zone === station.id ? "active" : ""}`} style={{ left: `${station.x}%`, top: `${station.y}%` }}
        onClick={() => { setZone(station.id); setSelectedAgent(""); setEventId(0); }} title={station.caption}><strong>{station.name}</strong><small>{station.caption}</small></button>)}
      <div className="room-shelf-items" aria-label="Workspace shelf">{[...shelf].slice(0, 2).map(([name, group]) => <button key={name}
        title={`/workspace/${name}\n${group.folder ? `${group.count} files` : "File"} · ${bytes(group.size)}\nSelect to inspect`}
        onClick={() => { setZone("files"); setSelectedAgent(""); setEventId(0); if (group.folder) { setSearch(`${name}/`); setFile(""); } else { setSearch(""); setFile(name); } }}>
        <span>{group.folder ? "▸" : "▤"} {name}</span><small>{group.folder ? `${group.count} files` : bytes(group.size)}</small></button>)}
        {shelf.size > 2 && <button onClick={() => { setZone("files"); setSelectedAgent(""); setEventId(0); setFile(""); setSearch(""); }}>+ {shelf.size - 2} more entries</button>}
      </div>
      {activities.map(({ agent, mood, zone: place, event, recent }) => {
        const neighbors = activities.filter(value => value.zone === place);
        const index = neighbors.findIndex(value => value.agent.id === agent.id);
        const columns = Math.min(neighbors.length, 3);
        const centers: Record<Zone, [number, number]> = { files: [20, 36], terminal: [80, 36], memory: [81, 62], lounge: [19, 62], tasks: [49, 41] };
        const [x, y] = centers[place];
        const caption = recent && event ? `Recent: ${toolLabel(event)}` : CAPTIONS[mood];
        return <button key={agent.id} className={`room-resident ${x < 30 ? "side-left" : x > 70 ? "side-right" : ""} ${selectedAgent === agent.id ? "selected" : ""}`} style={{ left: `${x + (index % columns - (columns - 1) / 2) * 10}%`, top: `${y + Math.floor(index / columns) * 18}%`, "--pet-delay": `${signature(agent.id) % 2000}ms` } as CSSProperties}
          onClick={() => pickAgent(agent.id)} aria-label={`${agent.name}, ${agent.model.model}, ${live ? caption : agent.state.toLowerCase()}. Inspect mascot`}
          onPointerMove={e => { const box = e.currentTarget.getBoundingClientRect(); e.currentTarget.style.setProperty("--gaze-x", `${(e.clientX - box.left - box.width / 2) / box.width * 4}px`); e.currentTarget.style.setProperty("--gaze-y", `${(e.clientY - box.top - box.height / 2) / box.height * 3}px`); }}
          onPointerLeave={e => { e.currentTarget.style.setProperty("--gaze-x", "0px"); e.currentTarget.style.setProperty("--gaze-y", "0px"); }}>
          <span className="mascot-tip"><strong>{agent.name}</strong><span>{agent.model.model}</span><span>{live ? caption : `${run.status.toLowerCase()} · ${agent.state.toLowerCase()}`}</span><span>Lifetime {duration(agent.lifetime_ms)} · {agent.processes.length} processes</span>{event && <span>{toolLabel(event)}</span>}</span>
          <Mascot seed={mascotSeed(agent)} mood={mood} /><span className="resident-name">{agent.name}</span>
          <span className="resident-state" title={live ? caption : agent.state.toLowerCase()}>{live ? caption : agent.state.toLowerCase()}</span>
        </button>;
      })}
      {!agents.length && <p className="room-empty">No agents for this model.</p>}
    </div>
    {agents.length > 6 && <div className="room-pages"><button disabled={residentPage === 0} onClick={() => setPage(residentPage - 1)}>Previous residents</button><span>{residentPage * 6 + 1}–{Math.min(agents.length, residentPage * 6 + 6)} of {agents.length}</span><button disabled={(residentPage + 1) * 6 >= agents.length} onClick={() => setPage(residentPage + 1)}>Next residents</button></div>}
    <div className="room-roster" aria-label="Room residents">{agents.map(agent => <button key={agent.id} className={agent.id === selectedAgent ? "selected" : ""} onClick={() => pickAgent(agent.id)}>
      <span className="resident-dot" style={{ background: COLORS[mascotSeed(agent) % 6] }} /><span><strong>{agent.name}</strong><small>{agent.model.model} · {agent.state.toLowerCase()}</small></span></button>)}</div>
    {error && <p className="error" role="alert">{error} <button onClick={() => setRefresh(value => value + 1)}>Retry metadata</button></p>}
    <div className="room-details">
      <section className="room-explorer"><div className="section-heading"><h3>Shared filesystem</h3><button onClick={() => setRefresh(value => value + 1)} aria-label="Refresh sandbox metadata">Refresh</button></div>
        <label>Find a file<input type="search" placeholder="Path or filename…" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <p className="muted room-file-count">{room ? `${room.files.length} files · ${bytes(used)} · observed at ${duration(room.sim_ms)}` : "Loading sandbox metadata…"}</p>
        <div className="room-tree-scroll"><FileTree files={files} selected={file} expanded={Boolean(search)} choose={path => { setFile(path); setZone("files"); setSelectedAgent(""); setEventId(0); }} />{room && !files.length && <p className="muted">{search ? "No matching files." : "The recorded workspace is empty."}</p>}</div>
      </section>
      <section id="room-inspector" className="room-inspection" aria-label="Room inspector">
        {selectedCall ? <><div className="section-heading"><h3>{toolLabel(selectedCall)}</h3><button onClick={() => setEventId(0)}>Close tool</button></div>
          <p className="muted">#{selectedCall.sequence} · {duration(selectedCall.sim_ms)} · {run.agents.find(agent => agent.id === selectedCall.agent_id)?.name || selectedCall.agent_id}</p>
          <h4>Arguments</h4><pre>{JSON.stringify(selectedCall.payload.arguments, null, 2)}</pre><h4>Result</h4><pre>{result ? JSON.stringify(result.payload.result, null, 2) : "No result in the current event window."}</pre>
        </> : chosen ? <><div className="room-agent-heading"><Mascot seed={mascotSeed(chosen)} mood={activities.find(value => value.agent.id === chosen.id)?.mood || "idle"} /><div><h3>{chosen.name}</h3><p className="muted">{chosen.model.model}</p></div></div>
          <dl className="room-properties"><dt>State</dt><dd>{chosen.state.toLowerCase()}{chosen.queued ? " · queued" : ""}</dd><dt>Lifetime</dt><dd>{duration(chosen.lifetime_ms)}</dd><dt>Provider</dt><dd>{chosen.model.provider}</dd><dt>Context</dt><dd>{chosen.model.context_tokens.toLocaleString()} tokens · {chosen.context_profile}</dd><dt>Processes</dt><dd>{chosen.processes.join(", ") || "None observed"}</dd><dt>Notebook</dt><dd>{chosen.memory_count ?? 0} entries</dd><dt>Generation</dt><dd>{chosen.generation} · lineage {chosen.lineage_generation ?? 0}</dd></dl>
          <div className="room-inspect-actions"><button onClick={() => inspectAgent(chosen.id)}>Open full agent inspector</button>{chosen.task_id && <button onClick={() => selectTask(chosen.task_id!)}>Inspect assigned task</button>}</div>
        </> : zone === "files" && entry ? <><h3 className="mono">/workspace/{entry.path}</h3><dl className="room-properties"><dt>Type</dt><dd>{entry.symlink ? "Symbolic link" : "File"}</dd><dt>Size</dt><dd>{entry.size === undefined ? "Not recorded" : bytes(entry.size)}</dd><dt>Permissions</dt><dd>{entry.mode === undefined ? "Not recorded" : entry.mode.toString(8).padStart(3, "0")}</dd>{entry.symlink && <><dt>Target</dt><dd>{entry.symlink}</dd></>}<dt>SHA-256</dt><dd className="mono">{entry.sha256 || "Not recorded"}</dd><dt>Recent change</dt><dd>{touched ? `#${touched.sequence} · ${duration(touched.sim_ms)} · ${run.agents.find(agent => agent.id === touched.agent_id)?.name || "Unattributed"}` : "No change in the current event window"}</dd></dl><p className="muted">Shared by all agents. Metadata comes from the last sandbox observation.</p></> : <><h3>{ZONES.find(station => station.id === zone)?.name}</h3><p className="muted">{zone === "files" ? "Select a file to inspect its metadata." : zone === "terminal" ? "Shell, Python, compile, service and process tools appear at this workbench." : zone === "memory" ? "Each agent keeps its own notebook. Select a mascot to inspect its memory and context." : zone === "tasks" ? `${run.tasks.filter(task => task.status === "AVAILABLE").length} available · ${run.tasks.filter(task => task.status === "COMPLETED").length} completed. Agents choose their own tasks.` : "Agents come here to relax, reflect and enjoy their time."}</p>
          {room && <dl className="room-properties"><dt>Sandbox</dt><dd>{room.sandbox_available ? "Docker connected" : "Saved snapshot · container unavailable"}</dd><dt>Image</dt><dd>{room.image}</dd><dt>Workspace limit</dt><dd>{room.limits.workspace_mb} MB</dd><dt>Memory limit</dt><dd>{room.limits.memory_mb} MB</dd><dt>CPU / PIDs</dt><dd>{room.limits.cpu_count} CPU · {room.limits.pid_limit} processes</dd><dt>Tool timeout</dt><dd>{room.limits.timeout_seconds} seconds</dd></dl>}
        </>}
      </section>
    </div>
    <section className="room-tools"><div className="section-heading"><h3>{chosen ? `${chosen.name} · tool calls` : "Tool calls"}</h3>{selectedAgent && <button onClick={() => setSelectedAgent("")}>Show all agents</button>}<span className="muted">Recent event window · select a call for arguments & result</span></div>
      {!calls.length ? <p className="muted">No tool calls in the current event window.</p> : <ol>{calls.map(call => {
        const outcome = toolResult(call, events);
        const failed = Boolean(object(outcome?.payload.result).error);
        return <li key={call.sequence}><button className={eventId === call.sequence ? "selected" : ""} onClick={() => { setEventId(call.sequence); requestAnimationFrame(() => document.getElementById("room-inspector")?.scrollIntoView({ block: "nearest" })); }}><time>{duration(call.sim_ms)}</time><span className="tool-agent">{run.agents.find(agent => agent.id === call.agent_id)?.name || call.agent_id}</span><strong>{toolLabel(call)}</strong><span className={failed ? "impossible" : "muted"}>{failed ? "Error" : outcome ? "Returned" : "No recorded result"}</span></button></li>;
      })}</ol>}
    </section>
  </section>;
}
