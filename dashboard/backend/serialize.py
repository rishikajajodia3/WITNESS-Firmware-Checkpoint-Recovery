"""
Converts real WITNESS simulation objects (witness.metrics.RunResult,
witness.node.Node, witness.coordinator.CommitLog) into JSON-safe dicts for
the dashboard API.

This module reads state off the actual simulation objects returned by
demo.scenarios / witness.simulation -- it does not compute or fabricate any
number itself. If a value looks wrong, the bug is upstream in the verified
simulation, not here.
"""

from witness.ftl import SealStatus


def seal_status_str(status: SealStatus | None) -> str:
    if status is None:
        return "UNKNOWN"
    return status.value.upper()  # SealStatus.SEALED -> "SEALED", NOT_SEALED -> "NOT_SEALED"


def shard_status(node, generation: int) -> str:
    """One display-friendly bucket, derived from the node's real attributes:
    FAILED (SSD unpowered -- nothing to ever recover), SEALED (durable and
    confirmed), or UNKNOWN (still writing, or unreachable but possibly fine).
    """
    if not node.ssd.powered:
        return "FAILED"
    status = node.ssd.get_seal_status(generation)
    if status == SealStatus.SEALED:
        return "SEALED"
    return "UNKNOWN"


def serialize_node(node, generation: int) -> dict:
    return {
        "node_id": node.node_id,
        "host_alive": node.process_alive,
        "bmc_reachable": node.bmc.network_reachable,
        "ssd_powered": node.ssd.powered,
        "seal_status": seal_status_str(node.ssd.get_seal_status(generation)),
        "shard_status": shard_status(node, generation),
    }


def serialize_run(coordinator_kind: str, result, commit_log, nodes, events: list[dict]) -> dict:
    node_dicts = [serialize_node(n, result.generation) for n in nodes]
    counts = {"SEALED": 0, "UNKNOWN": 0, "FAILED": 0}
    for n in node_dicts:
        counts[n["shard_status"]] += 1
    return {
        "coordinator": coordinator_kind,
        "verdict": result.verdict,
        "committed": result.committed,
        "latency_ms": result.latency_ms,
        "num_nodes": result.num_nodes,
        "generation": result.generation,
        "committed_generation": commit_log.latest_committed(),
        "events": events,
        "nodes": node_dicts,
        "shard_counts": counts,
    }
