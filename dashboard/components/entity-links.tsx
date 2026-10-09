import Link from "next/link";

export function AgentEntityLink({ runId, agentId, onNavigate }: {
  runId: string; agentId: string; onNavigate?: (id: string) => void;
}) {
  const label = agentId.slice(0, 8);
  return onNavigate
    ? <button type="button" className="text-link" onClick={() => onNavigate(agentId)}>{label}</button>
    : <Link className="entity-link mono" href={`/runs/${runId}/agents/${encodeURIComponent(agentId)}`}>{label}</Link>;
}

export function TaskEntityLink({ runId, taskId, onNavigate }: {
  runId: string; taskId: string; onNavigate?: (id: string) => void;
}) {
  return onNavigate
    ? <button type="button" className="text-link mono" onClick={() => onNavigate(taskId)}>{taskId}</button>
    : <Link className="entity-link mono" href={`/?run=${encodeURIComponent(runId)}&tab=Tasks&task=${encodeURIComponent(taskId)}`}>{taskId}</Link>;
}
