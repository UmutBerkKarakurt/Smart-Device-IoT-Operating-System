"""Entry point: Phase 1 demo by default; ``--phase0`` runs Phase 0 only."""

from __future__ import annotations

import sys

from os_core.simulation import Simulation


def main() -> None:
    sim = Simulation(total_memory=4096)
    if "--phase0" in sys.argv:
        sim.run_phase0_demo()
        return
    sim.run_phase1_demo()


if __name__ == "__main__":
    main()
