import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const source = join(root, "src/gui/dashboard");
const output = join(root, "dist/gui/dashboard");
const action = process.argv[2] || "build";
mkdirSync(output, { recursive: true });
cpSync(source, output, { recursive: true });
const manifest = JSON.parse(readFileSync(join(source, "package.json"), "utf8"));
manifest.scripts = {
  dev: "next dev --hostname 127.0.0.1",
  build: "next build",
  start: "next start --hostname 127.0.0.1",
  typecheck: "tsc --noEmit",
  lint: "eslint .",
};
writeFileSync(join(output, "package.json"), JSON.stringify(manifest, null, 2) + "\n");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
if (action === "install" || !existsSync(join(output, "node_modules/next"))) {
  const result = spawnSync(npm, ["ci"], { cwd: output, stdio: "inherit", shell: process.platform === "win32" });
  if (result.status !== 0) process.exit(result.status || 1);
}
if (action !== "install") {
  const result = spawnSync(npm, ["run", action], { cwd: output, stdio: "inherit", shell: process.platform === "win32" });
  process.exit(result.status ?? 1);
}
