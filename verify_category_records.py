"""ADVERSARIAL VERIFICATION of Task A (categorical records).

Independently re-fetches the ESPN API and recomputes per-team cumulative
category W-L-T from scratch, then cross-checks every invariant and compares
against the builder's CSVs. Prints a JSON verdict. Does not modify any
existing files.
"""
import os
import csv
import json
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
DATA = ROOT / "data"

LEAGUE_ID = os.environ["LEAGUE_ID"]
SEASON = os.environ["SEASON"]
COOKIES = {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["SWID"]}
BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

# The 12 scoring categories (statId str -> name).
CATS = [
    ("20", "R"), ("5", "HR"), ("21", "RBI"), ("23", "SB"),
    ("2", "AVG"), ("17", "OBP"), ("53", "W"), ("48", "K"),
    ("63", "QS"), ("83", "SVHD"), ("41", "WHIP"), ("47", "ERA"),
]
CAT_NAMES = [n for _, n in CATS]
CAT_IDS = [s for s, _ in CATS]


def fetch():
    r = requests.get(
        BASE, cookies=COOKIES,
        params=[("view", "mMatchupScore"), ("view", "mMatchup"), ("view", "mTeam")],
    )
    r.raise_for_status()
    return r.json()


def team_meta(data):
    meta = {}
    for t in data.get("teams", []):
        nm = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        nm = (nm or f"Team {t['id']}").strip()
        meta[t["id"]] = {"name": nm, "abbrev": (t.get("abbrev") or "").strip()}
    return meta


def side_completed(side):
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

    discrepancies = []

    # Independent recompute.
    # cum[tid] = {cat_name: [W, L, T]}
    cum = {tid: {n: [0, 0, 0] for n in CAT_NAMES} for tid in team_ids}
    # cum_total[tid] = [W, L, T]
    cum_total = {tid: [0, 0, 0] for tid in team_ids}
    # weekly_overall[tid][wk] = [W, L, T]
    weekly_overall = {tid: {} for tid in team_ids}
    completed_weeks = set()

    for g in data.get("schedule", []):
        if g.get("playoffTierType", "NONE") not in ("NONE", None):
            continue
        home, away = g.get("home"), g.get("away")
        if not (side_completed(home) and side_completed(away)):
            continue
        wk = g.get("matchupPeriodId")
        completed_weeks.add(wk)

        # Per-side weekly tallies + per-category result for cross-check.
        side_res = {}  # 'home'/'away' -> {cat_name: result}
        side_tally = {}  # 'home'/'away' -> [W, L, T]
        for key, side in (("home", home), ("away", away)):
            tid = side["teamId"]
            sbs = side["cumulativeScore"]["scoreByStat"]
            w = l = t = 0
            cat_results = {}
            for sid, name in CATS:
                res = (sbs.get(sid) or {}).get("result")
                cat_results[name] = res
                if res == "WIN":
                    cum[tid][name][0] += 1
                    w += 1
                elif res == "LOSS":
                    cum[tid][name][1] += 1
                    l += 1
                elif res == "TIE":
                    cum[tid][name][2] += 1
                    t += 1
                else:
                    discrepancies.append(
                        f"week {wk} team {meta[tid]['name']!r} cat {name}: "
                        f"unexpected result {res!r} (expected WIN/LOSS/TIE)"
                    )
            side_res[key] = cat_results
            side_tally[key] = [w, l, t]
            cum_total[tid][0] += w
            cum_total[tid][1] += l
            cum_total[tid][2] += t
            weekly_overall[tid][wk] = [w, l, t]

            # Invariant: W + L + T == 12 for each team each week.
            if w + l + t != 12:
                discrepancies.append(
                    f"week {wk} team {meta[tid]['name']!r}: W+L+T={w+l+t} (expected 12)"
                )

        # Invariant: home wins == away losses, ties match, per category.
        hid, aid = home["teamId"], away["teamId"]
        for name in CAT_NAMES:
            hr, ar = side_res["home"][name], side_res["away"][name]
            ok_pair = (
                (hr == "WIN" and ar == "LOSS")
                or (hr == "LOSS" and ar == "WIN")
                or (hr == "TIE" and ar == "TIE")
            )
            if not ok_pair:
                discrepancies.append(
                    f"week {wk} cat {name}: home {meta[hid]['name']!r}={hr} "
                    f"vs away {meta[aid]['name']!r}={ar} (not a valid opposite pair)"
                )
        # Overall: home W == away L, home L == away W, ties equal.
        hw, hl, ht = side_tally["home"]
        aw, al, at = side_tally["away"]
        if not (hw == al and hl == aw and ht == at):
            discrepancies.append(
                f"week {wk}: home {meta[hid]['name']!r} {hw}-{hl}-{ht} vs "
                f"away {meta[aid]['name']!r} {aw}-{al}-{at} (not mirrored)"
            )

    completed = sorted(completed_weeks)

    # Cross-check: per-category cumulative sums to overall cumulative per team.
    for tid in team_ids:
        if not weekly_overall[tid]:
            continue
        sw = sum(cum[tid][n][0] for n in CAT_NAMES)
        sl = sum(cum[tid][n][1] for n in CAT_NAMES)
        st = sum(cum[tid][n][2] for n in CAT_NAMES)
        if [sw, sl, st] != cum_total[tid]:
            discrepancies.append(
                f"team {meta[tid]['name']!r}: by-cat sum {sw}-{sl}-{st} != "
                f"overall {cum_total[tid][0]}-{cum_total[tid][1]}-{cum_total[tid][2]}"
            )

    # League-wide totals.
    tot_w = sum(cum_total[tid][0] for tid in team_ids)
    tot_l = sum(cum_total[tid][1] for tid in team_ids)
    tot_t = sum(cum_total[tid][2] for tid in team_ids)
    if tot_w != tot_l:
        discrepancies.append(
            f"league total W={tot_w} != total L={tot_l}"
        )
    if tot_t % 2 != 0:
        discrepancies.append(f"league total T={tot_t} is odd (ties must pair up)")

    # ---- Compare against builder's category_records.csv ----
    name_to_tid = {meta[tid]["name"]: tid for tid in team_ids}
    csv_main = DATA / "category_records.csv"
    csv_rows_seen = 0
    if not csv_main.exists():
        discrepancies.append(f"missing CSV: {csv_main}")
    else:
        with open(csv_main, newline="") as f:
            for row in csv.DictReader(f):
                csv_rows_seen += 1
                nm = row["team"].strip()
                tid = name_to_tid.get(nm)
                if tid is None:
                    discrepancies.append(f"CSV team {nm!r} not found in API teams")
                    continue
                cW, cL, cT = int(row["W"]), int(row["L"]), int(row["T"])
                eW, eL, eT = cum_total[tid]
                if [cW, cL, cT] != [eW, eL, eT]:
                    discrepancies.append(
                        f"CSV team {nm!r}: csv {cW}-{cL}-{cT} != recompute {eW}-{eL}-{eT}"
                    )
                cgp = int(row["GP"])
                if cgp != len(completed):
                    discrepancies.append(
                        f"CSV team {nm!r}: GP={cgp} != completed weeks {len(completed)}"
                    )

    # ---- Compare against builder's category_records_by_cat.csv ----
    csv_bycat = DATA / "category_records_by_cat.csv"
    if not csv_bycat.exists():
        discrepancies.append(f"missing CSV: {csv_bycat}")
    else:
        with open(csv_bycat, newline="") as f:
            for row in csv.DictReader(f):
                nm = row["team"].strip()
                tid = name_to_tid.get(nm)
                if tid is None:
                    discrepancies.append(f"by-cat CSV team {nm!r} not found in API teams")
                    continue
                sw = sl = st = 0
                for n in CAT_NAMES:
                    cell = row[n]
                    cw, cl, ct = (int(x) for x in cell.split("-"))
                    ew, el, et = cum[tid][n]
                    if [cw, cl, ct] != [ew, el, et]:
                        discrepancies.append(
                            f"by-cat CSV team {nm!r} cat {n}: csv {cell} != "
                            f"recompute {ew}-{el}-{et}"
                        )
                    sw += cw; sl += cl; st += ct
                # Internal: 12 per-category cells sum to the Overall cell.
                ow, ol, ot = (int(x) for x in row["Overall"].split("-"))
                if [sw, sl, st] != [ow, ol, ot]:
                    discrepancies.append(
                        f"by-cat CSV team {nm!r}: cat-sum {sw}-{sl}-{st} != "
                        f"Overall {ow}-{ol}-{ot}"
                    )
                # And Overall must match recompute total.
                eW, eL, eT = cum_total[tid]
                if [ow, ol, ot] != [eW, eL, eT]:
                    discrepancies.append(
                        f"by-cat CSV team {nm!r}: Overall {ow}-{ol}-{ot} != "
                        f"recompute {eW}-{eL}-{eT}"
                    )

    # ---- Compare against builder's reported JSON ----
    reported = {
        "3PeatLoading...": (84, 51, 9),
        "In this Ohtanomy?": (83, 51, 10),
        "Ladies Love The Busch": (76, 59, 9),
        "Right again  Ben": (75, 56, 13),
        "Mangnum Johnston": (72, 56, 16),
        "Shane Sucks Butt": (70, 66, 8),
        "Cruz Control": (70, 61, 13),
        "Ep-Skenes Island": (63, 74, 7),
        "Tie Cobb": (58, 77, 9),
        "King of the North": (55, 83, 6),
        "Schlitt's Creek": (49, 82, 13),
        "Bye Week": (47, 86, 11),
    }
    for nm, (rW, rL, rT) in reported.items():
        tid = name_to_tid.get(nm.strip())
        if tid is None:
            discrepancies.append(f"reported team {nm!r} not found in API teams")
            continue
        eW, eL, eT = cum_total[tid]
        if [rW, rL, rT] != [eW, eL, eT]:
            discrepancies.append(
                f"reported JSON team {nm!r}: {rW}-{rL}-{rT} != recompute {eW}-{eL}-{eT}"
            )

    # ---- Build the recomputed standings table for the report ----
    standings = sorted(
        (
            {
                "name": meta[tid]["name"],
                "abbrev": meta[tid]["abbrev"],
                "W": cum_total[tid][0],
                "L": cum_total[tid][1],
                "T": cum_total[tid][2],
            }
            for tid in team_ids
            if weekly_overall[tid]
        ),
        key=lambda d: (-d["W"], -(d["W"] / (d["W"] + d["L"] + d["T"]))),
    )

    verdict = {
        "ok": len(discrepancies) == 0,
        "completedWeeks": completed,
        "numTeams": sum(1 for tid in team_ids if weekly_overall[tid]),
        "leagueTotals": {"W": tot_w, "L": tot_l, "T": tot_t},
        "csvRowsMain": csv_rows_seen,
        "standings": standings,
        "discrepancies": discrepancies,
    }
    print(json.dumps(verdict, indent=2))


if __name__ == "__main__":
    main()
