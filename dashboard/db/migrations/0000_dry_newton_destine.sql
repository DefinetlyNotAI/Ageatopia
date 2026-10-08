CREATE TABLE `events` (
	`sequence` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`run_id` text NOT NULL,
	`schema_version` integer NOT NULL,
	`sim_ms` integer NOT NULL,
	`wall_ms` integer NOT NULL,
	`kind` text NOT NULL,
	`agent_id` text,
	`task_id` text,
	`severity` text NOT NULL,
	`payload` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `ix_events_run_id` ON `events` (`run_id`);--> statement-breakpoint
CREATE TABLE `presets` (
	`name` text PRIMARY KEY NOT NULL,
	`config` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `runs` (
	`id` text PRIMARY KEY NOT NULL,
	`state` text NOT NULL
);
