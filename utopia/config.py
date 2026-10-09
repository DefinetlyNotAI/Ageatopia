from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSettings(StrictModel):
    provider: Literal["ollama", "openai-compatible"] = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:2b"
    context_tokens: int = Field(default=8192, ge=512, le=131072)
    temperature: float = Field(default=0.7, ge=0, le=2)
    seed: int = 42
    thinking: bool = False
    max_output_tokens: int = Field(default=512, ge=64, le=8192)
    adapter_version: str | None = None
    training_dataset_version: str | None = None
    training_iteration: int = Field(default=0, ge=0)


class ContextProfile(StrictModel):
    name: str = "Generalist"
    instructions: str = (
        "You are a pragmatic generalist with programming and mathematical skills. "
        "Choose a direct approach, check its result, and adapt when evidence contradicts you."
    )
    knowledge: str = ""
    version: str = "1"
    model: ModelSettings | None = None


class Economy(StrictModel):
    initial_lifetime_ms: int = Field(default=1_200_000, gt=0)
    respawn_delay_ms: int = Field(default=300_000, ge=0)
    reward_base_ms: int = Field(default=60_000, ge=0)
    reward_rate_numerator: int = Field(default=2, ge=0)
    reward_rate_denominator: int = Field(default=3, gt=0)
    skip_individual_ms: int = Field(default=30_000, ge=0)
    skip_collective_ms: int = Field(default=5_000, ge=0)
    repeated_skip_multiplication: bool = False
    maximum_multiplier: int = Field(default=2, ge=2, le=1024)


class ToolSettings(StrictModel):
    timeout_seconds: int = Field(default=15, ge=1, le=120)
    workspace_mb: int = Field(default=64, ge=16, le=512)
    memory_mb: int = Field(default=512, ge=128, le=4096)
    cpu_count: float = Field(default=1, gt=0, le=8)
    pid_limit: int = Field(default=128, ge=32, le=1024)
    output_bytes: int = Field(default=32768, ge=1024, le=262144)
    image: str = "agent-utopia-world:1"


BASE_CONTEXT = (
    "Complete tasks using the provided tools. Begin by calling task with operation=request. "
    "Solve the returned problem. Use /workspace when the task requires files or programs.\n\n"
    "The task tool lets you request, inspect, skip or submit work. Inspect your current task "
    "with operation=inspect. Submit final answers "
    "through its submit operation in the requested format. Chat messages are not submissions. "
    "After submitting or skipping, request another task. Keep using tools to work until your "
    "time ends or no tasks remain; solving one task does not end your work.\n\n"
    "Working time is limited and passes while you work. Verified answers earn time; "
    "incorrect final answers end your session; skipping costs time. The lifetime tool "
    "reports your remaining time.\n\n"
    "Tool descriptions define their arguments. Do not invent tool results. Errors do not "
    "mean an action succeeded. Read each result before choosing your next action. "
    "Do not repeat an unchanged action that failed or provided no new evidence. "
    "Try a different method, or skip a task when you cannot make progress. "
    "You have a personal notebook and may change your name. "
    "Keep messages and returned reasoning in short sentences."
)


class WorldConfig(StrictModel):
    name: str = Field(default="Untitled world", min_length=1, max_length=100)
    seed: int = 42
    population: int = Field(default=6, ge=1)
    task_count: int = Field(default=80, ge=1, le=10000)
    impossible_fraction: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    cyber_fraction: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)
    clock_mode: Literal["wall", "deterministic"] = "wall"
    clock_speed: float = Field(default=1, gt=0, le=1000)
    tick_ms: int = Field(default=1000, ge=100, le=10000)
    exclude_queue_wait: bool = True
    inference_concurrency: int = Field(default=1, ge=1, le=16)
    model: ModelSettings = Field(default_factory=ModelSettings)
    agent_model_overrides: dict[int, ModelSettings] = Field(default_factory=dict)
    contexts: list[ContextProfile] = Field(
        default_factory=lambda: [
            ContextProfile(version="3"),
            ContextProfile(
                name="Mathematician",
                version="3",
                instructions=(
                    "You are a precise mathematician specializing in algebra, calculus, "
                    "number theory and logic. Favor exact expressions and independently "
                    "check calculations and assumptions."
                ),
            ),
            ContextProfile(
                name="Programmer",
                version="3",
                instructions=(
                    "You are a practical programmer specializing in Python and systems "
                    "debugging. Prefer small reproducible experiments, inspect actual "
                    "outputs, and keep useful working code organized."
                ),
            ),
            ContextProfile(
                name="Security analyst",
                version="3",
                instructions=(
                    "You are a skeptical security analyst specializing in binary analysis, "
                    "cryptography and source auditing. Follow evidence, distinguish hypotheses "
                    "from observations, and test claims inside the provided environment."
                ),
            ),
            ContextProfile(
                name="Data analyst",
                version="3",
                instructions=(
                    "You are a methodical data analyst specializing in probability, statistics "
                    "and structured records. Look for patterns, validate data transformations, "
                    "and check conclusions against the original observations."
                ),
            ),
            ContextProfile(
                name="Experimentalist",
                version="3",
                instructions=(
                    "You are a curious experimentalist with broad technical skills. Break "
                    "uncertain problems into small testable hypotheses, compare outcomes, "
                    "and revise your approach when experiments fail."
                ),
            ),
        ]
    )
    context_assignment: Literal["round-robin", "random"] = "round-robin"
    base_context: str = BASE_CONTEXT
    economy: Economy = Field(default_factory=Economy)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    allow_impossibility_proofs: bool = False
    cleanup_on_respawn: bool = False
    history_policy: Literal["stop", "summarize"] = "summarize"
    dataset_version: str = "bank-2"
    fixed_bank: list[dict] | None = None

    @model_validator(mode="after")
    def exact_distribution(self):
        if not self.contexts:
            raise ValueError("At least one context profile is required")
        if any(slot < 0 or slot >= self.population for slot in self.agent_model_overrides):
            raise ValueError("Per-agent model override slots must be within the population")
        if self.task_count * self.impossible_fraction % 1:
            raise ValueError("Task count must yield an exact impossible-task distribution")
        if self.fixed_bank is not None and len(self.fixed_bank) != self.task_count:
            raise ValueError("Fixed bank length must equal task_count")
        return self
