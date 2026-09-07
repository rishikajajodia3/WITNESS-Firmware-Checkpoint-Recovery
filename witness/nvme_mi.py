"""
Proposed vendor-specific NVMe-MI log page: "Checkpoint Seal Status".

REAL: NVMe-MI already lets a management controller tunnel Admin commands
(including Get Log Page, with a vendor-specific log identifier) to a
drive controller without going through the host's OS/driver stack. That
transport and command mechanism ships today on real enterprise SSDs.

PROPOSED BY THIS PROJECT: the log page *content* below. No commercial
SSD firmware defines a "checkpoint generation sealed" log page. This
module is our concrete proposal for what that payload would look like
on the wire, encoded/decoded exactly as a real log page would be, so
the demo isn't hand-waving a Python dict across the wire.
"""

import struct
from dataclasses import dataclass

# generation: uint32, reserved: uint8, sealed: bool (1 byte), sealed_at_ms: float64
_LOG_PAGE_FORMAT = "!IB?d"
LOG_PAGE_SIZE_BYTES = struct.calcsize(_LOG_PAGE_FORMAT)


@dataclass
class CheckpointSealLogPage:
    generation: int
    sealed: bool
    sealed_at_ms: float | None

    def encode(self) -> bytes:
        return struct.pack(
            _LOG_PAGE_FORMAT,
            self.generation,
            0,
            self.sealed,
            self.sealed_at_ms or 0.0,
        )

    @classmethod
    def decode(cls, raw: bytes) -> "CheckpointSealLogPage":
        generation, _reserved, sealed, sealed_at_ms = struct.unpack(_LOG_PAGE_FORMAT, raw)
        return cls(generation=generation, sealed=sealed, sealed_at_ms=sealed_at_ms if sealed else None)
