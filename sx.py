#!/usr/bin/env python3
"""CLI backend for the dsh-stellaris plugin.

Every command prints one JSON object on stdout so index.js can parse it without
guessing at formatting.

    sx.py state [--save PATH]
    sx.py fleets [--save PATH] [--class 军事舰船] [--system 伊玛卡]
    sx.py systems [--save PATH] [--filter 伊]
    sx.py advice [--save PATH]
    sx.py act move_fleet <fleet> <system>
    sx.py act select_research <technology>
    sx.py act colonize_planet <fleet> <planet_id>
    sx.py act toggle_building <colony> <building_id>
    sx.py act destroy_building <colony> <building_id>
    sx.py probe                     # is the runtime pipe alive?
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback

import state as state_mod
import advisor as advisor_mod
from actions import Game, RuntimeUnavailable


def _out(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _state(args) -> state_mod.StellarisState:
    return state_mod.StellarisState(args.save)


# ---------------------------------------------------------------- commands
def cmd_state(args) -> int:
    st = _state(args)
    res = st._raw_resources
    colonies = st._raw_colonies
    fleets = st.fleets(player_only=True)
    by_class: dict[str, int] = {}
    for f in fleets:
        by_class[f["class"]] = by_class.get(f["class"], 0) + 1
    _out({
        "ok": True,
        "date": st.date,
        "player": st.player,
        "save": st.save_path,
        "resources": {
            "income": res.get("income", {}),
            "expenses": res.get("expenses", {}),
            "balance": res.get("balance", {}),
        },
        "research": st._raw_research,
        "colonies": [
            {"id": c["id"], "name": c["name"], "size": c["size"],
             "build_queue": c["build_queue"], "system": c["system_name"]}
            for c in colonies
        ],
        "fleet_classes": by_class,
        "pops": st._pops if hasattr(st, "_pops") else None,
    })
    return 0


def cmd_fleets(args) -> int:
    st = _state(args)
    rows = st.fleets(name=args.name, cls=args.cls, system=args.system, player_only=not args.all)
    _out({
        "ok": True,
        "count": len(rows),
        "fleets": [
            {"id": f["id"], "name": f["name"], "class": f["class"],
             "system": f["system"], "system_name": f["system_name"], "owner": f["owner"]}
            for f in rows
        ],
    })
    return 0


def cmd_systems(args) -> int:
    st = _state(args)
    rows = st.systems(args.filter) if args.filter else st.systems()
    _out({"ok": True, "count": len(rows),
          "systems": [{"id": s["id"], "name": s["name"]} for s in rows[:args.limit]]})
    return 0


def cmd_advice(args) -> int:
    st = _state(args)
    lines = advisor_mod.Advisor(st).advise()
    _out({"ok": True, "date": st.date, "advice": lines})
    return 0


def cmd_probe(args) -> int:
    try:
        g = Game(args.save)
    except Exception as exc:                      # noqa: BLE001 - surface everything
        _out({"ok": False, "error": str(exc)})
        return 1
    try:
        reply = g.select_research("tech_lasers_1")
        _out({"ok": True, "status": reply.status, "result": reply.result})
    except RuntimeUnavailable as exc:
        _out({"ok": False, "runtime": "unavailable", "error": str(exc)})
        return 1
    except Exception as exc:                      # noqa: BLE001
        _out({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


ACTIONS = {
    "move_fleet": ("move_fleet", ("fleet", "system")),
    "select_research": ("select_research", ("technology",)),
    "colonize_planet": ("colonize_planet", ("fleet", "planet_id")),
    "toggle_building": ("toggle_building", ("colony", "building_id")),
    "destroy_building": ("destroy_building", ("colony", "building_id")),
}


def cmd_act(args) -> int:
    spec = ACTIONS.get(args.action)
    if spec is None:
        _out({"ok": False, "error": f"unknown action {args.action!r}; known: {sorted(ACTIONS)}"})
        return 2
    method, names = spec
    if len(args.params) != len(names):
        _out({"ok": False, "error": f"{args.action} takes {len(names)} params {list(names)}, got {len(args.params)}"})
        return 2
    # numeric params arrive as strings; coerce the ones the backend expects as int
    argv = []
    for name, raw in zip(names, args.params):
        argv.append(int(raw) if name.endswith("_id") or name == "planet_id" else raw)
    try:
        g = Game(args.save)
        fn = getattr(g, method)
        reply = fn(*argv)
    except ValueError as exc:
        _out({"ok": False, "error": f"argument: {exc}"})
        return 1
    except KeyError as exc:
        _out({"ok": False, "error": f"not found: {exc}"})
        return 1
    except RuntimeUnavailable as exc:
        _out({"ok": False, "runtime": "unavailable", "error": str(exc)})
        return 1
    except Exception as exc:                      # noqa: BLE001
        _out({"ok": False, "error": f"{type(exc).__name__}: {exc}",
              "trace": traceback.format_exc()[-800:]})
        return 1
    _out({"ok": reply.status in ("pending", "confirmed"),
          "action": args.action, "status": reply.status, "result": reply.result, "raw": reply.raw})
    return 0 if reply.status in ("pending", "confirmed") else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="sx.py")
    ap.add_argument("--save", default=None, help="save file path (default: newest)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("state"); s.set_defaults(fn=cmd_state)
    s = sub.add_parser("fleets")
    s.add_argument("--name"); s.add_argument("--class", dest="cls")
    s.add_argument("--system"); s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_fleets)
    s = sub.add_parser("systems")
    s.add_argument("--filter"); s.add_argument("--limit", type=int, default=60)
    s.set_defaults(fn=cmd_systems)
    s = sub.add_parser("advice"); s.set_defaults(fn=cmd_advice)
    s = sub.add_parser("probe"); s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("act")
    s.add_argument("action", choices=sorted(ACTIONS))
    s.add_argument("params", nargs="*")
    s.set_defaults(fn=cmd_act)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:                      # noqa: BLE001
        _out({"ok": False, "error": f"{type(exc).__name__}: {exc}",
              "trace": traceback.format_exc()[-800:]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
