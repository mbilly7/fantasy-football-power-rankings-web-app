import os
import logging
import sys
from datetime import datetime, timezone
import requests
from flask import Flask, render_template, request
from dotenv import load_dotenv
from power_rankings_generator import calculate_power_rankings, get_current_week
from database import (
    get_rankings,
    get_available_seasons,
    get_available_weeks,
    get_discovered_seasons,
    get_season_sync_status_map,
    upsert_season_sync_status,
    save_rankings,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    stream=sys.stdout,
    force=True,
)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
logger = logging.getLogger(__name__)
EARLIEST_PLAUSIBLE_ESPN_SEASON = 2000
DEFAULT_REGULAR_SEASON_WEEKS = 14


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

    logger.info("Fetching ESPN league data for league_id=%s season=%s", league_id, league_season)
    resp = requests.get(url, params=params, cookies=cookies, timeout=timeout)
    resp.raise_for_status()
    logger.info("Fetched ESPN league data for league_id=%s season=%s", league_id, league_season)
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

    logger.info("Fetching FantasyPros data")
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    logger.info("Fetched FantasyPros data")
    return resp.json()


def extract_completed_weeks(season_data):
    return sorted(
        {
            matchup.get("matchupPeriodId")
            for matchup in season_data.get("schedule", [])
            if matchup.get("winner") and matchup.get("winner") != "UNDECIDED"
        }
    )


def get_regular_season_weeks(season_data):
    schedule_settings = season_data.get("settings", {}).get("scheduleSettings", {})
    matchup_period_count = schedule_settings.get("matchupPeriodCount")
    if isinstance(matchup_period_count, int) and matchup_period_count > 0:
        return matchup_period_count

    all_weeks = [m.get("matchupPeriodId") for m in season_data.get("schedule", []) if m.get("matchupPeriodId")]
    if all_weeks:
        return max(all_weeks)

    return DEFAULT_REGULAR_SEASON_WEEKS


def discover_available_seasons(league_id, swid=None, espn_s2=None):
    current_year = datetime.now(timezone.utc).year
    sync_map = get_season_sync_status_map()
    discovered = []

    for season in range(current_year, EARLIEST_PLAUSIBLE_ESPN_SEASON - 1, -1):
        known = sync_map.get(season)
        if known is not None:
            status = known.get("status", "")
            is_discovered = known.get("discovered", False)
            # Re-probe seasons that failed due to auth/network errors (not definitive 404s).
            # A 404 means ESPN has no record of the season; auth errors may have been transient.
            auth_or_network_failure = status in ("probe_error",) or status.startswith("http_5")
            if not auth_or_network_failure:
                if is_discovered:
                    discovered.append(season)
                    logger.debug("Season %s: already discovered (status=%s) — skipping ESPN probe", season, status)
                else:
                    logger.debug("Season %s: previously probed, not available (status=%s) — skipping", season, status)
                continue
            logger.info("Season %s: re-probing after prior transient failure (status=%s)", season, status)

        try:
            data = fetch_espn_league_data(league_id, season, swid=swid, espn_s2=espn_s2)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            logger.info("Season %s: ESPN returned HTTP %s — marking not available", season, status_code)
            upsert_season_sync_status(season, discovered=False, status=f"http_{status_code or 'error'}")
            continue
        except Exception:
            logger.exception("Season %s: unexpected error during discovery probe", season)
            upsert_season_sync_status(season, discovered=False, status="probe_error")
            continue

        completed_weeks = extract_completed_weeks(data)
        if completed_weeks:
            logger.info("Season %s: discovered with %d completed week(s)", season, len(completed_weeks))
            upsert_season_sync_status(season, discovered=True, status="available")
            discovered.append(season)
        else:
            logger.info("Season %s: ESPN responded but no completed weeks found", season)
            upsert_season_sync_status(season, discovered=False, status="no_completed_weeks")

    if not discovered:
        discovered = get_discovered_seasons()

    logger.info("Discovery complete — available seasons: %s", sorted(discovered, reverse=True))
    return sorted(set(discovered), reverse=True)


def ensure_week_snapshot(league_id, season, requested_week=None, swid=None, espn_s2=None, fantasypros_key=None):
    # Fast path: if requesting a specific week and it's already cached, skip all ESPN calls.
    if requested_week is not None:
        cached_rankings, _ = get_rankings(season, requested_week)
        if cached_rankings:
            logger.info("Cache hit for season=%s week=%s — skipping ESPN fetch", season, requested_week)
            cached_weeks = get_available_weeks(season)
            return True, None, cached_weeks, requested_week

    try:
        espn_data = fetch_espn_league_data(league_id, season, swid=swid, espn_s2=espn_s2)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        upsert_season_sync_status(season, discovered=False, status=f"snapshot_http_{status_code or 'error'}")
        return False, None, [], None
    except Exception:
        logger.exception("Unexpected failure while fetching season=%s for snapshot", season)
        upsert_season_sync_status(season, discovered=False, status="snapshot_fetch_error")
        return False, None, [], None

    completed_weeks = extract_completed_weeks(espn_data)
    regular_season_weeks = get_regular_season_weeks(espn_data)
    if not completed_weeks:
        upsert_season_sync_status(season, discovered=False, status="snapshot_no_completed_weeks")
        return False, regular_season_weeks, [], None

    latest_week = get_current_week(espn_data.get("schedule", []))
    target_week = requested_week if requested_week is not None else latest_week
    if target_week < 1 or target_week > regular_season_weeks:
        return False, regular_season_weeks, completed_weeks, latest_week
    if target_week > latest_week:
        return False, regular_season_weeks, completed_weeks, latest_week

    cached_rankings, _ = get_rankings(season, target_week)
    if cached_rankings:
        upsert_season_sync_status(
            season,
            discovered=True,
            backfilled=True,
            max_week_backfilled=max(get_available_weeks(season)),
            status="snapshot_cached",
        )
        return True, regular_season_weeks, completed_weeks, latest_week

    fpros_data = fetch_fantasypros_data(fantasypros_key) if target_week == latest_week else None
    logger.info("Calculating rankings for season=%s week=%s", season, target_week)
    weekly_rankings = calculate_power_rankings(espn_data, fpros_json=fpros_data, target_week=target_week)
    logger.info("Saving rankings for season=%s week=%s", season, target_week)
    save_rankings(target_week, season, weekly_rankings)

    upsert_season_sync_status(
        season,
        discovered=True,
        backfilled=True,
        max_week_backfilled=max(get_available_weeks(season)),
        status="snapshot_computed",
    )
    return True, regular_season_weeks, completed_weeks, latest_week

@app.route("/")
def index():
    load_dotenv()
    SWID = os.getenv("SWID")
    ESPN_S2 = os.getenv("ESPN_S2")
    LEAGUE_ID = os.getenv("LEAGUE_ID")
    FANTASYPROS_KEY = os.getenv("FANTASYPROS_KEY")

    logger.info(
        "Request received — LEAGUE_ID=%s SWID=%s ESPN_S2=%s FANTASYPROS_KEY=%s",
        LEAGUE_ID or "MISSING",
        "set" if SWID else "MISSING",
        "set" if ESPN_S2 else "MISSING",
        "set" if FANTASYPROS_KEY else "MISSING",
    )

    if not LEAGUE_ID:
        logger.error("Aborting: LEAGUE_ID is not set in the environment")
        return "LEAGUE_ID must be set in the environment", 400

    missing_auth = [k for k, v in {"SWID": SWID, "ESPN_S2": ESPN_S2}.items() if not v]
    if missing_auth:
        logger.warning("Private league credentials not set: %s — may fail for private leagues", missing_auth)

    season_arg = request.args.get("season", "").strip()
    week_arg = request.args.get("week", "latest").strip().lower()
    discovered_seasons = discover_available_seasons(LEAGUE_ID, swid=SWID, espn_s2=ESPN_S2)
    cached_seasons = get_available_seasons()
    logger.info(
        "Query params — season=%r week=%r",
        season_arg or "(default)", week_arg,
    )
    all_known_seasons = sorted(set(discovered_seasons + cached_seasons), reverse=True)
    logger.info("Known seasons: %s", all_known_seasons)
    default_season = all_known_seasons[0] if all_known_seasons else datetime.now(timezone.utc).year

    if season_arg:
        try:
            selected_season = int(season_arg)
        except ValueError:
            logger.warning("Invalid season param: %r", season_arg)
            return "Invalid season query parameter", 400
    else:
        selected_season = default_season
        logger.info("No season param — defaulting to season=%s", selected_season)

    selected_week = None
    if week_arg not in {"", "latest"}:
        try:
            selected_week = int(week_arg)
            if selected_week <= 0:
                raise ValueError
        except ValueError:
            logger.warning("Invalid week param: %r", week_arg)
            return "Invalid week query parameter", 400

    logger.info("Resolved selection — season=%s week=%s", selected_season, selected_week or "latest")

    if season_arg:
        candidate_seasons = [selected_season]
    else:
        candidate_seasons = [selected_season] + [s for s in all_known_seasons if s != selected_season]
        if selected_season - 1 not in candidate_seasons:
            candidate_seasons.append(selected_season - 1)
    logger.info("Candidate seasons to try: %s", candidate_seasons)

    success = False
    regular_season_weeks = None
    latest_week = None
    last_attempted_season = selected_season
    for candidate_season in candidate_seasons:
        last_attempted_season = candidate_season
        logger.info("Attempting snapshot for season=%s week=%s", candidate_season, selected_week or "latest")
        success, regular_season_weeks, _, latest_week = ensure_week_snapshot(
            LEAGUE_ID,
            candidate_season,
            requested_week=selected_week,
            swid=SWID,
            espn_s2=ESPN_S2,
            fantasypros_key=FANTASYPROS_KEY,
        )
        if success:
            selected_season = candidate_season
            logger.info("Snapshot succeeded for season=%s week=%s", selected_season, latest_week)
            break
        logger.info("Snapshot failed for season=%s — trying next candidate", candidate_season)

    if not success:
        if selected_week is not None:
            logger.warning("No data for week=%s season=%s", selected_week, last_attempted_season)
            return f"No completed data available for week {selected_week} in season {last_attempted_season}", 404
        logger.warning("No completed weeks found across all candidate seasons: %s", candidate_seasons)
        return "No completed matchup weeks available for this season yet", 404

    rendered_week = selected_week if selected_week is not None else latest_week
    ranked_teams, saved_updated_at = get_rankings(selected_season, rendered_week)
    if not ranked_teams:
        logger.error("Rankings missing from DB after snapshot — season=%s week=%s", selected_season, rendered_week)
        return "Unable to load rankings from cache after calculation", 500

    available_seasons = sorted(set(discovered_seasons + cached_seasons + [selected_season]), reverse=True)
    # Use regular_season_weeks from ESPN if available; otherwise infer from cached weeks or use default
    if regular_season_weeks:
        week_count = regular_season_weeks
    else:
        cached_weeks = get_available_weeks(selected_season)
        week_count = max(cached_weeks) if cached_weeks else DEFAULT_REGULAR_SEASON_WEEKS
    available_weeks = list(range(1, week_count + 1))
    logger.info("Rendering rankings for season=%s week=%s", selected_season, rendered_week)
    return render_template(
        "index.html",
        teams=ranked_teams,
        last_updated=format_last_updated(saved_updated_at),
        available_seasons=available_seasons,
        available_weeks=available_weeks,
        selected_season=selected_season,
        selected_week=selected_week,
        displayed_week=rendered_week,
    )

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=debug_mode)
