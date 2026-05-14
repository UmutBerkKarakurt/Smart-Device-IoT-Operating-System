"""Entry point: Phase 5 demo by default; ``--final`` / ``--phase6`` for integrated final demo."""

from __future__ import annotations

import sys

from os_core.simulation import Simulation


def main() -> None:
    sim = Simulation(total_memory=4096)
    if "--phase0" in sys.argv:
        sim.run_phase0_demo()
        return
    if "--phase1" in sys.argv:
        sim.run_phase1_demo()
        return
    if "--phase2" in sys.argv:
        sim.run_phase2_demo()
        return
    if "--phase3" in sys.argv:
        sim.run_phase3_demo()
        return
    if "--phase4" in sys.argv:
        sim.run_phase4_demo()
        return
    if "--final" in sys.argv or "--phase6" in sys.argv:
        sim.run_final_demo()
        return
    sim.run_phase5_demo()


if __name__ == "__main__":
    main()
