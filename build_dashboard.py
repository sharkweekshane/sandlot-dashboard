"""Single consolidated build for the league dashboard.

Fetches ESPN once and produces ALL dashboard data (categorical records,
weekly cumulative roto standings, and weekly category spreads) into one JSON,
then injects it into dashboard_template.html to produce a self-contained
dist/index.html (no server, no separate data file needed).

This is the daily entrypoint:
    /Users/shane/Desktop/fantasy_project/.venv/bin/python build_dashboard.py

Only DERIVED STATS are written to dist/ — never the ESPN cookies. dist/ is
safe to publish; the .env (cookies) stays local/secret.
"""
import datetime
import json
import os
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import history

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)

LEAGUE_ID = os.environ["LEAGUE_ID"]
SEASON = os.environ["SEASON"]
COOKIES = {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["SWID"]}
BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

# Consistent team colors (assigned by alphabetical team name, matching the widgets)
COLORS = ["#185FA5", "#A8A69D", "#639922", "#993C1D", "#DC143C", "#378ADD",
          "#BA7517", "#1D9E75", "#7F77DD", "#5DCAA5", "#7d7c76", "#D4537E"]

CATS = ["R", "HR", "RBI", "SB", "AVG", "OBP", "W", "K", "QS", "SVHD", "ERA", "WHIP"]
LOWER_BETTER = ["ERA", "WHIP"]
# scoring category -> statId (the 12 cats that carry a WIN/LOSS/TIE result)
CAT_STAT = {"R": "20", "HR": "5", "RBI": "21", "SB": "23", "AVG": "2", "OBP": "17",
            "W": "53", "K": "48", "QS": "63", "SVHD": "83", "ERA": "47", "WHIP": "41"}
# component statIds we accumulate for correct cumulative roto values
COMP = {"AB": 0, "H": 1, "HR": 5, "BB": 10, "HBP": 12, "SF": 13, "R": 20, "RBI": 21,
        "SB": 23, "OUTS": 34, "P_H": 37, "P_BB": 39, "ER": 45, "K": 48, "W": 53,
        "QS": 63, "SVHD": 83}
ACCUM = list(COMP)
ROTO_CATS = [("R", True), ("HR", True), ("RBI", True), ("SB", True), ("AVG", True),
             ("OBP", True), ("W", True), ("K", True), ("QS", True), ("SVHD", True),
             ("ERA", False), ("WHIP", False)]


def fetch():
    r = requests.get(BASE, cookies=COOKIES,
                     params=[("view", "mMatchupScore"), ("view", "mMatchup"),
                             ("view", "mTeam"), ("view", "mStandings"), ("view", "mRoster")])
    r.raise_for_status()
    return r.json()


def team_meta(data):
    meta = {}
    for t in data.get("teams", []):
        nm = (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip()
        meta[t["id"]] = {"name": nm or f"Team {t['id']}", "abbrev": (t.get("abbrev") or "").strip()}
    names_sorted = sorted((m["name"] for m in meta.values()), key=str.lower)
    cmap = {n: COLORS[i] for i, n in enumerate(names_sorted)}
    for tid in meta:
        meta[tid]["color"] = cmap[meta[tid]["name"]]
    return meta


def completed_sides(data):
    """yield (week, teamId, scoreByStat) for completed regular-season matchup sides."""
    for g in data["schedule"]:
        if g.get("playoffTierType", "NONE") not in ("NONE", None):
            continue
        if g.get("winner") not in ("HOME", "AWAY", "TIE"):
            continue  # skip in-progress / undecided weeks
        wk = g.get("matchupPeriodId")
        for side in ("home", "away"):
            sd = g.get(side)
            sbs = (sd.get("cumulativeScore") or {}).get("scoreByStat") if sd else None
            if sbs:
                yield wk, sd["teamId"], sbs


def ratio_value(cat, x):
    if cat == "AVG":
        return x["H"] / x["AB"] if x["AB"] else 0.0
    if cat == "OBP":
        d = x["AB"] + x["BB"] + x["HBP"] + x["SF"]
        return (x["H"] + x["BB"] + x["HBP"]) / d if d else 0.0
    ip = x["OUTS"] / 3.0
    if cat == "ERA":
        return (9 * x["ER"] / ip) if ip else float("inf")
    if cat == "WHIP":
        return ((x["P_H"] + x["P_BB"]) / ip) if ip else float("inf")
    return x[cat]


def main():
    data = fetch()
    meta = team_meta(data)
    tids = list(meta)

    weeks = sorted({wk for wk, _, _ in completed_sides(data)})
    week_comp = {tid: {} for tid in tids}        # tid -> wk -> {component: val}
    records = {tid: {c: [0, 0, 0] for c in CATS} for tid in tids}
    overall = {tid: [0, 0, 0] for tid in tids}

    for wk, tid, sbs in completed_sides(data):
        week_comp[tid][wk] = {k: (sbs.get(str(v)) or {}).get("score", 0.0) or 0.0
                              for k, v in COMP.items()}
        for c in CATS:
            res = (sbs.get(CAT_STAT[c]) or {}).get("result")
            i = {"WIN": 0, "LOSS": 1, "TIE": 2}.get(res)
            if i is not None:
                records[tid][c][i] += 1
                overall[tid][i] += 1

    # ---- cumulative roto rank by week ----
    cum = {tid: {c: 0.0 for c in ACCUM} for tid in tids}
    roto_rank = {tid: [] for tid in tids}
    roto_points_last = {}
    for wk in weeks:
        for tid in tids:
            for c in ACCUM:
                cum[tid][c] += week_comp[tid].get(wk, {}).get(c, 0.0)
        vals = {tid: {c: ratio_value(c, cum[tid]) for c, _ in ROTO_CATS} for tid in tids}
        pts = {tid: 0.0 for tid in tids}
        for c, higher in ROTO_CATS:
            order = sorted(tids, key=lambda t: vals[t][c])  # ascending
            # average-rank for ties; points: higher value -> more points (12..1)
            i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and vals[order[j + 1]][c] == vals[order[i]][c]:
                    j += 1
                # ascending ranks i..j (1-based positions i+1..j+1)
                avg_pos = sum(range(i + 1, j + 2)) / (j - i + 1)
                points = avg_pos if higher else (len(tids) + 1 - avg_pos)
                for k in range(i, j + 1):
                    pts[order[k]] += points
                i = j + 1
        srt = sorted(tids, key=lambda t: -pts[t])
        rankmap = {}
        for pos, tid in enumerate(srt):
            rankmap[tid] = pos + 1 if pos == 0 or pts[tid] != pts[srt[pos - 1]] else rankmap[srt[pos - 1]]
        for tid in tids:
            roto_rank[tid].append(rankmap[tid])
        if wk == weeks[-1]:
            roto_points_last = {tid: round(pts[tid], 1) for tid in tids}

    # ---- weekly category spread values (per category -> team -> week-ordered) ----
    def fmt(cat, v):
        v = float(v or 0.0)
        if cat in ("AVG", "OBP", "ERA", "WHIP"):
            return None if v == float("inf") else round(v, 4)
        return int(v) if float(v).is_integer() else round(v, 4)

    raw = {(wk, tid): sbs for wk, tid, sbs in completed_sides(data)}
    weekly_values = {c: {} for c in CATS}
    for c in CATS:
        sid = CAT_STAT[c]
        for tid in tids:
            weekly_values[c][meta[tid]["name"]] = [
                fmt(c, (raw[(wk, tid)].get(sid) or {}).get("score", 0.0)) for wk in weeks
            ]

    # ---- assemble ----
    by_name = lambda tid: meta[tid]["name"]
    standings = sorted(tids, key=lambda t: (-overall[t][0], overall[t][1]))

    # Real H2H league standings (ESPN record + playoff seed)
    raw_teams = {t["id"]: t for t in data.get("teams", [])}
    h2h = []
    for tid in tids:
        ov = ((raw_teams.get(tid) or {}).get("record") or {}).get("overall") or {}
        slen = int(ov.get("streakLength") or 0)
        stype = ov.get("streakType") or "NONE"
        streak = (stype[0] + str(slen)) if stype in ("WIN", "LOSS") and slen else "—"
        h2h.append({
            "name": meta[tid]["name"], "rank": (raw_teams.get(tid) or {}).get("playoffSeed"),
            "w": ov.get("wins", 0), "l": ov.get("losses", 0), "t": ov.get("ties", 0),
            "pct": round(ov.get("percentage", 0.0), 3), "gb": ov.get("gamesBack", 0.0),
            "streak": streak,
        })
    h2h.sort(key=lambda x: (x["rank"] if x["rank"] is not None else 99))

    # Rostered players -> Wubbies (R+RBI) & HAGS (SB+HR) from season stats
    POS = {1: "SP", 2: "C", 3: "1B", 4: "2B", 5: "3B", 6: "SS", 7: "LF",
           8: "CF", 9: "RF", 10: "DH", 11: "RP", 12: "UT"}

    def season_stats(pl):
        for s in pl.get("stats", []):
            if (s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 0
                    and str(s.get("seasonId")) == str(SEASON)):
                return s.get("stats", {})
        return {}

    rosters = {}
    for rt in data.get("teams", []):
        plist = []
        for e in (rt.get("roster") or {}).get("entries", []):
            pl = (e.get("playerPoolEntry") or {}).get("player") or {}
            st = season_stats(pl)
            r, rbi = int(st.get("20", 0) or 0), int(st.get("21", 0) or 0)
            sb, hr = int(st.get("23", 0) or 0), int(st.get("5", 0) or 0)
            w, h = r + rbi, sb + hr
            if w > 0 or h > 0:  # hitters only (pitchers are 0/0)
                plist.append({"n": pl.get("fullName", "?"),
                              "pos": POS.get(pl.get("defaultPositionId"), "?"),
                              "w": w, "h": h})
        plist.sort(key=lambda p: -p["w"])
        rosters[meta[rt["id"]]["name"]] = plist

    now_et = datetime.datetime.now(ZoneInfo("America/New_York"))
    dash = {
        "meta": {
            "league": "Sandlot", "leagueId": int(LEAGUE_ID), "season": int(SEASON),
            "asOf": now_et.strftime("%Y-%m-%d %H:%M %Z"),
            "month": now_et.month,
            "weeks": weeks, "completedThrough": weeks[-1],
            "cats": CATS, "lowerBetter": LOWER_BETTER,
        },
        "teams": [{"name": meta[t]["name"], "abbrev": meta[t]["abbrev"], "color": meta[t]["color"]}
                  for t in standings],
        "standings": h2h,
        "rosters": rosters,
        "records": {by_name(t): {"o": overall[t], "r": {c: records[t][c] for c in CATS}}
                    for t in tids},
        "rotoRankByWeek": {by_name(t): roto_rank[t] for t in tids},
        "rotoPointsLast": {by_name(t): roto_points_last[t] for t in tids},
        "weeklyValues": weekly_values,
        "history": history.build(COOKIES, LEAGUE_ID),
    }

    (DIST / "dashboard_data.json").write_text(json.dumps(dash, separators=(",", ":")))

    # inject into template if present
    tmpl = ROOT / "dashboard_template.html"
    if tmpl.exists():
        html = tmpl.read_text().replace("/*__DASH_DATA__*/null",
                                        json.dumps(dash, separators=(",", ":")))
        (DIST / "index.html").write_text(html)
        built = "dist/index.html + dist/dashboard_data.json"
    else:
        built = "dist/dashboard_data.json (template not found yet)"

    # Copy static assets (header image, etc.) into dist/ so they deploy
    assets = ROOT / "assets"
    if assets.exists():
        for f in assets.iterdir():
            if f.is_file():
                shutil.copy(f, DIST / f.name)

    print(f"Built {built}")
    print(f"as of {dash['meta']['asOf']} | weeks 1-{weeks[-1]} | {len(standings)} teams")
    top = standings[0]
    print(f"records #1: {meta[top]['name']} {overall[top]}")
    print(f"roto rank (last wk) sample: " +
          ", ".join(f"{meta[t]['abbrev']}={roto_rank[t][-1]}" for t in standings[:3]))


if __name__ == "__main__":
    main()
