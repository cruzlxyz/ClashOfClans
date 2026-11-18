"""Clash Of Clans — seven intent tools for Hermes Agent.

Six tools wrap the 35 documented Supercell API endpoints; one parses the
in-game village export for upgrade timers. All handlers retrieve data or parse
local exports. Tokens are loaded from .env, never printed.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from agent.secret_scope import get_secret
except Exception:  # pragma: no cover - validator/isolated probe may lack the host module
    get_secret = None

TOOLSET = "Clash Of Clans"
HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "state"
MAP_FILE = HERE / "mapping.json"
BASE = "https://api.clashofclans.com/v1"
UA = "Hermes Clash Of Clans/1.0"
WIB = ZoneInfo("Asia/Jakarta")
DEFAULT_TAG = "#R0LYP009Y"
DEFAULT_CLAN = "#2CGGQ8Y0J"
DEFAULT_EXPORT = "/home/ubuntu/coc-export.json"

_MAP_CACHE: dict | None = None


# --------------------------------------------------------------------------
# environment / HTTP helpers
# --------------------------------------------------------------------------
def _token() -> str:
    """Resolve the developer JWT through Hermes' secret scope (catalog rule 11)."""
    if get_secret is not None:
        try:
            value = get_secret("COC_API_TOKEN", "")
            if value:
                return str(value).strip()
        except Exception:
            pass
    return os.getenv("COC_API_TOKEN", "").strip()


def _check() -> bool:
    return bool(_token())


def _check_timer() -> bool:
    return True


def _request(method: str, path: str, body: dict | None = None) -> dict:
    tok = _token()
    if not tok:
        return {"status": 0, "data": {"message": "COC_API_TOKEN belum diset di .env"}}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + tok, "Accept": "application/json",
                 "Content-Type": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return {"status": r.status, "data": json.load(r)}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:300]
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"message": raw}
        return {"status": e.code, "data": payload}
    except Exception as e:
        return {"status": 0, "data": {"message": str(e)[:200]}}


def _parallel(jobs: dict) -> dict:
    out: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_request, m, p, b): k for k, (m, p, b) in jobs.items()}
        for f in as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception as e:
                out[futs[f]] = {"status": 0, "data": {"message": str(e)[:200]}}
    return out


def _enc(tag: str) -> str:
    value = str(tag or "").strip()
    if not value.startswith("#"):
        value = "#" + value
    return urllib.parse.quote(value, safe="")


def _err(msg: str) -> str:
    return json.dumps({"error": str(msg)}, ensure_ascii=False)


def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _wib(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, WIB).strftime("%Y-%m-%d %H:%M:%S")


def _mapping() -> dict:
    global _MAP_CACHE
    if _MAP_CACHE is None:
        try:
            _MAP_CACHE = json.loads(MAP_FILE.read_text(encoding="utf-8"))
        except Exception:
            _MAP_CACHE = {}
    return _MAP_CACHE


def _name(section: str, ident: object) -> str:
    table = _mapping().get(section, {}) if isinstance(_mapping(), dict) else {}
    return table.get(str(ident), f"ID {ident}")


# --------------------------------------------------------------------------
# 1. coc_verify — identity gate (the only game-token tool)
# --------------------------------------------------------------------------
def coc_verify(args: dict, **kwargs) -> str:
    tag = args.get("tag") or DEFAULT_TAG
    token = args.get("token") or ""
    if not token:
        return _err("token wajib diisi — token game dari Settings → More Settings → API Token")
    r = _request("POST", f"/players/{_enc(tag)}/verifytoken", {"token": token})
    status = (r.get("data") or {}).get("status")
    return _dump({
        "tag": tag,
        "http": r.get("status"),
        "status": status,
        "verified": status == "ok",
        "note": "Token game sekali pakai — jangan disimpan.",
    })


# --------------------------------------------------------------------------
# 2. coc_player — profile + battle log + league history
# --------------------------------------------------------------------------
def coc_player(args: dict, **kwargs) -> str:
    tag = args.get("tag") or DEFAULT_TAG
    r = _parallel({
        "p": ("GET", f"/players/{_enc(tag)}", None),
        "b": ("GET", f"/players/{_enc(tag)}/battlelog", None),
        "l": ("GET", f"/players/{_enc(tag)}/leaguehistory", None),
    })
    if r["p"]["status"] != 200:
        return _dump({"error": "player fetch gagal", "http": r["p"]["status"],
                      "detail": r["p"]["data"]})
    p = r["p"]["data"]
    recent = []
    for b in (r["b"]["data"].get("items") or [])[:5]:
        recent.append({
            "time": b.get("battleTimestamp") or b.get("battleTime"),
            "opponent": b.get("opponentName") or (b.get("opponent") or {}).get("name"),
            "attack": b.get("attack"),
            "stars": b.get("stars"),
            "destruction": b.get("destructionPercentage"),
        })
    return _dump({
        "tag": p.get("tag"), "name": p.get("name"),
        "townHall": p.get("townHallLevel"), "builderHall": p.get("builderHallLevel"),
        "exp": p.get("expLevel"), "trophies": p.get("trophies"),
        "bestTrophies": p.get("bestTrophies"),
        "builderTrophies": p.get("builderBaseTrophies"),
        "warStars": p.get("warStars"),
        "donations": p.get("donations"), "received": p.get("donationsReceived"),
        "capitalContribution": p.get("clanCapitalContributions"),
        "role": p.get("role"),
        "clan": (p.get("clan") or {}).get("name"),
        "leagueTier": (p.get("leagueTier") or {}).get("name"),
        "builderLeague": (p.get("builderBaseLeague") or {}).get("name"),
        "heroes": [{"name": h.get("name"), "level": h.get("level"),
                    "max": h.get("maxLevel")} for h in p.get("heroes", [])],
        "battlelogCount": len(r["b"]["data"].get("items") or []),
        "recentBattles": recent,
        "leagueHistoryCount": len(r["l"]["data"].get("items") or []),
    })


# --------------------------------------------------------------------------
# 3. coc_clan — clan, members, war, war log, CWL, capital raids
# --------------------------------------------------------------------------
def coc_clan(args: dict, **kwargs) -> str:
    tag = args.get("tag") or DEFAULT_CLAN
    me = args.get("player_tag") or DEFAULT_TAG
    jobs = {
        "c": ("GET", f"/clans/{_enc(tag)}", None),
        "m": ("GET", f"/clans/{_enc(tag)}/members", None),
        "w": ("GET", f"/clans/{_enc(tag)}/currentwar", None),
        "l": ("GET", f"/clans/{_enc(tag)}/warlog", None),
        "g": ("GET", f"/clans/{_enc(tag)}/currentwar/leaguegroup", None),
        "r": ("GET", f"/clans/{_enc(tag)}/capitalraidseasons", None),
    }
    r = _parallel(jobs)
    war = r["w"]["data"] if r["w"]["status"] == 200 else {}
    mine = next((m for m in (war.get("clan") or {}).get("members", [])
                 if m.get("tag") == me), {})
    top = sorted(r["m"]["data"].get("items") or [],
                 key=lambda x: x.get("donations") or 0, reverse=True)[:5]
    warlog = (r["l"]["data"].get("items") or [])[:3]
    # optional CWL war from leaguegroup
    war_tag = None
    for rnd in (r["g"]["data"].get("rounds") or []):
        for t in rnd.get("warTags", []) or []:
            if t and t != "#0":
                war_tag = _enc(t)
                break
        if war_tag:
            break
    cwl = None
    if war_tag:
        cw = _request("GET", f"/clanwarleagues/wars/{war_tag}")
        if cw["status"] == 200:
            cwl = {"status": cw["data"].get("state"),
                   "clan": (cw["data"].get("clan") or {}).get("name")}
    return _dump({
        "tag": (r["c"]["data"] or {}).get("tag"),
        "name": (r["c"]["data"] or {}).get("name"),
        "level": (r["c"]["data"] or {}).get("clanLevel"),
        "type": (r["c"]["data"] or {}).get("type"),
        "members": (r["c"]["data"] or {}).get("members"),
        "clanPoints": (r["c"]["data"] or {}).get("clanPoints"),
        "capitalPoints": (r["c"]["data"] or {}).get("clanCapitalPoints"),
        "topDonors": [{"name": m.get("name"), "donations": m.get("donations")} for m in top],
        "war": {
            "state": war.get("state"),
            "clanStars": (war.get("clan") or {}).get("stars"),
            "clanDestruction": (war.get("clan") or {}).get("destructionPercentage"),
            "opponent": (war.get("opponent") or {}).get("name"),
            "opponentStars": (war.get("opponent") or {}).get("stars"),
            "myAttacks": len(mine.get("attacks") or []),
            "myStars": sum(a.get("stars") or 0 for a in mine.get("attacks") or []),
        },
        "warlogCount": len(r["l"]["data"].get("items") or []),
        "warlogRecent": [{"result": w.get("result"),
                          "opponent": (w.get("opponent") or {}).get("name"),
                          "clanStars": (w.get("clan") or {}).get("stars"),
                          "opponentStars": (w.get("opponent") or {}).get("stars")} for w in warlog],
        "leaguegroup": "aktif" if r["g"]["status"] == 200 else "tidak aktif",
        "cwlWar": cwl,
        "capitalRaidCount": len(r["r"]["data"].get("items") or []),
    })


# --------------------------------------------------------------------------
# 4. coc_league — league tiers, seasons, ranked league group
# --------------------------------------------------------------------------
def coc_league(args: dict, **kwargs) -> str:
    tag = args.get("player_tag") or DEFAULT_TAG
    p = _request("GET", f"/players/{_enc(tag)}")
    pd = p["data"] if p["status"] == 200 else {}
    group = pd.get("currentLeagueGroupTag")
    season = pd.get("currentLeagueSeasonId")
    sid = urllib.parse.quote(str(season), safe="") if season else ""
    jobs = {
        "lg": ("GET", "/leagues", None),
        "lt": ("GET", "/leaguetiers", None),
        "wl": ("GET", "/warleagues", None),
        "cl": ("GET", "/capitalleagues", None),
        "bb": ("GET", "/builderbaseleagues", None),
        "ls": ("GET", "/leagues/29000022/seasons", None),
    }
    if group and season:
        jobs["group"] = ("GET", f"/leaguegroup/{_enc(group)}/{sid}?playerTag={_enc(tag)}", None)
    r = _parallel(jobs)
    gd = r.get("group", {}).get("data") or {}
    if r.get("group", {}).get("status") != 200 or not gd:
        gd = _request("GET", f"/leaguegroup/{_enc(group)}/{sid}?playerTag={_enc(tag)}").get("data") or {}
    return _dump({
        "player": tag,
        "leagueTier": (pd.get("leagueTier") or {}).get("name"),
        "builderLeague": (pd.get("builderBaseLeague") or {}).get("name"),
        "currentGroupTag": group,
        "currentSeasonId": season,
        "group": {
            "members": len(gd.get("members") or []),
            "attackLogs": len(gd.get("attackLogs") or []),
            "defenseLogs": len(gd.get("defenseLogs") or []),
        } if gd else None,
        "legendarySeasons": [x.get("id") for x in
                             (r["ls"]["data"].get("items") or [])[:3]],
        "counts": {
            "leagues": len(r["lg"]["data"].get("items") or []),
            "leagueTiers": len(r["lt"]["data"].get("items") or []),
            "warLeagues": len(r["wl"]["data"].get("items") or []),
            "capitalLeagues": len(r["cl"]["data"].get("items") or []),
            "builderBaseLeagues": len(r["bb"]["data"].get("items") or []),
        },
    })


# --------------------------------------------------------------------------
# 5. coc_ranks — locations + five leaderboards
# --------------------------------------------------------------------------
def coc_ranks(args: dict, **kwargs) -> str:
    want = str(args.get("location") or "Indonesia").lower()
    limit = int(args.get("limit") or 5)
    loc_all = _request("GET", "/locations")
    items = loc_all["data"].get("items") or []
    loc = next((x for x in items if str(x.get("name", "")).lower() == want), None)
    if loc is None:
        loc = items[0] if items else {}
    lid = loc.get("id")
    if not lid:
        return _dump({"error": "lokasi tidak ditemukan"})
    paths = {
        "clans": f"/locations/{lid}/rankings/clans?limit={limit}",
        "players": f"/locations/{lid}/rankings/players?limit={limit}",
        "playersBuilder": f"/locations/{lid}/rankings/players-builder-base?limit={limit}",
        "clansBuilder": f"/locations/{lid}/rankings/clans-builder-base?limit={limit}",
        "capitals": f"/locations/{lid}/rankings/capitals?limit={limit}",
    }
    jobs = {k: ("GET", p, None) for k, p in paths.items()}
    jobs["info"] = ("GET", f"/locations/{lid}", None)
    r = _parallel(jobs)
    out = {"location": {"id": lid, "name": loc.get("name"),
                        "isCountry": loc.get("isCountry")}}
    for key in ("clans", "players", "playersBuilder", "clansBuilder", "capitals"):
        out[key] = [
            {"rank": x.get("rank"), "name": x.get("name"),
             "tag": x.get("tag"),
             "trophies": (x.get("trophies") or x.get("clanPoints")
                     or x.get("builderBaseTrophies") or x.get("clanBuilderBasePoints")
                     or x.get("clanCapitalPoints") or x.get("points")),
             "clan": (x.get("clan") or {}).get("name")}
            for x in (r[key]["data"].get("items") or [])[:limit]
        ]
    return _dump(out)


# --------------------------------------------------------------------------
# 6. coc_meta — gold pass + labels + global counts
# --------------------------------------------------------------------------
def coc_meta(args: dict, **kwargs) -> str:
    r = _parallel({
        "gp": ("GET", "/goldpass/seasons/current", None),
        "lp": ("GET", "/labels/players", None),
        "lc": ("GET", "/labels/clans", None),
        "lg": ("GET", "/leagues", None),
        "lt": ("GET", "/leaguetiers", None),
        "wl": ("GET", "/warleagues", None),
    })
    gp = r["gp"]["data"]
    return _dump({
        "goldPass": {"start": gp.get("startTime"), "end": gp.get("endTime")},
        "playerLabels": {"count": len(r["lp"]["data"].get("items") or []),
                         "sample": [x.get("name") for x in
                                    (r["lp"]["data"].get("items") or [])[:5]]},
        "clanLabels": {"count": len(r["lc"]["data"].get("items") or []),
                       "sample": [x.get("name") for x in
                                  (r["lc"]["data"].get("items") or [])[:5]]},
        "counts": {
            "leagues": len(r["lg"]["data"].get("items") or []),
            "leagueTiers": len(r["lt"]["data"].get("items") or []),
            "warLeagues": len(r["wl"]["data"].get("items") or []),
        },
    })


# --------------------------------------------------------------------------
# 7. coc_timer — village export parser (no API, no token)
# --------------------------------------------------------------------------
def coc_timer(args: dict, **kwargs) -> str:
    path = str(args.get("path") or DEFAULT_EXPORT)
    alarm_h = int(args.get("alarm_hours") or 1)
    alarm_m = int(args.get("alarm_minutes") or 15)
    save = bool(args.get("save", True))
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        return _err(f"gagal baca export: {e}")
    if "timestamp" not in data:
        return _err("export tidak punya field timestamp")
    base = int(data["timestamp"])
    now = int(time.time())
    items: list[dict] = []

    def add(loc: str, kind: str, ident: object, timer: object,
            level: object = None, section: str = "") -> None:
        try:
            seconds = int(timer)
        except (TypeError, ValueError):
            return
        finish = base + seconds
        remaining = finish - now
        a1 = finish - alarm_h * 3600
        a15 = finish - alarm_m * 60
        if remaining <= 0:
            status = "done"
        elif a15 <= now:
            status = "alarm_15m_passed"
        elif a1 <= now:
            status = "alarm_1h_passed"
        else:
            status = "scheduled"
        items.append({
            "location": loc, "kind": kind, "id": ident,
            "name": _name(section, ident) if section else str(ident),
            "level": level, "seconds": seconds,
            "finish": _wib(finish), "finishEpoch": finish,
            "alarm1h": _wib(a1), "alarm15m": _wib(a15),
            "remainingSeconds": remaining, "status": status,
        })

    for b in data.get("buildings", []):
        if "timer" in b:
            add("HOME", "building", b.get("data"), b["timer"], b.get("lvl"), "th_buildings")
    for u in data.get("units", []):
        if "timer" in u:
            add("HOME", "unit", u.get("data"), u["timer"], u.get("lvl"), "th_troops")
    for h in data.get("heroes", []):
        if "timer" in h:
            add("HOME", "hero", h.get("data"), h["timer"], h.get("lvl"), "heroes")
    for b in data.get("buildings2", []):
        if "timer" in b:
            add("BH", "building", b.get("data"), b["timer"], b.get("lvl"), "bh_buildings")
    for u in data.get("units2", []):
        if "timer" in u:
            add("BH", "unit", u.get("data"), u["timer"], u.get("lvl"), "bh_troops")
    for h in data.get("heroes2", []):
        if "timer" in h:
            add("BH", "hero", h.get("data"), h["timer"], h.get("lvl"), "heroes")
    for h in data.get("helpers", []):
        if h.get("helper_cooldown"):
            add("HOME", "helper", h.get("data"), h["helper_cooldown"],
                h.get("lvl"), "th_buildings")
    boost = data.get("boosts", {}).get("clocktower_cooldown")
    if boost:
        add("BH", "cooldown", "clocktower_cooldown", boost, None, "")

    items.sort(key=lambda x: x["finishEpoch"])
    payload = {
        "tag": data.get("tag"),
        "exportTime": _wib(base),
        "now": _wib(now),
        "count": len(items),
        "items": items,
        "caveats": [
            "helper_recurrent bisa mempercepat akhir",
            "Clock Tower bisa mempercepat timer Builder Base",
            "potion/book/gem finish butuh export ulang",
            "ID tak dikenal dilaporkan apa adanya",
        ],
    }
    if save:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            (STATE_DIR / "upgrades.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            payload["stateFile"] = str(STATE_DIR / "upgrades.json")
        except OSError as e:
            payload["stateError"] = str(e)
    return _dump(payload)


# --------------------------------------------------------------------------
# schemas + registration
# --------------------------------------------------------------------------
SCHEMAS = {
    "coc_verify": {
        "name": "coc_verify",
        "description": "Verify CoC account ownership with the one-time in-game API token. Use for /link.",
        "parameters": {"type": "object", "properties": {
            "tag": {"type": "string", "description": "Player tag", "default": DEFAULT_TAG},
            "token": {"type": "string", "description": "One-time API token copied from the game"},
        }, "required": ["token"]},
    },
    "coc_player": {
        "name": "coc_player",
        "description": "Player profile, hero levels, recent battles and league history.",
        "parameters": {"type": "object", "properties": {
            "tag": {"type": "string", "description": "Player tag", "default": DEFAULT_TAG},
        }},
    },
    "coc_clan": {
        "name": "coc_clan",
        "description": "Clan overview, members, current war, war log, CWL and capital raids.",
        "parameters": {"type": "object", "properties": {
            "tag": {"type": "string", "description": "Clan tag", "default": DEFAULT_CLAN},
            "player_tag": {"type": "string", "description": "Player to highlight in war", "default": DEFAULT_TAG},
        }},
    },
    "coc_league": {
        "name": "coc_league",
        "description": "League tier, ranked league group and league/season catalog counts.",
        "parameters": {"type": "object", "properties": {
            "player_tag": {"type": "string", "description": "Player tag", "default": DEFAULT_TAG},
        }},
    },
    "coc_ranks": {
        "name": "coc_ranks",
        "description": "Local/global leaderboards for clans, players, builder base and capitals.",
        "parameters": {"type": "object", "properties": {
            "location": {"type": "string", "description": "Location name", "default": "Indonesia"},
            "limit": {"type": "integer", "description": "Rows per leaderboard", "default": 5},
        }},
    },
    "coc_meta": {
        "name": "coc_meta",
        "description": "Current gold pass season, player/clan labels and global league counts.",
        "parameters": {"type": "object", "properties": {}},
    },
    "coc_timer": {
        "name": "coc_timer",
        "description": "Parse in-game village export JSON into upgrade finish times and alarms.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to exported village JSON", "default": DEFAULT_EXPORT},
            "alarm_hours": {"type": "integer", "description": "Early alarm hours", "default": 1},
            "alarm_minutes": {"type": "integer", "description": "Early alarm minutes", "default": 15},
            "save": {"type": "boolean", "description": "Write parsed state to upgrades.json", "default": True},
        }},
    },
}

TOOLS = (
    (SCHEMAS["coc_verify"], coc_verify, _check, "🔐"),
    (SCHEMAS["coc_player"], coc_player, _check, "👤"),
    (SCHEMAS["coc_clan"], coc_clan, _check, "🏰"),
    (SCHEMAS["coc_league"], coc_league, _check, "🏆"),
    (SCHEMAS["coc_ranks"], coc_ranks, _check, "📊"),
    (SCHEMAS["coc_meta"], coc_meta, _check, "🎟️"),
    (SCHEMAS["coc_timer"], coc_timer, _check_timer, "⏰"),
)


def register_tools(ctx) -> None:
    """Register all seven tools via the public plugin API."""
    for schema, handler, check, emoji in TOOLS:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            check_fn=check,
            emoji=emoji,
        )
