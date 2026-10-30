"""Name-first access to the current Stellaris save state.

Callers use names ("素斯娜舰队", "伊斯皮利亚"); numeric IDs stay internal and
are re-resolved on every reload. Switching saves or starting a new game only
requires reload(); nothing above this layer changes.

    st = StellarisState()
    st.fleets(cls="军事舰船")            # list
    st.fleet("素斯娜舰队")               # unique -> dict
    st.system_id("伊斯皮利亚")           # -> int
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import loc as locmod
import save_parser as sp


class StellarisState:
    def __init__(self, save: str | None = None, *, translate: bool = True):
        self._loc = locmod.load() if translate else {}
        self._translate = translate
        self.save_path: str = ""
        self.date: str = ""
        self.player: int = -1
        self._systems: list[dict] = []
        self._by_id: dict[int, str] = {}
        self._fleets: list[dict] = []
        self.reload(save)

    # ---------------------------------------------------------------- loading
    def reload(self, save: str | None = None) -> "StellarisState":
        path = save or self.save_path or sp.newest_save()
        root, _ = sp.load_gamestate(path)
        data = sp.summarize(root)

        self.save_path = path
        self.date = data["date"]
        self.player = data["player_country"]

        self._systems = [
            {"id": s["id"], "key": s["name"],
             "name": self._tr(s["name"])}
            for s in data["systems"]
        ]
        self._by_id = {s["id"]: s["name"] for s in self._systems}

        self._fleets = []
        for f in data["fleets"]:
            cls = self._tr(f["ship_class"])
            self._fleets.append({
                "id": f["id"],
                "name": self._tr(f["name"]),
                "key": f["name"],
                "class": cls,
                "owner": f["owner"],
                "system": f["system"],
                "system_name": self._by_id.get(f["system"]),
            })

        self._raw_resources = data.get("resources", {})
        self._raw_research = data.get("research", {})
        self._raw_colonies = []
        for c in data.get("colonies", []):
            self._raw_colonies.append({
                "id": c["id"],
                "name": self._tr(c.get("name_key")),
                "key": c.get("name_key"),
                "size": c.get("size"),
                "class": c.get("class"),
                "colony": c.get("colony"),
                "build_queue": c.get("build_queue"),
                "system": c.get("system"),
                "system_name": self._by_id.get(c.get("system")),
            })
        return self

    def _tr(self, key: str | None) -> str:
        if not self._translate or not key:
            return key or ""
        text = locmod.translate(self._loc, key) or key
        # localisation may carry unresolved variables like $PLANET$开采站
        return re.sub(r"\$[^$]{1,40}\$", "", text).strip()

    # ---------------------------------------------------------------- queries
    def fleets(self, *, name: str | None = None, cls: str | None = None,
               system: str | int | None = None, owner: int | None = None,
               player_only: bool = False) -> list[dict]:
        rows = self._fleets
        if player_only or owner is not None:
            want = self.player if owner is None else owner
            rows = [f for f in rows if f["owner"] == want]
        if name:
            rows = [f for f in rows if name in f["name"] or name in (f["key"] or "")]
        if cls:
            rows = [f for f in rows if cls in f["class"]]
        if system is not None:
            if isinstance(system, int):
                rows = [f for f in rows if f["system"] == system]
            else:
                sid = self.system_id(system, missing_ok=True)
                rows = [f for f in rows if f["system"] == sid] if sid is not None else []
        return rows

    def fleet(self, name: str, *, cls: str | None = None) -> dict:
        rows = self.fleets(name=name, cls=cls, player_only=True)
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise KeyError(f"no player fleet matching {name!r}")
        raise ValueError(
            f"{name!r} is ambiguous: " + ", ".join(f"[{r['id']}]{r['class']}" for r in rows))

    def fleet_id(self, name: str, *, cls: str | None = None) -> int:
        return self.fleet(name, cls=cls)["id"]

    def systems(self, name: str | None = None) -> list[dict]:
        if not name:
            return list(self._systems)
        return [s for s in self._systems if name in s["name"] or name in (s["key"] or "")]

    def system(self, name: str) -> dict:
        rows = self.systems(name)
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise KeyError(f"no system matching {name!r}")
        raise ValueError(f"{name!r} is ambiguous: {[r['id'] for r in rows]}")

    def system_id(self, name: str, *, missing_ok: bool = False) -> int | None:
        try:
            return self.system(name)["id"]
        except KeyError:
            if missing_ok:
                return None
            raise

    def system_name(self, sid: int) -> str | None:
        return self._by_id.get(sid)

    # ---------------------------------------------------------------- summary
    def summary(self) -> str:
        mine = self.fleets(player_only=True)
        lines = [f"date={self.date} player={self.player} save={os.path.basename(self.save_path)}",
                 f"fleets={len(self._fleets)} systems={len(self._systems)} mine={len(mine)}"]
        for f in mine:
            lines.append(f"  [{f['id']:>3}] {f['class']:8} {f['name']:16} @ {f['system_name']} (sys {f['system']})")
        return "\n".join(lines)


if __name__ == "__main__":
    st = StellarisState()
    print(st.summary())
    print("\n--- 查询演示 ---")
    print("军事舰船:", [(f['id'], f['name']) for f in st.fleets(cls="军事舰船", player_only=True)])
    print("伊玛卡的舰队:", [(f['id'], f['class']) for f in st.fleets(system="伊玛卡", player_only=True)])
    try:
        print("按名字取:", st.fleet("素斯娜舰队"))
    except Exception as e:
        print("按名字取失败:", e)
