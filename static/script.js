async function loadChart() {
    const res = await fetch("/api/data/latest");
    const data = await res.json();

    const labels = data.map(d => d.code);
    const values = data.map(d => d.severity === "critical" ? 3 : d.severity === "medium" ? 2 : 1);

    const ctx = document.getElementById("chart").getContext("2d");
    new Chart(ctx, {
        type: "bar",
        data: {
            labels: labels,
            datasets: [{
                label: "Závažnosť chýb",
                data: values
            }]
        }
    });
}

loadChart();
