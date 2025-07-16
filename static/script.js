const NUM_TEAMS = 12;
const NUM_COLS = 9;

const initializeTable = () => {
    tblBody = document.getElementById("power-rankings-tbody")
    for (let i = 0; i < NUM_TEAMS; i++) {
        // creates a table row
        const row = document.createElement("tr");

        for (let j = 0; j < NUM_COLS; j++) {
            const cell = document.createElement("td");
              const cellText = document.createTextNode(`row ${i}, column ${j}`);
              cell.appendChild(cellText);
            row.appendChild(cell);
        }

        tblBody.appendChild(row);
    }
}

window.addEventListener('DOMContentLoaded', initializeTable)