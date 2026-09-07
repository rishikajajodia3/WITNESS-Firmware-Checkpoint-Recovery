"""
Simulated BMC / UBM out-of-band relay.

REAL: a Baseboard Management Controller reachable over its own
management network, wired to each drive bay's SMBus/I2C sideband via a
backplane controller (Universal Backplane Management, UBM). This path
runs on its own power domain and does not route through the host CPU,
OS, or PCIe root complex -- which is exactly why it survives an OS/CPU
hang (Scenario B) but NOT a full loss of power to the chassis
(Scenario C), and why it can independently go unreachable over its
management network even while the drive it's attached to is fine
(the BMC-partition case).

This module intentionally does not implement real SMBus/I2C wire
signaling -- it models the two properties of that channel that matter
for this design: (1) its reachability is independent of the host
process/OS, and (2) it is slower than the in-band NVMe queue path
(SMBus/I2C is a low-bandwidth management bus, not a data path).
"""

from typing import Callable

from witness.clock import SimClock
from witness.ftl import SealStatus, SimulatedSSD
from witness.nvme_mi import CheckpointSealLogPage, LOG_PAGE_SIZE_BYTES


class SimulatedBMC:
    def __init__(self, clock: SimClock, ssd: SimulatedSSD, query_latency_ms: float = 15.0):
        self.clock = clock
        self.ssd = ssd
        self.query_latency_ms = query_latency_ms
        self.network_reachable = True  # False models a BMC/management-network partition
        self.bytes_transferred = 0  # metadata-overhead accounting

    def query_sealed(self, generation: int, callback: Callable[[SealStatus | None], None]) -> None:
        """Async query over the simulated OOB path.

        callback receives:
          SealStatus.SEALED      -- device reachable, generation is sealed
          SealStatus.NOT_SEALED  -- device reachable, explicitly not sealed yet
          None                   -- unreachable (BMC partitioned, or SSD unpowered):
                                     an UNKNOWN, never to be treated as a negative
        """

        def _respond():
            if not self.network_reachable:
                callback(None)
                return
            status = self.ssd.get_seal_status(generation)
            if status is None:
                callback(None)
                return
            page = CheckpointSealLogPage(
                generation=generation,
                sealed=(status == SealStatus.SEALED),
                sealed_at_ms=self.ssd.reclaim_units[generation].sealed_at_ms
                if generation in self.ssd.reclaim_units
                else None,
            )
            # Actually round-trip through the wire encoding, not just pass the
            # Python object by reference -- the coordinator only ever learns
            # what the decoded bytes say, matching the "not hand-waving a dict
            # across the wire" claim in witness/nvme_mi.py's module docstring.
            raw = page.encode()
            received = CheckpointSealLogPage.decode(raw)
            self.bytes_transferred += LOG_PAGE_SIZE_BYTES
            callback(SealStatus.SEALED if received.sealed else SealStatus.NOT_SEALED)

        self.clock.schedule(self.query_latency_ms, _respond)

    def partition(self) -> None:
        self.network_reachable = False

    def heal(self) -> None:
        self.network_reachable = True
