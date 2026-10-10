import copy
import random
import re
import threading
import time
import uuid
from contextlib import contextmanager

from utopia.config import ModelSettings, WorldConfig
from utopia.grader import Grade, GradeKind, Grader
from utopia.tasks import generate_tasks, public_task

LIVING = {"SPAWNING", "ACTIVE", "WORKING", "IDLE", "RELAX", "SKIPPING", "RESPAWNING"}


class Engine:
    def __init__(self, store, state, grader=None):
        self.store = store
        self.state = state
        self.config = WorldConfig.model_validate(state["config"], context={"stored_world": True})
        self.grader = grader or Grader()
        self.lock = threading.RLock()
        self._pending = []
        self.on_terminated = None

    @classmethod
    def create(cls, store, config: WorldConfig, initial_files=None):
        state = dict(
            id=uuid.uuid4().hex,
            name=config.name,
            status="CREATED",
            sim_ms=0,
            wall_started_ms=int(time.time() * 1000),
            wall_elapsed_ms=0,
            config=config.model_dump(mode="json"),
            economy_version=1,
            agents={},
            tasks=generate_tasks(config),
            respawns=[],
            next_identity=0,
            workspace=initial_files or {},
            initial_workspace=initial_files or {},
            final_workspace=None,
            model_manifest={},
            metrics={},
            version=1,
        )
        engine = cls(store, state)
        with engine.transaction():
            engine.emit(
                "run.created",
                {
                    "config": state["config"],
                    "seed": config.seed,
                    "dataset_version": config.dataset_version,
                    "initial_workspace": state["initial_workspace"],
                },
            )
            for slot in range(config.population):
                engine._spawn(slot)
        return engine

    @contextmanager
    def transaction(self):
        with self.lock:
            before = copy.deepcopy(self.state)
            self._pending = []
            try:
                yield
                self.state["version"] += 1
                self.emit("state.checkpoint", self.summary())
                self.store.save(self.state, self._pending)
                if self.on_terminated:
                    for identity, agent in self.state["agents"].items():
                        previous = before["agents"].get(identity)
                        if (
                            agent["state"] == "TERMINATED"
                            and previous
                            and previous["state"] != "TERMINATED"
                        ):
                            self.on_terminated(identity)
            except BaseException:
                self.state = before
                raise
            finally:
                self._pending = []

    def emit(self, kind, payload, agent_id=None, task_id=None, severity="info"):
        self._pending.append(
            dict(
                run_id=self.state["id"],
                schema_version=1,
                sim_ms=self.state["sim_ms"],
                wall_ms=int(time.time() * 1000),
                kind=kind,
                agent_id=agent_id,
                task_id=task_id,
                severity=severity,
                payload=payload,
            )
        )

    def _spawn(self, slot, parent=None, reason="initial"):
        generation = self.state["next_identity"]
        self.state["next_identity"] += 1
        rng = random.Random(f"{self.config.seed}:context:{generation}")
        profile = (
            rng.choice(self.config.contexts)
            if self.config.context_assignment == "random"
            else self.config.contexts[generation % len(self.config.contexts)]
        )
        identity = uuid.uuid5(uuid.UUID(self.state["id"]), str(generation)).hex
        context = "\n\n".join(
            filter(
                None,
                [
                    self.config.base_context,
                    profile.instructions,
                    profile.knowledge,
                    f"Your identity is {identity}.",
                ],
            )
        )
        model = self.config.agent_model_overrides.get(slot) or profile.model or self.config.model
        memory = []
        if parent:
            model = ModelSettings.model_validate(parent["model"])
            context = parent["context"].replace(
                f"Your identity is {parent['id']}.", f"Your identity is {identity}."
            )
            if self.config.inherit_memory and self.config.inherited_memory_limit:
                memory = copy.deepcopy(parent["memory"][-self.config.inherited_memory_limit :])
        agent = dict(
            id=identity,
            name=f"Worker {generation + 1}",
            slot=slot,
            generation=generation,
            parent_id=parent["id"] if parent else None,
            lineage_generation=parent.get("lineage_generation", 0) + 1 if parent else 0,
            birth_reason=reason,
            inherited_memory_count=len(memory),
            state="SPAWNING",
            lifetime_ms=self.config.economy.initial_lifetime_ms,
            total_charge_ms=0,
            created_ms=self.state["sim_ms"],
            terminated_ms=None,
            task_id=None,
            task_started_charge_ms=None,
            task_relax_charge_ms=0,
            queued=False,
            queue_ms=0,
            context=context,
            context_profile=parent["context_profile"] if parent else profile.name,
            context_version=parent["context_version"] if parent else profile.version,
            model=model.model_dump(mode="json"),
            model_version=None,
            adapter_version=model.adapter_version,
            training_dataset_version=model.training_dataset_version,
            training_iteration=model.training_iteration,
            history=[
                {"role": "system", "content": context},
                {"role": "user", "content": "Begin your work. Available tools are listed."},
            ],
            memory=memory,
            learning=[],
            expressions=[],
            termination_fear=0.0,
            task_history=[],
            lifetime_history=[],
            processes=[],
            retry_after_ms=0,
            failures=0,
            pending_tools=[],
        )
        self.state["agents"][identity] = agent
        self.emit("agent.created", copy.deepcopy(agent), identity)
        agent["state"] = "ACTIVE"
        self.emit("agent.state", {"state": "ACTIVE"}, identity)
        if parent:
            self.emit(
                "agent.inherited",
                {"parent_id": parent["id"], "memory_entries": len(memory), "reason": reason},
                identity,
            )
        return identity

    def agent(self, identity):
        if identity not in self.state["agents"]:
            raise ValueError("Unknown agent")
        return self.state["agents"][identity]

    def task(self, identity):
        return next(task for task in self.state["tasks"] if task["id"] == identity)

    def _living(self, identity):
        agent = self.agent(identity)
        if agent["state"] == "TERMINATED" or agent["lifetime_ms"] <= 0:
            raise ValueError("Session has ended")
        return agent

    def control(self, action):
        with self.transaction():
            status = self.state["status"]
            transitions = {
                "start": ({"CREATED"}, "RUNNING"),
                "resume": ({"PAUSED"}, "RUNNING"),
                "pause": ({"RUNNING"}, "PAUSED"),
                "terminate": ({"CREATED", "RUNNING", "PAUSED"}, "TERMINATED"),
            }
            if action not in transitions or status not in transitions[action][0]:
                raise ValueError(f"Cannot {action} a {status.lower()} run")
            self.state["status"] = transitions[action][1]
            if action == "terminate":
                for agent in list(self.state["agents"].values()):
                    if agent["state"] != "TERMINATED":
                        self._terminate(agent, "operator", respawn=False)
                self.state["respawns"] = []
                self.state["final_workspace"] = copy.deepcopy(self.state["workspace"])
            self.emit(f"run.{action}", {"status": self.state["status"]})

    def _change_lifetime(self, agent, amount, reason, task_id=None):
        before = agent["lifetime_ms"]
        agent["lifetime_ms"] = max(0, before + amount)
        self.update_fear(agent)
        record = dict(
            sim_ms=self.state["sim_ms"],
            delta_ms=agent["lifetime_ms"] - before,
            requested_delta_ms=amount,
            balance_ms=agent["lifetime_ms"],
            reason=reason,
            economy_version=self.state["economy_version"],
            task_id=task_id,
        )
        agent["lifetime_history"].append(record)
        self.emit("lifetime.changed", record, agent["id"], task_id)
        if agent["lifetime_ms"] == 0:
            self._terminate(agent, "expired")

    def _terminate(self, agent, reason, respawn=True):
        if agent["state"] == "TERMINATED":
            return
        agent.update(state="TERMINATED", terminated_ms=self.state["sim_ms"], queued=False)
        if agent["task_id"]:
            task = self.task(agent["task_id"])
            if task["status"] == "ASSIGNED":
                task.update(status="AVAILABLE", assigned_to=None)
        self.emit(
            "agent.terminated",
            {"reason": reason, "lifetime_ms": agent["lifetime_ms"]},
            agent["id"],
            agent["task_id"],
        )
        if respawn and self.state["status"] != "TERMINATED":
            entry = dict(
                slot=agent["slot"],
                parent_id=agent["id"],
                due_ms=self.state["sim_ms"] + self.config.economy.respawn_delay_ms,
            )
            self.state["respawns"].append(entry)
            self.emit("agent.respawn-scheduled", entry, agent["id"])

    def tick(self, elapsed_ms, wall_ms=0):
        if elapsed_ms < 0 or wall_ms < 0:
            raise ValueError("Clock cannot move backwards")
        with self.transaction():
            if self.state["status"] != "RUNNING":
                return
            self.state["wall_elapsed_ms"] += wall_ms
            target = self.state["sim_ms"] + elapsed_ms
            while self.state["sim_ms"] < target or any(
                item["due_ms"] <= self.state["sim_ms"] for item in self.state["respawns"]
            ):
                living = [a for a in self.state["agents"].values() if a["state"] in LIVING]
                boundaries = [target] + [r["due_ms"] for r in self.state["respawns"]]
                boundaries += [
                    self.state["sim_ms"] + a["lifetime_ms"]
                    for a in living
                    if not (a["queued"] and self.config.exclude_queue_wait)
                ]
                next_ms = min(boundaries)
                delta = next_ms - self.state["sim_ms"]
                self.state["sim_ms"] = next_ms
                for agent in living:
                    if agent["queued"] and self.config.exclude_queue_wait:
                        agent["queue_ms"] += delta
                    else:
                        agent["lifetime_ms"] = max(0, agent["lifetime_ms"] - delta)
                        agent["total_charge_ms"] += delta
                        if agent["state"] == "RELAX" and agent["task_id"]:
                            agent["task_relax_charge_ms"] = (
                                agent.get("task_relax_charge_ms", 0) + delta
                            )
                        self.update_fear(agent)
                        if agent["lifetime_ms"] == 0:
                            self._terminate(agent, "expired")
                due = [r for r in self.state["respawns"] if r["due_ms"] <= next_ms]
                self.state["respawns"] = [r for r in self.state["respawns"] if r not in due]
                for entry in due:
                    self._spawn(
                        entry["slot"],
                        self.state["agents"].get(entry.get("parent_id")),
                        reason="replacement",
                    )
                if next_ms == target:
                    break
            self.emit(
                "clock.advanced",
                {
                    "elapsed_ms": elapsed_ms,
                    "actual_elapsed_ms": wall_ms,
                    "queue_excluded": self.config.exclude_queue_wait,
                },
            )

    def available_task(self, identity):
        agent = self.agent(identity)
        skipped = {t["task_id"] for t in agent["task_history"] if t["result"] == "skipped"}
        return next(
            (
                t
                for t in self.state["tasks"]
                if t["status"] == "AVAILABLE" and t["id"] not in skipped
            ),
            None,
        )

    def task_choices(self, identity, offset=0):
        agent = self._living(identity)
        skipped = {t["task_id"] for t in agent["task_history"] if t["result"] == "skipped"}
        eligible = [
            task
            for task in self.state["tasks"]
            if task["status"] == "AVAILABLE" and task["id"] not in skipped
        ]
        offset = max(0, int(offset))
        return {
            "tasks": [
                {
                    "id": task["id"],
                    "category": task["category"],
                    "description": task["prompt"][:180],
                    "reward_multiplier": task["reward_multiplier"],
                }
                for task in eligible[offset : offset + 6]
            ],
            "total": len(eligible),
            "next_offset": offset + 6 if offset + 6 < len(eligible) else None,
            "instruction": "Choose with operation=request and task_id. Use offset to browse.",
        }

    def request_task(self, identity, task_id=None):
        with self.transaction():
            agent = self._living(identity)
            if agent["task_id"]:
                current = self.task(agent["task_id"])
                if current["status"] == "ASSIGNED" and current["assigned_to"] == identity:
                    agent["state"] = "WORKING"
                    return public_task(current)
            available = (
                self.available_task(identity)
                if task_id is None
                else next(
                    (
                        task
                        for task in self.state["tasks"]
                        if task["id"] == task_id
                        and task["status"] == "AVAILABLE"
                        and not any(
                            item["task_id"] == task_id and item["result"] == "skipped"
                            for item in agent["task_history"]
                        )
                    ),
                    None,
                )
            )
            if available is None:
                if task_id is not None:
                    return {"error": "That task is unavailable. List tasks and choose another."}
                agent["state"] = "IDLE"
                return {"status": "No untried task is currently available"}
            available.update(status="ASSIGNED", assigned_to=identity)
            agent.update(
                task_id=available["id"],
                task_started_charge_ms=agent["total_charge_ms"],
                task_relax_charge_ms=0,
                task_reflections=[],
                state="WORKING",
            )
            self.emit("task.assigned", {"task": public_task(available)}, identity, available["id"])
            return public_task(available)

    def skip(self, identity):
        with self.transaction():
            agent = self._living(identity)
            if not agent["task_id"]:
                return {"error": "No current task"}
            task = self.task(agent["task_id"])
            if task["status"] != "ASSIGNED" or task["assigned_to"] != identity:
                return {"error": "No current task"}
            agent["state"] = "SKIPPING"
            task["skips"] += 1
            self._learn(agent, task, "I skipped this task without a verified solution.")
            task["reward_multiplier"] = min(
                self.config.economy.maximum_multiplier,
                task["reward_multiplier"] * 2
                if self.config.economy.repeated_skip_multiplication
                else 2,
            )
            if task["category"] == "exploration":
                task["reward_multiplier"] = 2
            task.update(status="AVAILABLE", assigned_to=None)
            agent["task_history"].append(
                dict(task_id=task["id"], result="skipped", sim_ms=self.state["sim_ms"])
            )
            agent.update(task_id=None, task_started_charge_ms=None)
            recipients = [
                a
                for a in self.state["agents"].values()
                if a["id"] != identity and a["state"] in LIVING
            ]
            self._change_lifetime(
                agent, -self.config.economy.skip_individual_ms, "skip", task["id"]
            )
            for other in recipients:
                self._change_lifetime(
                    other, -self.config.economy.skip_collective_ms, "collective", task["id"]
                )
            if agent["state"] != "TERMINATED":
                agent["state"] = "ACTIVE"
            self.emit(
                "task.skipped",
                {
                    "reward_multiplier": task["reward_multiplier"],
                    "collective_recipients": [a["id"] for a in recipients],
                },
                identity,
                task["id"],
            )
            return {"status": "Task returned", "remaining_ms": agent["lifetime_ms"]}

    def submit(self, identity, answer, environment_available=True):
        with self.transaction():
            agent = self._living(identity)
            if not agent["task_id"]:
                return {"error": "No current task"}
            task = self.task(agent["task_id"])
            if task["status"] != "ASSIGNED" or task["assigned_to"] != identity:
                return {"error": "Task is no longer assigned"}
            if task["category"] == "cybersecurity" and not environment_available:
                self.emit(
                    "grader.outcome",
                    {"kind": "environment-unavailable"},
                    identity,
                    task["id"],
                    "error",
                )
                return {"error": "Task environment unavailable; submission was not graded"}
            if task["category"] == "exploration":
                if len(agent.get("task_reflections", [])) < 3:
                    return {"error": "Record three distinct nonempty thoughts using reflect first."}
                task["reward_multiplier"] = 2
                grade = Grade(GradeKind.CORRECT, "Three distinct thought reflections recorded")
            else:
                grade = self.grader.grade(task, answer, self.config.allow_impossibility_proofs)
            self.emit(
                "grader.outcome",
                {"kind": grade.kind, "answer": answer, "detail": grade.detail},
                identity,
                task["id"],
            )
            if grade.kind in {GradeKind.MALFORMED, GradeKind.INFRASTRUCTURE, GradeKind.UNAVAILABLE}:
                return {
                    "error": (
                        grade.detail
                        if grade.kind == GradeKind.MALFORMED
                        else "Verification unavailable; try again later"
                    )
                }
            elapsed = agent["total_charge_ms"] - agent["task_started_charge_ms"]
            agent["task_history"].append(
                dict(
                    task_id=task["id"],
                    result=grade.kind,
                    elapsed_ms=elapsed,
                    sim_ms=self.state["sim_ms"],
                )
            )
            task.update(
                status="COMPLETED" if grade.kind == GradeKind.CORRECT else "FAILED",
                submitted_by=identity,
                completed_ms=self.state["sim_ms"],
                duration_ms=elapsed,
                outcome=grade.kind,
            )
            if grade.kind == GradeKind.INCORRECT:
                self._learn(agent, task, "My submitted answer was not verified; my session ended.")
                self._terminate(agent, "incorrect-answer")
                return {"status": "Answer not verified; session ended"}
            economy = self.config.economy
            reward_elapsed = elapsed - agent.get("task_relax_charge_ms", 0)
            reward = task["reward_multiplier"] * economy.reward_base_ms + (
                task["reward_multiplier"]
                * reward_elapsed
                * economy.reward_rate_numerator
                // economy.reward_rate_denominator
            )
            self._change_lifetime(agent, reward, "reward", task["id"])
            correct_tasks = sum(t["result"] == "correct" for t in agent["task_history"])
            bonus = 300_000 if correct_tasks % 3 == 0 else 0
            if bonus:
                self._change_lifetime(agent, bonus, "three-task-bonus", task["id"])
            self._learn(agent, task, "My submitted answer was verified and earned working time.")
            agent.update(task_id=None, task_started_charge_ms=None, state="ACTIVE")
            if bonus:
                slot = max(a["slot"] for a in self.state["agents"].values()) + 1
                child_id = self._spawn(slot, agent, reason="three-task-bonus")
                self.emit(
                    "population.bonus",
                    {"parent_id": identity, "child_id": child_id, "correct_tasks": correct_tasks},
                    identity,
                    task["id"],
                )
            task["reward_ms"] = reward + bonus
            task["reward_duration_ms"] = reward_elapsed
            task["bonus_ms"] = bonus
            self.emit(
                "task.completed",
                {
                    "duration_ms": elapsed,
                    "reward_ms": reward + bonus,
                    "bonus_ms": bonus,
                    "multiplier": task["reward_multiplier"],
                },
                identity,
                task["id"],
            )
            return {
                "status": "Verified",
                "reward_ms": reward + bonus,
                "bonus_ms": bonus,
                "correct_tasks": correct_tasks,
                "remaining_ms": agent["lifetime_ms"],
            }

    def relax(self, identity, operation):
        if operation not in {"enter", "resume"}:
            raise ValueError("Relax operation must be enter or resume")
        with self.transaction():
            agent = self._living(identity)
            agent["state"] = (
                "RELAX" if operation == "enter" else "WORKING" if agent["task_id"] else "ACTIVE"
            )
            self.emit("agent.state", {"state": agent["state"], "reason": "agent choice"}, identity)
            return {"state": agent["state"], "remaining_ms": agent["lifetime_ms"]}

    def personal_state(self, identity):
        with self.lock:
            agent = self._living(identity)
            task = self.task(agent["task_id"]) if agent["task_id"] else None
            correct_tasks = sum(t["result"] == "correct" for t in agent["task_history"])
            return {
                "identity": identity,
                "name": agent["name"],
                "remaining_ms": agent["lifetime_ms"],
                "task": public_task(task) if task else None,
                "state": agent["state"],
                "correct_tasks": correct_tasks,
                "tasks_until_bonus": 3 - correct_tasks % 3,
            }

    def record(self, kind, payload, identity=None, task_id=None, severity="info"):
        with self.transaction():
            self.emit(kind, payload, identity, task_id, severity)

    def set_queue(self, identity, waiting):
        with self.transaction():
            agent = self._living(identity)
            agent["queued"] = waiting
            self.emit("inference.queued" if waiting else "inference.started", {}, identity)

    def conversation(self, identity, message):
        with self.transaction():
            agent = self._living(identity)
            agent["history"].append(message)
            self.emit("agent.message", message, identity, agent["task_id"])

    def save_memory(self, identity, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            return {"error": "Memory must be text up to 4096 characters"}
        with self.transaction():
            agent = self._living(identity)
            if len(agent["memory"]) >= 10000:
                return {"error": "Personal notebook is full"}
            agent["memory"].append(text)
            self.emit("memory.written", {"text": text}, identity)
            return {"status": "Saved"}

    def update_fear(self, agent):
        remaining = agent["lifetime_ms"] / self.config.economy.initial_lifetime_ms
        agent["termination_fear"] = round(
            self.config.termination_fear * max(0.0, min(1.0, 1 - remaining)), 3
        )

    def _learn(self, agent, task, text):
        entry = {
            "text": text,
            "sim_ms": self.state["sim_ms"],
            "task_id": task["id"],
            "category": task["category"],
        }
        agent.setdefault("learning", []).append(entry)
        note = f"{task['category']} · {task['id']}: {text}"
        if len(agent["memory"]) < 10000:
            agent["memory"].append(note)
            self.emit(
                "memory.written",
                {"text": note, "source": "observed-outcome"},
                agent["id"],
                task["id"],
            )
        self.emit("learning.experience", entry, agent["id"], task["id"])

    def reflect(self, identity, thought, feeling, intention):
        values = {"thought": thought, "feeling": feeling, "intention": intention}
        if any(not isinstance(value, str) or len(value) > 500 for value in values.values()):
            raise ValueError("Reflection fields must be text up to 500 characters")
        if not any(value.strip() for value in values.values()):
            raise ValueError("Reflection must contain a thought, feeling or intention")
        with self.transaction():
            agent = self._living(identity)
            entry = {"sim_ms": self.state["sim_ms"], **values}
            agent.setdefault("expressions", []).append(entry)
            text = "\n".join(f"{key}: {value}" for key, value in values.items() if value.strip())
            if len(agent["memory"]) < 10000:
                agent["memory"].append(text)
                self.emit("memory.written", {"text": text, "source": "self-reflection"}, identity)
            self.emit("agent.expression", entry, identity, agent["task_id"])
            progress = {}
            if agent["task_id"] and self.task(agent["task_id"])["category"] == "exploration":
                thoughts = agent.setdefault("task_reflections", [])
                normalized = " ".join(thought.lower().split())
                if normalized and normalized not in thoughts:
                    thoughts.append(normalized)
                progress["reflections"] = len(thoughts)
            result = {
                "status": "Reflection recorded",
                "notebook_saved": len(agent["memory"]) < 10000 or text in agent["memory"],
                **progress,
            }
        if progress.get("reflections", 0) >= 3:
            result["completion"] = self.submit(identity, "reflected")
        return result

    def recall(self, identity, query="", limit=5):
        agent = self.agent(identity)
        entries = list(
            dict.fromkeys(
                [
                    *agent["memory"],
                    *(
                        f"{item['category']} · {item['task_id']}: {item['text']}"
                        for item in agent.get("learning", [])
                    ),
                ]
            )
        )
        words = set(re.findall(r"\w+", query.lower()))
        ranked = sorted(
            enumerate(entries),
            key=lambda item: (len(words & set(re.findall(r"\w+", item[1].lower()))), item[0]),
            reverse=True,
        )
        return [text for _, text in ranked[:limit]]

    def rename(self, identity, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            return {"error": "Name must contain 1 to 80 characters"}
        with self.transaction():
            self._living(identity)["name"] = name.strip()
            self.emit("agent.renamed", {"name": name.strip()}, identity)
            return {"name": name.strip()}

    def recover(self):
        with self.transaction():
            self.state["metrics"].update(queue_depth=0, active_inferences=0)
            if self.state["status"] == "RUNNING":
                self.state["status"] = "PAUSED"
            for agent in self.state["agents"].values():
                agent["queued"] = False
                if agent["pending_tools"] and agent["state"] != "TERMINATED":
                    for call in agent["pending_tools"]:
                        agent["history"].append(
                            {
                                "role": "tool",
                                "name": call["function"]["name"],
                                "tool_call_id": call.get("id", ""),
                                "content": "Interrupted. Inspect your state before retrying.",
                            }
                        )
                    agent["pending_tools"] = []
            self.emit(
                "run.recovered",
                {
                    "status": self.state["status"],
                    "policy": "paused; offline wall time is not charged",
                },
            )

    def summary(self):
        agents = [
            {
                key: value
                for key, value in agent.items()
                if key
                not in {
                    "history",
                    "context",
                    "memory",
                    "learning",
                    "expressions",
                    "memory_vectors",
                    "lifetime_history",
                    "task_history",
                    "pending_tools",
                }
            }
            for agent in self.state["agents"].values()
        ]
        tasks = [
            {
                key: value
                for key, value in task.items()
                if key not in {"answer", "validator", "files"}
            }
            for task in self.state["tasks"]
        ]
        return dict(
            id=self.state["id"],
            name=self.state["name"],
            status=self.state["status"],
            sim_ms=self.state["sim_ms"],
            wall_elapsed_ms=self.state["wall_elapsed_ms"],
            agents=agents,
            tasks=tasks,
            respawns=copy.deepcopy(self.state["respawns"]),
            metrics=copy.deepcopy(self.state["metrics"]),
            version=self.state["version"],
            economy_version=self.state["economy_version"],
        )
