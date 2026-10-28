"""Dobles del directorio de instalaciones y de la cola de sincronización."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from nave.application.dto.installations import InstallationInfo, RemoteRepository
from nave.application.errors import TransientError


@dataclass
class FakeDirectory:
    installations: dict[int, InstallationInfo] = field(default_factory=dict)
    repositories: dict[int, list[RemoteRepository]] = field(default_factory=dict)
    fail: bool = False
    calls: list[str] = field(default_factory=list)

    def get_installation(self, installation_id: int) -> InstallationInfo | None:
        self.calls.append("get")
        if self.fail:
            raise TransientError("github down")
        return self.installations.get(installation_id)

    def list_repositories(self, installation_id: int) -> Sequence[RemoteRepository]:
        self.calls.append("repos")
        return self.repositories.get(installation_id, [])

    def list_installation_ids(self) -> Sequence[int]:
        return list(self.installations)


class FakeSyncQueue:
    def __init__(self) -> None:
        self.queued: list[int] = []

    def enqueue_sync(self, installation_id: int) -> None:
        self.queued.append(installation_id)
