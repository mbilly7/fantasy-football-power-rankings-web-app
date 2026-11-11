import os
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from flask import Flask, render_template, request
from dotenv import load_dotenv
from power_rankings_generator import calculate_power_rankings, is_playoff_matchup
from database import (
    init_db,
    get_rankings,
    get_available_seasons,
    get_available_weeks,
    get_discovered_seasons,
    get_season_weeks,
    get_all_season_weeks,
    save_season_weeks,
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
init_db()
EARLIEST_PLAUSIBLE_ESPN_SEASON = 2000
# ESPN's season endpoint 404s for seasons before this; they're only served by the leagueHistory endpoint.
FIRST_SEASON_ENDPOINT_SEASON = 2018
ESPN_FFL_API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
# How often to re-check the current season with ESPN while it has no completed weeks yet.
CURRENT_SEASON_REPROBE_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class Config:
    league_id: str
    swid: Optional[str]
    espn_s2: Optional[str]
    fantasypros_key: Optional[str]


@dataclass(frozen=True)
class Selection:
    season: Optional[int]  # None means "latest known season"
    week: Optional[int]  # None means "latest completed week"


@dataclass(frozen=True)
class Snapshot:
    """A week of rankings: saved in the database, or only in uncached_rankings when it shouldn't be saved."""
    season: int
    week: int
    uncached_rankings: Optional[list] = None  # set when the rankings are missing data that may be available later


class RequestError(Exception):
    """Raised to end a request with a plain-text error response."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@app.errorhandler(RequestError)
def handle_request_error(error):
    return error.message, error.status_code


def current_year():
    return datetime.now(timezone.utc).year


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
    params = {"view": ["mMatchup", "mTeam", "mMatchupScore"]}
    is_history_season = int(league_season) < FIRST_SEASON_ENDPOINT_SEASON
    if is_history_season:
        url = f"{ESPN_FFL_API}/leagueHistory/{league_id}"
        params["seasonId"] = league_season
    else:
        url = f"{ESPN_FFL_API}/seasons/{league_season}/segments/0/leagues/{league_id}"
    cookies = {}
    if swid:
        cookies["SWID"] = swid
    if espn_s2:
        cookies["espn_s2"] = espn_s2

    logger.info("Fetching ESPN league data for league_id=%s season=%s", league_id, league_season)
    resp = requests.get(url, params=params, cookies=cookies, timeout=timeout)
    resp.raise_for_status()
    logger.info("Fetched ESPN league data for league_id=%s season=%s", league_id, league_season)
    data = resp.json()
    if is_history_season:
        # leagueHistory returns a list of matching seasons (empty if the league didn't exist yet).
        return data[0] if data else {}
    return data

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
    # The key is a query parameter, and requests puts the full URL in its error messages,
    # so log only the status code or error type.
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.warning("FantasyPros request failed with HTTP %s; ROS strength unavailable", status_code)
        return None
    except (requests.RequestException, ValueError) as exc:
        logger.warning("FantasyPros request failed (%s); ROS strength unavailable", type(exc).__name__)
        return None
    if not isinstance(data, dict) or not data.get("standings"):
        # e.g. {"error": "no league with this key", "key": ...} with HTTP 200. Don't log it: it echoes the key.
        logger.warning("FantasyPros response had no standings; ROS strength unavailable")
        return None
    logger.info("Fetched FantasyPros data")
    return data


def fetch_season_or_record_failure(league_id, season, swid, espn_s2, http_status_prefix, error_status):
    """
    Fetch a season from ESPN, recording any failure in the season sync table.

    Returns:
        dict: ESPN league data, or None if the fetch failed.
    """
    try:
        return fetch_espn_league_data(league_id, season, swid=swid, espn_s2=espn_s2)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.info("Season %s: ESPN returned HTTP %s", season, status_code)
        upsert_season_sync_status(season, discovered=False, status=f"{http_status_prefix}{status_code or 'error'}")
    except Exception:
        logger.exception("Season %s: unexpected error fetching ESPN data", season)
        upsert_season_sync_status(season, discovered=False, status=error_status)
    return None


def extract_completed_weeks(season_data):
    return sorted(
        {
            matchup.get("matchupPeriodId")
            for matchup in season_data.get("schedule", [])
            if matchup.get("winner") and matchup.get("winner") != "UNDECIDED"
        }
    )


def is_season_in_progress(season_data):
    """True while the schedule still has unplayed head-to-head matchups (byes never get a winner)."""
    return any(
        "away" in matchup and matchup.get("winner") in (None, "UNDECIDED")
        for matchup in season_data.get("schedule", [])
    )


def describe_completed_weeks(season_data):
    """
    Week picker options for every completed matchup period, oldest first.

    Regular-season weeks are labeled "Week N". Playoff rounds are labeled by round and the NFL
    weeks they cover, since some seasons used two-week playoff matchups.

    Returns:
        list: [{"week": matchup_period, "label": str}]
    """
    schedule = season_data.get("schedule", [])
    regular_season_end = max(
        (m["matchupPeriodId"] for m in schedule if not is_playoff_matchup(m)), default=0
    )
    nfl_weeks = defaultdict(set)
    for match in schedule:
        for side in ("home", "away"):
            team = match.get(side) or {}
            nfl_weeks[match["matchupPeriodId"]].update(int(p) for p in team.get("pointsByScoringPeriod") or {})

    options = []
    for week in extract_completed_weeks(season_data):
        if week <= regular_season_end:
            label = f"Week {week}"
        else:
            span = sorted(nfl_weeks[week]) or [week]
            weeks_text = f"Week {span[0]}" if len(span) == 1 else f"Weeks {span[0]}–{span[-1]}"
            label = f"Playoffs Round {week - regular_season_end} ({weeks_text})"
        options.append({"week": week, "label": label})
    return options


def is_transient_failure(status):
    # A 404 means ESPN has no record of the season; auth/network errors may have been transient.
    return status in ("probe_error", "snapshot_fetch_error") or status.startswith(
        ("http_5", "history_http_5", "snapshot_http_5")
    )


def needs_probe(season, known, now, has_week_list=True):
    """
    Whether discovery should ask ESPN about a season, given its saved sync status (or None).
    Discovered seasons are re-probed once if their week picker options were never saved.
    """
    if known is None:
        return True
    if known["discovered"]:
        return not has_week_list
    if is_transient_failure(known["status"] or ""):
        return True
    if season < FIRST_SEASON_ENDPOINT_SEASON and (known["status"] or "").startswith("http_"):
        # Saved by the season endpoint, which can't serve this season. Retry once via leagueHistory.
        return True
    # The current season starts out unavailable (league not renewed yet, or no games played),
    # so unlike past seasons it can't be written off. Re-check it periodically.
    if season == now.year:
        last_checked_at = known.get("last_checked_at")
        if last_checked_at is None:
            return True
        if last_checked_at.tzinfo is None:  # SQLite returns naive datetimes; they are stored as UTC
            last_checked_at = last_checked_at.replace(tzinfo=timezone.utc)
        return now - last_checked_at >= CURRENT_SEASON_REPROBE_INTERVAL
    return False


def probe_season(league_id, season, swid=None, espn_s2=None):
    """Ask ESPN whether a season has completed weeks, recording the result. Returns True if it does."""
    # History seasons record their own status prefix, so 404s saved before they were fetched from
    # leagueHistory ("http_404") can be told apart and retried (see needs_probe).
    http_status_prefix = "history_http_" if season < FIRST_SEASON_ENDPOINT_SEASON else "http_"
    data = fetch_season_or_record_failure(
        league_id, season, swid, espn_s2, http_status_prefix=http_status_prefix, error_status="probe_error"
    )
    if data is None:
        return False

    week_options = describe_completed_weeks(data)
    if not week_options:
        logger.info("Season %s: ESPN responded but no completed weeks found", season)
        upsert_season_sync_status(season, discovered=False, status="no_completed_weeks")
        return False

    logger.info("Season %s: discovered with %d completed week(s)", season, len(week_options))
    save_season_weeks(season, week_options)
    upsert_season_sync_status(season, discovered=True, status="available")
    return True


def discover_available_seasons(league_id, swid=None, espn_s2=None):
    sync_map = get_season_sync_status_map()
    seasons_with_week_lists = set(get_all_season_weeks())
    discovered = []

    now = datetime.now(timezone.utc)

    for season in range(now.year, EARLIEST_PLAUSIBLE_ESPN_SEASON - 1, -1):
        known = sync_map.get(season)
        if not needs_probe(season, known, now, has_week_list=season in seasons_with_week_lists):
            if known["discovered"]:
                discovered.append(season)
            continue
        if known is not None:
            logger.info("Season %s: re-probing (previous status=%s)", season, known["status"])

        if probe_season(league_id, season, swid=swid, espn_s2=espn_s2):
            discovered.append(season)

    if not discovered:
        discovered = get_discovered_seasons()

    logger.info("Discovery complete — available seasons: %s", sorted(discovered, reverse=True))
    return sorted(set(discovered), reverse=True)


def mark_season_backfilled(season, status):
    upsert_season_sync_status(
        season,
        discovered=True,
        backfilled=True,
        max_week_backfilled=max(get_available_weeks(season)),
        status=status,
    )


def ensure_week_snapshot(league_id, season, requested_week=None, swid=None, espn_s2=None, fantasypros_key=None):
    """
    Make sure rankings for one week of a season are saved, computing them from ESPN data if needed.

    Args:
        requested_week (int, optional): Week to load. Defaults to the latest completed week.

    Returns:
        Snapshot: The saved week, or None if the season/week has no completed data.
    """
    # Fast path: if requesting a specific week that's already cached (along with the season's
    # week list for the picker), skip all ESPN calls.
    if requested_week is not None and get_season_weeks(season) is not None:
        cached_rankings, _ = get_rankings(season, requested_week)
        if cached_rankings:
            logger.info("Cache hit for season=%s week=%s — skipping ESPN fetch", season, requested_week)
            return Snapshot(season=season, week=requested_week)

    espn_data = fetch_season_or_record_failure(
        league_id, season, swid, espn_s2,
        http_status_prefix="snapshot_http_", error_status="snapshot_fetch_error",
    )
    if espn_data is None:
        return None

    week_options = describe_completed_weeks(espn_data)
    if not week_options:
        upsert_season_sync_status(season, discovered=False, status="snapshot_no_completed_weeks")
        return None
    save_season_weeks(season, week_options)

    completed_weeks = [option["week"] for option in week_options]
    latest_week = completed_weeks[-1]
    target_week = requested_week if requested_week is not None else latest_week
    if target_week not in completed_weeks:
        return None
    snapshot = Snapshot(season=season, week=target_week)

    cached_rankings, _ = get_rankings(season, target_week)
    if cached_rankings:
        mark_season_backfilled(season, status="snapshot_cached")
        return snapshot

    # FantasyPros only offers current ROS data, so it only applies to the latest week of a season
    # still being played. A finished season's final week is its "latest" week too, but today's
    # ROS rankings say nothing about it.
    use_ros = target_week == latest_week and is_season_in_progress(espn_data)
    fpros_data = fetch_fantasypros_data(fantasypros_key) if use_ros else None
    logger.info("Calculating rankings for season=%s week=%s", season, target_week)
    weekly_rankings = calculate_power_rankings(espn_data, fpros_json=fpros_data, target_week=target_week)
    if use_ros and fpros_data is None and (fantasypros_key or os.getenv("FANTASYPROS_KEY")):
        # FantasyPros failed this time. Show these rankings without ROS, but don't cache them,
        # so the next request tries FantasyPros again.
        return replace(snapshot, uncached_rankings=weekly_rankings)
    save_rankings(target_week, season, weekly_rankings)
    mark_season_backfilled(season, status="snapshot_computed")
    return snapshot


def load_config():
    config = Config(
        league_id=os.getenv("LEAGUE_ID"),
        swid=os.getenv("SWID"),
        espn_s2=os.getenv("ESPN_S2"),
        fantasypros_key=os.getenv("FANTASYPROS_KEY"),
    )
    logger.info(
        "Request received — LEAGUE_ID=%s SWID=%s ESPN_S2=%s FANTASYPROS_KEY=%s",
        config.league_id or "MISSING",
        "set" if config.swid else "MISSING",
        "set" if config.espn_s2 else "MISSING",
        "set" if config.fantasypros_key else "MISSING",
    )

    if not config.league_id:
        logger.error("Aborting: LEAGUE_ID is not set in the environment")
        raise RequestError("LEAGUE_ID must be set in the environment", 400)
    if not (config.swid and config.espn_s2):
        logger.warning("SWID/ESPN_S2 not set — requests may fail for private leagues")
    return config


def parse_selection(args):
    season_arg = args.get("season", "").strip()
    week_arg = args.get("week", "latest").strip().lower()
    logger.info("Query params — season=%r week=%r", season_arg or "(default)", week_arg)

    season = None
    if season_arg:
        try:
            season = int(season_arg)
        except ValueError:
            logger.warning("Invalid season param: %r", season_arg)
            raise RequestError("Invalid season query parameter", 400)

    week = None
    if week_arg not in {"", "latest"}:
        try:
            week = int(week_arg)
        except ValueError:
            week = 0
        if week <= 0:
            logger.warning("Invalid week param: %r", week_arg)
            raise RequestError("Invalid week query parameter", 400)

    return Selection(season=season, week=week)


def build_candidate_seasons(requested_season, known_seasons):
    """
    Seasons to try, in order. An explicit season is tried alone; otherwise start from the
    latest known season and fall back through older ones (including the year before, in
    case the newest season has no completed weeks yet).
    """
    if requested_season is not None:
        return [requested_season]

    default_season = known_seasons[0] if known_seasons else current_year()
    candidates = [default_season] + [s for s in known_seasons if s != default_season]
    if default_season - 1 not in candidates:
        candidates.append(default_season - 1)
    return candidates


def load_first_available_snapshot(config, candidate_seasons, week):
    for season in candidate_seasons:
        logger.info("Attempting snapshot for season=%s week=%s", season, week or "latest")
        snapshot = ensure_week_snapshot(
            config.league_id,
            season,
            requested_week=week,
            swid=config.swid,
            espn_s2=config.espn_s2,
            fantasypros_key=config.fantasypros_key,
        )
        if snapshot:
            return snapshot

    last_attempted_season = candidate_seasons[-1]
    if week is not None:
        logger.warning("No data for week=%s season=%s", week, last_attempted_season)
        raise RequestError(f"No completed data available for week {week} in season {last_attempted_season}", 404)
    logger.warning("No completed weeks found across all candidate seasons: %s", candidate_seasons)
    raise RequestError("No completed matchup weeks available for this season yet", 404)


def get_week_options(season):
    """Week picker options for a season, falling back to plain labels for its cached weeks."""
    return get_season_weeks(season) or [{"week": w, "label": f"Week {w}"} for w in get_available_weeks(season)]


@app.route("/")
def index():
    config = load_config()
    selection = parse_selection(request.args)

    discovered_seasons = discover_available_seasons(config.league_id, swid=config.swid, espn_s2=config.espn_s2)
    known_seasons = sorted(set(discovered_seasons + get_available_seasons()), reverse=True)
    logger.info("Known seasons: %s", known_seasons)

    candidate_seasons = build_candidate_seasons(selection.season, known_seasons)
    snapshot = load_first_available_snapshot(config, candidate_seasons, selection.week)

    if snapshot.uncached_rankings:
        ranked_teams, saved_updated_at = snapshot.uncached_rankings, None
    else:
        ranked_teams, saved_updated_at = get_rankings(snapshot.season, snapshot.week)
    if not ranked_teams:
        logger.error("Rankings missing from DB after snapshot — season=%s week=%s", snapshot.season, snapshot.week)
        raise RequestError("Unable to load rankings from cache after calculation", 500)

    available_seasons = sorted(set(known_seasons + [snapshot.season]), reverse=True)
    saved_week_options = get_all_season_weeks()
    # Lets the page swap the week picker's options when a different season is chosen.
    season_week_options = {season: saved_week_options.get(season) for season in available_seasons}
    week_options = get_week_options(snapshot.season)
    season_week_options[snapshot.season] = week_options
    week_labels = {option["week"]: option["label"] for option in week_options}

    logger.info("Rendering rankings for season=%s week=%s", snapshot.season, snapshot.week)
    return render_template(
        "index.html",
        teams=ranked_teams,
        last_updated=format_last_updated(saved_updated_at),
        available_seasons=available_seasons,
        week_options=week_options,
        season_week_options=season_week_options,
        selected_season=snapshot.season,
        selected_week=snapshot.week,
        selected_week_label=week_labels.get(snapshot.week, f"Week {snapshot.week}"),
    )

if __name__ == "__main__":
    load_dotenv()
    debug_mode = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=debug_mode)
