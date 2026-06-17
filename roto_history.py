"""Reconstruct weekly CUMULATIVE roto standings for the H2H 'Sandlot' league.

This league is H2H–Most-Categories, so ESPN never computes roto standings.
We rebuild them: for every completed matchup week we accumulate each team's
component stats (AB, H, OUTS, ER, ...), recompute the 12 category values from
cumulative totals (ratios done correctly from components, never averaged),
rank the 12 teams in each category (12 = best ... 1 = worst, ties share the
average), sum to roto points, and record each team's roto rank that week.

Outputs (written to ./data/):
  roto_history_long.csv    one row per team-week: cat values, cat points, total, rank
  roto_rank_by_week.csv    wide grid: team x week -> overall roto rank
  roto_points_by_week.csv  wide grid: team x week -> roto points
  roto_progression.png     bump chart of roto standing across the season

Run:
  /Users/shane/Desktop/fantasy_project/.venv/bin/python roto_history.py
"""
import os
from pathlib import Path

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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

# Your team — highlighted in the chart. Change if a different team is yours.
MY_TEAM = "In this Ohtanomy?"

# statId -> component name (verified against ESPN's own ratio values)
S = dict(AB=0, H=1, HR=5, BB=10, HBP=12, SF=13, R=20, RBI=21, SB=23,
         OUTS=34, P_H=37, P_BB=39, ER=45, K=48, W=53, QS=63, SVHD=83)

# raw counting components we accumulate week over week
ACCUM = ["AB", "H", "HR", "BB", "HBP", "SF", "R", "RBI", "SB",
         "OUTS", "P_H", "P_BB", "ER", "K", "W", "QS", "SVHD"]

# the 12 scoring categories -> (higher_is_better?)
CATS = [("R", True), ("HR", True), ("RBI", True), ("SB", True),
        ("AVG", True), ("OBP", True), ("W", True), ("K", True),
        ("QS", True), ("SVHD", True), ("ERA", False), ("WHIP", False)]


def fetch():
    r = requests.get(
        BASE, cookies=COOKIES,
        params=[("view", "mMatchupScore"), ("view", "mMatchup"), ("view", "mTeam")],
    )
    r.raise_for_status()
    return r.json()


def team_names(data):
    names = {}
    for t in data.get("teams", []):
        nm = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        names[t["id"]] = nm or f"Team {t['id']}"
    return names


def weekly_components(data):
    """{week: {team_id: {component: value}}} for COMPLETED regular-season weeks."""
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
            # a week is "final" once a scoring stat has a non-null result
            if (sbs.get("20") or {}).get("result") is None:
                continue
            comp = {k: (sbs.get(str(v)) or {}).get("score", 0.0) or 0.0
                    for k, v in S.items()}
            weeks.setdefault(wk, {})[sd["teamId"]] = comp
    return weeks


def cat_values(x):
    """12 category values from a team's cumulative component dict x."""
    ip = x["OUTS"] / 3.0
    denom_obp = x["AB"] + x["BB"] + x["HBP"] + x["SF"]
    return {
        "R": x["R"], "HR": x["HR"], "RBI": x["RBI"], "SB": x["SB"],
        "AVG": x["H"] / x["AB"] if x["AB"] else 0.0,
        "OBP": (x["H"] + x["BB"] + x["HBP"]) / denom_obp if denom_obp else 0.0,
        "W": x["W"], "K": x["K"], "QS": x["QS"], "SVHD": x["SVHD"],
        "ERA": (9 * x["ER"] / ip) if ip else float("inf"),
        "WHIP": ((x["P_H"] + x["P_BB"]) / ip) if ip else float("inf"),
    }


def build(data):
    names = team_names(data)
    weeks = weekly_components(data)
    completed = sorted(w for w, d in weeks.items() if len(d) == len(names))
    team_ids = list(names)

    cum = {tid: {c: 0.0 for c in ACCUM} for tid in team_ids}
    rows = []
    for wk in completed:
        for tid in team_ids:
            for c in ACCUM:
                cum[tid][c] += weeks[wk].get(tid, {}).get(c, 0.0)

        vals = {tid: cat_values(cum[tid]) for tid in team_ids}
        df = pd.DataFrame(vals).T  # index = team_id, columns = categories

        pts = pd.DataFrame(index=df.index)
        for cat, higher in CATS:
            pts[cat] = df[cat].rank(ascending=higher, method="average")
        total = pts.sum(axis=1)
        rank = total.rank(ascending=False, method="min").astype(int)

        for tid in team_ids:
            row = {"week": wk, "team_id": tid, "team": names[tid],
                   "roto_total": round(float(total[tid]), 1),
                   "roto_rank": int(rank[tid])}
            for cat, _ in CATS:
                v = df.loc[tid, cat]
                row[cat] = (round(float(v), 4) if v != float("inf") else None)
                row[f"pts_{cat}"] = round(float(pts.loc[tid, cat]), 1)
            rows.append(row)
    return pd.DataFrame(rows), names, completed


def plot_bump(rank_wide, completed):
    fig, ax = plt.subplots(figsize=(13, 7.5))
    palette = plt.get_cmap("tab20", len(rank_wide))
    for i, (team, row) in enumerate(rank_wide.iterrows()):
        me = team.strip() == MY_TEAM
        ax.plot(completed, [row[w] for w in completed],
                marker="o", markersize=6 if me else 4,
                linewidth=3.5 if me else 1.6,
                color="crimson" if me else palette(i),
                alpha=1.0 if me else 0.7, zorder=6 if me else 3)
        ax.text(completed[-1] + 0.15, row[completed[-1]], f" {team}",
                va="center", fontsize=9,
                color="crimson" if me else palette(i),
                fontweight="bold" if me else "normal")
    n = len(rank_wide)
    ax.set_yticks(range(1, n + 1))
    ax.set_ylim(n + 0.5, 0.5)            # invert: rank 1 on top
    ax.set_xticks(completed)
    ax.set_xlim(completed[0] - 0.3, completed[-1] + 4.2)
    ax.set_xlabel("Matchup Week")
    ax.set_ylabel("Cumulative ROTO Standing  (1 = best)")
    ax.set_title("League 'Sandlot' — Cumulative ROTO Standings by Week (2026)")
    ax.grid(True, axis="both", alpha=0.25)
    fig.tight_layout()
    out = DATA / "roto_progression.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    return out


def main():
    data = fetch()
    long_df, names, completed = build(data)

    long_df.to_csv(DATA / "roto_history_long.csv", index=False)
    last = completed[-1]
    rank_wide = long_df.pivot(index="team", columns="week", values="roto_rank")
    rank_wide = rank_wide.sort_values(by=last)
    pts_wide = long_df.pivot(index="team", columns="week", values="roto_total").loc[rank_wide.index]
    rank_wide.to_csv(DATA / "roto_rank_by_week.csv")
    pts_wide.to_csv(DATA / "roto_points_by_week.csv")
    png = plot_bump(rank_wide, completed)

    print(f"Teams: {len(names)}   Completed weeks: {completed}")
    final = long_df[long_df.week == last].sort_values("roto_rank")
    print(f"\n=== Hypothetical ROTO standings through week {last} ===")
    show = final[["roto_rank", "team", "roto_total"]].rename(
        columns={"roto_rank": "#", "roto_total": "roto_pts"})
    print(show.to_string(index=False))
    print(f"\nWrote:\n  {DATA/'roto_history_long.csv'}\n  {DATA/'roto_rank_by_week.csv'}"
          f"\n  {DATA/'roto_points_by_week.csv'}\n  {png}")


if __name__ == "__main__":
    main()
