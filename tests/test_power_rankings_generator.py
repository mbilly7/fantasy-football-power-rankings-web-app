import pytest
from power_rankings_generator import (
    get_current_week,
    get_team_info,
    calculate_team_scores,
    calculate_ppg,
    calculate_consistency,
    calculate_overall_wins_and_losses,
    build_power_scores,
    rank_teams_by_score,
    calculate_power_rankings,
    parse_fantasypros_ros,
    rank_stat,
)


@pytest.fixture
def sample_schedule():
    return [
        {"matchupPeriodId": 1, "winner": "HOME"},
        {"matchupPeriodId": 1, "winner": "AWAY"},
        {"matchupPeriodId": 2, "winner": "HOME"},
        {"matchupPeriodId": 2, "winner": "UNDECIDED"},
    ]


@pytest.fixture
def sample_teams():
    return [
        {"id": 1, "name": "Team A", "record": {"overall": {"wins": 2, "losses": 0, "ties": 0}}},
        {"id": 2, "name": "Team B", "record": {"overall": {"wins": 1, "losses": 1, "ties": 0}}},
        {"id": 3, "name": "Team C", "record": {"overall": {"wins": 0, "losses": 2, "ties": 0}}},
    ]


@pytest.fixture
def sample_schedule_with_scores():
    return [
        {
            "matchupPeriodId": 1,
            "home": {"teamId": 1, "totalPoints": 100.0},
            "away": {"teamId": 2, "totalPoints": 90.0},
            "winner": "HOME",
        },
        {
            "matchupPeriodId": 1,
            "home": {"teamId": 3, "totalPoints": 80.0},
            "away": {"teamId": 4, "totalPoints": 85.0},
            "winner": "AWAY",
        },
    ]


def test_get_current_week(sample_schedule):
    assert get_current_week(sample_schedule) == 2


def test_get_team_info(sample_teams):
    team_info = get_team_info(sample_teams)
    assert team_info[1]["team_name"] == "Team A"
    assert team_info[1]["record_score"] == 1.0  # 2 wins
    assert team_info[2]["record_score"] == 0.5  # 1 win, 1 loss


def test_calculate_team_scores(sample_schedule_with_scores):
    team_scores = calculate_team_scores(sample_schedule_with_scores)
    assert team_scores[1] == [100.0]
    assert team_scores[2] == [90.0]
    assert team_scores[3] == [80.0]
    assert team_scores[4] == [85.0]


def test_calculate_ppg(sample_schedule_with_scores):
    team_scores = calculate_team_scores(sample_schedule_with_scores)
    ppg, ppg_ranks = calculate_ppg(team_scores)
    assert ppg[1] == 100.0
    assert ppg_ranks[1] == 1  # Best PPG
    assert ppg_ranks[2] == 2


def test_calculate_consistency(sample_schedule_with_scores):
    team_scores = calculate_team_scores(sample_schedule_with_scores)
    ppg, _ = calculate_ppg(team_scores)
    consistency_scores, consistency_ranks = calculate_consistency(ppg, team_scores)
    assert isinstance(consistency_scores[1], float)
    assert consistency_ranks[1] == 1  # Assuming calculation


def test_calculate_overall_wins_and_losses(sample_schedule_with_scores):
    overall_wins, overall_losses, overall_ranks = calculate_overall_wins_and_losses(
        sample_schedule_with_scores, 4
    )
    assert overall_wins[1] == 3
    assert overall_losses[1] == 0
    assert overall_ranks[1] == 1 

def test_rank_stat():
    stat_dict = {1: 10, 2: 20, 3: 20}  # Ties
    ranks = rank_stat(stat_dict, reverse=True)
    assert ranks[2] == 1  # Highest
    assert ranks[3] == 1  # Tie
    assert ranks[1] == 3


def test_parse_fantasypros_ros():
    fpros_json = {
        "standings": [
            {"teamId": 1, "rank": 2},
            {"teamId": 2, "rank": 1},
        ]
    }
    teams = [{"id": 1}, {"id": 2}, {"id": 3}]
    ros_map = parse_fantasypros_ros(fpros_json, teams)
    assert ros_map[1] == 2
    assert ros_map[2] == 1
    assert ros_map[3] == 3  # Default


def test_calculate_power_rankings(sample_teams, sample_schedule_with_scores):
    data = {"teams": sample_teams, "schedule": sample_schedule_with_scores}
    ranked_teams = calculate_power_rankings(data)
    assert len(ranked_teams) == 3
    assert ranked_teams[0]["power_rank"] == 1
    assert "pr_score" in ranked_teams[0]

def test_build_power_scores(sample_teams):
    team_info = get_team_info(sample_teams)
    stats = {
        "ppg": {1: 100.0, 2: 90.0, 3: 80.0},
        "ppg_ranks": {1: 1, 2: 2, 3: 3},
        "consistency_scores": {1: 0.1, 2: 0.2, 3: 0.3},
        "consistency_ranks": {1: 3, 2: 2, 3: 1},
        "overall_wins": {1: 2, 2: 1, 3: 0},
        "overall_losses": {1: 0, 2: 1, 3: 2},
        "overall_ranks": {1: 1, 2: 2, 3: 3},
        "ros_strength": {1: 50, 2: 40, 3: 30},
        "ros_ranks": {1: 1, 2: 2, 3: 3},
        "record_ranks": {1: 1, 2: 2, 3: 3},
    }
    weights = {"record": 1.0, "overall": 1.0, "consistency": 1.0, "ppg": 1.0, "ros": 1.0}
    current_week = 3
    power_scores = build_power_scores(team_info, stats, weights, current_week)
    assert len(power_scores) == 3
    assert power_scores[0]["team_id"] == 1
    assert "pr_score" in power_scores[0]
    assert power_scores[0]["pr_score"] == 7.0  # 1+1+3+1+1


def test_rank_teams_by_score():
    power_scores = [
        {"team_id": 1, "pr_score": 10.0},
        {"team_id": 2, "pr_score": 5.0},
        {"team_id": 3, "pr_score": 15.0},
    ]
    ranked = rank_teams_by_score(power_scores)
    # Sorts by pr_score ascending (lower is better)
    assert ranked[0]["power_rank"] == 1
    assert ranked[0]["team_id"] == 2  # pr_score 5.0
    assert ranked[1]["team_id"] == 1  # 10.0
    assert ranked[2]["team_id"] == 3  # 15.0
    