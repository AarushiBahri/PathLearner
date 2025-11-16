import { useState } from "react";
import axios from "axios";
import { v4 as uuidv4 } from "uuid";

import { ReactFlow, Controls, Background } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./App.css";

export default function App() {
  const updateNodeProgress = (newStatus) => {
  setNodes((prev) =>
    prev.map((n) => {
      if (n.data.label === selectedTopic.label) {
        return {
          ...n,
          data: { ...n.data, progress: newStatus },
          style: makeNodeStyle(newStatus)
        };
      }
      return n;
    })
  );

  // Update selectedTopic so dropdown updates too
  setSelectedTopic({
    ...selectedTopic,
    progress: newStatus
  });
};

  const [goal, setGoal] = useState("");
  const [hours, setHours] = useState("");
  const [background, setBackground] = useState("");


  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);

  const [selectedTopic, setSelectedTopic] = useState(null);
  const [summary, setSummary] = useState("");
  const [materials, setMaterials] = useState([]);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingMaterials, setLoadingMaterials] = useState(false);
  const [confusionText, setConfusionText] = useState("");

  // NEW → loader for roadmap generation
  const [loadingRoadmap, setLoadingRoadmap] = useState(false);

  const makeNodeStyle = (progress) => {
  let bg = "#ffffff";   // default white

  if (progress === "in-progress") bg = "#f4f18eff";     // yellow
  if (progress === "completed") bg = "#5cf09aff";       // green

  return {
    padding: 12,
    borderRadius: 14,
    border: "1px solid #e2e4ea",
    background: bg,
    boxShadow: "0 8px 20px rgba(15, 23, 42, 0.06)",
    fontSize: 13,
    transition: "background 0.25s ease",
    cursor: "pointer",
  };
};


  // Generate roadmap from backend
  const generateRoadmap = async () => {
    try {
      setLoadingRoadmap(true); // START LOADER

      const resp = await axios.post("http://localhost:8000/generate_roadmap", {
        goal,
        time_per_week_hours: Number(hours),
        background,
      });

      const roadmap = resp.data.roadmap || [];

      const newNodes = roadmap.map((item, index) => ({
        id: String(item.id) || uuidv4(),
        data: { label: item.title || "Untitled", progress: "not-started"   // <— add this
        },

        position: { x: 300, y: index * 120 },
        style: makeNodeStyle("not-started"),
      }));

      const newEdges = roadmap
        .map((item, idx) => {
          if (idx === 0) return null;
          return {
            id: `e${idx - 1}-${idx}`,
            source: String(roadmap[idx - 1].id),
            target: String(roadmap[idx].id),
          };
        })
        .filter(Boolean);

      setNodes(newNodes);
      setEdges(newEdges);

      setSelectedTopic(null);
      setSummary("");
      setMaterials([]);
      setConfusionText("");

    } catch (err) {
      console.error(err);
      alert("Backend error — check console.");
    }

    setLoadingRoadmap(false); // STOP LOADER
  };

  // node click → summary & materials
  const onNodeClick = async (event, node) => {
  const topic = node.data.label;   // MUST BE DEFINED
  const progress = node.data.progress;

  setSelectedTopic({ label: topic, progress });

  setSummary("");
  setMaterials([]);
  setConfusionText("");

  // SUMMARY
  setLoadingSummary(true);
  try {
    const resp = await axios.post("http://localhost:8000/get_summary", {
      topic,
    });
    setSummary(resp.data.summary || "No summary available.");
  } catch (err) {
    console.error("Summary error:", err);
    setSummary("No summary available.");
  }
  setLoadingSummary(false);

  // MATERIALS
  setLoadingMaterials(true);
  try {
    const resp = await axios.post("http://localhost:8000/get_materials", {
      topic,
    });
    setMaterials(resp.data.resources || []);
  } catch (err) {
    console.error("Materials error:", err);
    setMaterials([]);
  }
  setLoadingMaterials(false);




    setSummary("");
    setMaterials([]);
    setConfusionText("");

    setLoadingSummary(true);
    try {
      const resp = await axios.post("http://localhost:8000/get_summary", {
        topic,
      });
      setSummary(resp.data.summary || "No summary available.");
    } catch (err) {
      console.error("Summary error:", err);
      setSummary("No summary available.");
    }
    setLoadingSummary(false);

    setLoadingMaterials(true);
    try {
      const resp = await axios.post("http://localhost:8000/get_materials", {
        topic,
      });
      setMaterials(resp.data.resources || []);
    } catch (err) {
      console.error("Materials error:", err);
      setMaterials([]);
    }
    setLoadingMaterials(false);
  };

  // Fix confusion → add prerequisite nodes
  const fixConfusion = async () => {
    if (!selectedTopic || !confusionText.trim()) {
      alert("Select a topic and describe your confusion first.");
      return;
    }

    try {
      const resp = await axios.post("http://localhost:8000/handle_confusion", {
        goal,
        current_topic: selectedTopic,
        confusion_text: confusionText,
        roadmap: nodes.map((n) => ({
          id: n.id,
          title: n.data.label,
        })),
      });

      const prereqs = resp.data.new_prereqs || [];
      if (prereqs.length === 0) {
        alert("No missing prerequisites detected.");
        return;
      }

      const idx = nodes.findIndex((n) => n.data.label === selectedTopic);
      if (idx === -1) return;

      const baseX = 60;
      const baseY = nodes[idx].position.y - 160;

      const updatedNodes = [...nodes];
      const updatedEdges = [...edges];

      prereqs.forEach((p, i) => {
        const id = p.id || uuidv4();
        const label = p.title || "Missing prerequisite";

        updatedNodes.push({
          id,
          data: { label, status: "not-started" },
          position: { x: baseX, y: baseY + i * 140 },
          style: makeNodeStyle("not-started"),
        });

        updatedEdges.push({
          id: `pre-${id}-${nodes[idx].id}`,
          source: id,
          target: nodes[idx].id,
        });
      });

      setNodes(updatedNodes);
      setEdges(updatedEdges);
      setConfusionText("");
      alert("Prerequisite topics added to your roadmap.");

    } catch (err) {
      console.error(err);
      alert("Error while adjusting roadmap.");
    }
  };

  return (
    <div className="app-root">
      <div className="bg-orbit bg-orbit-1" />
      <div className="bg-orbit bg-orbit-2" />

      {/* SIDEBAR */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <img src="/PathLearner.png" alt="PathLearner logo" className="logo-img"/>
          <div>
            <h1 className="app-title">PathLearn</h1>
            <p className="app-subtitle">AI-powered learning roadmap</p>
          </div>
        </div>

        <div className="sidebar-section">
          <label className="field-label">Learning goal</label>
          <input
          className="input-field"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Learn Machine Learning"/>

        </div>

        <div className="sidebar-grid">
          <div className="sidebar-section">
            <label className="field-label">Hours per week</label>
            <input
            type="number"
            className="input-field"
            value={hours}
            onChange={(e) => setHours(e.target.value)}
            placeholder="5"/>

          </div>
          <div className="sidebar-section">
            <label className="field-label">Background</label>
            <input 
            className="input-field"
            value={background}
            onChange={(e) => setBackground(e.target.value)}
            placeholder="Knows Python"/>

          </div>
        </div>

        <button className="primary-button" onClick={generateRoadmap}>
          Generate roadmap
        </button>

        <p className="hint-text">
          Click nodes to see explanations and add missing prerequisites.
        </p>
      </aside>

      {/* MAIN AREA */}
      <main className="main-layout">
        <div className="graph-shell">
          <div className="graph-toolbar">
            <span className="toolbar-title">Learning map</span>
            <span className="toolbar-subtitle">Drag to move • Scroll to zoom</span>
          </div>

          <div className="graph-wrapper" style={{ position: "relative" }}>
            
            {/* NEW: Roadmap loader */}
            {loadingRoadmap && (
              <div className="loader-overlay">
                <div className="spinner"></div>
                <p style={{ marginTop: 10 }}>Building your roadmap…</p>
              </div>
            )}

            <ReactFlow
  nodes={nodes}
  edges={edges}
  onNodeClick={onNodeClick}
  fitView
  panOnScroll={true}          // <— smooth scroll to pan
  zoomOnScroll={true}         // <— natural trackpad zoom
  zoomOnPinch={true}
  panOnDrag={true}            // <— click + drag to move the map
  style={{ width: "100%", height: "100%" }}
>

              <Background gap={24} color="#e5e7eb" />
              <Controls />
            </ReactFlow>
          </div>
        </div>

        {selectedTopic && (
          <section className="info-panel">
            <button className="close-panel-btn" onClick={() => setSelectedTopic(null)}>✕</button>

            <div className="info-header">
              <div className="pill-label">Topic</div>
              <h2 className="info-title">{selectedTopic.label}</h2>
            </div>
            <div className="info-block">
  <h3>Progress</h3>

  <select
    className="input-field"
    value={selectedTopic.progress}
    onChange={(e) => updateNodeProgress(e.target.value)}
  >
    <option value="not-started">Not started</option>
    <option value="in-progress">In progress</option>
    <option value="completed">Completed</option>
  </select>
</div>

            <div className="info-block">
              <h3>Summary</h3>
              {loadingSummary ? (
                <p className="muted-text">Generating a simple explanation…</p>
              ) : (
                <p className="body-text">{summary}</p>
              )}
            </div>

            <div className="info-block">
              <h3>Resources</h3>
              {loadingMaterials ? (
                <p className="muted-text">Finding resources…</p>
              ) : (
                <ul className="resource-list">
                  {materials.map((m, i) => (
                    <li key={i} className="resource-item">
                      <a href={m.url} target="_blank" rel="noreferrer">
                        {m.title}
                      </a>
                      <span className="resource-tag">{m.type}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="info-block">
              <h3>I'm confused about…</h3>
              <textarea
                className="textarea"
                placeholder="Type your confusion here. PathLearn will fix your RoadMap."
                value={confusionText}
                onChange={(e) => setConfusionText(e.target.value)}>
                  </textarea>
              <button className="secondary-button" onClick={fixConfusion}>
                Fix my roadmap
              </button>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
