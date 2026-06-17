"""TASK B — WEEKLY CATEGORY SPREADS.

For every completed regular-season week and team, record the weekly value of
each of the 12 scoring categories (scoreByStat[statId]['score']).

Counting cats (R,HR,RBI,SB,W,K,QS,SVHD) are weekly totals; ratio cats
(AVG,OBP,ERA,WHIP) are that week's ratio value as given by ESPN.

Outputs (written to ./data/):
  category_weekly_values.json       structured per-category per-team weekly arrays
  category_weekly_values_long.csv   tidy: week, team, abbrev, category, value

Run:
  /Users/shane/Desktop/fantasy_project/.venv/bin/python category_weekly_values.py
"""
import json
import math
import os
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

LEAGUE_ID = os.environ["LEAGUE_ID"]
SEASON = os.environ["SEASON"]
COOKIES = {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["SWID"]}
BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

# statId (str) -> category name, in the desired output order.
CAT_BY_STAT = [
    ("20", "R"), ("5", "HR"), ("21", "RBI"), ("23", "SB"),
    ("2", "AVG"), ("17", "OBP"), ("53", "W"), ("48", "K"),
    ("63", "QS"), ("83", "SVHD"), ("47", "ERA"), ("41", "WHIP"),
]
CATEGORIES = [name for _, name in CAT_BY_STAT]
LOWER_BETTER = ["ERA", "WHIP"]
RATIO_CATS = {"AVG", "OBP", "ERA", "WHIP"}


def fetch():
    r = requests.get(
        BASE, cookies=COOKIES,
        params=[("view", "mMatchupScore"), ("view", "mMatchup"), ("view", "mTeam")],
    )
    r.raise_for_status()
    return r.json()


def teams_meta(data):
    """{team_id: {"name": str, "abbrev": str}} with whitespace-stripped names."""
    meta = {}
    for t in data.get("teams", []):
        nm = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}"
        nm = (nm or f"Team {t['id']}").strip()
        meta[t["id"]] = {"name": nm, "abbrev": (t.get("abbrev") or "").strip()}
    return meta


def weekly_values(data):
    """{week: {team_id: {category: weekly_value}}} for COMPLETED reg-season weeks."""
    weeks = {}
    for g in data["schedule"]:
        if g.get("playoffTierType", "NONE") not in ("NONE", None):
            continue  # skip playoff bracket games
        if g.get("winner") not in ("HOME", "AWAY", "TIE"):
            continue  # skip in-progress / undecided weeks (e.g. the live week)
        wk = g.get("matchupPeriodId")
        for side in ("home", "away"):
            sd = g.get(side)
            if not sd:
                continue
            sbs = (sd.get("cumulativeScore") or {}).get("scoreByStat")
            if not sbs:
                continue
            # a week is "final" once R (statId 20) carries a non-null result
            if (sbs.get("20") or {}).get("result") is None:
                continue
            vals = {}
            for stat_id, name in CAT_BY_STAT:
                vals[name] = (sbs.get(stat_id) or {}).get("score", 0.0)
            weeks.setdefault(wk, {})[sd["teamId"]] = vals
    return weeks


def main():
    data = fetch()
    meta = teams_meta(data)
    weeks_raw = weekly_values(data)

    # Completed weeks = those where every team has a recorded side.
    team_ids = list(meta)
    completed = sorted(w for w, d in weeks_raw.items() if len(d) == len(team_ids))

    # Stable team ordering by display name for the "teams" list.
    ordered_ids = sorted(team_ids, key=lambda tid: meta[tid]["name"].lower())
    teams_list = [{"name": meta[tid]["name"], "abbrev": meta[tid]["abbrev"]}
                  for tid in ordered_ids]

    def fmt(cat, v):
        v = float(v or 0.0)
        if not math.isfinite(v):
            return None  # ERA/WHIP in a 0-IP week -> no data point (valid JSON null)
        if cat in RATIO_CATS:
            return round(v, 4)
        # counting category: keep as int when it's a whole number
        return int(v) if float(v).is_integer() else round(v, 4)

    # Build structured "values": category -> team name -> [week-ordered values]
    values = {cat: {} for cat in CATEGORIES}
    long_rows = []
    for cat in CATEGORIES:
        for tid in ordered_ids:
            name = meta[tid]["name"]
            arr = []
            for wk in completed:
                v = weeks_raw[wk][tid][cat]
                fv = fmt(cat, v)
                arr.append(fv)
                long_rows.append({
                    "week": wk,
                    "team": name,
                    "abbrev": meta[tid]["abbrev"],
                    "category": cat,
                    "value": fv,
                })
            values[cat][name] = arr

    out = {
        "weeks": completed,
        "lowerBetter": LOWER_BETTER,
        "categories": CATEGORIES,
        "teams": teams_list,
        "values": values,
    }

    json_path = DATA / "category_weekly_values.json"
    with open(json_path, "w") as f:
        json.dump(out, f, indent=2)

    long_df = pd.DataFrame(long_rows, columns=["week", "team", "abbrev", "category", "value"])
    long_df = long_df.sort_values(["week", "category", "team"]).reset_index(drop=True)
    csv_path = DATA / "category_weekly_values_long.csv"
    long_df.to_csv(csv_path, index=False)

    # --- stdout summary ---
    print(f"numTeams={len(teams_list)}")
    print(f"numWeeks={len(completed)}")
    print(f"numCategories={len(CATEGORIES)}")
    print(f"weeks={completed}")
    print(f"categories={CATEGORIES}")
    print(f"lowerBetter={LOWER_BETTER}")
    sample_teams = [meta[tid]["name"] for tid in ordered_ids[:2]]
    print("\nSample — HR weekly arrays:")
    for nm in sample_teams:
        print(f"  {nm}: {values['HR'][nm]}")
    # integrity check: every category/team array has length == len(weeks)
    bad = [(c, t) for c in CATEGORIES for t in values[c]
           if len(values[c][t]) != len(completed)]
    print(f"\nlength_check_failures={len(bad)}")
    print(f"\nWrote:\n  {json_path}\n  {csv_path}")


if __name__ == "__main__":
    main()
