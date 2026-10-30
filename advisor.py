"""Rule-based advisor: reads StellarisState, emits concrete next-step suggestions.

Deliberately simple. No LLM, no learning — just the checks a human player runs
every few months. The point is to prove the state layer carries enough signal to
drive decisions at all.
"""
from __future__ import annotations

from state import StellarisState

# monthly balance thresholds (per resource)
NEGATIVE_WARN = -1.0
MINERALS_LOW = 15.0
ENERGY_LOW = 10.0
ALLOYS_LOW = 5.0
RESEARCH_LOW = 8.0


class Advisor:
    def __init__(self, st: StellarisState):
        self.st = st

    # ------------------------------------------------------------------ helpers
    def _balance(self, resource: str) -> float:
        return float(self.st._raw_resources.get("balance", {}).get(resource, 0.0))

    def _home_system(self) -> int | None:
        """Most fleets cluster at the capital; use that as a stand-in."""
        counts: dict[int, int] = {}
        for f in self.st.fleets(player_only=True):
            if isinstance(f["system"], int):
                counts[f["system"]] = counts.get(f["system"], 0) + 1
        if not counts:
            return None
        return max(counts, key=counts.get)

    # ------------------------------------------------------------------ rules
    def advise(self) -> list[str]:
        out: list[str] = []
        home = self._home_system()
        home_name = self.st.system_name(home) or "?"

        # 1. negative monthly balance
        for res, val in sorted(self.st._raw_resources.get("balance", {}).items()):
            if val < NEGATIVE_WARN:
                out.append(f"[资源] {res} 月净 -{abs(val):.1f}，需要处理")

        # 2. strategic shortages
        checks = [("minerals", MINERALS_LOW, "矿"), ("energy", ENERGY_LOW, "能量"),
                  ("alloys", ALLOYS_LOW, "合金"), ("physics_research", RESEARCH_LOW, "物理科研")]
        for key, floor, label in checks:
            v = self._balance(key)
            if 0 <= v < floor:
                out.append(f"[短板] {label}月增仅 +{v:.1f}，低于 {floor:.0f}")

        # 3. idle construction queues
        empty = [c for c in self.st._raw_colonies if c.get("build_queue") in (0, None)]
        if empty:
            out.append(f"[建造] {len(empty)} 颗星球建造队列是空的，该补建筑了")

        # 4. science ship parked at home
        idle_sci = [f for f in self.st.fleets(cls="科研船", player_only=True)
                    if f["system"] == home]
        if idle_sci:
            out.append(f"[探索] {len(idle_sci)} 艘科研船停在 {home_name}，该派出去勘测")

        # 5. constructor parked at home
        idle_con = [f for f in self.st.fleets(cls="工程船", player_only=True)
                    if f["system"] == home]
        if idle_con:
            out.append(f"[基建] {len(idle_con)} 艘工程船停在 {home_name}，可以去建矿站/研究站")

        # 6. fleet composition sanity
        mil = self.st.fleets(cls="军事舰船", player_only=True)
        if not mil:
            out.append("[军事] 没有军用舰队，至少该有一支")

        # 7. colony count sanity — only meaningful well past the opening.
        # A young empire legitimately runs on its capital alone: habitable
        # worlds are scarce, and many builds do not expand early at all.
        try:
            year = int(str(self.st.date).split(".")[0])
        except (ValueError, AttributeError):
            year = 0
        n_colonies = len(self.st._raw_colonies)
        if year >= 2230 and n_colonies < 3:
            out.append(f"[扩张] {year} 年了只有 {n_colonies} 颗殖民地，扩张偏慢")

        if not out:
            out.append("[状态] 没发现明显问题")
        return out


if __name__ == "__main__":
    st = StellarisState()
    print(st.summary())
    print("\n--- 建议 ---")
    for line in Advisor(st).advise():
        print("  " + line)
