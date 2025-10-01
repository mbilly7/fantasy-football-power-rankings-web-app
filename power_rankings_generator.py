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

def calculate_ppg_and_consistency(team_scores):
    """
    Calculates points per game (PPG), consistency scores, and ranks for each team.
    
    Args:
        team_scores (dict): Mapping of team ID to list of scores.
    
    Returns:
        tuple: (team_ppg, team_consistency_scores, team_consistency_rank)
    """
    team_ppg = {tid: sum(scores)/len(scores) if scores else 0 for tid, scores in team_scores.items()}
    team_stdev = {tid: statistics.stdev(scores) if len(scores) > 1 else 0 for tid, scores in team_scores.items()}
    league_avg_ppg = sum(team_ppg.values()) / len(team_ppg) if team_ppg else 0

    team_consistency_scores = {
        tid: ((team_ppg[tid] - 3 * team_stdev[tid]) / (0.75 * league_avg_ppg)) if league_avg_ppg else 0
        for tid in team_ppg
    }

    sorted_consistency = sorted(team_consistency_scores.items(), key=lambda x: x[1], reverse=True)
    team_consistency_rank = {tid: rank+1 for rank, (tid, _) in enumerate(sorted_consistency)}

    return team_ppg, team_consistency_scores, team_consistency_rank

def calculate_overall_wins_and_losses(schedule, num_teams):
    """
    Calculates overall wins and losses for each team based on weekly scores.
    
    Args:
        schedule (list): List of matchup dictionaries.
        num_teams (int): Number of teams in the league.
    
    Returns:
        tuple: (overall_wins, overall_losses) as defaultdicts.
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

    return overall_wins, overall_losses

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

def build_power_scores(team_info, team_ppg, team_consistency_scores, team_consistency_rank, overall_wins, overall_losses, ros_strength, record_weight, current_week):
    """
    Combines all metrics to build a power score for each team.
    
    Args:
        team_info (dict): Team info mapping.
        team_ppg (dict): Points per game mapping.
        team_consistency_scores (dict): Consistency scores mapping.
        team_consistency_rank (dict): Consistency rank mapping.
        overall_wins (dict): Overall wins mapping.
        overall_losses (dict): Overall losses mapping.
        ros_strength (dict): Rest-of-season strength mapping.
        record_weight (float): Weight for record score.
        current_week (int): The current week number.
    
    Returns:
        list: List of team power score dictionaries.
    """
    # Compute ranks for each stat (lower is better)
    record_ranks = rank_stat({tid: info["record_score"] for tid, info in team_info.items()}, reverse=True)
    overall_ranks = rank_stat(overall_wins, reverse=True)
    consistency_ranks = rank_stat(team_consistency_scores, reverse=True)
    ppg_ranks = rank_stat(team_ppg, reverse=True)
    ros_ranks = rank_stat(ros_strength, reverse=True)

    power_scores = []
    for tid, info in team_info.items():
        record_val = record_ranks[tid] * record_weight
        overall_val = overall_ranks[tid] * 1.0
        consistency_val = consistency_ranks[tid] * (1.0 if current_week >= 3 else 0)
        ppg_val = ppg_ranks[tid] * 1.0
        ros_val = ros_ranks[tid] * 1.2

        pr_score = record_val + overall_val + consistency_val + ppg_val + ros_val

        power_scores.append({
            "team_id": tid,
            "team_name": info["team_name"],
            "record": info["record_str"],
            "overall_wins": f"{overall_wins[tid]}-{overall_losses[tid]}",
            "consistency": team_consistency_rank[tid],
            "ppg": round(team_ppg[tid], 2),
            "ros_strength": ros_strength.get(tid, 50),
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
    Main function to calculate power rankings for all teams.
    
    Args:
        data (dict): Dictionary containing 'teams' and 'schedule'.
        ros_strength (dict, optional): Rest-of-season strength mapping.
    
    Returns:
        list: Ranked list of team power score dictionaries.
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
    record_weight = 1.2 * current_week if current_week < 3 else 3

    team_info = get_team_info(teams)
    team_scores = calculate_team_scores(schedule)
    team_ppg, team_consistency_scores, team_consistency_rank = calculate_ppg_and_consistency(team_scores)
    overall_wins, overall_losses = calculate_overall_wins_and_losses(schedule, num_teams)
    power_scores = build_power_scores(
        team_info,
        team_ppg,
        team_consistency_scores,
        team_consistency_rank,
        overall_wins,
        overall_losses,
        ros_strength,
        record_weight,
        current_week
    )
    ranked_teams = rank_teams_by_score(power_scores)

    return ranked_teams
