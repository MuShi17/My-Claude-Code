"""Strict contract probes for the canonical runtime store."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime_fixtures import FaultInjector, build_scenario, scenario_events


def test_c01_event_contract_is_importable_and_canonical():
    from mini_claude.runtime_event import RuntimeEvent

    event = RuntimeEvent.from_dict(scenario_events(build_scenario())[0])
    encoded = event.canonical_bytes()
    assert event.schema_version == 2
    assert encoded == event.canonical_bytes()
    assert event.validate() is None


def test_c05_store_contract_has_reopen_and_exact_replay(tmp_path: Path):
    from mini_claude.runtime_event import RuntimeEvent
    from mini_claude.runtime_store import SQLiteRuntimeStore

    event = RuntimeEvent.from_dict(scenario_events(build_scenario())[0])
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        first = store.append(event)
        second = store.append(event)
        assert first.ordinal == second.ordinal
        assert store.read_events() == [event]
    with SQLiteRuntimeStore(database) as reopened:
        assert reopened.read_events() == [event]


@pytest.mark.parametrize(
    "fault_point",
    ["store.open", "store.append", "store.commit", "store.corrupt_read"],
)
def test_c05_store_fault_matrix_is_exercisable(tmp_path: Path, fault_point: str):
    from mini_claude.runtime_event import RuntimeEvent
    from mini_claude.runtime_store import SQLiteRuntimeStore

    event = RuntimeEvent.from_dict(scenario_events(build_scenario())[0])
    database = tmp_path / f"{fault_point.replace('.', '-')}.sqlite"
    fault = FaultInjector(fault_point)
    with pytest.raises(RuntimeError, match=f"fixture fault at {fault_point}"):
        with SQLiteRuntimeStore(database, fault_hook=fault) as store:
            store.append(event)
