"""F7-14: la regla «una organización nunca se queda sin administrador» bajo concurrencia.

Con el nivel de aislamiento por defecto (READ COMMITTED) dos administradores que se retiran
mutuamente a la vez comprobaban «quedan dos» y borraban ambos. El caso de uso bloquea ahora
las filas de los administradores antes de decidir."""

import threading

import pytest
from sqlalchemy import Engine, func, select

from nave.application.errors import LastAdminError
from nave.application.services.authorization import Authorizer
from nave.application.use_cases.manage_membership import ManageMembership, ManageMembershipCommand
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.role import Role
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.fakes.memory import FakeClock
from tests.integration.persistence.seed import ADMIN, ORG, TRIAGER, seed_tenancy

pytestmark = pytest.mark.integration

WAIT = 10.0


def _two_admins(uow: SqlUnitOfWorkFactory, clock: FakeClock) -> None:
    with uow() as tx:
        for user in (ADMIN, TRIAGER):
            tx.memberships.set_role(user, ORG, Role.ADMIN, granted_by=None, at=clock.now())
        tx.commit()


def _admins(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count())
                .select_from(t.memberships)
                .where(
                    t.memberships.c.organization_id == ORG.value, t.memberships.c.role == "ADMIN"
                )
            ).scalar_one()
        )


def test_lock_admins_makes_a_concurrent_transaction_wait_for_the_first(engine: Engine) -> None:
    seed_tenancy(engine)
    uow, clock = SqlUnitOfWorkFactory(engine), FakeClock()
    _two_admins(uow, clock)
    holding, release = threading.Event(), threading.Event()
    second_done = threading.Event()
    seen: dict[str, int] = {}

    def first() -> None:
        with uow() as tx:
            assert tx.memberships.lock_admins(ORG) == 2
            holding.set()
            release.wait(WAIT)
            tx.memberships.remove(TRIAGER, ORG)
            tx.commit()

    def second() -> None:
        with uow() as tx:
            seen["count"] = tx.memberships.lock_admins(ORG)
            second_done.set()

    t1 = threading.Thread(target=first)
    t1.start()
    assert holding.wait(WAIT)
    t2 = threading.Thread(target=second)
    t2.start()
    assert not second_done.wait(0.5), "la segunda transacción no debería pasar el bloqueo"
    release.set()
    t1.join(WAIT)
    t2.join(WAIT)
    assert seen["count"] == 1  # ve el resultado ya confirmado de la primera


def test_two_admins_removing_each_other_at_once_never_leave_the_organization_empty(
    engine: Engine,
) -> None:
    seed_tenancy(engine)
    uow, clock = SqlUnitOfWorkFactory(engine), FakeClock()
    members = ManageMembership(
        uow_factory=uow, authorizer=Authorizer(uow_factory=uow, clock=clock), clock=clock
    )

    def remove(
        barrier: threading.Barrier, failures: list[BaseException], actor: UserId, target: UserId
    ) -> None:
        barrier.wait(WAIT)
        try:
            members.execute(
                ManageMembershipCommand(actor=actor, organization_id=ORG, user=target, role=None)
            )
        except LastAdminError:
            pass  # es el resultado correcto para quien llega segundo
        except BaseException as error:  # noqa: BLE001 - se comprueba abajo
            failures.append(error)

    for _ in range(12):
        _two_admins(uow, clock)
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []
        threads = [
            threading.Thread(target=remove, args=(barrier, failures, ADMIN, TRIAGER)),
            threading.Thread(target=remove, args=(barrier, failures, TRIAGER, ADMIN)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT)
        assert not failures, failures
        assert _admins(engine) >= 1
