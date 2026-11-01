"""Fetch free-tier data from football-data.org and write clean JSON files to data/.

Run:  FOOTBALL_DATA_KEY=xxxx python3 scripts/fetch_data.py [PD PL ...]
Only the Python standard library is used. A failed league keeps its old file.
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://api.football-data.org/v4"
# Free-tier competitions that exist on Statshub
LEAGUES = ["PD", "PL", "ELC", "FL1", "SA", "BL1", "PPL", "DED", "BSA"]
PAUSE = 7  # seconds between requests; the free limit is 10 per minute


def get(path, key, retries=2):
    req = urllib.request.Request(BASE + path, headers={"X-Auth-Token": key})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(65)  # rate limit hit: wait out the minute
                continue
            raise
    raise RuntimeError("unreachable")


def team_ref(t):
    return {"id": t["id"], "name": t.get("name"), "short": t.get("shortName"),
            "tla": t.get("tla"), "crest": t.get("crest")}


def pair(block):
    if not block or block.get("home") is None or block.get("away") is None:
        return None
    return [block["home"], block["away"]]


def normalize_matches(raw):
    out = []
    for m in raw.get("matches", []):
        s = m.get("score") or {}
        out.append({
            "id": m["id"], "matchday": m.get("matchday"), "stage": m.get("stage"),
            "utc": m.get("utcDate"), "status": m.get("status"),
            "home": team_ref(m["homeTeam"]), "away": team_ref(m["awayTeam"]),
            "ft": pair(s.get("fullTime")), "ht": pair(s.get("halfTime")),
        })
    return out


def form_strings(matches, n=5):
    """Last n finished results per team, oldest to newest, e.g. 'WWDLW'."""
    done = sorted((m for m in matches if m["status"] == "FINISHED" and m["ft"]),
                  key=lambda m: m["utc"])
    res = {}
    for m in done:
        h, a = m["ft"]
        for side, mine, theirs in (("home", h, a), ("away", a, h)):
            r = "W" if mine > theirs else "L" if mine < theirs else "D"
            res.setdefault(m[side]["id"], []).append(r)
    return {tid: "".join(r[-n:]) for tid, r in res.items()}


def normalize_standings(raw, forms):
    total = next((s for s in raw.get("standings", []) if s.get("type") == "TOTAL"), None)
    if not total:
        return []
    rows = []
    for r in total["table"]:
        t = r["team"]
        rows.append({
            "pos": r["position"], **team_ref(t), "played": r["playedGames"],
            "won": r["won"], "draw": r["draw"], "lost": r["lost"],
            "gf": r["goalsFor"], "ga": r["goalsAgainst"], "gd": r["goalDifference"],
            "points": r["points"], "form": r.get("form") or forms.get(t["id"], ""),
        })
    return rows


def normalize_teams(raw):
    out = []
    for t in raw.get("teams", []):
        coach = t.get("coach") or {}
        out.append({
            **team_ref(t), "venue": t.get("venue"), "founded": t.get("founded"),
            "colors": t.get("clubColors"), "website": t.get("website"),
            "coach": {"name": coach.get("name"), "nationality": coach.get("nationality"),
                      "dob": coach.get("dateOfBirth")} if coach else None,
            "squad": [{"id": p["id"], "name": p["name"],
                       "position": None if p.get("position") in (None, "null") else p["position"],
                       "dob": p.get("dateOfBirth"), "nationality": p.get("nationality")}
                      for p in t.get("squad", [])],
        })
    return out


def normalize_scorers(raw):
    return [{"player_id": s["player"]["id"], "name": s["player"]["name"],
             "nationality": s["player"].get("nationality"), "dob": s["player"].get("dateOfBirth"),
             "section": s["player"].get("section"), "shirt": s["player"].get("shirtNumber"),
             "team_id": s["team"]["id"], "team": s["team"].get("shortName") or s["team"].get("name"),
             "played": s.get("playedMatches"), "goals": s.get("goals"),
             "assists": s.get("assists"), "penalties": s.get("penalties")}
            for s in raw.get("scorers", [])]


def build(code, standings_raw, matches_raw, teams_raw, scorers_raw):
    matches = normalize_matches(matches_raw)
    comp, season = standings_raw["competition"], standings_raw["season"]
    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "competition": {"code": code, "name": comp["name"], "emblem": comp.get("emblem")},
        "season": {"start": season["startDate"], "end": season["endDate"],
                   "matchday": season.get("currentMatchday")},
        "standings": normalize_standings(standings_raw, form_strings(matches)),
        "matches": matches,
        "teams": normalize_teams(teams_raw),
        "scorers": normalize_scorers(scorers_raw),
    }


def main():
    key = os.environ.get("FOOTBALL_DATA_KEY")
    if not key:
        sys.exit("Set FOOTBALL_DATA_KEY")
    codes = sys.argv[1:] or LEAGUES
    os.makedirs("data", exist_ok=True)
    failed = []
    for code in codes:
        try:
            parts = {}
            for name, path in (("standings", f"/competitions/{code}/standings"),
                               ("matches", f"/competitions/{code}/matches"),
                               ("teams", f"/competitions/{code}/teams"),
                               ("scorers", f"/competitions/{code}/scorers?limit=50")):
                parts[name] = get(path, key)
                time.sleep(PAUSE)
            data = build(code, parts["standings"], parts["matches"], parts["teams"], parts["scorers"])
            with open(f"data/{code}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            print(f"{code}: ok ({len(data['standings'])} teams, {len(data['matches'])} matches)")
        except Exception as e:  # keep the old file, carry on with the next league
            failed.append(code)
            print(f"{code}: FAILED - {e}")
    with open("data/index.json", "w") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "leagues": [c for c in codes if c not in failed]}, f)
    if failed:
        print("Failed:", ", ".join(failed))


if __name__ == "__main__":
    main()
