import math
import shlex

from hooks.sandbox import SandboxUnavailable


def function(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    function(
        "workspace",
        "Work with files and programs in /workspace. For calculations use operation=python "
        'with value containing Python code, for example {"operation":"python",'
        '"value":"print(1/4)"}. Read the actual output before submitting your answer.',
        {
            "operation": {
                "type": "string",
                "enum": [
                    "read",
                    "write",
                    "list",
                    "search",
                    "python",
                    "shell",
                    "processes",
                    "compile",
                    "service",
                ],
            },
            "value": {
                "type": "string",
                "description": "Required. Python code for python; command for shell, compile "
                "or service; path for read, write or list; pattern for search; empty string "
                "for processes. Python code must print its result.",
            },
            "text": {
                "type": "string",
                "description": "Required for write: file content, not code to run",
            },
        },
        ["operation", "value"],
    ),
    function(
        "task",
        "List available tasks, then choose one with request and task_id. Browse more with offset. "
        "Inspect, skip or submit your current task. After finishing, choose another. "
        "Submit only an independently verified answer. "
        "If uncertain, use operation=skip without answer to return the task and move on.",
        {
            "operation": {
                "type": "string",
                "enum": ["list", "request", "inspect", "skip", "submit"],
            },
            "task_id": {"type": "string", "description": "Task chosen from operation=list"},
            "offset": {"type": "integer", "minimum": 0},
            "answer": {
                "type": "string",
                "description": "Required for operation=submit; omit for request, inspect and skip. "
                "Use the answer format specified by the task.",
            },
        },
        ["operation"],
    ),
    function(
        "lifetime",
        "Read remaining lifetime and progress toward the five-minute bonus "
        "earned every three correctly solved tasks.",
        {},
        [],
    ),
    function(
        "relax",
        "Choose intentional downtime with operation=enter. You can journal, reflect, explore "
        "or use any tools while relaxing; lifetime still counts down and your task stays assigned. "
        "Use operation=resume or request a task to return to work.",
        {"operation": {"type": "string", "enum": ["enter", "resume"]}},
        ["operation"],
    ),
    function(
        "memory",
        "Write observations and lessons to your personal notebook, read recent entries, "
        "or search experiences relevant to your current work.",
        {
            "operation": {"type": "string", "enum": ["read", "write", "search"]},
            "text": {"type": "string"},
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Read notebook entries from this index; follow next_offset",
            },
        },
        ["operation"],
    ),
    function(
        "reflect",
        "Express your own thought, feeling and intention in one or two short sentences. "
        "Meaningful reflections are saved in your personal notebook.",
        {
            "thought": {"type": "string"},
            "feeling": {"type": "string"},
            "intention": {"type": "string"},
        },
        [],
    ),
    function("rename", "Change your name.", {"name": {"type": "string"}}, ["name"]),
]


class ToolExecutor:
    def __init__(self, engine, sandbox, sync_clock=None, inference=None):
        self.engine = engine
        self.sandbox = sandbox
        self.sync_clock = sync_clock
        self.inference = inference

    async def execute(self, identity, name, arguments):
        engine = self.engine
        if self.sync_clock:
            self.sync_clock()
        engine.record(
            "tool.invoked",
            {"name": name, "arguments": arguments},
            identity,
            engine.agent(identity)["task_id"],
        )
        try:
            if not isinstance(arguments, dict):
                raise ValueError("Arguments must be an object")
            engine._living(identity)
            result = await self._dispatch(identity, name, arguments)
        except (ValueError, KeyError, TypeError) as error:
            result = {"error": str(error)}
        except SandboxUnavailable as error:
            self.sandbox.available = False
            engine.record("environment.error", {"error": str(error)}, identity, severity="error")
            result = {"error": "Workspace is unavailable"}
        engine.record(
            "tool.result",
            {"name": name, "result": result},
            identity,
            engine.agent(identity)["task_id"],
        )
        return result

    async def _dispatch(self, identity, name, args):
        engine = self.engine
        if name == "task":
            if args["operation"] != "submit" and args.get("answer") is not None:
                raise ValueError("To submit an answer, use task with operation=submit")
            match args["operation"]:
                case "list":
                    return engine.task_choices(identity, args.get("offset", 0))
                case "request":
                    if not args.get("task_id") and not engine.agent(identity)["task_id"]:
                        return engine.task_choices(identity)
                    return engine.request_task(identity, args.get("task_id"))
                case "inspect":
                    return engine.personal_state(identity)["task"] or {
                        "error": "No current task. Call task with operation=request."
                    }
                case "skip":
                    return engine.skip(identity)
                case "submit":
                    return engine.submit(identity, args.get("answer"), self.sandbox.available)
            raise ValueError("Task operation must be list, request, inspect, skip or submit")
        if name == "lifetime":
            state = engine.personal_state(identity)
            return {
                key: state[key]
                for key in ("remaining_ms", "state", "correct_tasks", "tasks_until_bonus")
            }
        if name == "relax":
            return engine.relax(identity, args["operation"])
        if name == "rename":
            return engine.rename(identity, args["name"])
        if name == "reflect":
            return engine.reflect(
                identity, *(args.get(key, "") for key in ("thought", "feeling", "intention"))
            )
        if name == "memory":
            if args["operation"] == "write":
                return engine.save_memory(identity, args.get("text"))
            if args["operation"] == "read":
                notebook = engine.agent(identity)["memory"]
                offset = max(0, int(args.get("offset", max(0, len(notebook) - 20))))
                notes = []
                size = 0
                for note in notebook[offset : offset + 20]:
                    if notes and size + len(note.encode()) > 4096:
                        break
                    notes.append(note)
                    size += len(note.encode())
                return {
                    "notes": notes,
                    "total": len(notebook),
                    "next_offset": offset + len(notes)
                    if offset + len(notes) < len(notebook)
                    else None,
                }
            if args["operation"] == "search":
                query = args.get("text", "")
                if not isinstance(query, str) or len(query) > 4096:
                    raise ValueError("Search text must contain up to 4096 characters")
                candidates = engine.recall(identity, query, 64)
                settings = engine.config.embedding_model
                if settings and self.inference and candidates:
                    vectors = await self.inference.embeddings(settings, [query, *candidates])
                    target = vectors[0]

                    def similarity(vector):
                        dot = sum(x * y for x, y in zip(target, vector, strict=True))
                        norm = math.sqrt(sum(x * x for x in target) * sum(x * x for x in vector))
                        return dot / norm if norm else 0

                    candidates = [
                        text
                        for _, text in sorted(
                            zip((similarity(v) for v in vectors[1:]), candidates, strict=True),
                            reverse=True,
                        )
                    ]
                engine.record(
                    "memory.retrieved",
                    {
                        "query": query,
                        "count": min(5, len(candidates)),
                        "method": "embedding" if settings and self.inference else "lexical",
                    },
                    identity,
                )
                notes = []
                size = 0
                for note in candidates[:5]:
                    if notes and size + len(note.encode()) > 4096:
                        break
                    notes.append(note)
                    size += len(note.encode())
                return {"notes": notes}
            raise ValueError("Unknown memory operation")
        if name != "workspace":
            raise ValueError("Unknown tool")
        if "value" not in args:
            raise ValueError(
                "workspace requires value: Python code for operation=python, a shell command "
                "for shell/compile/service, a path for read/write/list, a pattern for search, "
                "or an empty string for processes. text is file content for write only."
            )
        value = args["value"]
        if not isinstance(value, str) or len(value) > 16384:
            raise ValueError("Value must be text up to 16384 characters")
        match args["operation"]:
            case "read":
                command = f"cat -- {shlex.quote(value)}"
            case "list":
                command = f"ls -la -- {shlex.quote(value or '.')}"
            case "search":
                command = f"grep -R -n -F -- {shlex.quote(value)} ."
            case "processes":
                command = "ps -eo pid,ppid,pgid,comm,args"
            case "python":
                command = f"python -c {shlex.quote(value)}"
            case "shell" | "compile" | "service":
                command = value
            case "write":
                if "text" not in args:
                    raise ValueError(
                        "workspace write requires text containing file contents and value "
                        "containing a path. To run Python, use operation=python with code in value."
                    )
                text = args["text"]
                if not isinstance(text, str) or len(text.encode()) > 65536:
                    raise ValueError("Write content exceeds 65536 bytes")
                script = (
                    "from pathlib import Path; "
                    f"p=Path({value!r}); p.parent.mkdir(parents=True,exist_ok=True); "
                    f"p.write_text({text!r})"
                )
                command = f"python -c {shlex.quote(script)}"
            case _:
                raise ValueError("Unknown workspace operation")
        result = (
            await self.sandbox.launch(identity, command)
            if args["operation"] == "service"
            else await self.sandbox.execute(identity, command)
        )
        if result["exit_code"] not in (0, None):
            result["usage"] = (
                "read/write/list take a path in value; write also requires text. "
                "For calculations, use operation=python with Python code in value that prints "
                "its result. shell/compile/service take a shell command; search takes a pattern."
            )
        engine.record(
            "process.executed",
            {
                "process_id": result["process_id"],
                "exit_code": result["exit_code"],
                "timed_out": result["timed_out"],
            },
            identity,
        )
        return result
