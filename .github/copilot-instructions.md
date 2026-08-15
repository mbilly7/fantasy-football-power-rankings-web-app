# Copilot Instructions

## Commands

- Install dependencies: `python -m pip install -r requirements.txt`
- Run the application: `python app.py` (binds to `0.0.0.0` on `PORT`, default `5001`)
- Run all tests: `python -m pytest tests/`
- Run one test: `python -m pytest tests/test_power_rankings_generator.py::test_calculate_power_rankings`

Set `LEAGUE_ID` and `LEAGUE_SEASON` before loading `/`. `SWID`, `ESPN_S2`, and `FANTASYPROS_KEY` enable the corresponding upstream requests; `MAX_WEEK` limits the schedule used in ranking calculations. `DATABASE_URL` defaults to `sqlite:///rankings.db`.

## Architecture

- `app.py` owns the Flask route and upstream integration. The `/` route loads environment variables, returns the latest cached rankings for a season when present, otherwise fetches ESPN league data and optional FantasyPros rest-of-season standings, calculates rankings, saves them, and renders `templates/index.html`.
- `power_rankings_generator.py` is the pure ranking pipeline: transform ESPN teams and schedule into per-team stats, rank the stats, apply weights, then sort the composite power score. Keep API fetching and database access out of this module.
- `database.py` defines the `weekly_rankings` SQLAlchemy model and creates its table at import time. Rankings are stored as a JSON string and are replaced for an existing `(week, season)` pair; reads select the most recently updated row for a season.
- The template consumes the ranking dictionaries returned by `calculate_power_rankings`, including `power_rank`, `team_name`, `record`, `overall_wins`, `consistency`, `ppg`, `ros_strength`, and `pr_score`.

## Ranking and testing conventions

- All rank values use `1` for best, and a lower composite `pr_score` is better. `rank_stat` uses competition ranking for ties (`1, 1, 3`); retain this behavior when adding metrics.
- The record weight increases by completed week (`1.2` in week 1, `2.4` in week 2, then `3.0`); consistency does not contribute until week 3. FantasyPros ROS ranks are displayed and scored directly, with missing teams assigned the league size.
- Schedule-based calculations use only matchups with both `home` and `away`; `get_current_week` considers only decided winners. Apply `MAX_WEEK` filtering before deriving current week and all schedule statistics.
- Tests use pytest fixtures and import module functions directly. Database tests replace the module-level `database.Session` with an in-memory SQLite session and restore it in `finally`; preserve that isolation pattern. HTTP tests patch `app.requests.get`, not `requests.get` globally.
