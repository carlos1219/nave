from collections.abc import Sequence

from nave.application.ports.scanning import ScannerPort


class ScannerRegistry:
    """Escáneres habilitados, en el orden en que se configuran (CLAUDE.md §5.4).

    Añadir un escáner es construir su adaptador y pasarlo aquí; ni `domain` ni
    `application` cambian.
    """

    def __init__(self, scanners: Sequence[ScannerPort]) -> None:
        if not scanners:
            raise ValueError("at least one scanner must be enabled")
        kinds = [s.kind.value for s in scanners]
        if len(set(kinds)) != len(kinds):
            raise ValueError("scanner kinds must be unique")
        self._scanners = tuple(scanners)

    def scanners(self) -> Sequence[ScannerPort]:
        return self._scanners
