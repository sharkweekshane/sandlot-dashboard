"""All-time league history (2021-present): manager-vs-manager H2H records and
per-season weekly roto standings — regular season only, managers deduped.

Managers are tracked as PEOPLE across team slots/seasons. Two people created a
second ESPN account due to login issues; CANON merges those duplicate accounts.

Exposed as build(cookies, league_id) -> dict, embedded in the dashboard as
DASH.history. Categories have been the same 12 every year, so roto is comparable.
"""
import requests

SEASONS = list(range(2016, 2027))  # all available years (2015 and earlier are 404)
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb"

# Duplicate-account merges (by ESPN displayName) -> canonical manager name.
CANON = {
    "Iphonelover7": "Matt Klayman", "espnfan82069820": "Matt Klayman",
    "les fishers": "Michael Fisher", "espnfan3508595072": "Michael Fisher",
}

# Per-season corrections: (year, name) -> who really ran that team that year.
# The name key may be the ESPN displayName OR the resolved person name (handy
# when the account's real name is already correct every year except one).
SEASON_OVERRIDE = {
    (2017, "shane_ubc"): "Josh Cagan",        # the shane_ubc account was Josh in 2017
}

# Teams whose ESPN member record was deleted (so they resolve to None / "vacant")
# but which a real person actually ran — reassigned by (year, teamId). Andrew
# "Flookey" Schmelling's accounts are gone, leaving his 2016 (3rd place) and 2023
# (replaced Mitch L) teams blank without this.
TEAM_OVERRIDE = {
    (2016, 5): "Andrew Schmelling",
    (2023, 12): "Andrew Schmelling",
}

# Championships won before the league we have on record (pre-2016, a different
# league — no bracket or category data exists). Added to the playoff championship
# count ONLY; appearances/records/roto are untouched. Shane won 2013 and 2015.
EXTRA_TITLES = {"Shane Simon": [2013, 2015]}

# component statId map (same as the main build)
COMP = {"AB": 0, "H": 1, "HR": 5, "BB": 10, "HBP": 12, "SF": 13, "R": 20,
        "RBI": 21, "SB": 23, "OUTS": 34, "P_H": 37, "P_BB": 39, "ER": 45,
        "K": 48, "W": 53, "QS": 63, "SV": 57, "SVHD": 83}
ACCUM = list(COMP)
# 11 categories used every season; the saves cat is SV (2016-19) or SVHD (2020+).
ROTO_BASE = [("R", True), ("HR", True), ("RBI", True), ("SB", True), ("AVG", True),
             ("OBP", True), ("W", True), ("K", True), ("QS", True),
             ("ERA", False), ("WHIP", False)]


def roto_cats(data):
    sc = (data.get("settings") or {}).get("scoringSettings") or {}
    sids = {int(i["statId"]) for i in sc.get("scoringItems", [])}
    return ROTO_BASE + [("SVHD", True) if 83 in sids else ("SV", True)]

PALETTE = ["#185FA5", "#1D9E75", "#BA7517", "#639922", "#D4537E", "#7F77DD",
           "#378ADD", "#993C1D", "#5DCAA5", "#888780", "#7d7c76", "#c0392b",
           "#16a085", "#8e44ad", "#2c7fb8", "#d68910", "#27ae60", "#a93226"]
ME = "Shane Simon"


def fetch(year, cookies, lid):
    if year == 2026:
        url = f"{BASE}/seasons/{year}/segments/0/leagues/{lid}"
        params = [("view", "mMatchupScore"), ("view", "mMatchup"),
                  ("view", "mTeam"), ("view", "mSettings")]
    else:
        url = f"{BASE}/leagueHistory/{lid}"
        params = [("seasonId", year), ("view", "mMatchupScore"), ("view", "mMatchup"),
                  ("view", "mTeam"), ("view", "mSettings")]
    r = requests.get(url, cookies=cookies, params=params)
    r.raise_for_status()
    d = r.json()
    return d[0] if isinstance(d, list) else d


def clean(fn, ln):
    return " ".join(f"{fn or ''} {ln or ''}".split()).title()


def managers(data, year):
    """teamId -> canonical manager name (or None if vacant)."""
    members = {m["id"]: m for m in data.get("members", [])}
    out = {}
    for t in data.get("teams", []):
        if (year, t["id"]) in TEAM_OVERRIDE:
            out[t["id"]] = TEAM_OVERRIDE[(year, t["id"])]
            continue
        owners = t.get("owners") or []
        pid = t.get("primaryOwner") or (owners[0] if owners else None)
        m = members.get(pid)
        if m:
            dn = m.get("displayName")
            resolved = CANON.get(dn) or clean(m.get("firstName"), m.get("lastName"))
            out[t["id"]] = (SEASON_OVERRIDE.get((year, dn))
                            or SEASON_OVERRIDE.get((year, resolved))
                            or resolved)
        else:
            out[t["id"]] = None
    return out


def ratio(cat, x):
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


def reg_weeks(data):
    return int(((data.get("settings") or {}).get("scheduleSettings") or {}).get("matchupPeriodCount") or 22)


def completed_sides(data, rw):
    """yield (week, teamId, scoreByStat) for completed regular-season sides."""
    for g in data["schedule"]:
        if g.get("playoffTierType", "NONE") not in ("NONE", None):
            continue
        if g.get("winner") not in ("HOME", "AWAY", "TIE"):
            continue
        wk = g.get("matchupPeriodId")
        if wk is None or wk > rw:
            continue
        for side in ("home", "away"):
            sd = g.get(side)
            sbs = (sd.get("cumulativeScore") or {}).get("scoreByStat") if sd else None
            if sbs and (sbs.get("20") or {}).get("result") is not None:
                yield wk, sd["teamId"], sbs


def season_roto(data, rw, mgr, rcats):
    week_comp = {}
    for wk, tid, sbs in completed_sides(data, rw):
        week_comp.setdefault(tid, {})[wk] = {
            k: (sbs.get(str(v)) or {}).get("score", 0.0) or 0.0 for k, v in COMP.items()}
    tids = [t["id"] for t in data["teams"]]
    weeks = sorted({wk for t in week_comp for wk in week_comp[t]})
    weeks = [w for w in weeks if all(w in week_comp.get(t, {}) for t in tids)]
    if not weeks:
        return None
    cum = {t: {c: 0.0 for c in ACCUM} for t in tids}
    rbw = {t: [] for t in tids}
    pts_last = {}
    for wk in weeks:
        for t in tids:
            for c in ACCUM:
                cum[t][c] += week_comp[t][wk][c]
        vals = {t: {cat: ratio(cat, cum[t]) for cat, _ in rcats} for t in tids}
        pts = {t: 0.0 for t in tids}
        for cat, higher in rcats:
            order = sorted(tids, key=lambda t: vals[t][cat])
            i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and vals[order[j + 1]][cat] == vals[order[i]][cat]:
                    j += 1
                avg = sum(range(i + 1, j + 2)) / (j - i + 1)
                p = avg if higher else (len(tids) + 1 - avg)
                for k in range(i, j + 1):
                    pts[order[k]] += p
                i = j + 1
        srt = sorted(tids, key=lambda t: -pts[t])
        rk = {}
        for pos, t in enumerate(srt):
            rk[t] = pos + 1 if pos == 0 or pts[t] != pts[srt[pos - 1]] else rk[srt[pos - 1]]
        for t in tids:
            rbw[t].append(rk[t])
        if wk == weeks[-1]:
            pts_last = {t: round(pts[t], 1) for t in tids}
    names = {t: data_team_name(data, t) for t in tids}
    label = {t: (mgr.get(t) or names[t]) for t in tids}
    return {"weeks": weeks,
            "byWeek": {label[t]: rbw[t] for t in tids},
            "final": {label[t]: pts_last[t] for t in tids}}


def data_team_name(data, tid):
    for t in data.get("teams", []):
        if t["id"] == tid:
            return (t.get("name") or f"{t.get('location','')} {t.get('nickname','')}").strip() or f"Team {tid}"
    return f"Team {tid}"


def build(cookies, league_id):
    h2h = {}          # mgrA -> mgrB -> [W,L,T]  (all-time)
    h2h_year = {}     # season(str) -> mgrA -> mgrB -> [W,L,T]  (per-season, for the slider)
    seen = {}         # manager -> set of seasons
    roto = {}
    roto_pts = {}     # manager -> [normalized roto points per season played]
    roto_fin = {}     # manager -> {season(str): final/current roto rank}
    roto_titles = {}  # manager -> count of completed-season roto championships
    playoffs = {}     # season(str) -> {champion, byes:[mgr], games:[...]} (winners bracket)
    pchamp, pbye, pappear = {}, {}, {}  # manager -> championships / byes / playoff appearances
    p_h2h = {}        # manager -> mgr -> [W,L,T] in playoff (winners-bracket) games

    def add(a, b, res):
        i = {"W": 0, "L": 1, "T": 2}[res]
        h2h.setdefault(a, {}).setdefault(b, [0, 0, 0])[i] += 1
        h2h_year.setdefault(str(yr), {}).setdefault(a, {}).setdefault(b, [0, 0, 0])[i] += 1

    def padd(a, b, res):
        p_h2h.setdefault(a, {}).setdefault(b, [0, 0, 0])[{"W": 0, "L": 1, "T": 2}[res]] += 1

    for yr in SEASONS:
        try:
            data = fetch(yr, cookies, league_id)
        except Exception:
            continue
        if not data.get("teams"):
            continue
        rw = reg_weeks(data)
        mgr = managers(data, yr)
        for t, name in mgr.items():
            if name:
                seen.setdefault(name, set()).add(yr)
        # H2H from completed reg-season matchups
        for g in data["schedule"]:
            if g.get("playoffTierType", "NONE") not in ("NONE", None):
                continue
            if g.get("winner") not in ("HOME", "AWAY", "TIE"):
                continue
            if (g.get("matchupPeriodId") or 99) > rw:
                continue
            hm = mgr.get((g.get("home") or {}).get("teamId"))
            am = mgr.get((g.get("away") or {}).get("teamId"))
            if not hm or not am or hm == am:
                continue
            w = g["winner"]
            if w == "HOME":
                add(hm, am, "W"); add(am, hm, "L")
            elif w == "AWAY":
                add(am, hm, "W"); add(hm, am, "L")
            else:
                add(hm, am, "T"); add(am, hm, "T")
        rt = season_roto(data, rw, mgr, roto_cats(data))
        if rt:
            roto[str(yr)] = rt
            ranks = {nm: rt["byWeek"][nm][-1] for nm in rt["byWeek"]}
            N = len(ranks)
            done = len(rt["weeks"]) >= rw  # season fully played (vs in-progress)
            for nm, rk in ranks.items():
                if nm in seen:  # real managers only (skip vacant team labels)
                    roto_pts.setdefault(nm, []).append(12 - (rk - 1) * 11 / (N - 1) if N > 1 else 12)
                    roto_fin.setdefault(nm, {})[str(yr)] = rk
                    if done and rk == 1:
                        roto_titles[nm] = roto_titles.get(nm, 0) + 1

        # Playoffs — winners bracket (the championship bracket); completed years only
        seed = {t["id"]: t.get("playoffSeed") for t in data["teams"]}
        wb = [g for g in data["schedule"] if g.get("playoffTierType") == "WINNERS_BRACKET"]
        champ = next((mgr.get(t["id"]) for t in data["teams"]
                      if t.get("rankCalculatedFinal") == 1), None)
        if wb and champ:
            r0 = min(g.get("matchupPeriodId") for g in wb)
            games, byes_yr, appear_yr = [], [], set()
            for g in wb:
                ht = (g.get("home") or {}).get("teamId")
                at = (g.get("away") or {}).get("teamId")
                hm, am = mgr.get(ht), mgr.get(at)
                if ht is None or at is None:  # bye placeholder slot
                    ptid = ht if ht is not None else at
                    bm = mgr.get(ptid)
                    if bm:
                        byes_yr.append({"s": seed.get(ptid), "m": bm})
                        appear_yr.add(bm)
                    continue
                if hm:
                    appear_yr.add(hm)
                if am:
                    appear_yr.add(am)
                w = g.get("winner")
                games.append({
                    "r": g.get("matchupPeriodId") - r0 + 1,
                    "hs": seed.get(ht), "hm": hm,
                    "hw": ((g.get("home") or {}).get("cumulativeScore") or {}).get("wins"),
                    "as": seed.get(at), "am": am,
                    "aw": ((g.get("away") or {}).get("cumulativeScore") or {}).get("wins"),
                    "win": w})
                if w in ("HOME", "AWAY", "TIE") and hm and am and hm != am:
                    if w == "HOME":
                        padd(hm, am, "W"); padd(am, hm, "L")
                    elif w == "AWAY":
                        padd(am, hm, "W"); padd(hm, am, "L")
                    else:
                        padd(hm, am, "T"); padd(am, hm, "T")
            games.sort(key=lambda x: (x["r"], x["hs"] if x["hs"] is not None else 99))
            playoffs[str(yr)] = {"champion": champ, "byes": byes_yr, "games": games}
            pchamp[champ] = pchamp.get(champ, 0) + 1
            for b in byes_yr:
                pbye[b["m"]] = pbye.get(b["m"], 0) + 1
            for am in appear_yr:
                pappear[am] = pappear.get(am, 0) + 1

    # manager summaries + colors
    mlist = sorted(seen)
    totals = {}
    for a in mlist:
        W = sum(v[0] for v in h2h.get(a, {}).values())
        L = sum(v[1] for v in h2h.get(a, {}).values())
        T = sum(v[2] for v in h2h.get(a, {}).values())
        totals[a] = (W, L, T)
    color = {}
    i = 0
    for a in mlist:
        if a == ME:
            color[a] = "#DC143C"
        else:
            color[a] = PALETTE[i % len(PALETTE)]
            i += 1
    managers_out = [{
        "name": a, "color": color[a],
        "seasons": sorted(seen[a]),
        "W": totals[a][0], "L": totals[a][1], "T": totals[a][2],
    } for a in mlist]
    managers_out.sort(key=lambda m: (-((m["W"] + 0.5 * m["T"]) / ((m["W"] + m["L"] + m["T"]) or 1)), -m["W"]))

    roto_rankings = [{
        "name": nm, "avg": round(sum(p) / len(p), 2), "seasons": len(p),
        "titles": roto_titles.get(nm, 0),
        "finishes": roto_fin[nm],
    } for nm, p in roto_pts.items()]
    roto_rankings.sort(key=lambda r: (-r["avg"], -r["seasons"]))

    for nm, yrs in EXTRA_TITLES.items():
        pchamp[nm] = pchamp.get(nm, 0) + len(yrs)

    pset = set(pappear) | set(pchamp)
    playoff_summary = []
    for nm in pset:
        rec = p_h2h.get(nm, {})
        playoff_summary.append({
            "name": nm, "champs": pchamp.get(nm, 0), "byes": pbye.get(nm, 0),
            "app": pappear.get(nm, 0), "extra": EXTRA_TITLES.get(nm, []),
            "W": sum(v[0] for v in rec.values()),
            "L": sum(v[1] for v in rec.values()),
            "T": sum(v[2] for v in rec.values()),
        })
    playoff_summary.sort(key=lambda m: (-m["champs"], -m["app"], -(m["W"] - m["L"])))

    return {
        "seasons": [y for y in SEASONS if str(y) in roto] or SEASONS,
        "managers": managers_out,
        "h2h": h2h,
        "h2hByYear": h2h_year,
        "roto": roto,
        "rotoRankings": roto_rankings,
        "playoffs": playoffs,
        "playoffSummary": playoff_summary,
        "playoffH2H": p_h2h,
    }
