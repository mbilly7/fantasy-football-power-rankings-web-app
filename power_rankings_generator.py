import os
import statistics
from collections import defaultdict
from dotenv import load_dotenv

def get_current_week(schedule):
    """
    Returns the current week number based on the schedule.
    Only considers matchups with a decided winner.
    
    Args:
        schedule (list): List of matchup dictionaries.
    
    Returns:
        int: The highest matchupPeriodId with a decided winner.
    """
    return max(
        m["matchupPeriodId"]
        for m in schedule
        if m.get("winner") and m["winner"] != "UNDECIDED"
    )

def get_team_info(teams):
    """
    Extracts basic team information and computes record score.
    
    Args:
        teams (list): List of team dictionaries.
    
    Returns:
        dict: Mapping of team ID to team info dictionary.
    """
    team_info = {}
    for team in teams:
        tid = team["id"]
        name = team["name"]
        wins = team["record"]["overall"]["wins"]
        losses = team["record"]["overall"]["losses"]
        ties = team["record"]["overall"]["ties"]
        games_played = wins + losses + ties
        record_score = (wins + 0.5 * ties) / games_played if games_played else 0

        team_info[tid] = {
            "team_name": name,
            "record_str": f"{wins}-{losses}-{ties}",
            "record_score": record_score,
            "games_played": games_played
        }
    return team_info

def calculate_team_scores(schedule):
    """
    Aggregates total points scored by each team per matchup.
    
    Args:
        schedule (list): List of matchup dictionaries.
    
    Returns:
        defaultdict: Mapping of team ID to list of scores.
    """
    team_scores = defaultdict(list)
    for match in schedule:
        if "home" in match and "away" in match:
            team_scores[match["home"]["teamId"]].append(match["home"]["totalPoints"])
            team_scores[match["away"]["teamId"]].append(match["away"]["totalPoints"])
    return team_scores

def calculate_ppg(team_scores):
    """
    Calculate points-per-game (PPG) per team and return PPG values and PPG ranks.
    
    Args:
        team_scores (dict): Mapping of team ID to list of scores.
    
    Returns:
        tuple: (team_ppg, ppg_ranks)
            team_ppg (dict): {team_id: ppg}
            ppg_ranks (dict): {team_id: rank} (1 = best)
    """
    team_ppg = {tid: sum(scores) / len(scores) if scores else 0 for tid, scores in team_scores.items()}
    ppg_ranks = rank_stat(team_ppg, reverse=True)
    return team_ppg, ppg_ranks

def calculate_consistency(team_ppg, team_scores):
    """
    Calculate consistency score per team and return scores and ranks.
    
    Consistency formula: ((PPG - 3 * STDDEV) / (0.75 * league_avg_ppg))
    
    Args:
        team_ppg (dict): Mapping of team ID to PPG.
        team_scores (dict): Mapping of team ID to list of scores.
    
    Returns:
        tuple: (team_consistency_scores, consistency_ranks)
            team_consistency_scores (dict): raw consistency values
            consistency_ranks (dict): {team_id: rank} (1 = best)
    """
    team_stdev = {tid: statistics.stdev(scores) if len(scores) > 1 else 0 for tid, scores in team_scores.items()}
    league_avg_ppg = sum(team_ppg.values()) / len(team_ppg) if team_ppg else 0

    team_consistency_scores = {
        tid: ((team_ppg[tid] - 3 * team_stdev[tid]) / (0.75 * league_avg_ppg)) if league_avg_ppg else 0
        for tid in team_ppg
    }

    consistency_ranks = rank_stat(team_consistency_scores, reverse=True)
    return team_consistency_scores, consistency_ranks

def calculate_overall_wins_and_losses(schedule, num_teams):
    """
    Calculates overall wins and losses for each team based on weekly scores,
    and returns overall wins/losses plus overall ranks (ties allowed).
    
    Args:
        schedule (list): List of matchup dictionaries.
        num_teams (int): Number of teams in the league.
    
    Returns:
        tuple: (overall_wins, overall_losses, overall_ranks)
    """
    overall_wins = defaultdict(int)
    overall_losses = defaultdict(int)
    weekly_matchups = defaultdict(list)

    for match in schedule:
        if "home" in match and "away" in match:
            week = match["matchupPeriodId"]
            weekly_matchups[week].extend([
                (match["home"]["teamId"], match["home"]["totalPoints"]),
                (match["away"]["teamId"], match["away"]["totalPoints"])
            ])

    for week, matchups in weekly_matchups.items():
        sorted_week = sorted(matchups, key=lambda x: x[1], reverse=True)
        for i, (tid, _) in enumerate(sorted_week):
            wins = num_teams - 1 - i
            losses = i
            overall_wins[tid] += wins
            overall_losses[tid] += losses

    overall_ranks = rank_stat(overall_wins, reverse=True)
    return overall_wins, overall_losses, overall_ranks

def rank_stat(stat_dict, reverse=True):
    """
    Assigns ranks to each team for a given stat.
    Lower rank is better (1 is best).
    Args:
        stat_dict (dict): Mapping of team ID to stat value.
        reverse (bool): If True, higher stat is better (rank 1). If False, lower stat is better.
    Returns:
        dict: Mapping of team ID to rank (1-based).
    """
    sorted_items = sorted(stat_dict.items(), key=lambda x: x[1], reverse=reverse)
    ranks = {}
    prev_value = None
    prev_rank = 0
    for idx, (tid, value) in enumerate(sorted_items, start=1):
        if value == prev_value:
            ranks[tid] = prev_rank
        else:
            ranks[tid] = idx
            prev_rank = idx
            prev_value = value
    return ranks

def build_power_scores(team_info, stats, weights, current_week):
    """
    Combines all metrics to build a power score for each team using rank-based scoring.
    Lower PR score is better.

    Args:
        team_info (dict): Team info mapping.
        stats (dict): Dict containing per-team stats and ranks. Expected keys:
            - 'ppg', 'ppg_ranks'
            - 'consistency_scores', 'consistency_ranks'
            - 'overall_wins', 'overall_losses', 'overall_ranks'
            - 'ros_strength', 'ros_ranks'
        weights (dict): Weights for categories. Expected keys: 'record','overall','consistency','ppg','ros'
        current_week (int): Current week number.

    Returns:
        list: List of team power score dictionaries.
    """
    # record ranks derived from team_info
    record_ranks = rank_stat({tid: info["record_score"] for tid, info in team_info.items()}, reverse=True)

    power_scores = []
    for tid, info in team_info.items():
        record_val = record_ranks[tid] * weights.get("record", 1.0)
        overall_val = stats["overall_ranks"][tid] * weights.get("overall", 1.0)
        consistency_val = stats["consistency_ranks"][tid] * weights.get("consistency", 1.0) if current_week >= 3 else 0
        ppg_val = stats["ppg_ranks"][tid] * weights.get("ppg", 1.0)
        ros_val = stats["ros_ranks"][tid] * weights.get("ros", 1.0)

        pr_score = record_val + overall_val + consistency_val + ppg_val + ros_val

        power_scores.append({
            "team_id": tid,
            "team_name": info["team_name"],
            "record": info["record_str"],
            "overall_wins": f"{stats['overall_wins'][tid]}-{stats['overall_losses'][tid]}",
            "consistency": stats["consistency_ranks"].get(tid, None),
            "ppg": round(stats["ppg"].get(tid, 0), 2),
            "ros_strength": stats["ros_strength"].get(tid, 50),
            "pr_score": round(pr_score, 2),
        })

    return power_scores

def rank_teams_by_score(power_scores):
    """
    Ranks teams by their power score and assigns rank and change.
    
    Args:
        power_scores (list): List of team power score dictionaries.
    
    Returns:
        list: Sorted list of team power score dictionaries with rank.
    """
    sorted_scores = sorted(power_scores, key=lambda x: x["pr_score"])
    for rank, entry in enumerate(sorted_scores, start=1):
        entry["power_rank"] = rank
        entry["change"] = "–"  # Placeholder
    return sorted_scores

def calculate_power_rankings(data, ros_strength=None):
    """
    Calculate power rankings for all teams.

    This function orchestrates the entire power ranking computation:
    - Loads optional MAX_WEEK from the environment to limit schedule processing.
    - Builds per-team info and per-team stats (PPG, consistency, overall wins/losses, ROS).
    - Converts raw stats into ranks (ties allowed) and combines those ranks using
      configured weights to produce a composite PR score (lower is better).
    - Returns a list of teams sorted by PR score with assigned power_rank values.

    Args:
        data (dict): Dictionary containing 'teams' (list of team dicts) and
                     'schedule' (list of matchup dicts).
        ros_strength (dict, optional): Rest-of-season roster strength mapping
                                       keyed by team ID. If not provided, a
                                       default of 50 is used for each team.

    Returns:
        list: Ranked list of team power score dictionaries (best team first).
    """
    load_dotenv()
    max_week = os.getenv("MAX_WEEK")
    max_week = int(max_week) if max_week else None

    teams = data["teams"]
    schedule = data["schedule"]

    if max_week:
        schedule = [m for m in schedule if m.get("matchupPeriodId", 0) <= max_week]

    num_teams = len(teams)
    ros_strength = ros_strength or {team["id"]: 50 for team in teams}

    current_week = get_current_week(schedule)
    team_info = get_team_info(teams)
    team_scores = calculate_team_scores(schedule)

    # split PPG and consistency calculations
    team_ppg, ppg_ranks = calculate_ppg(team_scores)
    consistency_scores, consistency_ranks = calculate_consistency(team_ppg, team_scores)

    overall_wins, overall_losses, overall_ranks = calculate_overall_wins_and_losses(schedule, num_teams)
    ros_ranks = rank_stat(ros_strength, reverse=True)

    record_weight = 1.2 * current_week if current_week < 3 else 3
    stats = {
        "ppg": team_ppg,
        "ppg_ranks": ppg_ranks,
        "consistency_scores": consistency_scores,
        "consistency_ranks": consistency_ranks,
        "overall_wins": overall_wins,
        "overall_losses": overall_losses,
        "overall_ranks": overall_ranks,
        "ros_strength": ros_strength,
        "ros_ranks": ros_ranks,
    }

    weights = {
        "record": record_weight,
        "overall": 1.0,
        "consistency": 1.0,
        "ppg": 1.0,
        "ros": 1.2,
    }

    power_scores = build_power_scores(team_info, stats, weights, current_week)
    ranked_teams = rank_teams_by_score(power_scores)

    return ranked_teams
