const NUM_TEAMS = 12;
const NUM_COLS = 9;

const loadSettings = () => {
    fetch('./settings.json')
    .then((response) => response.json())
    .then((json) => console.log(json));
}

const initializeTable = () => {
    tblBody = document.getElementById("power-rankings-tbody")
    for (let i = 0; i < NUM_TEAMS; i++) {
        // creates a table row
        const row = document.createElement("tr");

        for (let j = 0; j < NUM_COLS; j++) {
            const cell = document.createElement("td");
              const cellText = document.createTextNode(`cell in row ${i}, column ${j}`);
              cell.appendChild(cellText);
            row.appendChild(cell);
        }

        tblBody.appendChild(row);
    }
}

loadSettings();
// getESPNFantasyData();
window.addEventListener('DOMContentLoaded', initializeTable)