"""TASK A — CATEGORICAL RECORDS for the H2H 'Most Categories' league.

Each completed week, every team plays ONE opponent across the 12 scoring
categories, so it earns a weekly category record (e.g. won 8 / lost 4 => 8-4-0).
We accumulate those category outcomes across all completed regular-season weeks.

Outputs (written to ./data/):
  category_records.csv          one row per team: season category W-L-T, GP,
                                cat_win_pct, and per-week "W-L-T" strings.
  category_records_by_cat.csv   one row per team: a "W-L-T" cell per category
                                plus an "Overall" column.

Run:
  /Users/shane/Desktop/fantasy_project/.venv/bin/python category_records.py
"""
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

# The 12 scoring categories: statId (str) -> display name, in a fixed order.
CATS = [
    ("20", "R"), ("5", "HR"), ("21", "RBI"), ("23", "SB"),
    ("2", "AVG"), ("17", "OBP"), ("53", "W"), ("48", "K"),
    ("63", "QS"), ("83", "SVHD"), ("41", "WHIP"), ("47", "ERA"),
]
CAT_NAMES = [name for _, name in CATS]


def fetch():
    r = requests.get(
        BASE, cookies=COOKIES,
        params=[("view", "mMatchupScore"), ("view", "mMatchup"), ("view", "mTeam")],
    )
    r.raise_for_status()
    return r.json()


def team_meta(data):
    """team_id -> (display name, abbrev)."""
    meta = {}
    for t in data.get("teams", []):
        nm = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        nm = (nm or f"Team {t['id']}").strip()
        meta[t["id"]] = (nm, (t.get("abbrev") or "").strip())
    return meta


def is_completed_side(side):
    """A matchup side counts iff its R category has a non-null result."""
    if not side:
        return False
    sbs = (side.get("cumulativeScore") or {}).get("scoreByStat")
    if not sbs:
        return False
    return (sbs.get("20") or {}).get("result") is not None


def main():
    data = fetch()
    meta = team_meta(data)
    team_ids = list(meta)

    # team_id -> {category_name -> [W, L, T]} season-long category record
    by_cat = {tid: {name: [0, 0, 0] for name in CAT_NAMES} for tid in team_ids}
    # team_id -> {week -> [W, L, T]} weekly overall (across the 12 cats) record
    weekly = {tid: {} for tid in team_ids}
    completed_weeks = set()

    for g in data.get("schedule", []):
        if g.get("playoffTierType", "NONE") not in ("NONE", None):
            continue  # skip playoff bracket games
        if g.get("winner") not in ("HOME", "AWAY", "TIE"):
            continue  # skip in-progress / undecided weeks (e.g. the live week)
        home, away = g.get("home"), g.get("away")
        # Both sides must be present and completed for this matchup to count.
        if not (is_completed_side(home) and is_completed_side(away)):
            continue
        wk = g.get("matchupPeriodId")
        completed_weeks.add(wk)

        for side in (home, away):
            tid = side["teamId"]
            sbs = side["cumulativeScore"]["scoreByStat"]
            w = l = t = 0
            for sid, name in CATS:
                res = (sbs.get(sid) or {}).get("result")
                if res == "WIN":
                    by_cat[tid][name][0] += 1
                    w += 1
                elif res == "LOSS":
                    by_cat[tid][name][1] += 1
                    l += 1
                elif res == "TIE":
                    by_cat[tid][name][2] += 1
                    t += 1
                # null/other results are ignored (shouldn't happen for the 12)
            weekly[tid][wk] = [w, l, t]

    completed = sorted(completed_weeks)
    gp = len(completed)

    # ---- Build category_records.csv (one row per team, season overall) ----
    rows = []
    for tid in team_ids:
        if not weekly[tid]:
            continue  # team with no completed matchups (defensive)
        name, abbrev = meta[tid]
        W = sum(weekly[tid][wk][0] for wk in completed if wk in weekly[tid])
        L = sum(weekly[tid][wk][1] for wk in completed if wk in weekly[tid])
        T = sum(weekly[tid][wk][2] for wk in completed if wk in weekly[tid])
        denom = W + L + T
        pct = round(W / denom, 3) if denom else 0.0
        weekly_str = ";".join(
            "{}-{}-{}".format(*weekly[tid][wk]) for wk in completed if wk in weekly[tid]
        )
        rows.append({
            "team": name, "abbrev": abbrev,
            "W": W, "L": L, "T": T, "GP": gp,
            "cat_win_pct": pct, "weekly": weekly_str,
        })

    df = pd.DataFrame(rows).sort_values(
        by=["W", "cat_win_pct"], ascending=[False, False]
    ).reset_index(drop=True)
    df.to_csv(DATA / "category_records.csv", index=False)

    # ---- Build category_records_by_cat.csv (per-category breakdown) ----
    bycat_rows = []
    for tid in team_ids:
        if not weekly[tid]:
            continue
        name, abbrev = meta[tid]
        row = {"team": name, "abbrev": abbrev}
        ow = ol = ot = 0
        for cname in CAT_NAMES:
            w, l, t = by_cat[tid][cname]
            row[cname] = f"{w}-{l}-{t}"
            ow += w; ol += l; ot += t
        row["Overall"] = f"{ow}-{ol}-{ot}"
        bycat_rows.append((tid, ow, row))

    # Order rows to match the main standings sort (W desc, win_pct desc).
    order = {name: i for i, name in enumerate(df["team"].tolist())}
    bycat_rows.sort(key=lambda x: order.get(x[2]["team"], 1e9))
    bycat_df = pd.DataFrame([r for _, _, r in bycat_rows])
    cols = ["team", "abbrev"] + CAT_NAMES + ["Overall"]
    bycat_df = bycat_df[cols]
    bycat_df.to_csv(DATA / "category_records_by_cat.csv", index=False)

    # ---- Sanity check: league-wide wins must equal league-wide losses ----
    total_W = int(df["W"].sum())
    total_L = int(df["L"].sum())
    total_T = int(df["T"].sum())
    league_equal = total_W == total_L

    # ---- Print standings to stdout ----
    print(f"Teams: {len(df)}   Completed weeks ({gp}): {completed}")
    print(f"\n=== Categorical records through week {completed[-1]} ===")
    print(f"{'#':>2}  {'Team':<26} {'W-L-T':>10}  {'win%':>6}")
    for i, r in enumerate(df.itertuples(index=False), 1):
        wlt = f"{r.W}-{r.L}-{r.T}"
        print(f"{i:>2}  {r.team:<26} {wlt:>10}  {r.cat_win_pct:>6.3f}")
    print(f"\nLeague totals: W={total_W}  L={total_L}  T={total_T}  "
          f"(wins==losses: {league_equal})")
    print(f"\nWrote:\n  {DATA/'category_records.csv'}"
          f"\n  {DATA/'category_records_by_cat.csv'}")

    return df, completed, league_equal


if __name__ == "__main__":
    main()
