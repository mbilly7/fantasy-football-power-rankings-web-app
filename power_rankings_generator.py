import os
import statistics
from collections import defaultdict
from dotenv import load_dotenv

def get_current_week(schedule):
    return max(
        m["matchupPeriodId"]
        for m in schedule
        if m.get("winner") and m["winner"] != "UNDECIDED"
    )

def get_team_info(teams):
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
    team_scores = defaultdict(list)
    for match in schedule:
        if "home" in match and "away" in match:
            team_scores[match["home"]["teamId"]].append(match["home"]["totalPoints"])
            team_scores[match["away"]["teamId"]].append(match["away"]["totalPoints"])
    return team_scores

def calculate_ppg_and_consistency(team_scores):
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

def build_power_scores(team_info, team_ppg, team_consistency_scores, team_consistency_rank, overall_wins, overall_losses, ros_strength, record_weight):
    power_scores = []
    for tid, info in team_info.items():
        record_val = info["record_score"] * record_weight
        overall_val = overall_wins[tid] * 1.0
        consistency_val = team_consistency_scores[tid] * 1.0
        ppg_val = team_ppg[tid] * 1.0
        ros_val = ros_strength.get(tid, 50) * 1.2

        pr_score = record_val + overall_val + consistency_val + ppg_val + ros_val

        power_scores.append({
            "team_id": tid,
            "team_name": info["team_name"],
            "record": info["record_str"],
            "overall_wins": f"{overall_wins[tid]}-{overall_losses[tid]}",
            "consistency": team_consistency_rank[tid],
            "ppg": round(ppg_val, 2),
            "ros_strength": ros_strength.get(tid, 50),
            "pr_score": round(pr_score, 2),
        })

    return power_scores

def rank_teams_by_score(power_scores):
    sorted_scores = sorted(power_scores, key=lambda x: x["pr_score"], reverse=True)
    for rank, entry in enumerate(sorted_scores, start=1):
        entry["power_rank"] = rank
        entry["change"] = "–"  # Placeholder
    return sorted_scores

def calculate_power_rankings(data, ros_strength=None):
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
    power_scores = build_power_scores(team_info, team_ppg, team_consistency_scores, team_consistency_rank, overall_wins, overall_losses, ros_strength, record_weight)
    ranked_teams = rank_teams_by_score(power_scores)

    return ranked_teams
