# Sandlot Dashboard — hosting & daily refresh

The dashboard is one self-contained page (`dist/index.html`) built by `build_dashboard.py`.
GitHub builds it nightly and serves it on GitHub Pages, so your leaguemates just get a URL.

```
build_dashboard.py  ──>  dist/index.html  ──>  GitHub Pages (public URL)
   needs cookies              derived stats        what people see
   (GitHub SECRETS)           (no credentials)
```

**Security:** your ESPN cookies live ONLY as encrypted GitHub Action **secrets**. They are never
committed (`.env` is gitignored) and never appear in `dist/`. Only derived stats are published.

---

## One-time setup (~5 minutes)

### 1. Create the repo and push
A **public** repo is simplest (GitHub Pages is free on public repos; the code holds no secrets).

With the GitHub CLI:
```bash
cd /Users/shane/Desktop/fantasy_project
gh repo create sandlot-dashboard --public --source=. --remote=origin --push
```
…or create an empty repo on github.com and:
```bash
git remote add origin https://github.com/<you>/sandlot-dashboard.git
git push -u origin main
```

### 2. Add your two ESPN cookies as secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|------|-------|
| `ESPN_S2` | your `espn_s2` cookie (the long `%`-encoded string) |
| `SWID` | your `SWID` cookie, **including** the `{ }` braces |

(`LEAGUE_ID` and `SEASON` are non-secret and already set in the workflow — bump `SEASON` each year.)

### 3. Turn on Pages
Repo → **Settings → Pages → Build and deployment → Source: GitHub Actions**.

### 4. Run it once
Repo → **Actions → "Build & deploy dashboard" → Run workflow**. When it's green, your dashboard is at:
```
https://<your-username>.github.io/sandlot-dashboard/
```
Share that link with the league. 🎉

---

## Recurring schedule
The workflow runs automatically every night at **07:00 UTC (~3am US Eastern)** via the `cron` in
`.github/workflows/build-dashboard.yml`. It also rebuilds on every push to `main`, and you can
trigger it any time from the Actions tab. Change the time by editing the `cron:` line.

## Maintenance
- **Cookie expired?** If the nightly job starts failing with a 401, your `espn_s2` rotated (usually
  after a logout or password change). Grab a fresh cookie and update the `ESPN_S2` secret.
- **New season?** Update `SEASON` in the workflow (and re-grab cookies if needed).
- **Local preview:** `python build_dashboard.py` then open `dist/index.html`.
