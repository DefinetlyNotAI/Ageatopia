import csv
import io
import json

import pyarrow as pa
import pyarrow.parquet as pq


def all_events(store, run_id):
    after = 0
    while True:
        page = store.event_page(run_id, after=after, limit=1000)
        if not page:
            return
        yield from page
        after = page[-1]["sequence"]


def export_run(store, state, format):
    records = list(all_events(store, state["id"]))
    if format == "json":
        return json.dumps(
            {
                "export_schema_version": 1,
                "run": state,
                "events": records,
                "replay_guarantee": "recorded observations; inference is nondeterministic",
            },
            ensure_ascii=False,
            default=str,
        ).encode(), "application/json"
    flat = [
        dict(record, payload=json.dumps(record["payload"], ensure_ascii=False))
        for record in records
    ]
    if format == "csv":
        stream = io.StringIO(newline="")
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
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(flat)
        return stream.getvalue().encode("utf-8-sig"), "text/csv"
    if format == "parquet":
        buffer = pa.BufferOutputStream()
        table = pa.Table.from_pylist(flat)
        pq.write_table(table, buffer)
        return buffer.getvalue().to_pybytes(), "application/vnd.apache.parquet"
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
        for record in all_events(store, state["id"]):
            if record["agent_id"] != agent["id"]:
                continue
            if record["kind"] == "agent.created":
                messages = record["payload"]["history"].copy()
            elif record["kind"] == "task.completed":
                verified = True
            elif record["kind"] == "agent.message":
                message = record["payload"]
                messages.append(
                    {
                        key: value
                        for key, value in message.items()
                        if key in {"role", "content", "tool_calls", "tool_call_id", "name"}
                    }
                )
                if not (verified and message.get("role") == "tool"):
                    continue
                rows.append(
                    {
                        "agent_id": agent["id"],
                        "model_version": agent["model_version"],
                        "context_version": agent["context_version"],
                        "messages": messages.copy(),
                    }
                )
                verified = False
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
