"""Final phase: unified timeline, metrics dashboard, analysis, integrated demo stability."""

from __future__ import annotations

from os_core.observability import analyze_system, collect_average_metrics_from_pcbs
from os_core.simulation import Simulation
from process.pcb import ProcessState
from process.scheduler import create_scheduler


def test_timeline_and_metrics_record_page_activity() -> None:
    sim = Simulation(total_memory=768, enable_memory_access=True)
    sim.record_system_events = True
    sim.scheduler = create_scheduler("rr", quantum=2)
    sim._reset_cpu_execution_state()
    p = sim.processes.create_process(
        "PagingProbe",
        priority=1,
        memory_required=256,
        cpu_burst_time=12,
        arrival_time=0,
    )
    assert sim.memory.allocate(p.pid, p.memory_required)
    sim.processes.change_state(p.pid, ProcessState.READY)
    sim.scheduler.enqueue(p)
    sim._run_until_all_terminated()
    kinds = [e.event_type for e in sim._system_events]
    assert "DISPATCH" in kinds
    assert sim._metrics_dashboard.total_page_faults >= 1
    assert "PAGE_FAULT" in kinds


def test_context_switch_counter_matches_dashboard() -> None:
    sim = Simulation(4096, enable_memory_access=False)
    sim.scheduler = create_scheduler("rr", quantum=2)
    sim._reset_cpu_execution_state()
    procs = sim._create_phase1_iot_processes()
    for p in procs:
        assert sim.memory.allocate(p.pid, p.memory_required)
    for p in procs:
        sim.processes.change_state(p.pid, ProcessState.READY)
        sim.scheduler.enqueue(p)
    sim._run_until_all_terminated()
    assert sim.context_switches == sim._metrics_dashboard.total_context_switches


def test_collect_average_metrics_terminated_only() -> None:
    sim = Simulation(256, enable_memory_access=False)
    sim._reset_cpu_execution_state()
    p = sim.processes.create_process("x", cpu_burst_time=1, arrival_time=0)
    sim.memory.allocate(p.pid, 64)
    sim.processes.change_state(p.pid, ProcessState.READY)
    sim.scheduler.enqueue(p)
    sim._run_until_all_terminated()
    aw, at, ar = collect_average_metrics_from_pcbs(sim.processes.list_processes())
    assert aw >= 0 and at >= 0 and ar >= 0
    assert p.waiting_time is not None


def test_analysis_utility_deterministic() -> None:
    sim = Simulation(768, enable_memory_access=True)
    sim.scheduler = create_scheduler("rr", quantum=2)
    sim._reset_cpu_execution_state()
    p = sim.processes.create_process("a", memory_required=256, cpu_burst_time=8, arrival_time=0)
    sim.memory.allocate(p.pid, p.memory_required)
    sim.processes.change_state(p.pid, ProcessState.READY)
    sim.scheduler.enqueue(p)
    sim._run_until_all_terminated()
    out = analyze_system(sim._system_events, sim._metrics_dashboard, policy_label="RR")
    assert out["policy"] == "RR"
    assert isinstance(out["timeline_length"], int)
    assert out["timeline_length"] > 0


def test_run_final_demo_all_terminated_and_nonempty_timeline() -> None:
    sim = Simulation(4096)
    sim.run_final_demo()
    assert all(p.state == ProcessState.TERMINATED for p in sim.processes.list_processes())
    assert len(sim._system_events) >= 10
    assert sim._metrics_dashboard.total_scheduler_dispatches >= 1
