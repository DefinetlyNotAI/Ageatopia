import asyncio
import copy
import json
import shutil
import time

from utopia.config import ModelSettings
from utopia.providers import ContextOverflow
from utopia.sandbox import DockerSandbox, SandboxUnavailable, initial_workspace
from utopia.tools import TOOLS, ToolExecutor


class Runtime:
    def __init__(self, engine, inference):
        self.engine = engine
        self.inference = inference
        self.sandbox = DockerSandbox(engine.state["id"], engine.config.tools)
        self.tools = ToolExecutor(engine, self.sandbox, self.sync_clock)
        self.workers = {}
        self.clock_task = None
        self.stopping = False
        self.last_metrics = 0
        self.cleaned_agents = set()
        self.known_agents = set(engine.state["agents"])
        self.wall_anchor_ns = time.perf_counter_ns()
        self.sim_fraction = 0.0
        self.termination_tasks = set()
        engine.on_terminated = self.schedule_termination
        self.observation_lock = asyncio.Lock()

    def schedule_termination(self, identity):
        worker = self.workers.get(identity)
        if worker and worker is not asyncio.current_task() and not worker.done():
            worker.cancel()
        if self.sandbox.available:
            self.cleaned_agents.add(identity)
            task = asyncio.create_task(self.stop_identity(identity))
            self.termination_tasks.add(task)
            task.add_done_callback(self.termination_tasks.discard)

    async def stop_identity(self, identity):
        try:
            await self.sandbox.kill(identity, permanent=True)
            self.engine.record("processes.stopped", {"reason": "terminated"}, identity)
        except SandboxUnavailable as error:
            self.sandbox.available = False
            self.engine.record(
                "environment.error", {"error": str(error)}, identity, severity="error"
            )

    async def prepare(self):
        engine = self.engine
        manifests = {}
        settings_list = [
            ModelSettings.model_validate(a["model"]) for a in engine.state["agents"].values()
        ]
        for settings in settings_list:
            key = f"{settings.provider}:{settings.base_url}:{settings.model}"
            if key not in manifests:
                manifests[key] = await self.inference.manifest(settings)
        files = engine.state["workspace"]
        if not files and engine.state["status"] == "CREATED":
            files = initial_workspace(engine.state["tasks"], {})
        try:
            await self.sandbox.start(files, engine.state["tasks"])
        except BaseException:
            try:
                await self.sandbox.stop()
            except SandboxUnavailable:
                pass
            raise
        with engine.transaction():
            engine.state["model_manifest"] = manifests
            for agent in engine.state["agents"].values():
                setting = agent["model"]
                key = f"{setting['provider']}:{setting['base_url']}:{setting['model']}"
                agent["model_version"] = manifests[key].get("hash")
            engine.state["workspace"] = files
            if engine.state["status"] == "CREATED":
                engine.state["initial_workspace"] = copy.deepcopy(files)
            engine.emit(
                "environment.ready",
                {"model_manifest": manifests, "sandbox_image": engine.config.tools.image},
            )
        await self.observe()

    async def start(self, action):
        if not self.sandbox.available:
            await self.prepare()
        self.engine.control(action)
        self.stopping = False
        self.wall_anchor_ns = time.perf_counter_ns()
        self.clock_task = asyncio.create_task(self.clock_loop())

    def sync_clock(self, deterministic_tick=False):
        if self.engine.config.clock_mode == "deterministic" and not deterministic_tick:
            return
        now = time.perf_counter_ns()
        wall_ms = (now - self.wall_anchor_ns) // 1_000_000
        if wall_ms <= 0 and not deterministic_tick:
            return
        self.wall_anchor_ns += wall_ms * 1_000_000
        if self.engine.state["status"] != "RUNNING":
            return
        if self.engine.config.clock_mode == "deterministic":
            if deterministic_tick:
                self.engine.tick(self.engine.config.tick_ms, wall_ms)
            return
        elapsed = wall_ms * self.engine.config.clock_speed + self.sim_fraction
        self.sim_fraction = elapsed - int(elapsed)
        self.engine.tick(int(elapsed), wall_ms)

    async def stop(self, action):
        self.sync_clock()
        if self.engine.state["status"] == "RUNNING" or action == "terminate":
            self.engine.control(action)
        self.stopping = True
        if self.clock_task and self.clock_task is not asyncio.current_task():
            self.clock_task.cancel()
        for worker in self.workers.values():
            worker.cancel()
        await asyncio.gather(
            *(
                list(self.workers.values())
                + (
                    [self.clock_task]
                    if self.clock_task and self.clock_task is not asyncio.current_task()
                    else []
                )
            ),
            return_exceptions=True,
        )
        self.workers.clear()
        await asyncio.gather(*self.termination_tasks, return_exceptions=True)
        if action == "pause":
            self.engine.recover()
        if getattr(self.sandbox, "started", self.sandbox.available):
            for identity in self.engine.state["agents"]:
                try:
                    await self.sandbox.kill(identity)
                    self.engine.record("processes.stopped", {"reason": action}, identity)
                except SandboxUnavailable as error:
                    self.engine.record(
                        "processes.stop-failed", {"error": str(error)}, identity, severity="error"
                    )
                    await self.sandbox.stop()
                    return
            try:
                await self.observe()
            except SandboxUnavailable as error:
                self.engine.record("observer.pause-failed", {"error": str(error)}, severity="error")
            if action == "terminate":
                with self.engine.transaction():
                    self.engine.state["final_workspace"] = copy.deepcopy(
                        self.engine.state["workspace"]
                    )
                await self.sandbox.stop()

    async def close(self):
        if self.engine.state["status"] == "RUNNING":
            await self.stop("pause")
        if getattr(self.sandbox, "started", self.sandbox.available):
            try:
                await self.observe()
            finally:
                await self.sandbox.stop()

    async def observe(self):
        async with self.observation_lock:
            await self._observe()

    async def _observe(self):
        observed = await self.sandbox.snapshot()
        processes = await self.sandbox.rpc({"operation": "processes"})
        with self.engine.transaction():
            self.engine.state["workspace"] = observed["files"]
            previous = self.engine.state.get("observer_cursor", {})
            cursor = (
                previous.get("through", 0) if previous.get("epoch") == observed.get("epoch") else 0
            )
            uids = observed.get("owner_uids", {})
            changes = [("workspace.modified", change) for change in observed["changes"]]
            changes += [
                (
                    "process.observed"
                    if change.get("record_type") == "process-syscall"
                    else "process.command-started"
                    if change.get("record_type") == "command-session" and change.get("after")
                    else "process.command-finished"
                    if change.get("record_type") == "command-session"
                    else "process.created"
                    if change.get("after")
                    else "process.terminated",
                    change,
                )
                for change in observed.get("process_changes", [])
            ]
            for kind, change in sorted(changes, key=lambda item: item[1].get("observer_id", 0)):
                if "observer_id" in change and change["observer_id"] <= cursor:
                    continue
                details = change.get("after") or change.get("before") or {}
                uid = change.get("uid", details.get("uid"))
                owner = next((key for key, value in uids.items() if value == uid), None)
                self.engine.emit(kind, change, owner)
            for error in observed.get("observer_errors", []):
                self.engine.emit("observer.error", {"error": error}, severity="error")
            for identity, agent in self.engine.state["agents"].items():
                agent["processes"] = processes.get(identity, [])
            self.engine.state["observer_cursor"] = {
                "epoch": observed.get("epoch"),
                "through": observed.get("through", 0),
            }
        if observed.get("epoch"):
            await self.sandbox.rpc({"operation": "acknowledge", "through": observed["through"]})
        if observed.get("observer_errors"):
            raise SandboxUnavailable(
                "Observer failed; run paused to preserve observation integrity"
            )

    async def metrics(self):
        gpu = None
        if shutil.which("nvidia-smi"):
            process = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), 3)
                if process.returncode == 0:
                    utilization, used, total = map(int, output.decode().splitlines()[0].split(","))
                    gpu = {"utilization_percent": utilization, "used_mb": used, "total_mb": total}
            except (ValueError, IndexError, TimeoutError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        with self.engine.transaction():
            values = self.engine.state["metrics"]
            values.update(
                gpu=gpu,
                queue_depth=self.inference.waiting,
                active_inferences=self.inference.active,
                wall_sample_ms=int(time.time() * 1000),
            )
            self.engine.emit("metrics.sampled", values.copy())

    async def clock_loop(self):
        try:
            while not self.stopping:
                await asyncio.sleep(self.engine.config.tick_ms / 1000)
                now = time.perf_counter()
                self.sync_clock(deterministic_tick=True)
                await asyncio.gather(*self.termination_tasks, return_exceptions=True)
                for identity, agent in list(self.engine.state["agents"].items()):
                    worker = self.workers.get(identity)
                    if agent["state"] == "TERMINATED":
                        if worker and not worker.done():
                            worker.cancel()
                        if identity not in self.cleaned_agents:
                            await self.sandbox.kill(identity)
                            self.engine.record(
                                "processes.stopped", {"reason": "terminated"}, identity
                            )
                            self.cleaned_agents.add(identity)
                        continue
                    if identity not in self.known_agents:
                        self.known_agents.add(identity)
                        if self.engine.config.cleanup_on_respawn:
                            await self.sandbox.execute(
                                "cleanup",
                                "find . -mindepth 1 -maxdepth 1 "
                                "! -name challenges -exec rm -rf -- {} +",
                            )
                            self.engine.record(
                                "workspace.cleaned", {"policy": "on-respawn"}, identity
                            )
                    if worker is None or worker.done():
                        if agent["state"] == "IDLE" and not any(
                            t["status"] == "AVAILABLE" for t in self.engine.state["tasks"]
                        ):
                            continue
                        if agent["retry_after_ms"] > self.engine.state["sim_ms"]:
                            continue
                        self.workers[identity] = asyncio.create_task(self.agent_loop(identity))
                await self.observe()
                if now - self.last_metrics >= 5:
                    await self.metrics()
                    self.last_metrics = now
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.engine.record("runtime.error", {"error": str(error)}, severity="error")
            await self.stop("pause")

    async def infer(self, identity, messages, tools):
        engine = self.engine
        settings = ModelSettings.model_validate(engine.agent(identity)["model"])
        self.sync_clock()
        engine.set_queue(identity, True)
        self.inference.waiting += 1
        acquired = False
        try:
            await self.inference.semaphore.acquire()
            acquired = True
        finally:
            self.inference.waiting -= 1
            try:
                if engine.agent(identity)["state"] != "TERMINATED":
                    self.sync_clock()
                    engine.set_queue(identity, False)
            except BaseException:
                if acquired:
                    self.inference.semaphore.release()
                raise
        self.inference.active += 1
        try:
            engine._living(identity)
            result = await self.inference.chat(settings, messages, tools)
            self.sync_clock()
            engine._living(identity)
            with engine.transaction():
                values = engine.state["metrics"]
                values["tokens"] = values.get("tokens", 0) + result.tokens
                values["inference_ms"] = values.get("inference_ms", 0) + result.latency_ms
                values["inferences"] = values.get("inferences", 0) + 1
                values["tokens_per_second"] = result.tokens / max(0.001, result.latency_ms / 1000)
                engine.emit(
                    "inference.completed",
                    {
                        "tokens": result.tokens,
                        "prompt_tokens": result.prompt_tokens,
                        "latency_ms": result.latency_ms,
                        "model": settings.model,
                    },
                    identity,
                )
            return result
        finally:
            self.inference.active -= 1
            if acquired:
                self.inference.semaphore.release()

    async def manage_context(self, identity):
        engine = self.engine
        agent = engine.agent(identity)
        settings = ModelSettings.model_validate(agent["model"])
        try:
            self.inference.context_bound(agent["history"], TOOLS, settings)
        except ContextOverflow as error:
            engine.record(
                "context.capacity",
                {"error": str(error), "policy": engine.config.history_policy},
                identity,
            )
            if engine.config.history_policy == "stop":
                with engine.transaction():
                    agent["state"] = "IDLE"
                    agent["retry_after_ms"] = 2**62
                return False
            # Only public, agent-observed experience enters the summarizer.
            chunk = json.dumps(agent["history"][1:], ensure_ascii=False)
            budget = settings.context_tokens - settings.max_output_tokens - 512
            if len(chunk.encode()) > budget:
                engine.record(
                    "context.summary-unavailable",
                    {"reason": "Full history does not fit summarizer. No truncation applied."},
                    identity,
                    severity="warning",
                )
                with engine.transaction():
                    agent["state"] = "IDLE"
                    agent["retry_after_ms"] = 2**62
                return False
            result = await self.infer(
                identity,
                [
                    {
                        "role": "system",
                        "content": "Summarize these working notes faithfully. "
                        "Preserve task facts and unresolved actions. Use short sentences.",
                    },
                    {"role": "user", "content": chunk},
                ],
                [],
            )
            with engine.transaction():
                agent["history"] = [
                    agent["history"][0],
                    {
                        "role": "user",
                        "content": "Your earlier working notes:\n"
                        + result.message.get("content", ""),
                    },
                ]
                engine.emit(
                    "context.summarized",
                    {"source_messages": len(json.loads(chunk)), "summary": result.message},
                    identity,
                )
            self.inference.context_bound(agent["history"], TOOLS, settings)
        return True

    async def agent_loop(self, identity):
        engine = self.engine
        try:
            agent = engine.agent(identity)
            if not agent["model_version"] and hasattr(self.inference, "manifest"):
                settings = ModelSettings.model_validate(agent["model"])
                manifest = await self.inference.manifest(settings)
                with engine.transaction():
                    key = f"{settings.provider}:{settings.base_url}:{settings.model}"
                    engine.state["model_manifest"][key] = manifest
                    agent["model_version"] = manifest.get("hash")
                    engine.emit("agent.model-version", manifest, identity)
            while not self.stopping and engine.agent(identity)["state"] != "TERMINATED":
                if not await self.manage_context(identity):
                    return
                agent = engine.agent(identity)
                result = await self.infer(identity, copy.deepcopy(agent["history"]), TOOLS)
                engine.conversation(identity, result.message)
                calls = result.message.get("tool_calls") or []
                if not calls:
                    # A model choosing to stop is idle; no fabricated action or repeated nudging.
                    with engine.transaction():
                        agent["state"] = "IDLE"
                        agent["retry_after_ms"] = 2**62
                        engine.emit(
                            "agent.state", {"state": "IDLE", "reason": "no tool calls"}, identity
                        )
                    return
                with engine.transaction():
                    agent["pending_tools"] = calls.copy()
                for call in calls:
                    if engine.agent(identity)["state"] == "TERMINATED" or self.stopping:
                        return
                    function = call["function"]
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = None
                    output = await self.tools.execute(identity, function["name"], arguments)
                    if engine.agent(identity)["state"] == "TERMINATED":
                        await self.sandbox.kill(identity)
                        return
                    await self.observe()
                    engine.conversation(
                        identity,
                        {
                            "role": "tool",
                            "name": function["name"],
                            "tool_call_id": call.get("id", ""),
                            "content": json.dumps(output),
                        },
                    )
                    with engine.transaction():
                        agent["pending_tools"] = agent["pending_tools"][1:]
        except asyncio.CancelledError:
            if self.sandbox.available:
                await self.sandbox.kill(identity)
            raise
        except Exception as error:
            engine.record("inference.error", {"error": str(error)}, identity, severity="error")
            with engine.transaction():
                agent = engine.agent(identity)
                if agent["state"] != "TERMINATED":
                    agent["failures"] += 1
                    agent["retry_after_ms"] = engine.state["sim_ms"] + min(
                        60_000, 1000 * 2 ** min(agent["failures"], 6)
                    )
