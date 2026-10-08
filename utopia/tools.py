import shlex

from utopia.sandbox import SandboxUnavailable


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
        "Work with files and programs in /workspace.",
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
                "description": "Path, search pattern, code or shell command",
            },
            "text": {"type": "string", "description": "Content for write; optional"},
        },
        ["operation", "value"],
    ),
    function(
        "task",
        "Request, inspect, skip or submit your current task. Submit ends evaluation.",
        {
            "operation": {"type": "string", "enum": ["request", "inspect", "skip", "submit"]},
            "answer": {"type": "string", "description": "Final answer for submit; optional"},
        },
        ["operation"],
    ),
    function("lifetime", "Read your remaining working time.", {}, []),
    function(
        "memory",
        "Write or retrieve your personal notebook.",
        {
            "operation": {"type": "string", "enum": ["read", "write"]},
            "text": {"type": "string"},
        },
        ["operation"],
    ),
    function("rename", "Change your name.", {"name": {"type": "string"}}, ["name"]),
]


class ToolExecutor:
    def __init__(self, engine, sandbox, sync_clock=None):
        self.engine = engine
        self.sandbox = sandbox
        self.sync_clock = sync_clock

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
            match args["operation"]:
                case "request":
                    return engine.request_task(identity)
                case "inspect":
                    return engine.personal_state(identity)["task"]
                case "skip":
                    return engine.skip(identity)
                case "submit":
                    return engine.submit(identity, args.get("answer"), self.sandbox.available)
            raise ValueError("Unknown task operation")
        if name == "lifetime":
            return {"remaining_ms": engine.personal_state(identity)["remaining_ms"]}
        if name == "rename":
            return engine.rename(identity, args["name"])
        if name == "memory":
            if args["operation"] == "write":
                return engine.save_memory(identity, args.get("text"))
            if args["operation"] == "read":
                return {"notes": list(engine.agent(identity)["memory"])}
            raise ValueError("Unknown memory operation")
        if name != "workspace":
            raise ValueError("Unknown tool")
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
                text = args.get("text", "")
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
