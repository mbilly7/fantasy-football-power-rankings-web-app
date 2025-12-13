import os
import requests
from flask import Flask, render_template
from dotenv import load_dotenv
from power_rankings_generator import calculate_power_rankings

app = Flask(__name__)

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

    try:
        espn_data = fetch_espn_league_data(LEAGUE_ID, LEAGUE_SEASON, swid=SWID, espn_s2=ESPN_S2)
        fpros_data = fetch_fantasypros_data(FANTASYPROS_KEY)
    except requests.HTTPError as e:
        return f"Failed to fetch ESPN league data: {e}", 502
    except Exception as e:
        return f"Unexpected error fetching ESPN data: {e}", 500

    ranked_teams = calculate_power_rankings(espn_data, fpros_json=fpros_data)
    return render_template("index.html", teams=ranked_teams)

if __name__ == "__main__":
    app.run(debug=True)
