import os
from flask import Flask, render_template
import requests
from dotenv import load_dotenv
from power_rankings_generator import calculate_power_rankings

app = Flask(__name__)

@app.route("/")
def index():
    load_dotenv()
    SWID = os.getenv("SWID")
    ESPN_S2 = os.getenv("ESPN_S2")
    LEAGUE_ID = os.getenv("LEAGUE_ID")
    LEAGUE_SEASON = os.getenv("LEAGUE_SEASON")

    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{LEAGUE_SEASON}/segments/0/leagues/{LEAGUE_ID}"
    params = {
        "view": ["mMatchup", "mTeam", "mMatchupScore"]
    }
    cookies = {
        "SWID": SWID,
        "espn_s2": ESPN_S2
    }

    response = requests.get(url, params=params, cookies=cookies, timeout=10)
    if response.status_code == 200:
        data = response.json()
        ranked_teams = calculate_power_rankings(data)
        return render_template("index.html", teams=ranked_teams)
    else:
        return f"Failed to fetch data: {response.status_code}"

if __name__ == "__main__":
    app.run(debug=True)
