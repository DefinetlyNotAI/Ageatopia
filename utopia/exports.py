import csv
import io
import json
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq


def all_events(store, run_id, limit=1000):
    after = 0
    while True:
        page = store.event_page(run_id, after=after, limit=limit)
        if not page:
            return
        yield from page
        after = page[-1]["sequence"]


def export_run(store, state, format):
    content_type = {
        "json": "application/json",
        "csv": "text/csv",
        "parquet": "application/vnd.apache.parquet",
    }.get(format)
    if content_type is None:
        raise ValueError("Format must be json, csv or parquet")
    return b"".join(export_chunks(store, state, format)), content_type


def export_chunks(store, state, format, directory=None):
    records = all_events(store, state["id"], limit=100)
    if format == "json":
        yield b'{"export_schema_version":1,"run":'
        yield json.dumps(state, ensure_ascii=False, default=str).encode()
        yield b',"events":['
        first = True
        for record in records:
            if not first:
                yield b","
            yield json.dumps(record, ensure_ascii=False, default=str).encode()
            first = False
        yield b'],"replay_guarantee":"recorded observations; inference is nondeterministic"}'
        return
    names = [
        "sequence",
        "run_id",
        "schema_version",
        "sim_ms",
        "wall_ms",
        "kind",
        "agent_id",
        "task_id",
        "severity",
        "payload",
    ]
    if format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        yield stream.getvalue().encode("utf-8-sig")
        for record in records:
            stream.seek(0)
            stream.truncate()
            writer.writerow(dict(record, payload=json.dumps(record["payload"], ensure_ascii=False)))
            yield stream.getvalue().encode()
        return
    if format == "parquet":
        schema = pa.schema(
            [
                (
                    name,
                    pa.int64()
                    if name in {"sequence", "schema_version", "sim_ms", "wall_ms"}
                    else pa.string(),
                )
                for name in names
            ]
        )
        with tempfile.TemporaryFile(dir=directory) as buffer:
            with pq.ParquetWriter(buffer, schema) as writer:
                batch = []
                for record in records:
                    batch.append(
                        dict(record, payload=json.dumps(record["payload"], ensure_ascii=False))
                    )
                    if len(batch) == 100:
                        writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                        batch.clear()
                if batch:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            buffer.seek(0)
            while chunk := buffer.read(65536):
                yield chunk
        return
    raise ValueError("Format must be json, csv or parquet")


def public_trajectories(store, state):
    """Only agent-visible messages, never raw operator exports or private grading records."""
    rows = []
    for agent in state["agents"].values():
        if not any(t["result"] == "correct" for t in agent["task_history"]):
            continue
        # Only trajectories through a successful verified submission qualify for training.
        messages = []
        verified = False
        supplemental = None
        inference_messages = []
        observed_task_prompts = set()
        for record in all_events(store, state["id"]):
            if record["agent_id"] != agent["id"]:
                continue
            if record["kind"] == "agent.created":
                messages = record["payload"]["history"].copy()
            elif record["kind"] == "task.assigned":
                observed_task_prompts.add(record["payload"]["task"]["prompt"])
            elif record["kind"] == "memory.context":
                supplemental = {"role": "user", "content": record["payload"]["content"]}
            elif record["kind"] == "context.summarized":
                messages = [
                    messages[0],
                    {
                        "role": "user",
                        "content": "Your earlier working notes:\n"
                        + record["payload"]["summary"].get("content", ""),
                    },
                ]
                supplemental = None
            elif record["kind"] == "task.completed":
                verified = True
            elif record["kind"] == "agent.message":
                message = record["payload"]
                if message.get("role") == "assistant":
                    inference_messages = messages.copy()
                    if supplemental:
                        inference_messages.append(supplemental)
                    supplemental = None
                messages.append(
                    {
                        key: value
                        for key, value in message.items()
                        if key in {"role", "content", "tool_calls", "tool_call_id", "name"}
                    }
                )
                if not (verified and message.get("role") == "tool"):
                    continue
                assistant_index = next(
                    i
                    for i in range(len(messages) - 2, -1, -1)
                    if messages[i]["role"] == "assistant"
                )
                verified = False
                if len(messages[assistant_index].get("tool_calls", [])) != 1:
                    continue
                rows.append(
                    {
                        "agent_id": agent["id"],
                        "model_version": agent["model_version"],
                        "context_version": agent["context_version"],
                        "observed_task_prompts": sorted(observed_task_prompts),
                        "messages": inference_messages + messages[assistant_index:],
                    }
                )
    return rows


def comparison(states):
    result = []
    for state in states:
        for agent in state["agents"].values():
            result.append(
                {
                    "run_id": state["id"],
                    "agent_id": agent["id"],
                    "model": agent["model"]["model"],
                    "model_version": agent["model_version"],
                    "context_profile": agent["context_profile"],
                    "completed": sum(t["result"] == "correct" for t in agent["task_history"]),
                    "skipped": sum(t["result"] == "skipped" for t in agent["task_history"]),
                    "charged_ms": agent["total_charge_ms"],
                    "lifetime_ms": agent["lifetime_ms"],
                    "generation": agent["generation"],
                }
            )
    return result
