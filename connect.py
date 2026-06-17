"""Connect to the ESPN fantasy baseball league and run a quick auth test.

Usage:
    /Users/shane/Desktop/fantasy_project/.venv/bin/python connect.py
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from espn_api.baseball import League

# Load credentials from the local .env (never committed)
load_dotenv(Path(__file__).parent / ".env")


def get_league() -> League:
    """Return an authenticated League object built from .env settings."""
    return League(
        league_id=int(os.environ["LEAGUE_ID"]),
        year=int(os.environ["SEASON"]),
        espn_s2=os.environ.get("ESPN_S2"),
        swid=os.environ.get("SWID"),
    )


if __name__ == "__main__":
    league = get_league()
    print(f"✅ Connected to: {league}")
    print(f"Found {len(league.teams)} teams:")
    for team in league.teams:
        print(f"  - {team.team_name}")
