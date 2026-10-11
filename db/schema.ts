import { sqliteTable, text, integer, index } from "drizzle-orm/sqlite-core";

export const runs = sqliteTable("runs", {
  id: text("id").primaryKey(),
  state: text("state").notNull(),
});
export const events = sqliteTable("events", {
  sequence: integer("sequence").primaryKey({ autoIncrement: true }),
  runId: text("run_id").notNull(),
  schemaVersion: integer("schema_version").notNull(),
  simMs: integer("sim_ms").notNull(),
  wallMs: integer("wall_ms").notNull(),
  kind: text("kind").notNull(),
  agentId: text("agent_id"),
  taskId: text("task_id"),
  severity: text("severity").notNull(),
  payload: text("payload").notNull(),
}, (table) => [index("ix_events_run_id").on(table.runId)]);
export const presets = sqliteTable("presets", {
  name: text("name").primaryKey(),
  config: text("config").notNull(),
});
