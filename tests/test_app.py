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


@patch("app.get_all_season_weeks", return_value={})
@patch("app.get_season_weeks")
@patch("app.get_rankings")
@patch("app.ensure_week_snapshot")
@patch("app.get_available_seasons")
@patch("app.discover_available_seasons")
def test_index_defaults_to_latest_cached_season_without_league_season(
    mock_discover_available_seasons,
    mock_get_available_seasons,
    mock_ensure_week_snapshot,
    mock_get_rankings,
    mock_get_season_weeks,
    _mock_get_all_season_weeks,
    monkeypatch,
):
    monkeypatch.setenv("LEAGUE_ID", "123")
    monkeypatch.delenv("LEAGUE_SEASON", raising=False)

    mock_discover_available_seasons.return_value = [2024, 2023]
    mock_get_available_seasons.return_value = [2024]
    mock_ensure_week_snapshot.return_value = Snapshot(season=2024, week=1)
    mock_get_season_weeks.return_value = [{"week": 1, "label": "Week 1"}]
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
    assert b"Showing season 2024, Week 1." in response.data
    mock_get_rankings.assert_called_once_with(2024, 1)


@patch("app.get_all_season_weeks", return_value={})
@patch("app.get_season_weeks")
@patch("app.get_rankings")
@patch("app.ensure_week_snapshot")
@patch("app.get_available_seasons")
@patch("app.discover_available_seasons")
def test_index_falls_back_to_previous_season_when_current_has_no_completed_weeks(
    mock_discover_available_seasons,
    mock_get_available_seasons,
    mock_ensure_week_snapshot,
    mock_get_rankings,
    mock_get_season_weeks,
    _mock_get_all_season_weeks,
    monkeypatch,
):
    monkeypatch.setenv("LEAGUE_ID", "123")
    monkeypatch.delenv("LEAGUE_SEASON", raising=False)

    mock_discover_available_seasons.return_value = [2026, 2025]
    mock_get_available_seasons.return_value = [2025]

    def ensure_side_effect(_league_id, season, **_kwargs):
        if season == 2025:
            return Snapshot(season=2025, week=17)
        return None

    mock_ensure_week_snapshot.side_effect = ensure_side_effect
    mock_get_season_weeks.return_value = [{"week": 14, "label": "Week 14"}] + [
        {"week": w, "label": f"Playoffs Round {w - 14} (Week {w})"} for w in (15, 16, 17)
    ]
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
    assert b"Showing season 2025, Playoffs Round 3 (Week 17)." in response.data
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


def test_describe_completed_weeks_labels_playoffs_and_skips_unplayed_weeks():
    from app import describe_completed_weeks

    def matchup(week, winner="HOME", tier="NONE", periods=None):
        periods = periods or [week]
        side = lambda tid: {"teamId": tid, "totalPoints": 100.0, "pointsByScoringPeriod": {str(p): 50.0 for p in periods}}
        return {"matchupPeriodId": week, "playoffTierType": tier, "winner": winner, "home": side(1), "away": side(2)}

    season = {"schedule": [
        matchup(1), matchup(2),
        matchup(3, tier="WINNERS_BRACKET", periods=[3, 4]),  # two-week playoff round
        matchup(4, tier="WINNERS_BRACKET", periods=[5, 6], winner="UNDECIDED"),  # not played yet
    ]}

    assert describe_completed_weeks(season) == [
        {"week": 1, "label": "Week 1"},
        {"week": 2, "label": "Week 2"},
        {"week": 3, "label": "Playoffs Round 1 (Weeks 3–4)"},
    ]


def test_is_season_in_progress_ignores_byes():
    from app import is_season_in_progress

    finished = {"schedule": [
        {"matchupPeriodId": 17, "home": {"teamId": 1}, "away": {"teamId": 2}, "winner": "HOME"},
        {"matchupPeriodId": 15, "home": {"teamId": 3}, "winner": "UNDECIDED"},  # playoff bye
    ]}
    in_progress = {"schedule": finished["schedule"] + [
        {"matchupPeriodId": 18, "home": {"teamId": 1}, "away": {"teamId": 3}, "winner": "UNDECIDED"},
    ]}

    assert not is_season_in_progress(finished)
    assert is_season_in_progress(in_progress)


def test_needs_probe_backfills_missing_week_list_once():
    from datetime import datetime, timezone
    from app import needs_probe

    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    known = {"discovered": True, "status": "snapshot_computed", "last_checked_at": None}

    assert needs_probe(2019, known, now, has_week_list=False)
    assert not needs_probe(2019, known, now, has_week_list=True)



@patch('app.requests.get')
def test_fetch_espn_league_data_uses_league_history_before_2018(mock_get):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = [{"seasonId": 2017, "teams": [], "schedule": []}]
    mock_get.return_value = mock_response

    data = fetch_espn_league_data("123", 2017)

    assert data == {"seasonId": 2017, "teams": [], "schedule": []}
    url = mock_get.call_args.args[0]
    assert url.endswith("/leagueHistory/123")
    assert mock_get.call_args.kwargs["params"]["seasonId"] == 2017


def test_needs_probe_retries_pre_2018_404s_from_old_endpoint_once():
    from datetime import datetime, timezone
    from app import needs_probe

    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    def status(state):
        return {"discovered": False, "status": state, "last_checked_at": None}

    assert needs_probe(2017, status("http_404"), now)  # from the season endpoint
    assert not needs_probe(2017, status("history_http_404"), now)  # leagueHistory said no
    assert needs_probe(2017, status("history_http_503"), now)  # transient
    assert not needs_probe(2018, status("http_404"), now)
