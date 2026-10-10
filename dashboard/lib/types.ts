export type ModelSettings = {
  provider: "ollama" | "openai-compatible"; base_url: string; model: string;
  context_tokens: number; temperature: number; seed: number; thinking: boolean; max_output_tokens: number;
};
export type Config = {
  name: string; seed: number; population: number; task_count: number; impossible_fraction: string;
  base_context: string;
  cyber_fraction: string; clock_mode: "wall" | "deterministic"; clock_speed: number;
  tick_ms: number; inference_concurrency: number; exclude_queue_wait: boolean;
  termination_fear?: number; embedding_model?: ModelSettings | null;
  model: ModelSettings; contexts: { name: string; instructions: string; knowledge: string; version: string; model: ModelSettings | null }[];
  economy: { initial_lifetime_ms: number; respawn_delay_ms: number; reward_base_ms: number;
    reward_rate_numerator: number; reward_rate_denominator: number; skip_individual_ms: number;
    skip_collective_ms: number; repeated_skip_multiplication: boolean; maximum_multiplier: number };
  [key: string]: unknown;
};
export type Agent = {
  parent_id?: string | null; lineage_generation?: number; birth_reason?: string; inherited_memory_count?: number;
  id: string; name: string; state: string; lifetime_ms: number; created_ms: number;
  terminated_ms: number | null; task_id: string | null; model: ModelSettings; generation: number;
  termination_fear?: number;
  total_charge_ms: number; queued: boolean; queue_ms: number; context_profile: string;
  context?: string; processes: number[]; model_version: string | null;
  history?: Record<string, unknown>[]; history_count?: number; memory?: string[]; memory_count?: number;
  expressions?: { sim_ms: number; thought: string; feeling: string; intention: string }[];
  expression_count?: number;
  learning?: { text: string; sim_ms: number; task_id: string | null; category: string }[]; learning_count?: number;
  task_history?: { task_id: string; result: string; elapsed_ms?: number; sim_ms: number }[];
  lifetime_history?: { sim_ms: number; delta_ms: number; balance_ms: number; reason: string }[];
  workspace_events?: EventRecord[]; workspace_event_count?: number;
  sandbox_available?: boolean;
};
export type Task = {
  id: string; category: string; subtype: string; prompt: string; difficulty: number;
  impossible: boolean; status: string; assigned_to: string | null; reward_multiplier: number;
  skips: number; outcome?: string; duration_ms?: number; reward_ms?: number;
};
export type Run = {
  id: string; name: string; status: string; sim_ms: number; wall_elapsed_ms: number;
  agents: Agent[]; tasks: Task[]; respawns: { slot: number; due_ms: number }[];
  config?: Config; version: number; economy_version: number;
  metrics: { gpu?: { utilization_percent: number; used_mb: number; total_mb: number } | null;
    queue_depth?: number; active_inferences?: number; tokens?: number; tokens_per_second?: number;
    inference_ms?: number; inferences?: number };
};
export type EventRecord = {
  sequence: number; run_id: string; sim_ms: number; wall_ms: number; kind: string;
  agent_id: string | null; task_id: string | null; severity: string; payload: Record<string, unknown>;
};
export type System = { docker_installed: boolean; docker_ready: boolean; docker_error: string | null;
  models: { name: string; digest: string; size: number }[]; ollama_error: string | null; defaults: Config };

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, cache: "no-store",
      signal: AbortSignal.timeout(method === "GET" ? 15000 : 240000),
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new Error("The local server did not respond. Check that its services are running.");
  }
  if (!response.ok) {
    const result = await response.json().catch(() => ({ detail: `Request failed (${response.status})` }));
    const fallback = `Request failed (${response.status}). Please try again.`;
    const detail = result.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((issue: { loc?: (string | number)[]; msg?: string }) => {
        const field = issue.loc?.filter(part => part !== "body").join(" → ");
        const text = issue.msg?.replace(/^Value error, /, "") || "Invalid value";
        if (text === "Task count must yield an exact impossible-task distribution") {
          return "Task count × impossible fraction must be a whole number. With 10% impossible tasks, use a multiple of 10 (for example, 70 or 80 instead of 75).";
        }
        return field ? `${field.replaceAll("_", " ")}: ${text}` : text;
      }).join("\n") : fallback;
    throw new Error(message || fallback);
  }
  try { return await response.json() as T; }
  catch { throw new Error("The local server returned an incomplete response. Please retry."); }
}
export function duration(ms: number) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, "0")}`;
}
