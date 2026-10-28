from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from nave.application.dto.maintenance import PurgeCandidate, PurgeProgress
from nave.application.errors import PurgeRefusedError
from nave.application.use_cases.purge_uninstalled_organizations import (
    MAX_BATCHES_PER_ORGANIZATION,
    PURGE_GRACE,
    PurgeUninstalledOrganizations,
)
from nave.domain.value_objects.identifiers import OrganizationId
from tests.fakes.memory import FakeClock, Store

A = OrganizationId(UUID(int=0xA))
B = OrganizationId(UUID(int=0xB))


@dataclass
class FakePurgeStore:
    uninstalled: dict[OrganizationId, datetime] = field(default_factory=dict)
    batches_needed: dict[OrganizationId, int] = field(default_factory=dict)
    refuse: set[OrganizationId] = field(default_factory=set)
    calls: list[OrganizationId] = field(default_factory=list)
    asked_cutoff: datetime | None = None

    def candidates(self, uninstalled_before: datetime, limit: int) -> list[PurgeCandidate]:
        self.asked_cutoff = uninstalled_before
        due = sorted((at, org) for org, at in self.uninstalled.items() if at < uninstalled_before)
        return [PurgeCandidate(org, at) for at, org in due][:limit]

    def purge_batch(self, organization_id: OrganizationId) -> PurgeProgress:
        self.calls.append(organization_id)
        if organization_id in self.refuse:
            raise PurgeRefusedError("no")
        left = self.batches_needed.get(organization_id, 1) - 1
        self.batches_needed[organization_id] = left
        return PurgeProgress(done=left <= 0, deleted=10, step="scans")


def setup(store: FakePurgeStore) -> tuple[PurgeUninstalledOrganizations, Store, FakeClock]:
    state, clock = Store(), FakeClock()
    return (
        PurgeUninstalledOrganizations(uow_factory=state.uow, store=store, clock=clock),
        state,
        clock,
    )


def actions(state: Store) -> list[tuple[str, str]]:
    return [
        (e.action, str(e.organization_id.value)) for e in state.state.audit if e.organization_id
    ]


def test_only_organizations_past_the_grace_period_are_requested() -> None:
    store = FakePurgeStore()
    use_case, _state, clock = setup(store)
    store.uninstalled = {
        A: clock.now() - PURGE_GRACE - timedelta(days=1),
        B: clock.now() - timedelta(days=29),
    }
    report = use_case.execute()
    assert (report.purged, report.in_progress, report.refused) == (1, 0, 0)
    assert store.calls == [A]
    assert store.asked_cutoff == clock.now() - PURGE_GRACE


def test_the_grace_period_is_thirty_days() -> None:
    assert timedelta(days=30) == PURGE_GRACE


def test_audit_brackets_the_purge() -> None:
    store = FakePurgeStore(uninstalled={A: datetime(2020, 1, 1, tzinfo=FakeClock().now().tzinfo)})
    use_case, state, _ = setup(store)
    use_case.execute()
    assert [a for a, _ in actions(state)] == ["organization.purge_requested", "organization.purged"]
    purged = state.state.audit[-1]
    assert purged.details == {"rows": "10"}


def test_a_large_organization_is_drained_in_batches() -> None:
    store = FakePurgeStore(batches_needed={A: 5})
    use_case, state, clock = setup(store)
    store.uninstalled = {A: clock.now() - timedelta(days=60)}
    report = use_case.execute()
    assert report.purged == 1
    assert len(store.calls) == 5
    assert state.state.audit[-1].details == {"rows": "50"}


def test_work_is_bounded_per_run_and_resumed_next_time() -> None:
    store = FakePurgeStore(batches_needed={A: MAX_BATCHES_PER_ORGANIZATION + 5})
    use_case, state, clock = setup(store)
    store.uninstalled = {A: clock.now() - timedelta(days=60)}
    first = use_case.execute()
    assert (first.purged, first.in_progress) == (0, 1)
    assert len(store.calls) == MAX_BATCHES_PER_ORGANIZATION
    assert "organization.purged" not in [a for a, _ in actions(state)]
    second = use_case.execute()
    assert second.purged == 1


def test_a_database_refusal_is_audited_and_does_not_stop_the_others() -> None:
    store = FakePurgeStore(refuse={A})
    use_case, state, clock = setup(store)
    store.uninstalled = {
        A: clock.now() - timedelta(days=90),
        B: clock.now() - timedelta(days=60),
    }
    report = use_case.execute()
    assert (report.purged, report.refused) == (1, 1)
    names = [a for a, _ in actions(state)]
    assert "organization.purge_refused" in names
    assert names.count("organization.purged") == 1


def test_nothing_to_do() -> None:
    use_case, state, _ = setup(FakePurgeStore())
    report = use_case.execute()
    assert (report.purged, report.in_progress, report.refused) == (0, 0, 0)
    assert state.state.audit == []
