import { AgentPage } from "@/components/inspector";
export default async function Page({ params }: { params: Promise<{ runId: string; agentId: string }> }) {
  const { runId, agentId } = await params;
  return <AgentPage runId={runId} agentId={agentId} />;
}
