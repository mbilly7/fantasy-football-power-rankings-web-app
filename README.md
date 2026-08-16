# Fantasy Football Power Rankings Web App

https://fantasy-football-power-rankings-web-app.onrender.com

A web application that generates and displays fantasy football power rankings based on real-time data from ESPN and FantasyPros APIs. The app calculates composite scores using metrics like record, PPG, consistency, overall wins, and ROS strength to rank teams in a league.

## Features

### Implemented
- **Data Integration**: Fetches league data from the ESPN Fantasy API and roster strength from FantasyPros.
- **Power Rankings Calculation**: Computes rankings using weighted metrics (record, PPG, consistency, overall wins, ROS strength) with tie-handling for accurate positioning.
- **Table Display**: Presents rankings in a clean, sortable table with columns for team name, record, overall wins, consistency, PPG, ROS strength, and PR score.

## Screenshots
![Fantasy Football Power Rankings](screenshots/app-screenshot.png)

### Future Enhancements
- Graph showing power rankings of each team over time
- Explanations of each column
- Displays of other stats, like records for example
- Historical stats
- AI-generated summary of each week's rankings

## Tech Stack
- **Backend**: Python, Flask
- **APIs**: ESPN Fantasy API, FantasyPros API
- **Frontend**: HTML, Jinja2 templates
- **Deployment**: Render (or similar cloud platform)

## Prerequisites
- Python 3.8+
- Environment variables for API keys and league details (see Setup)

## Setup
1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/fantasy-football-power-rankings-web-app.git
   cd fantasy-football-power-rankings-web-app
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a .env file with the following variables:
   ```
   SWID=your_espn_swid
   ESPN_S2=your_espn_s2
   LEAGUE_ID=your_league_id
   FANTASYPROS_KEY=your_fantasypros_key
   ```

4. Run the app locally:
   ```bash
   python app.py
   ```
   Visit `http://localhost:5000` to view the rankings.

## Testing
Run the unit tests to validate the core functionality:
```bash
python -m pytest tests/
```

## Deployment
The app is deployed on Render. To deploy your own instance:
- Push code to a GitHub repository.
- Connect the repo to Render, set environment variables, and deploy as a Python web service.
- Use the start command: `gunicorn --bind 0.0.0.0:$PORT app:app` (add `gunicorn` to requirements.txt).

## Contributing
Contributions are welcome! Please open an issue or submit a pull request for bug fixes, features, or improvements.

## License
This project is licensed under the MIT License. See `LICENSE` for details.
