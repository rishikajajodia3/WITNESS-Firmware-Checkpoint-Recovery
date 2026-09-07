"""
Simulated SSD / Flash Translation Layer.

REAL STANDARD MODELED: NVMe Flexible Data Placement (FDP, NVMe 2.0+).
Each checkpoint generation is written as a tagged group of writes -- the
simulated equivalent of an FDP Reclaim Unit. When every byte expected
for that generation has durably landed, the FTL seals it.

PROPOSED (NOT SHIPPING) FEATURE: everything past "seal the generation"
-- i.e. remembering per-generation seal state as first-class, externally
queryable firmware state -- is WITNESS's contribution, not something any
commercial SSD does today. See docs/ARCHITECTURE.md.

get_seal_status() is deliberately the single source of truth read by
BOTH the in-band path (host asks its own driver) and the out-of-band
path (BMC asks over NVMe-MI). The only thing that differs between those
two paths is the transport and its failure modes -- not the data.
"""

from dataclasses import dataclass
from enum import Enum

from witness.clock import SimClock


class SealStatus(Enum):
    NOT_SEALED = "not_sealed"
    SEALED = "sealed"


@dataclass
class ReclaimUnit:
    generation: int
    bytes_expected: int
    bytes_written: int = 0
    status: SealStatus = SealStatus.NOT_SEALED
    sealed_at_ms: float | None = None


class SimulatedSSD:
    def __init__(
        self,
        clock: SimClock,
        device_id: str,
        write_bandwidth_bytes_per_ms: float = 50_000,
    ):
        self.clock = clock
        self.device_id = device_id
        self.write_bandwidth = write_bandwidth_bytes_per_ms
        self.reclaim_units: dict[int, ReclaimUnit] = {}
        self.powered = True  # False models total device power loss

    def open_generation(self, generation: int, bytes_expected: int) -> None:
        self.reclaim_units[generation] = ReclaimUnit(generation, bytes_expected)

    def write(self, generation: int, num_bytes: int, on_complete=None) -> None:
        """Schedule a durable write, modeled with a simple bandwidth delay.

        If the device is powered off before the write lands, the write is
        lost -- matching real behavior: an SSD cannot durably commit data
        it never had power to write.
        """
        delay_ms = num_bytes / self.write_bandwidth

        def _land():
            if not self.powered:
                return
            ru = self.reclaim_units[generation]
            ru.bytes_written += num_bytes
            if ru.bytes_written >= ru.bytes_expected and ru.status == SealStatus.NOT_SEALED:
                ru.status = SealStatus.SEALED
                ru.sealed_at_ms = self.clock.now()
            if on_complete:
                on_complete()

        self.clock.schedule(delay_ms, _land)

    def get_seal_status(self, generation: int) -> SealStatus | None:
        """Returns None if the device is unpowered (unreachable by ANY path,
        in-band or out-of-band) -- distinct from NOT_SEALED, which is a
        positive answer from a live device that just isn't done yet."""
        if not self.powered:
            return None
        ru = self.reclaim_units.get(generation)
        if ru is None:
            return SealStatus.NOT_SEALED
        return ru.status

    def power_off(self) -> None:
        self.powered = False

    def power_on(self) -> None:
        self.powered = True
