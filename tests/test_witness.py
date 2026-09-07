"""
Correctness tests for WITNESS's core claims. These are the tests that
matter more than the demo output: they prove the invariants
programmatically, not just under one lucky timing in a console run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from witness.clock import SimClock
from witness.simulation import build_nodes, run_scenario


def test_ftl_seal_status_transitions_from_not_sealed_to_sealed_only_on_full_write():
    """Unit-level check of the primitive the entire claim rests on: a
    generation reads NOT_SEALED while partially written, flips to SEALED
    exactly when the last expected byte lands, and get_seal_status() is the
    same call both the in-band and OOB paths make (no separate code path
    that could drift out of sync with this one)."""
    from witness.ftl import SealStatus, SimulatedSSD

    clock = SimClock()
    ssd = SimulatedSSD(clock, "rank-0", write_bandwidth_bytes_per_ms=1_000)
    ssd.open_generation(1, bytes_expected=1_000)

    assert ssd.get_seal_status(1) == SealStatus.NOT_SEALED

    ssd.write(1, 400)  # partial: 400/1000 bytes
    clock.run_until_idle()
    assert ssd.get_seal_status(1) == SealStatus.NOT_SEALED

    ssd.write(1, 600)  # completes the generation: 1000/1000 bytes
    clock.run_until_idle()
    assert ssd.get_seal_status(1) == SealStatus.SEALED
    assert ssd.reclaim_units[1].sealed_at_ms == clock.now()

    # A dead/unpowered device answers UNKNOWN (None), never a stale SEALED/
    # NOT_SEALED -- this is the exact signal the coordinator's fencing logic
    # depends on to distinguish "confirmed" from "can't ask."
    ssd.power_off()
    assert ssd.get_seal_status(1) is None


def test_normal_path_both_coordinators_commit():
    for kind in ("baseline", "witness"):
        clock = SimClock()
        nodes = build_nodes(clock, num_nodes=8)
        result, commit_log = run_scenario(f"NORMAL-{kind}", kind, nodes, clock)
        assert result.committed
        assert commit_log.latest_committed() == 1


def test_baseline_falsely_rejects_after_host_dies_post_write():
    """The headline bug: the shard's data is fully, durably written -- the
    SSD write timer runs independently of the host process -- but because
    the process dies before it can send its barrier ack, the barrier-based
    baseline has no way to know the data is safe, and must reject on
    timeout."""
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes=4)

    def kill_rank0(ns):
        ns[0].kill_process()

    result, _ = run_scenario(
        "HOST_HANG",
        "baseline",
        nodes,
        clock,
        fault_at_write_fraction=0.1,  # kill well before the write completes...
        fault_fn=kill_rank0,
        coordinator_kwargs={"barrier_timeout_ms": 60_000},
    )

    # The data is provably fine: node 0's SSD sealed generation 1 despite its
    # process being dead.
    from witness.ftl import SealStatus
    assert nodes[0].ssd.get_seal_status(1) == SealStatus.SEALED

    # Yet the baseline coordinator rejected it, and paid the full timeout to find out.
    assert result.committed is False
    assert result.latency_ms == 60_000


def test_witness_recovers_the_same_case_baseline_rejects():
    """Same fault, same data-on-disk -- WITNESS commits it, fast, via the
    simulated BMC/NVMe-MI out-of-band path."""
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes=4)

    def kill_rank0(ns):
        ns[0].kill_process()

    result, commit_log = run_scenario(
        "HOST_HANG",
        "witness",
        nodes,
        clock,
        fault_at_write_fraction=0.1,
        fault_fn=kill_rank0,
        coordinator_kwargs={"oob_poll_interval_ms": 100, "fencing_timeout_ms": 5_000},
    )

    assert result.committed is True
    assert commit_log.latest_committed() == 1
    # Recovery decision was bounded by OOB polling, nowhere near a barrier timeout.
    assert result.latency_ms < 5_000


def test_ssd_failure_is_correctly_rejected_by_both():
    """No regression: if the drive itself is genuinely gone, WITNESS must
    reject too, not fabricate a commit."""
    for kind in ("baseline", "witness"):
        clock = SimClock()
        nodes = build_nodes(clock, num_nodes=4)

        def kill_ssd(ns):
            ns[0].kill_ssd()

        kwargs = {"barrier_timeout_ms": 20_000} if kind == "baseline" else {"fencing_timeout_ms": 3_000}
        result, _ = run_scenario(
            "SSD_FAILURE", kind, nodes, clock,
            fault_at_write_fraction=0.3, fault_fn=kill_ssd, coordinator_kwargs=kwargs,
        )
        assert result.committed is False


def test_full_power_loss_gives_witness_no_special_advantage():
    """Explicitly enforce the scoping promise: total power loss degrades
    WITNESS to the same conservative behavior as the baseline -- no OOB
    magic, because the OOB path is dark too."""
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes=4)

    def full_loss(ns):
        ns[0].full_power_loss()

    result, _ = run_scenario(
        "FULL_POWER_LOSS", "witness", nodes, clock,
        fault_at_write_fraction=0.5, fault_fn=full_loss,
        coordinator_kwargs={"fencing_timeout_ms": 3_000},
    )
    assert result.committed is False


def test_bmc_partition_that_heals_before_fencing_timeout_still_commits():
    """The anti-bare-timeout invariant: an UNKNOWN must be retried, not
    treated as a rejection, as long as it resolves within the fencing
    window."""
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes=4)

    def partition_then_heal(ns):
        ns[0].kill_process()
        ns[0].partition_bmc()
        clock.schedule(1_000, lambda: ns[0].heal_bmc())

    result, commit_log = run_scenario(
        "BMC_PARTITION_HEALS", "witness", nodes, clock,
        fault_at_write_fraction=0.1, fault_fn=partition_then_heal,
        coordinator_kwargs={"oob_poll_interval_ms": 100, "fencing_timeout_ms": 5_000},
    )
    assert result.committed is True
    assert commit_log.latest_committed() == 1


def test_bmc_partition_that_never_heals_is_rejected_after_fencing_window():
    clock = SimClock()
    nodes = build_nodes(clock, num_nodes=4)

    def partition_forever(ns):
        ns[0].kill_process()
        ns[0].partition_bmc()

    result, _ = run_scenario(
        "BMC_PARTITION_PERMANENT", "witness", nodes, clock,
        fault_at_write_fraction=0.1, fault_fn=partition_forever,
        coordinator_kwargs={"oob_poll_interval_ms": 100, "fencing_timeout_ms": 2_000},
    )
    assert result.committed is False


def test_recovery_lookup_is_o1_and_returns_latest_committed_generation():
    from witness.coordinator import CommitLog

    log = CommitLog()
    log.commit(1)
    log.commit(2)
    log.commit(3)
    assert log.latest_committed() == 3


def test_commit_log_accumulates_across_sequential_generations_on_same_coordinator():
    """Recovery's real usage pattern: one long-lived coordinator (and its one
    commit log) runs many checkpoint rounds over a training job's lifetime,
    not a fresh log per round. Confirms latest_committed() tracks the most
    recent SUCCESSFUL round through the actual WitnessCoordinator -- not just
    through CommitLog in isolation -- and that running one generation after
    another on the same coordinator/nodes doesn't corrupt earlier state."""
    from witness.coordinator import CommitLog, WitnessCoordinator

    clock = SimClock()
    nodes = build_nodes(clock, num_nodes=4)
    commit_log = CommitLog()
    coordinator = WitnessCoordinator(clock, nodes, commit_log=commit_log)

    for gen in (1, 2, 3):
        result_box = {}
        coordinator.run_checkpoint(
            gen, lambda committed, latency_ms: result_box.update(committed=committed, latency_ms=latency_ms)
        )
        clock.run_until_idle()
        assert result_box["committed"] is True
        assert commit_log.latest_committed() == gen
