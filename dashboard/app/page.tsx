import Dashboard from "@/components/dashboard";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;
function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  return <Dashboard initialNavigation={{
    run: first(params.run), tab: first(params.tab), task: first(params.task), agent: first(params.agent),
  }} />;
}
