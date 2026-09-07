"""
Shared scenario-construction logic used by every demo script, so the
fault-injection and coordinator-configuration details live in one place
instead of being copy-pasted eight times.

Every function here just wires up witness.simulation.run_scenario with a
named fault and returns (result, commit_log, nodes) -- the demo scripts
are responsible for printing, not for scenario logic.
"""

import demo._common as common
from witness.clock import SimClock
from witness.simulation import build_nodes, run_scenario


def witness_kwargs(**overrides):
    kwargs = {
        "oob_poll_interval_ms": common.WITNESS_POLL_INTERVAL_MS,
        "fencing_timeout_ms": common.WITNESS_FENCING_TIMEOUT_MS,
        "round_timeout_ms": common.WITNESS_ROUND_TIMEOUT_MS,
    }
    kwargs.update(overrides)
    return kwargs


def baseline_kwargs(**overrides):
    kwargs = {"barrier_timeout_ms": common.BASELINE_BARRIER_TIMEOUT_MS}
    kwargs.update(overrides)
    return kwargs


def _kill_process(ns):
    ns[0].kill_process()


def _kill_ssd(ns):
    ns[0].kill_ssd()


def _full_power_loss(ns):
    ns[0].full_power_loss()


def run_normal(coordinator_kind, num_nodes=common.DEFAULT_NUM_NODES, on_event=None):
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes, common.DEFAULT_SHARD_BYTES,
                         common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)
    kwargs = baseline_kwargs() if coordinator_kind == "baseline" else witness_kwargs()
    result, commit_log = run_scenario("NORMAL", coordinator_kind, nodes, clock,
                                       coordinator_kwargs=kwargs, on_event=on_event)
    return result, commit_log, nodes


def run_host_hang(coordinator_kind, num_nodes=common.DEFAULT_NUM_NODES, on_event=None):
    """Scenario B: rank-0's host OS/CPU hangs BEFORE its write completes.
    The SSD write timer is independent of the host process, so the data
    still lands; only the rank's ability to acknowledge is lost."""
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes, common.DEFAULT_SHARD_BYTES,
                         common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)
    kwargs = baseline_kwargs() if coordinator_kind == "baseline" else witness_kwargs()
    result, commit_log = run_scenario("HOST_HANG", coordinator_kind, nodes, clock,
                                       fault_at_write_fraction=0.1, fault_fn=_kill_process,
                                       coordinator_kwargs=kwargs, on_event=on_event)
    return result, commit_log, nodes


def run_ssd_failure(coordinator_kind, num_nodes=common.DEFAULT_NUM_NODES, on_event=None):
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes, common.DEFAULT_SHARD_BYTES,
                         common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)
    kwargs = (baseline_kwargs(barrier_timeout_ms=20_000.0) if coordinator_kind == "baseline"
              else witness_kwargs(fencing_timeout_ms=3_000.0))
    result, commit_log = run_scenario("SSD_FAILURE", coordinator_kind, nodes, clock,
                                       fault_at_write_fraction=0.3, fault_fn=_kill_ssd,
                                       coordinator_kwargs=kwargs, on_event=on_event)
    return result, commit_log, nodes


# Both channels are equally dark in FULL_POWER_LOSS (host, SSD, and BMC all lose
# power together), so there is no real detection-time difference to compare here
# -- unlike SSD_FAILURE or HOST_HANG, WITNESS's OOB path gives it no information
# advantage at all in this scenario. Both coordinators are given the SAME
# timeout value on purpose, so the demo's latency numbers can't be misread as
# "WITNESS is still faster even here": any remaining gap is pure per-poll
# overhead (WITNESS's oob_poll_interval_ms/round_timeout_ms bookkeeping), not a
# capability difference.
FULL_POWER_LOSS_TIMEOUT_MS = 20_000.0


def run_full_power_loss(coordinator_kind, num_nodes=common.DEFAULT_NUM_NODES, on_event=None):
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes, common.DEFAULT_SHARD_BYTES,
                         common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)
    # round_timeout_ms is left at its default (30s), comfortably above
    # FULL_POWER_LOSS_TIMEOUT_MS + fault-injection offset + one OOB round trip,
    # so the fencing window -- not the outer round-timeout backstop -- is what
    # actually produces the rejection, matching the other fencing-window demos.
    kwargs = (baseline_kwargs(barrier_timeout_ms=FULL_POWER_LOSS_TIMEOUT_MS) if coordinator_kind == "baseline"
              else witness_kwargs(fencing_timeout_ms=FULL_POWER_LOSS_TIMEOUT_MS))
    result, commit_log = run_scenario("FULL_POWER_LOSS", coordinator_kind, nodes, clock,
                                       fault_at_write_fraction=0.5, fault_fn=_full_power_loss,
                                       coordinator_kwargs=kwargs, on_event=on_event)
    return result, commit_log, nodes


def run_bmc_partition_heals(num_nodes=common.DEFAULT_NUM_NODES, heal_after_ms=1_000.0, on_event=None):
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes, common.DEFAULT_SHARD_BYTES,
                         common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)

    def partition_then_heal(ns):
        ns[0].kill_process()
        ns[0].partition_bmc()
        clock.schedule(heal_after_ms, lambda: ns[0].heal_bmc())

    result, commit_log = run_scenario("BMC_PARTITION_HEALS", "witness", nodes, clock,
                                       fault_at_write_fraction=0.1, fault_fn=partition_then_heal,
                                       coordinator_kwargs=witness_kwargs(), on_event=on_event)
    return result, commit_log, nodes


def run_bmc_partition_permanent(num_nodes=common.DEFAULT_NUM_NODES, on_event=None):
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes, common.DEFAULT_SHARD_BYTES,
                         common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)

    def partition_forever(ns):
        ns[0].kill_process()
        ns[0].partition_bmc()

    result, commit_log = run_scenario("BMC_PARTITION_PERMANENT", "witness", nodes, clock,
                                       fault_at_write_fraction=0.1, fault_fn=partition_forever,
                                       coordinator_kwargs=witness_kwargs(), on_event=on_event)
    return result, commit_log, nodes
