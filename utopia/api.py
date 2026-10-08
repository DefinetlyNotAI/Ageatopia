import asyncio
import copy
import hmac
import json
import logging
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from utopia.config import Economy, WorldConfig
from utopia.engine import Engine
from utopia.exports import all_events, comparison, export_run, public_trajectories
from utopia.providers import SharedInference
from utopia.runtime import Runtime
from utopia.sandbox import SandboxUnavailable
from utopia.store import Store

logger = logging.getLogger("utopia")


class PresetRequest(BaseModel):
    name: str
    config: WorldConfig


def create_app(directory=None, store=None, token=None):
    directory = Path(directory or os.environ.get("UTOPIA_DATA", ".data")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    token_file = directory / "operator.token"
    operator_token = token or os.environ.get("UTOPIA_OPERATOR_TOKEN")
    if not operator_token:
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        operator_token = token_file.read_text(encoding="utf-8").strip()
    database = store or Store.local(directory)
    engines = {state["id"]: Engine(database, state) for state in database.all_runs()}
    inference = SharedInference()
    runtimes = {}
    control_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        for engine in engines.values():
            if not engine.state.get("deleted") and engine.state["status"] not in {
                "TERMINATED",
                "COMPLETED",
            }:
                engine.recover()
        yield
        for runtime in runtimes.values():
            await runtime.close()
        await inference.close()
        database.engine.dispose()

    app = FastAPI(
        title="Agent Utopia operator API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.engines = engines
    app.state.store = database
    app.state.runtimes = runtimes

    async def authenticate(request: Request, authorization: str | None = Header(default=None)):
        expected = f"Bearer {operator_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "Operator authentication required")
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"localhost", "127.0.0.1", "testserver"}:
            raise HTTPException(403, "Local operator access required")

    protected = [Depends(authenticate)]

    def lookup(run_id, include_deleted=False):
        if run_id not in engines or (engines[run_id].state.get("deleted") and not include_deleted):
            raise HTTPException(404, "Run not found")
        return engines[run_id]

    @app.exception_handler(ValueError)
    async def validation_error(request, error):
        return Response(
            json.dumps({"detail": str(error)}), status_code=409, media_type="application/json"
        )

    @app.exception_handler(SandboxUnavailable)
    async def sandbox_error(request, error):
        return Response(
            json.dumps({"detail": str(error)}), status_code=503, media_type="application/json"
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "schema_version": 1}

    @app.get("/system", dependencies=protected)
    async def system():
        docker = shutil.which("docker") is not None
        docker_ready = False
        docker_error = None
        if docker:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "info",
                "--format",
                "{{.ID}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                docker_ready = process.returncode == 0 and bool(stdout.strip())
                docker_error = (
                    (
                        stderr.decode(errors="replace")[:400].strip()
                        or "Docker daemon did not return its identity"
                    )
                    if not docker_ready
                    else None
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                docker_error = "Docker daemon did not respond"
        models = []
        ollama_error = None
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get("http://127.0.0.1:11434/api/tags")
                response.raise_for_status()
                models = response.json()["models"]
        except (httpx.HTTPError, KeyError) as error:
            ollama_error = str(error)
        return {
            "docker_installed": docker,
            "docker_ready": docker_ready,
            "docker_error": docker_error,
            "models": models,
            "ollama_error": ollama_error,
            "defaults": WorldConfig().model_dump(mode="json"),
        }

    @app.get("/runs", dependencies=protected)
    async def list_runs(deleted: bool = False):
        return [
            engine.summary()
            for engine in reversed(list(engines.values()))
            if bool(engine.state.get("deleted")) == deleted
        ]

    @app.delete("/runs/{run_id}", dependencies=protected)
    async def delete_run(run_id: str):
        async with control_lock:
            engine = lookup(run_id)
            if engine.state["status"] == "RUNNING":
                raise ValueError("Pause or terminate the run before deleting it")
            if run_id in runtimes:
                await runtimes[run_id].close()
                del runtimes[run_id]
            with engine.transaction():
                engine.state["deleted"] = True
                engine.emit("run.deleted", {"recoverable": True})
            return {"id": run_id, "deleted": True, "recoverable": True}

    @app.post("/runs/{run_id}/restore", dependencies=protected)
    async def restore_run(run_id: str):
        async with control_lock:
            engine = lookup(run_id, include_deleted=True)
            if not engine.state.get("deleted"):
                raise ValueError("World is not deleted")
            with engine.transaction():
                engine.state["deleted"] = False
                engine.emit("run.restored", {})
            return engine.summary()

    @app.post("/runs", dependencies=protected, status_code=201)
    async def create_run(config: WorldConfig):
        engine = Engine.create(database, config)
        engines[engine.state["id"]] = engine
        logger.info(json.dumps({"event": "run.created", "run_id": engine.state["id"]}))
        return engine.summary()

    @app.get("/runs/{run_id}", dependencies=protected)
    async def get_run(run_id: str):
        engine = lookup(run_id)
        return {
            **engine.summary(),
            "config": engine.state["config"],
            "model_manifest": engine.state["model_manifest"],
        }

    @app.get("/runs/{run_id}/agents/{identity}", dependencies=protected)
    async def get_agent(run_id: str, identity: str):
        agent = lookup(run_id).agent(identity)
        records = list(all_events(database, run_id))
        paths = set()
        for record in records:
            if record["agent_id"] == identity and record["kind"] == "tool.invoked":
                payload = record["payload"]
                arguments = payload.get("arguments") or {}
                if payload.get("name") == "workspace" and isinstance(arguments, dict):
                    if arguments.get("operation") in {"read", "write"}:
                        paths.add(
                            str(arguments.get("value", ""))
                            .removeprefix("/workspace/")
                            .removeprefix("./")
                        )
        evidence = [
            record
            for record in records
            if record["kind"] == "workspace.modified" and record["payload"].get("path") in paths
        ]
        return {
            **agent,
            "workspace_events": evidence[-200:],
            "workspace_event_count": len(evidence),
        }

    @app.post("/runs/{run_id}/control/{action}", dependencies=protected)
    async def control(run_id: str, action: str):
        async with control_lock:
            engine = lookup(run_id)
            if action not in {"start", "resume", "pause", "terminate"}:
                raise HTTPException(404, "Unknown control")
            if action in {"start", "resume"}:
                if any(e.state["status"] == "RUNNING" for e in engines.values()):
                    raise ValueError("Pause the active run before starting another")
                inference.semaphore = asyncio.Semaphore(engine.config.inference_concurrency)
                runtime = runtimes.setdefault(run_id, Runtime(engine, inference))
                try:
                    await runtime.start(action)
                except (httpx.HTTPError, ValueError) as error:
                    engine.record("run.start-failed", {"error": str(error)}, severity="error")
                    raise HTTPException(503, str(error)) from error
                except SandboxUnavailable as error:
                    engine.record("run.start-failed", {"error": str(error)}, severity="error")
                    raise
            elif run_id in runtimes:
                await runtimes[run_id].stop(action)
            else:
                engine.control(action)
            return engine.summary()

    @app.put("/runs/{run_id}/economy", dependencies=protected)
    async def economy(run_id: str, rules: Economy):
        engine = lookup(run_id)
        if engine.state["status"] == "TERMINATED":
            raise ValueError("Terminated run rules cannot change")
        with engine.transaction():
            previous = engine.state["config"]["economy"]
            engine.state["config"]["economy"] = rules.model_dump(mode="json")
            engine.config.economy = rules
            engine.state["economy_version"] += 1
            engine.emit(
                "economy.changed",
                {
                    "before": previous,
                    "after": rules.model_dump(),
                    "version": engine.state["economy_version"],
                },
            )
        return engine.summary()

    @app.get("/runs/{run_id}/events", dependencies=protected)
    async def event_page(
        run_id: str,
        after: int = 0,
        agent_id: str | None = None,
        kind: str | None = None,
        task_id: str | None = None,
        severity: str | None = None,
    ):
        lookup(run_id)
        return database.event_page(
            run_id,
            after=max(0, after),
            agent_id=agent_id,
            kind=kind,
            task_id=task_id,
            severity=severity,
        )

    @app.get("/runs/{run_id}/stream", dependencies=protected)
    async def stream(run_id: str, request: Request, after: int = 0):
        lookup(run_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError as error:
            raise HTTPException(400, "Invalid event cursor") from error

        async def records():
            nonlocal cursor
            while not await request.is_disconnected():
                page = database.event_page(run_id, cursor, limit=100)
                for record in page:
                    cursor = record["sequence"]
                    yield f"id: {cursor}\ndata: {json.dumps(record)}\n\n"
                if not page:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(0)

        return StreamingResponse(
            records(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/runs/{run_id}/replay", dependencies=protected)
    async def replay(run_id: str, at_ms: int = 0, agent_id: str | None = None):
        lookup(run_id)
        state = None
        experience = []
        for record in all_events(database, run_id):
            if record["sim_ms"] > at_ms:
                break
            if record["kind"] == "state.checkpoint":
                state = record["payload"]
            if (not agent_id or record["agent_id"] == agent_id) and record[
                "kind"
            ] != "state.checkpoint":
                experience.append(record)
        return {
            "state": state,
            "events": experience,
            "guarantee": "Recorded experience only; model responses are not regenerated",
        }

    @app.get("/runs/{run_id}/export/{format}", dependencies=protected)
    async def export(run_id: str, format: str):
        engine = lookup(run_id)
        payload, content_type = await asyncio.to_thread(
            export_run, database, copy.deepcopy(engine.state), format
        )
        return Response(
            payload,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="utopia-{run_id}.{format}"'},
        )

    @app.post("/runs/{run_id}/repeat", dependencies=protected, status_code=201)
    async def repeat(run_id: str):
        source = lookup(run_id)
        config = WorldConfig.model_validate(source.state["config"])
        engine = Engine.create(database, config, copy.deepcopy(source.state["initial_workspace"]))
        engines[engine.state["id"]] = engine
        engine.record(
            "run.repeated", {"source_run_id": run_id, "inference_determinism": "not guaranteed"}
        )
        return engine.summary()

    @app.get("/presets", dependencies=protected)
    async def get_presets():
        return database.get_presets()

    @app.post("/presets", dependencies=protected)
    async def put_preset(preset: PresetRequest):
        if not preset.name.strip() or len(preset.name) > 100:
            raise ValueError("Preset name must contain 1 to 100 characters")
        database.put_preset(preset.name, preset.config.model_dump(mode="json"))
        return {"name": preset.name}

    @app.get("/comparison", dependencies=protected)
    async def compare():
        return comparison([e.state for e in engines.values() if not e.state.get("deleted")])

    @app.get("/runs/{run_id}/analytics", dependencies=protected)
    async def analytics(run_id: str):
        engine = lookup(run_id)
        population, completions, skips, collective, throughput = [], [], [], [], []
        tools, rewards, evidence = {}, [], []
        completed = skipped = deductions = 0
        files = {}
        for record in all_events(database, run_id):
            kind, payload, timestamp = record["kind"], record["payload"], record["sim_ms"]
            if kind == "state.checkpoint":
                population.append(
                    [timestamp, sum(a["state"] != "TERMINATED" for a in payload["agents"])]
                )
            elif kind == "task.completed":
                completed += 1
                completions.append([timestamp, completed])
                rewards.append(payload["reward_ms"])
            elif kind == "task.skipped":
                skipped += 1
                skips.append([timestamp, skipped])
            elif kind == "lifetime.changed" and payload["reason"] == "collective":
                deductions += -payload["delta_ms"] / 60000
                collective.append([timestamp, deductions])
            elif kind == "inference.completed":
                throughput.append(
                    [timestamp, payload["tokens"] / max(0.001, payload["latency_ms"] / 1000)]
                )
            elif kind == "workspace.modified":
                files[payload["path"]] = record["sequence"]
            elif kind == "tool.invoked":
                tools[payload["name"]] = tools.get(payload["name"], 0) + 1
                arguments = payload.get("arguments") or {}
                if isinstance(arguments, dict) and payload["name"] == "workspace":
                    path = (
                        str(arguments.get("value", ""))
                        .removeprefix("/workspace/")
                        .removeprefix("./")
                    )
                    if arguments.get("operation") in {"read", "write"} and path in files:
                        evidence.append(
                            {
                                "path": path,
                                "agent_id": record["agent_id"],
                                "modification_sequence": files[path],
                                "tool_sequence": record["sequence"],
                                "interpretation": "Changed artifact accessed. Intention unknown.",
                            }
                        )

        def compact(points):
            if len(points) <= 250:
                return points
            stride = max(1, len(points) // 249)
            return points[::stride][:249] + [points[-1]]

        return {
            "population": compact(population),
            "completions": compact(completions),
            "skips": compact(skips),
            "collective": compact(collective),
            "throughput": compact(throughput),
            "tools": tools,
            "rewards_ms": rewards,
            "artifact_evidence": evidence,
            "lifespans": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "start": a["created_ms"],
                    "end": a["terminated_ms"]
                    if a["terminated_ms"] is not None
                    else engine.state["sim_ms"],
                }
                for a in engine.state["agents"].values()
            ],
        }

    @app.get("/runs/{run_id}/training-data", dependencies=protected)
    async def training_data(run_id: str):
        engine = lookup(run_id)
        records = public_trajectories(database, engine.state)
        payload = "\n".join(json.dumps(row) for row in records)
        return Response(
            payload,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="trajectories-{run_id}.jsonl"'},
        )

    return app
