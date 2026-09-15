from enum import StrEnum

from pydantic import BaseModel


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class InvocationStatus(StrEnum):
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Run(BaseModel):
    id: str
    employee_id: str
    status: RunStatus
    current_step: int
    pause_reason: str | None
    approval_decision: str | None
    owner_token: str | None
    lease_expires_at: str | None
    max_steps: int
    tool_call_count: int
    max_tool_calls: int
    error: str | None
    created_at: str
    updated_at: str


class Step(BaseModel):
    run_id: str
    idx: int
    name: str
    status: StepStatus
    attempts: int
    output: dict | None
    error: str | None
    started_at: str | None
    completed_at: str | None


class TraceEvent(BaseModel):
    id: int
    run_id: str
    step_idx: int | None
    event: str
    detail: dict | None
    at: str
