const $ = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="dag-csrf"]')?.content || "";
let dag = {
  schema_version: 1,
  id: crypto.randomUUID(),
  title: "Untitled DAG",
  nodes: [],
  edges: [],
  revision: 0,
  status: "draft",
};

function render() {
  $("title").value = dag.title;
  $("items").textContent = `${dag.nodes.length} nodes, ${dag.edges.length} edges`;
}

$("new").onclick = () => {
  dag = { ...dag, id: crypto.randomUUID(), title: "Untitled DAG", nodes: [], edges: [], revision: 0 };
  render();
};
$("addNode").onclick = () => {
  const id = prompt("Node ID");
  const label = id && prompt("Label", id);
  if (id && label) {
    dag.nodes.push({ id, label, role: "unknown", position: { x: 100 + dag.nodes.length * 30, y: 100 } });
    render();
  }
};
$("addEdge").onclick = () => {
  const source_node_id = prompt("Source node ID");
  const target_node_id = prompt("Target node ID");
  if (source_node_id && target_node_id) {
    dag.edges.push({ id: crypto.randomUUID(), source_node_id, target_node_id, relationship: "causes" });
    render();
  }
};
$("title").onchange = (event) => { dag.title = event.target.value; };
$("save").onclick = async () => {
  const response = await fetch("./api/dags", {
    method: dag.revision ? "PUT" : "POST",
    headers: { "Content-Type": "application/json", "X-DAG-CSRF": csrf },
    body: JSON.stringify(dag),
  });
  const result = await response.json();
  if (result.ok) { dag = result.data; render(); } else { alert(result.error.message); }
};
$("validate").onclick = async () => {
  const response = await fetch(`./api/dags/${encodeURIComponent(dag.id)}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-DAG-CSRF": csrf },
    body: JSON.stringify(dag),
  });
  const result = await response.json();
  $("findings").textContent = result.ok
    ? result.data.map((finding) => `${finding.severity}: ${finding.message}`).join("\n")
    : result.error.message;
};
$("export").onclick = () => {
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(new Blob([JSON.stringify(dag, null, 2)], { type: "application/json" }));
  anchor.download = `${dag.id}.json`;
  anchor.click();
};
render();
