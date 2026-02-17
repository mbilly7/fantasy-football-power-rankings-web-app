import os
import requests
from flask import Flask, render_template
from dotenv import load_dotenv
from power_rankings_generator import calculate_power_rankings, get_current_week
from database import get_latest_rankings, save_rankings

app = Flask(__name__)


def format_last_updated(timestamp):
    if not timestamp:
        return None
    if timestamp.tzinfo:
        return timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")

def fetch_espn_league_data(league_id, league_season, swid=None, espn_s2=None, timeout=10):
    """
    Fetch league JSON from ESPN.

    Args:
        league_id (str|int): ESPN league ID.
        league_season (str|int): Season year.
        swid (str, optional): SWID cookie value.
        espn_s2 (str, optional): espn_s2 cookie value.
        timeout (int, optional): Request timeout in seconds.

    Returns:
        dict: Parsed JSON response.
    """
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{league_season}/segments/0/leagues/{league_id}"
    params = {"view": ["mMatchup", "mTeam", "mMatchupScore"]}
    cookies = {}
    if swid:
        cookies["SWID"] = swid
    if espn_s2:
        cookies["espn_s2"] = espn_s2

    resp = requests.get(url, params=params, cookies=cookies, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def fetch_fantasypros_data(api_key=None, timeout=10):
    """
    Fetch raw FantasyPros league analysis JSON using the mpbnfl endpoint.

    Args:
        api_key (str, optional): Full FantasyPros key.
            If not provided the function will read FANTASYPROS_KEY
        timeout (int): Request timeout in seconds.

    Returns:
        dict: Parsed JSON response from FantasyPros on success.
        None: If no API key is available or the request fails (caller can handle retries/fallback).
    """
    api_key = api_key or os.getenv("FANTASYPROS_KEY")
    if not api_key:
        return None

    url = "https://mpbnfl.fantasypros.com/api/getLeagueAnalysisJSON"
    params = {"key": api_key, "period": "ros"}
    headers = {
        "User-Agent": "fantasy-rankings/1.0",
        "Accept": "application/json",
        "Referer": "https://www.fantasypros.com",
        "Origin": "https://www.fantasypros.com",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

@app.route("/")
def index():
    load_dotenv()
    SWID = os.getenv("SWID")
    ESPN_S2 = os.getenv("ESPN_S2")
    LEAGUE_ID = os.getenv("LEAGUE_ID")
    LEAGUE_SEASON = os.getenv("LEAGUE_SEASON")
    FANTASYPROS_KEY = os.getenv("FANTASYPROS_KEY")

    if not LEAGUE_ID or not LEAGUE_SEASON:
        return "LEAGUE_ID and LEAGUE_SEASON must be set in the environment", 400

    # Check for cached rankings first
    cached_rankings, cached_updated_at = get_latest_rankings(int(LEAGUE_SEASON))
    if cached_rankings:
        return render_template(
            "index.html",
            teams=cached_rankings,
            last_updated=format_last_updated(cached_updated_at),
        )

    try:
        espn_data = fetch_espn_league_data(LEAGUE_ID, LEAGUE_SEASON, swid=SWID, espn_s2=ESPN_S2)
        fpros_data = fetch_fantasypros_data(FANTASYPROS_KEY)
    except requests.HTTPError:
        app.logger.exception("Upstream API request failed while building rankings")
        return "Failed to fetch league data from an upstream provider", 502
    except Exception:
        app.logger.exception("Unexpected server error while building rankings")
        return "An unexpected server error occurred", 500

    ranked_teams = calculate_power_rankings(espn_data, fpros_json=fpros_data)
    current_week = get_current_week(espn_data.get("schedule", []))
    save_rankings(current_week, int(LEAGUE_SEASON), ranked_teams)

    _, saved_updated_at = get_latest_rankings(int(LEAGUE_SEASON))
    return render_template(
        "index.html",
        teams=ranked_teams,
        last_updated=format_last_updated(saved_updated_at),
    )

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=debug_mode)
