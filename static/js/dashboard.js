async function loadData() {
  const response = await fetch("/api/data/latest");
  const data = await response.json();
  console.log(data);
  // TODO: vykresliť graf Chart.js
}

loadData();
