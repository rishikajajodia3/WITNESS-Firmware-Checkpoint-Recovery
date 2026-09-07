"""
A Node bundles one training rank's three independently-failable parts:
the host process (the training job), the local SSD, and the local BMC.

The independence of these three is the entire point of WITNESS. Real
hardware topology: host CPU/OS, SSD controller, and BMC are on separate
silicon with separate (though related) power domains and communication
paths. kill_process() below deliberately does NOT touch the SSD or BMC
-- that's what makes it a faithful model of Scenario B (OS/CPU hang,
SSD+BMC alive) rather than a full node failure.
"""

from typing import Callable

from witness.bmc import SimulatedBMC
from witness.clock import SimClock
from witness.ftl import SealStatus, SimulatedSSD


class Node:
    def __init__(
        self,
        clock: SimClock,
        node_id: str,
        shard_bytes: int,
        ssd_bandwidth_bytes_per_ms: float = 50_000,
        bmc_query_latency_ms: float = 15.0,
    ):
        self.clock = clock
        self.node_id = node_id
        self.shard_bytes = shard_bytes
        self.ssd = SimulatedSSD(clock, node_id, ssd_bandwidth_bytes_per_ms)
        self.bmc = SimulatedBMC(clock, self.ssd, bmc_query_latency_ms)
        self.process_alive = True

    def start_checkpoint_write(self, generation: int, on_local_done: Callable[[], None] | None = None) -> None:
        self.ssd.open_generation(generation, self.shard_bytes)
        self.ssd.write(generation, self.shard_bytes, on_complete=on_local_done)

    def report_status_inband(self, generation: int) -> SealStatus | None:
        """The training process's own view of its shard. Returns None if the
        process is dead -- it cannot report anything, regardless of what its
        drive actually holds. This is the exact blind spot WITNESS targets."""
        if not self.process_alive:
            return None
        return self.ssd.get_seal_status(generation)

    # --- fault injection hooks -------------------------------------------------

    def kill_process(self) -> None:
        """Models an OS/CPU hang or crash: Scenario B. SSD and BMC stay powered."""
        self.process_alive = False

    def kill_ssd(self) -> None:
        """Models an SSD hardware failure: no seal is ever reachable again."""
        self.ssd.power_off()

    def partition_bmc(self) -> None:
        """Models the BMC/management network becoming unreachable, independent
        of whether the host process or SSD are actually fine."""
        self.bmc.partition()

    def heal_bmc(self) -> None:
        self.bmc.heal()

    def full_power_loss(self) -> None:
        """Models Scenario C: the whole chassis goes dark. WITNESS claims NO
        advantage here -- process, SSD, and BMC all become unreachable
        together, same as the original power-loss case."""
        self.process_alive = False
        self.ssd.power_off()
        self.bmc.network_reachable = False
