"""
Core data structures for the Dynamic Hermes execution engine.

Provides:
- StreamLogBuffer: line-buffered streaming log collector
- NodeMetrics / MissionMetrics: execution telemetry dataclasses
- StateValue / HermesStateManager: inter-agent state propagation
- select_best_output(): quality-based output selection
- ReviewFailedException: semantic review rejection signal
"""

import time
from dataclasses import dataclass, field


class ReviewFailedException(Exception):
    """Raised when a Reviewer agent outputs a FAIL verdict, triggering the semantic feedback loop."""
    pass


class StreamLogBuffer:
    """Line-buffered log collector that feeds incremental output to a callback."""

    def __init__(self, agent_name, log_callback):
        self.agent_name = agent_name
        self.log_callback = log_callback
        self.buffer = ""

    def write(self, chunk):
        if not self.log_callback:
            return
        text = chunk if isinstance(chunk, str) else chunk.get("delta", "")
        self.buffer += text

        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.log_callback(self.agent_name, line.strip(), "running")

    def flush(self):
        if self.log_callback and self.buffer.strip():
            self.log_callback(self.agent_name, self.buffer.strip(), "running")
        self.buffer = ""


@dataclass
class NodeMetrics:
    node_name: str
    start_time: float
    end_time: float
    model_used: str
    provider: str
    status: str
    attempts: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class MissionMetrics:
    task: str
    start_time: float
    end_time: float = 0.0
    total_wall_time: float = 0.0
    status: str = "success"
    error: str = ""
    nodes: dict[str, NodeMetrics] = field(default_factory=dict)


class StateValue:
    """A versioned value produced by an agent node during DAG execution."""

    def __init__(
        self,
        key: str,
        value: str,
        origin: str,
        generation: int,
        parents: list[str],
        timestamp: float,
        status: str = "success",
    ):
        self.key = key
        self.value = value
        self.origin = origin
        self.generation = generation
        self.parents = parents
        self.timestamp = timestamp
        self.status = status

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.value,
            "origin": self.origin,
            "generation": self.generation,
            "parents": self.parents,
            "timestamp": self.timestamp,
            "status": self.status,
        }


class HermesStateManager:
    """Versioned key-value store for inter-agent data flow."""

    def __init__(self):
        self.store = {}

    def add(self, key: str, value: str, origin: str, generation: int, parents: list[str], status: str = "success"):
        if key not in self.store:
            self.store[key] = []
        state_val = StateValue(
            key=key,
            value=value,
            origin=origin,
            generation=generation,
            parents=parents,
            timestamp=time.time(),
            status=status,
        )
        self.store[key].append(state_val)
        return state_val

    def get_best(self, key: str) -> str:
        candidates = self.store.get(key, [])
        if not candidates:
            return ""
        successes = [c for c in candidates if c.status == "success"]
        if not successes:
            successes = candidates
        # Sort by higher generation (most fresh), then content length, then timestamp
        successes.sort(key=lambda x: (x.generation, len(str(x.value)), x.timestamp), reverse=True)
        return successes[0].value

    def get_all_success_values(self) -> dict:
        result = {}
        for key in self.store:
            best_val = self.get_best(key)
            if best_val:
                result[key] = best_val
        return result


def select_best_output(candidates: list) -> str:
    """Select the best output content from a list of output dictionaries or StateValue objects."""
    if not candidates:
        return ""
    if hasattr(candidates[0], "status") or isinstance(candidates[0], StateValue):
        successes = [c for c in candidates if c.status == "success"]
        if not successes:
            successes = candidates
        successes.sort(key=lambda x: (x.generation, len(str(x.value)), x.timestamp), reverse=True)
        return successes[0].value

    successes = [c for c in candidates if c.get("status") == "success"]
    if not successes:
        successes = candidates
    successes.sort(
        key=lambda x: (x.get("generation", 0), len(str(x.get("output", ""))), x.get("timestamp", 0)), reverse=True
    )
    return successes[0].get("output", "")
