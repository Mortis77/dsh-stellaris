"""Name-first action layer.

Ties StellarisState (names -> ids) to the injected native runtime (ids -> command).
Callers pass names; ids are resolved on every call so a save reload never leaves
stale ids behind.

    g = Game()
    g.move_fleet("素斯娜舰队", "伊玛卡")
    g.select_research("tech_lasers_1")
    g.toggle_building("伊玛卡", building_id)

Requires: game running and the runtime DLL injected
(loader.exe iag_stellaris_native_runtime.dll).
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass

from state import StellarisState

PIPE = r"\\.\pipe\iag-stellaris-native"
PROTOCOL = "IAG1"

_ids = itertools.count(1)


@dataclass
class Reply:
    status: str
    result: str
    raw: str

    @property
    def ok(self) -> bool:
        return self.status in ("pending", "confirmed")


class RuntimeUnavailable(RuntimeError):
    pass


class Game:
    def __init__(self, save: str | None = None):
        self.state = StellarisState(save)

    # ---------------------------------------------------------------- transport
    def _send(self, action: str, *params, country: int | None = None,
              timeout_reply: bool = True) -> Reply:
        cid = self.state.player if country is None else country
        fields = [PROTOCOL, f"py{next(_ids)}", action, str(cid)]
        fields += [str(p) for p in params]
        line = "\t".join(fields) + "\n"
        try:
            handle = open(PIPE, "r+b", buffering=0)
        except OSError as exc:
            raise RuntimeUnavailable(
                f"pipe {PIPE} not available - is the game running and the DLL injected?"
            ) from exc
        try:
            handle.write(line.encode())
            handle.flush()
            raw = handle.readline().decode(errors="replace").strip()
        finally:
            handle.close()
        parts = raw.split("\t")
        if len(parts) < 8:
            return Reply("unknown", raw, raw)
        return Reply(parts[3], parts[-1], raw)

    # ---------------------------------------------------------------- actions
    def move_fleet(self, fleet: str, system: str, *, cls: str | None = None) -> Reply:
        fid = self.state.fleet_id(fleet, cls=cls)
        sid = self.state.system_id(system)
        return self._send("move_fleet", fid, sid)

    def colonize_planet(self, fleet: str, planet_id: int) -> Reply:
        fid = self.state.fleet_id(fleet, cls="殖民船")
        return self._send("colonize_planet", fid, planet_id)

    def select_research(self, technology_key: str) -> Reply:
        return self._send("select_research", technology_key)

    def toggle_building(self, colony: str, building_id: int) -> Reply:
        col = self.state._raw_colonies
        match = [c for c in col if colony in (c.get("name") or "")]
        if len(match) != 1:
            raise ValueError(f"colony {colony!r} matched {len(match)} colonies")
        return self._send("toggle_building", match[0]["id"], building_id)

    def destroy_building(self, colony: str, building_id: int) -> Reply:
        col = self.state._raw_colonies
        match = [c for c in col if colony in (c.get("name") or "")]
        if len(match) != 1:
            raise ValueError(f"colony {colony!r} matched {len(match)} colonies")
        return self._send("destroy_building", match[0]["id"], building_id)

    # ---------------------------------------------------------------- helpers
    def reload(self, save: str | None = None) -> "Game":
        self.state.reload(save)
        return self


if __name__ == "__main__":
    import sys

    g = Game()
    print(g.state.summary())
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        try:
            r = g.select_research(sys.argv[2] if len(sys.argv) > 2 else "tech_lasers_1")
            print(f"\nprobe -> status={r.status} result={r.result}")
        except RuntimeUnavailable as exc:
            print(f"\nprobe skipped: {exc}")
