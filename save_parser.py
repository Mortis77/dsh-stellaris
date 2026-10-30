"""Paradox Clausewitz savegame parser for Stellaris.

Reads a .sav (zip with `gamestate` + `meta`), parses the Clausewitz text into
nested dicts/lists, then extracts the pieces the native runtime needs:
countries, fleets, systems, planets, and their IDs.

Usage:
    python save_parser.py                     # newest save, summary
    python save_parser.py <path.sav>          # specific save
    python save_parser.py --json out.json     # dump structured state
"""
from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from typing import Any, Iterator

SAVE_DIR = os.environ.get(
    "STELLARIS_SAVE_DIR",
    os.path.join(os.path.expanduser("~"), "Documents",
                 "Paradox Interactive", "Stellaris", "save games"),
)


# ------------------------------------------------------------------ tokenizer

def tokens(text: str) -> Iterator[tuple[str, str]]:
    """Yield ('key'|'val'|'{'|'}', text). Bare strings, quoted strings, braces."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in "{}":
            yield (c, c)
            i += 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            yield ("val", "".join(buf))
            i = j + 1
            continue
        j = i
        while j < n and text[j] not in " \t\r\n{}=":
            j += 1
        word = text[i:j]
        if j < n and text[j] == "=":
            yield ("key", word)
            i = j + 1
            continue
        yield ("val", word)
        i = j


def _scalar(v: str):
    if v == "yes":
        return True
    if v == "no":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


# ------------------------------------------------------------------ parser

def parse_block(tok: Iterator[tuple[str, str]], stop_at_brace: bool = False):
    """Parse one block. Returns dict; lists become arrays when values repeat."""
    out: dict[str, Any] = {}
    pending_key: str | None = None
    for kind, val in tok:
        if kind == "}":
            return out
        if kind == "{":
            child = parse_block(tok)
            if pending_key is None:
                # anonymous array element
                out.setdefault("__list__", []).append(child)
            else:
                _store(out, pending_key, child)
                pending_key = None
            continue
        if kind == "key":
            pending_key = val
            continue
        # value
        if pending_key is None:
            out.setdefault("__list__", []).append(_scalar(val))
        else:
            _store(out, pending_key, _scalar(val))
            pending_key = None
    return out


def _store(out: dict, key: str, value) -> None:
    if key in out:
        cur = out[key]
        if isinstance(cur, list) and not isinstance(value, list):
            cur.append(value)
        else:
            out[key] = [cur, value]
    else:
        out[key] = value


def load_gamestate(path: str) -> tuple[dict, str]:
    with zipfile.ZipFile(path) as z:
        raw = z.read("gamestate").decode("utf-8", "replace")
    tok = tokens(raw)
    root = parse_block(tok)
    return root, raw


# ------------------------------------------------------------------ extract

def as_id_list(block) -> list[int]:
    """Clausewitz encodes `{ 1 2 3 }` as {'__list__': [1,2,3]} or a scalar."""
    if block is None:
        return []
    if isinstance(block, dict):
        lst = block.get("__list__")
        if isinstance(lst, list):
            return [int(x) for x in lst if isinstance(x, (int, float))]
        return []
    if isinstance(block, (int, float)):
        return [int(block)]
    if isinstance(block, list):
        return [int(x) for x in block if isinstance(x, (int, float))]
    return []


def fleet_owner_map(root: dict) -> dict[int, int]:
    """fleet_id -> country_id, taken from each country's owned_fleets."""
    owners: dict[int, int] = {}
    countries = root.get("country")
    if not isinstance(countries, dict):
        return owners
    for cid, cbody in countries.items():
        if cid == "__list__" or not isinstance(cbody, dict):
            continue
        try:
            cid_i = int(cid)
        except ValueError:
            continue
        mgr = cbody.get("fleets_manager")
        owned = mgr.get("owned_fleets") if isinstance(mgr, dict) else None
        for item in (owned.get("__list__") if isinstance(owned, dict) else None) or []:
            if isinstance(item, dict) and "fleet" in item:
                owners[int(item["fleet"])] = cid_i
    return owners


def summarize(root: dict) -> dict:
    meta_date = root.get("date")
    player = root.get("player")
    player_country = None
    if isinstance(player, dict):
        lst = player.get("__list__") or []
        if lst and isinstance(lst[0], dict):
            player_country = lst[0].get("country")

    owners = fleet_owner_map(root)

    fleets = []
    fblock = root.get("fleet") or {}
    if isinstance(fblock, dict):
        for fid, fbody in fblock.items():
            if fid == "__list__" or not isinstance(fbody, dict):
                continue
            try:
                fid_i = int(fid)
            except ValueError:
                continue
            name = fbody.get("name")
            name_key = None
            if isinstance(name, dict):
                name_key = name.get("key")
            elif isinstance(name, str):
                name_key = name
            mm = fbody.get("movement_manager")
            cur_sys = None
            if isinstance(mm, dict):
                coord = mm.get("coordinate")
                if isinstance(coord, dict):
                    cur_sys = coord.get("origin")
            fleets.append({
                "id": fid_i,
                "name": name_key,
                "ship_class": fbody.get("ship_class"),
                "owner": owners.get(fid_i),
                "system": cur_sys,
                "ships": fbody.get("ships"),
            })

    systems = []
    gblock = root.get("galactic_object") or {}
    if isinstance(gblock, dict):
        for sid, sbody in gblock.items():
            if sid == "__list__" or not isinstance(sbody, dict):
                continue
            try:
                sid_i = int(sid)
            except ValueError:
                continue
            name = sbody.get("name")
            systems.append({
                "id": sid_i,
                "name": name if isinstance(name, str) else (name or {}).get("key") if isinstance(name, dict) else None,
            })

    planets = []
    pblock = root.get("planets") or {}
    if isinstance(pblock, dict) and "planet" in pblock:
        pblock = pblock["planet"]
    if isinstance(pblock, dict):
        for pid, pbody in pblock.items():
            if pid == "__list__" or not isinstance(pbody, dict):
                continue
            try:
                pid_i = int(pid)
            except ValueError:
                continue
            planets.append({"id": pid_i, "planet_size": pbody.get("planet_size"),
                            "owner": pbody.get("owner")})

    return {
        "date": meta_date,
        "player_country": player_country,
        "counts": {"fleets": len(fleets), "systems": len(systems), "planets": len(planets)},
        "resources": _resources(root, player_country),
        "colonies": _colonies(root, player_country),
        "research": _research(root, player_country),
        "pops": _pops(root, {c["id"] for c in _colonies(root, player_country)}),
        "fleets": fleets,
        "systems": systems,
        "planets": planets[:60],
    }


def _sum_categories(block: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(block, dict):
        return out
    for _cat, vals in block.items():
        if isinstance(vals, dict):
            for res, v in vals.items():
                if isinstance(v, (int, float)):
                    out[res] = out.get(res, 0.0) + float(v)
    return out


def _resources(root: dict, player: Any) -> dict:
    """Monthly income / expenses / balance per resource. Stockpiles are runtime-only."""
    try:
        country = root["country"][str(player)]
    except (KeyError, TypeError):
        return {}
    budget = country.get("budget") or {}
    month = budget.get("current_month") or {}
    return {
        "income": _sum_categories(month.get("income")),
        "expenses": _sum_categories(month.get("expenses")),
        "balance": _sum_categories(month.get("balance")),
    }


def _colonies(root: dict, player: Any) -> list[dict]:
    """Actual colonised worlds.

    `controlled_planets` lists every controlled celestial body, including the
    rocks your mining/research stations sit on (dozens of them). Real colonies
    live in `owned_planets`; even late game a run usually has fewer than ten.
    """
    try:
        country = root["country"][str(player)]
    except (KeyError, TypeError):
        return []
    ids = as_id_list(country.get("owned_planets")) or as_id_list(country.get("controlled_colonies"))
    planets = (root.get("planets") or {}).get("planet") or {}
    out = []
    for pid in ids:
        body = planets.get(str(pid))
        if not isinstance(body, dict):
            continue
        name = body.get("name")
        key = name.get("key") if isinstance(name, dict) else name
        coord = body.get("coordinate")
        sys_id = coord.get("origin") if isinstance(coord, dict) else None
        out.append({
            "id": pid,
            "name_key": key,
            "size": body.get("planet_size"),
            "class": body.get("planet_class"),
            "colony": body.get("colony"),
            "build_queue": body.get("build_queue"),
            "system": sys_id,
        })
    return out


def _research(root: dict, player: Any) -> dict:
    try:
        country = root["country"][str(player)]
    except (KeyError, TypeError):
        return {}
    ts = country.get("tech_status") or {}
    techs = as_id_list(ts.get("technology")) if isinstance(ts.get("technology"), dict) else ts.get("technology")
    if isinstance(techs, list):
        count = len(techs)
    elif isinstance(techs, int):
        count = techs
    else:
        count = 0
    return {
        "researched_count": count,
        "auto_physics": ts.get("auto_researching_physics"),
        "auto_society": ts.get("auto_researching_society"),
        "auto_engineering": ts.get("auto_researching_engineering"),
    }


def _pops(root: dict, colony_ids: set[int] | None = None) -> dict:
    """Population per planet.

    Each planet carries a pop_groups list of composite group ids; the groups
    live in the top-level pop_groups table and store a fixed-point size
    (thousandths). Sum per planet, then divide by POP_SCALE.
    """
    POP_SCALE = 1000.0
    groups = root.get("pop_groups") or {}
    planets = (root.get("planets") or {}).get("planet") or {}
    by_planet: dict[int, float] = {}
    total_raw = 0.0
    if isinstance(planets, dict):
        for pid_key, body in planets.items():
            if not isinstance(body, dict):
                continue
            try:
                pid = int(pid_key)
            except ValueError:
                continue
            if colony_ids is not None and pid not in colony_ids:
                continue
            group_ids = body.get("pop_groups")
            ids = group_ids.get("__list__") if isinstance(group_ids, dict) else None
            if not ids:
                continue
            raw = 0.0
            for gid in ids:
                g = groups.get(str(gid))
                if isinstance(g, dict) and isinstance(g.get("size"), (int, float)):
                    raw += float(g["size"])
            if raw > 0:
                by_planet[pid] = raw
                total_raw += raw
    return {
        "total": round(total_raw / POP_SCALE, 1),
        "by_planet": {k: round(v / POP_SCALE, 1) for k, v in by_planet.items()},
    }


def newest_save() -> str:
    best, best_t = None, 0
    for root, _d, files in os.walk(SAVE_DIR):
        for f in files:
            if f.endswith(".sav"):
                p = os.path.join(root, f)
                t = os.path.getmtime(p)
                if t > best_t:
                    best, best_t = p, t
    return best


def main(argv: list[str]) -> int:
    path = argv[0] if argv and not argv[0].startswith("--") else newest_save()
    if not path or not os.path.exists(path):
        print("no save found"); return 2
    print("save:", os.path.basename(path))
    root, _raw = load_gamestate(path)
    print("top-level keys:", len(root))
    data = summarize(root)
    print("date:", data["date"], " player_country:", data["player_country"])
    print("counts:", data["counts"])
    print("\n--- player fleets ---")
    for f in data["fleets"]:
        if f["owner"] == data["player_country"]:
            print(f"  fleet {f['id']:>3}  class={str(f['ship_class'])[:22]:22} name={str(f['name'])[:40]}")
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\nwrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
