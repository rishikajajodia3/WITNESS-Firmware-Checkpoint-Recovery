"""
WITNESS: Firmware-Assisted Checkpoint Recovery for AI Training.

A software simulation of a proposed SSD firmware feature that tracks
checkpoint-generation completeness and exposes it through an out-of-band
management path, so a coordinator can determine checkpoint validity even
when the host OS/CPU that was writing it has hung.

See docs/ARCHITECTURE.md for exactly which pieces are real, ratified
standards (NVMe FDP, NVMe-MI, UBM/SMBus, BMC) versus this project's
proposed extension (the checkpoint-seal log page) versus pure simulation
scaffolding (the FTL, the write-bandwidth model).
"""
