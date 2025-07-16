from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def index():
    rankings = [
        {"team": "Team A", "score": 95},
        {"team": "Team B", "score": 89},
        {"team": "Team C", "score": 75}
    ]
    return render_template("index.html", rankings=rankings)

if __name__ == "__main__":
    app.run(debug=True)
    