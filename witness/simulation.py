"""Orchestration: build a fleet of simulated nodes, run one checkpoint
round under a chosen coordinator and fault schedule, return a RunResult.
"""

from typing import Callable

from witness.clock import SimClock
from witness.coordinator import BaselineCoordinator, CommitLog, WitnessCoordinator
from witness.metrics import RunResult
from witness.node import Node


def build_nodes(
    clock: SimClock,
    num_nodes: int,
    shard_bytes: int = 10_000_000,
    ssd_bandwidth_bytes_per_ms: float = 50_000,
    bmc_query_latency_ms: float = 15.0,
) -> list[Node]:
    return [
        Node(clock, f"rank-{i}", shard_bytes, ssd_bandwidth_bytes_per_ms, bmc_query_latency_ms)
        for i in range(num_nodes)
    ]


def run_scenario(
    scenario_name: str,
    coordinator_kind: str,  # "baseline" or "witness"
    nodes: list[Node],
    clock: SimClock,
    generation: int = 1,
    fault_at_write_fraction: float | None = None,
    fault_fn: Callable[[list[Node]], None] | None = None,
    coordinator_kwargs: dict | None = None,
    on_event: Callable[[float, str], None] | None = None,
) -> tuple[RunResult, CommitLog]:
    coordinator_kwargs = coordinator_kwargs or {}
    commit_log = CommitLog()

    if coordinator_kind == "baseline":
        coordinator = BaselineCoordinator(clock, nodes, commit_log=commit_log, **coordinator_kwargs)
    elif coordinator_kind == "witness":
        coordinator = WitnessCoordinator(clock, nodes, commit_log=commit_log, **coordinator_kwargs)
    else:
        raise ValueError(f"unknown coordinator_kind: {coordinator_kind}")

    result_box: dict = {}

    def on_result(committed: bool, latency_ms: float):
        result_box["committed"] = committed
        result_box["latency_ms"] = latency_ms

    if fault_fn is not None:
        if fault_at_write_fraction is not None:
            approx_write_ms = nodes[0].shard_bytes / nodes[0].ssd.write_bandwidth
            fault_time_ms = approx_write_ms * fault_at_write_fraction
        else:
            fault_time_ms = 0.0
        clock.schedule(fault_time_ms, lambda: fault_fn(nodes))

    coordinator.run_checkpoint(generation, on_result, on_event=on_event)
    clock.run_until_idle()

    result = RunResult(
        scenario=scenario_name,
        coordinator=coordinator_kind,
        num_nodes=len(nodes),
        committed=result_box.get("committed", False),
        generation=generation,
        latency_ms=result_box.get("latency_ms", float("nan")),
    )
    return result, commit_log
