from unittest.mock import patch, MagicMock
from app import app as flask_app, fetch_espn_league_data, fetch_fantasypros_data, Snapshot


@patch('app.requests.get')
def test_fetch_espn_league_data(mock_get):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"teams": [], "schedule": []}
    mock_get.return_value = mock_response

    data = fetch_espn_league_data("123", "2024", swid="test_swid", espn_s2="test_s2")
    assert data == {"teams": [], "schedule": []}
    mock_get.assert_called_once()


@patch('app.requests.get')
def test_fetch_fantasypros_data(mock_get):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"standings": []}
    mock_get.return_value = mock_response

    data = fetch_fantasypros_data("test_key")
    assert data == {"standings": []}
    mock_get.assert_called_once()


@patch('app.requests.get')
def test_fetch_fantasypros_data_no_key(mock_get):
    data = fetch_fantasypros_data()
    assert data is None
    mock_get.assert_not_called()


@patch("app.get_rankings")
@patch("app.ensure_week_snapshot")
@patch("app.get_available_seasons")
@patch("app.discover_available_seasons")
def test_index_defaults_to_latest_cached_season_without_league_season(
    mock_discover_available_seasons,
    mock_get_available_seasons,
    mock_ensure_week_snapshot,
    mock_get_rankings,
    monkeypatch,
):
    monkeypatch.setenv("LEAGUE_ID", "123")
    monkeypatch.delenv("LEAGUE_SEASON", raising=False)

    mock_discover_available_seasons.return_value = [2024, 2023]
    mock_get_available_seasons.return_value = [2024]
    mock_ensure_week_snapshot.return_value = Snapshot(season=2024, week=1, regular_season_weeks=14)
    mock_get_rankings.return_value = (
        [
            {
                "power_rank": 1,
                "team_name": "Team A",
                "record": "1-0-0",
                "overall_wins": "1-0",
                "consistency": 1,
                "ppg": 100.0,
                "ros_strength": "N/A",
                "pr_score": 5.2,
            }
        ],
        None,
    )

    client = flask_app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Showing season 2024, week 1." in response.data
    mock_get_rankings.assert_called_once_with(2024, 1)


@patch("app.get_rankings")
@patch("app.ensure_week_snapshot")
@patch("app.get_available_seasons")
@patch("app.discover_available_seasons")
def test_index_falls_back_to_previous_season_when_current_has_no_completed_weeks(
    mock_discover_available_seasons,
    mock_get_available_seasons,
    mock_ensure_week_snapshot,
    mock_get_rankings,
    monkeypatch,
):
    monkeypatch.setenv("LEAGUE_ID", "123")
    monkeypatch.delenv("LEAGUE_SEASON", raising=False)

    mock_discover_available_seasons.return_value = [2026, 2025]
    mock_get_available_seasons.return_value = [2025]

    def ensure_side_effect(_league_id, season, **_kwargs):
        if season == 2025:
            return Snapshot(season=2025, week=17, regular_season_weeks=14)
        return None

    mock_ensure_week_snapshot.side_effect = ensure_side_effect
    mock_get_rankings.return_value = (
        [
            {
                "power_rank": 1,
                "team_name": "Team A",
                "record": "10-4-0",
                "overall_wins": "91-35",
                "consistency": 1,
                "ppg": 123.4,
                "ros_strength": "N/A",
                "pr_score": 4.2,
            }
        ],
        None,
    )

    client = flask_app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Showing season 2025, week 17." in response.data
    mock_get_rankings.assert_called_once_with(2025, 17)


@patch("app.fetch_espn_league_data")
@patch("app.get_season_sync_status_map")
@patch("app.upsert_season_sync_status")
def test_discover_available_seasons_backtracks_until_misses(
    mock_upsert_season_sync_status,
    mock_get_season_sync_status_map,
    mock_fetch_espn_league_data,
):
    mock_get_season_sync_status_map.return_value = {}

    def side_effect(_league_id, season, **_kwargs):
        if season in {2024, 2023}:
            return {
                "schedule": [
                    {"matchupPeriodId": 1, "winner": "HOME"},
                ]
            }
        raise Exception("missing")

    mock_fetch_espn_league_data.side_effect = side_effect

    from app import discover_available_seasons

    seasons = discover_available_seasons("123", swid="s", espn_s2="e")

    assert 2024 in seasons and 2023 in seasons
    assert seasons == sorted(seasons, reverse=True)
    assert mock_upsert_season_sync_status.call_count >= 2

def test_needs_probe_rechecks_current_season_until_discovered():
    from datetime import datetime, timedelta, timezone
    from app import needs_probe

    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    def status(state, checked_ago, discovered=False):
        # Naive, like SQLite returns
        return {"discovered": discovered, "status": state, "last_checked_at": (now - checked_ago).replace(tzinfo=None)}

    # Current season checked before games were played: re-check once the interval passes
    assert needs_probe(2026, status("no_completed_weeks", timedelta(days=47)), now)
    assert needs_probe(2026, status("http_404", timedelta(hours=2)), now)
    assert not needs_probe(2026, status("no_completed_weeks", timedelta(minutes=10)), now)
    # Discovered seasons and definitive results for past seasons are final
    assert not needs_probe(2026, status("available", timedelta(days=1), discovered=True), now)
    assert not needs_probe(2025, status("no_completed_weeks", timedelta(days=47)), now)
    assert not needs_probe(2019, status("http_404", timedelta(days=47)), now)
    # Unknown seasons and transient failures are always probed
    assert needs_probe(2026, None, now)
    assert needs_probe(2024, status("probe_error", timedelta(minutes=1)), now)
    assert needs_probe(2024, status("snapshot_fetch_error", timedelta(minutes=1)), now)
    assert needs_probe(2024, status("snapshot_http_503", timedelta(minutes=1)), now)
    assert not needs_probe(2024, status("snapshot_http_404", timedelta(minutes=1)), now)
