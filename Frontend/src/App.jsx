import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { v4 as uuidv4 } from "uuid";
import "./App.css";

export default function App() {
  const API_BASE = "http://localhost:8000";

  const getErrorMessage = (err, fallback) => {
    return (
      err?.response?.data?.detail ||
      err?.response?.data?.error ||
      err?.message ||
      fallback
    );
  };

  const getClientId = () => {
    const saved = localStorage.getItem("pathlearner_client_id");
    if (saved) return saved;
    const nextId = uuidv4();
    localStorage.setItem("pathlearner_client_id", nextId);
    return nextId;
  };

  const [goal, setGoal] = useState("");
  const [hours, setHours] = useState("");
  const [background, setBackground] = useState("");
  const [clientId, setClientId] = useState("");

  const [nodes, setNodes] = useState([]);

  const [selectedTopic, setSelectedTopic] = useState(null);
  const [summary, setSummary] = useState("");
  const [materials, setMaterials] = useState([]);
  const [diagnosticQuiz, setDiagnosticQuiz] = useState(null);
  const [diagnosticAnswers, setDiagnosticAnswers] = useState([]);
  const [diagnosticResult, setDiagnosticResult] = useState(null);
  const [loadingDiagnostic, setLoadingDiagnostic] = useState(false);
  const [submittingDiagnostic, setSubmittingDiagnostic] = useState(false);
  const [topicQuiz, setTopicQuiz] = useState(null);
  const [topicQuizAnswers, setTopicQuizAnswers] = useState([]);
  const [topicQuizResult, setTopicQuizResult] = useState(null);
  const [loadingTopicQuiz, setLoadingTopicQuiz] = useState(false);
  const [submittingTopicQuiz, setSubmittingTopicQuiz] = useState(false);
  const [seenResourceTitles, setSeenResourceTitles] = useState([]);
  const [resourceFilters, setResourceFilters] = useState({
    pricing: "any",
    resourceType: "any",
    difficulty: "any",
  });
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingMaterials, setLoadingMaterials] = useState(false);
  const [confusionText, setConfusionText] = useState("");
  const materialsRequestRef = useRef(0);
  const materialsStreamRef = useRef(null);

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

  const mapRoadmapToNodes = (roadmap) =>
    roadmap.map((item) => ({
      id: String(item.id) || uuidv4(),
      data: {
        label: item.title || "Untitled",
        progress: item.progress || "not-started",
        level: item.level || "basic",
        confidenceScore: item.confidence_score ?? null,
        confidenceLabel: item.confidence_label || "unknown",
      },
      description: item.description || "",
      estimatedHours: item.estimated_hours || item.estimatedHours || null,
      style: makeNodeStyle(item.progress || "not-started"),
    }));

  const fetchMaterials = async (topic, progress, filters = resourceFilters) => {
    const requestId = materialsRequestRef.current + 1;
    materialsRequestRef.current = requestId;
    if (materialsStreamRef.current) {
      materialsStreamRef.current.close();
      materialsStreamRef.current = null;
    }
    setLoadingMaterials(true);
    setMaterials([]);
    try {
      const params = new URLSearchParams({
        topic,
        progress: progress || "",
        seen_titles: JSON.stringify(seenResourceTitles),
        pricing: filters.pricing,
        resource_type: filters.resourceType,
        difficulty: filters.difficulty,
      });
      const stream = new EventSource(`${API_BASE}/stream_materials?${params.toString()}`);
      materialsStreamRef.current = stream;

      stream.onmessage = (event) => {
        if (materialsRequestRef.current !== requestId) {
          stream.close();
          return;
        }

        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "resource" && payload.resource) {
            setMaterials((prev) => {
              const exists = prev.some((item) => item.title === payload.resource.title);
              if (exists) return prev;
              return [...prev, payload.resource];
            });
          }

          if (payload.type === "done") {
            const nextMaterials = payload.resources || [];
            setMaterials(nextMaterials);
            setSeenResourceTitles((prev) => {
              const merged = new Set(prev);
              nextMaterials.forEach((item) => merged.add(item.title));
              return Array.from(merged);
            });
            setLoadingMaterials(false);
            stream.close();
            materialsStreamRef.current = null;
          }
        } catch (parseError) {
          console.error("Streaming parse error:", parseError);
        }
      };

      stream.onerror = async () => {
        stream.close();
        materialsStreamRef.current = null;
        if (materialsRequestRef.current !== requestId) {
          return;
        }
        try {
          const resp = await axios.post(`${API_BASE}/get_materials`, {
            topic,
            progress,
            seen_titles: seenResourceTitles,
            pricing: filters.pricing,
            resource_type: filters.resourceType,
            difficulty: filters.difficulty,
          });
          if (materialsRequestRef.current !== requestId) {
            return;
          }
          const nextMaterials = resp.data.resources || [];
          setMaterials(nextMaterials);
          setSeenResourceTitles((prev) => {
            const merged = new Set(prev);
            nextMaterials.forEach((item) => merged.add(item.title));
            return Array.from(merged);
          });
        } catch (err) {
          if (materialsRequestRef.current !== requestId) {
            return;
          }
          console.error("Materials error:", err);
          setMaterials([]);
        }
        setLoadingMaterials(false);
      };
    } catch (err) {
      if (materialsRequestRef.current !== requestId) {
        return;
      }
      console.error("Materials error:", err);
      setMaterials([]);
      setLoadingMaterials(false);
    }
  };

  useEffect(() => {
    const bootstrap = async () => {
      const nextClientId = getClientId();
      setClientId(nextClientId);

      try {
        await axios.post(`${API_BASE}/users/ensure`, {
          client_id: nextClientId,
        });

        const resp = await axios.get(
          `${API_BASE}/users/${nextClientId}/roadmaps/latest`
        );
        const latest = resp.data.roadmap;
        if (!latest) return;

        setGoal(latest.goal || "");
        setHours(String(latest.time_per_week_hours || ""));
        setBackground(latest.background || "");
        setNodes(mapRoadmapToNodes(latest.roadmap || []));
      } catch (err) {
        console.error("Load roadmap error:", err);
      }
    };

    bootstrap();
    return () => {
      if (materialsStreamRef.current) {
        materialsStreamRef.current.close();
        materialsStreamRef.current = null;
      }
    };
  }, []);

  const updateNodeProgress = async (newStatus) => {
    if (!selectedTopic) return;

    setNodes((prev) =>
      prev.map((n) => {
        if (n.id === selectedTopic.id) {
          return {
            ...n,
            data: { ...n.data, progress: newStatus },
            style: makeNodeStyle(newStatus),
          };
        }
        return n;
      })
    );

    setSelectedTopic((prev) =>
      prev ? { ...prev, progress: newStatus } : prev
    );

    try {
      await axios.patch(
        `${API_BASE}/users/${clientId}/roadmaps/latest/items/${selectedTopic.id}/progress`,
        { progress: newStatus }
      );
    } catch (err) {
      console.error("Progress update error:", err);
    }
  };

  // Generate roadmap from backend
  const generateRoadmap = async () => {
    try {
      setLoadingRoadmap(true); // START LOADER

      const resp = await axios.post(`${API_BASE}/generate_roadmap`, {
        client_id: clientId,
        goal,
        time_per_week_hours: Number(hours),
        background,
        diagnostic_result: diagnosticResult,
      });

      const roadmap = resp.data.roadmap || [];
      setNodes(mapRoadmapToNodes(roadmap));

      setSelectedTopic(null);
      setSummary("");
      setMaterials([]);
      setResourceFilters({
        pricing: "any",
        resourceType: "any",
        difficulty: "any",
      });
      setSeenResourceTitles([]);
      setConfusionText("");
      setTopicQuiz(null);
      setTopicQuizAnswers([]);
      setTopicQuizResult(null);

    } catch (err) {
      const message = getErrorMessage(err, "Backend error.");
      console.error("Roadmap error:", err);
      alert(message);
    }

    setLoadingRoadmap(false); // STOP LOADER
  };

  // node click → summary & materials
  const onNodeClick = async (event, node) => {
    const topic = node.data.label;
    const progress = node.data.progress;
    const level = node.data.level;
    const confidenceScore = node.data.confidenceScore;
    const confidenceLabel = node.data.confidenceLabel;

    setSelectedTopic({
      id: node.id,
      label: topic,
      progress,
      level,
      confidenceScore,
      confidenceLabel,
    });
    setSummary("");
    setMaterials([]);
    setTopicQuiz(null);
    setTopicQuizAnswers([]);
    setTopicQuizResult(null);
    materialsRequestRef.current += 1;
    setResourceFilters({
      pricing: "any",
      resourceType: "any",
      difficulty: "any",
    });
    setConfusionText("");

    setLoadingSummary(true);
    try {
      const resp = await axios.post(`${API_BASE}/get_summary`, {
        topic,
      });
      setSummary(resp.data.summary || "No summary available.");
    } catch (err) {
      console.error("Summary error:", err);
      setSummary("No summary available.");
    }
    setLoadingSummary(false);

    await fetchMaterials(topic, progress, {
      pricing: "any",
      resourceType: "any",
      difficulty: "any",
    });
  };

  useEffect(() => {
    if (!selectedTopic) return;
    fetchMaterials(selectedTopic.label, selectedTopic.progress);
  }, [resourceFilters]);

  // Fix confusion → add prerequisite nodes
  const fixConfusion = async () => {
    if (!selectedTopic || !confusionText.trim()) {
      alert("Select a topic and describe your confusion first.");
      return;
    }

    try {
      const resp = await axios.post(`${API_BASE}/handle_confusion`, {
        client_id: clientId,
        goal,
        current_topic: selectedTopic.label,
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

      const idx = nodes.findIndex((n) => n.id === selectedTopic.id);
      if (idx === -1) return;

      const existingLabels = new Set(nodes.map((n) => n.data.label.trim().toLowerCase()));
      const newPrereqNodes = prereqs
        .map((p) => ({
          id: p.id || uuidv4(),
          data: {
            label: p.title || "Missing prerequisite",
            progress: "not-started",
            level: "basic",
          },
        }))
        .filter((node) => {
          const normalized = node.data.label.trim().toLowerCase();
          if (existingLabels.has(normalized)) return false;
          existingLabels.add(normalized);
          return true;
        });

      if (newPrereqNodes.length === 0) {
        alert("Those prerequisite topics are already in your roadmap.");
        return;
      }

      setNodes((prev) => [
        ...prev.slice(0, idx),
        ...newPrereqNodes.map((node) => ({
          ...node,
          description:
            prereqs.find((item) => item.id === node.id)?.description || "",
          estimatedHours:
            prereqs.find((item) => item.id === node.id)?.estimated_hours || null,
          style: makeNodeStyle("not-started"),
        })),
        ...prev.slice(idx),
      ]);
      setConfusionText("");
      alert("Prerequisite topics added to your roadmap.");

    } catch (err) {
      const message = getErrorMessage(err, "Error while adjusting roadmap.");
      console.error("Confusion error:", err);
      alert(message);
    }
  };

  const startDiagnostic = async () => {
    if (!goal.trim()) {
      alert("Add your learning goal first.");
      return;
    }

    try {
      setLoadingDiagnostic(true);
      setDiagnosticResult(null);
      const resp = await axios.post(`${API_BASE}/diagnostic/generate`, {
        client_id: clientId,
        goal,
        background,
      });
      const quiz = resp.data;
      setDiagnosticQuiz(quiz);
      setDiagnosticAnswers(new Array((quiz.questions || []).length).fill(-1));
    } catch (err) {
      alert(getErrorMessage(err, "Could not generate the diagnostic quiz."));
    }
    setLoadingDiagnostic(false);
  };

  const submitDiagnostic = async () => {
    if (!diagnosticQuiz) return;
    if (diagnosticAnswers.some((answer) => answer < 0)) {
      alert("Answer all diagnostic questions first.");
      return;
    }

    try {
      setSubmittingDiagnostic(true);
      const resp = await axios.post(`${API_BASE}/quiz/submit`, {
        client_id: clientId,
        session_id: diagnosticQuiz.session_id,
        answers: diagnosticAnswers,
      });
      setDiagnosticResult(resp.data);
    } catch (err) {
      alert(getErrorMessage(err, "Could not submit the diagnostic."));
    }
    setSubmittingDiagnostic(false);
  };

  const startTopicQuiz = async () => {
    if (!selectedTopic) return;

    try {
      setLoadingTopicQuiz(true);
      setTopicQuizResult(null);
      const resp = await axios.post(`${API_BASE}/topic-quiz/generate`, {
        client_id: clientId,
        topic: selectedTopic.label,
        roadmap_item_id: selectedTopic.id,
        goal,
        background,
        level: selectedTopic.level,
      });
      const quiz = resp.data;
      setTopicQuiz(quiz);
      setTopicQuizAnswers(new Array((quiz.questions || []).length).fill(-1));
    } catch (err) {
      alert(getErrorMessage(err, "Could not generate the topic quiz."));
    }
    setLoadingTopicQuiz(false);
  };

  const submitTopicQuiz = async () => {
    if (!topicQuiz || !selectedTopic) return;
    if (topicQuizAnswers.some((answer) => answer < 0)) {
      alert("Answer all quiz questions first.");
      return;
    }

    try {
      setSubmittingTopicQuiz(true);
      const resp = await axios.post(`${API_BASE}/quiz/submit`, {
        client_id: clientId,
        session_id: topicQuiz.session_id,
        answers: topicQuizAnswers,
      });
      const result = resp.data;
      setTopicQuizResult(result);

      if (result.roadmap?.roadmap) {
        const updatedNodes = mapRoadmapToNodes(result.roadmap.roadmap);
        setNodes(updatedNodes);
        const nextSelected = updatedNodes.find((node) => node.id === selectedTopic.id);
        if (nextSelected) {
          setSelectedTopic({
            id: nextSelected.id,
            label: nextSelected.data.label,
            progress: nextSelected.data.progress,
            level: nextSelected.data.level,
            confidenceScore: nextSelected.data.confidenceScore,
            confidenceLabel: nextSelected.data.confidenceLabel,
          });
        }
      } else {
        setNodes((prev) =>
          prev.map((node) =>
            node.id === selectedTopic.id
              ? {
                  ...node,
                  data: {
                    ...node.data,
                    confidenceScore: result.confidence_score,
                    confidenceLabel: result.confidence_label,
                  },
                }
              : node
          )
        );
        setSelectedTopic((prev) =>
          prev
            ? {
                ...prev,
                confidenceScore: result.confidence_score,
                confidenceLabel: result.confidence_label,
              }
            : prev
        );
      }
    } catch (err) {
      alert(getErrorMessage(err, "Could not submit the topic quiz."));
    }
    setSubmittingTopicQuiz(false);
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
        <button className="secondary-button sidebar-secondary-button" onClick={startDiagnostic}>
          {loadingDiagnostic ? "Preparing diagnostic..." : "Take diagnostic assessment"}
        </button>

        <p className="hint-text">
          Take the diagnostic first if you want the roadmap to adapt to your starting level.
        </p>

        {(diagnosticQuiz || diagnosticResult) && (
          <div className="sidebar-diagnostic">
            <h3 className="sidebar-card-title">Diagnostic assessment</h3>
            {diagnosticQuiz && !diagnosticResult ? (
              <div className="quiz-stack">
                <p className="muted-text">Quick check before you start.</p>
                {diagnosticQuiz.questions.map((question, index) => (
                  <div key={question.id || index} className="quiz-card">
                    <div className="quiz-question">
                      {index + 1}. {question.question}
                    </div>
                    <div className="quiz-options">
                      {question.options.map((option, optionIndex) => (
                        <label key={optionIndex} className="quiz-option">
                          <input
                            type="radio"
                            name={`diagnostic-${index}`}
                            checked={diagnosticAnswers[index] === optionIndex}
                            onChange={() =>
                              setDiagnosticAnswers((prev) =>
                                prev.map((value, currentIndex) =>
                                  currentIndex === index ? optionIndex : value
                                )
                              )
                            }
                          />
                          <span>{option}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
                <button className="primary-button" onClick={submitDiagnostic}>
                  {submittingDiagnostic ? "Submitting..." : "Submit diagnostic"}
                </button>
              </div>
            ) : null}

            {diagnosticResult ? (
              <div className="diagnostic-result">
                <div className="diagnostic-score">{diagnosticResult.score_percent}% readiness</div>
                <div className={`topic-level topic-level-${diagnosticResult.readiness_level}`}>
                  {diagnosticResult.readiness_level}
                </div>
                <p className="body-text diagnostic-takeaway">{diagnosticResult.takeaway}</p>
                {diagnosticResult.weak_subskills?.length ? (
                  <div className="subskill-list">
                    {diagnosticResult.weak_subskills.map((subskill) => (
                      <span key={subskill} className="subskill-chip">{subskill}</span>
                    ))}
                  </div>
                ) : null}
                <p className="muted-text">
                  Retake recommended in about {diagnosticResult.recommended_retake_in_days} day(s).
                </p>
                {diagnosticResult.review?.length ? (
                  <div className="quiz-review-list">
                    {diagnosticResult.review.map((item, index) => (
                      <div key={item.id || index} className={`quiz-review-card ${item.is_correct ? "quiz-review-correct" : "quiz-review-wrong"}`}>
                        <div className="quiz-review-question">
                          {index + 1}. {item.question}
                        </div>
                        <div className="quiz-review-meta">
                          <span className="resource-tag">{item.subskill}</span>
                          <span className="resource-tag">{item.difficulty}</span>
                        </div>
                        <p className="muted-text">
                          {item.is_correct ? "Correct." : `Correct answer: ${item.correct_answer || "Not available"}`}
                        </p>
                        <p className="body-text">{item.explanation}</p>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        )}
      </aside>

      {/* MAIN AREA */}
      <main className="main-layout">
        <div className="graph-shell">
          <div className="graph-toolbar">
            <span className="toolbar-title">Learning path</span>
            <span className="toolbar-subtitle">Follow the steps from top to bottom</span>
          </div>

          <div className="graph-wrapper" style={{ position: "relative" }}>
            
            {/* NEW: Roadmap loader */}
            {loadingRoadmap && (
              <div className="loader-overlay">
                <div className="spinner"></div>
                <p style={{ marginTop: 10 }}>Building your roadmap…</p>
              </div>
            )}

            <div className="timeline">
              {nodes.length === 0 ? (
                <div className="timeline-empty">
                  Generate a roadmap to see your step-by-step learning path.
                </div>
              ) : (
                nodes.map((node, index) => (
                  <button
                    key={node.id}
                    type="button"
                    className={`timeline-card ${
                      selectedTopic?.id === node.id ? "timeline-card-active" : ""
                    }`}
                    onClick={() => onNodeClick(null, node)}
                  >
                    <div className="timeline-rail">
                      <div className="timeline-dot" />
                      {index < nodes.length - 1 && <div className="timeline-line" />}
                    </div>
                    <div className="timeline-content">
                      <div className="timeline-step">Step {index + 1}</div>
                      <div className="timeline-title">{node.data.label}</div>
                      {node.description ? (
                        <p className="timeline-description">{node.description}</p>
                      ) : null}
                      <div className="timeline-meta">
                        <span className={`timeline-status timeline-status-${node.data.progress}`}>
                          {node.data.progress.replace("-", " ")}
                        </span>
                        <span className={`topic-level topic-level-${node.data.level}`}>
                          {node.data.level}
                        </span>
                        <span className={`confidence-chip confidence-chip-${node.data.confidenceLabel}`}>
                          Confidence: {node.data.confidenceScore ?? "n/a"}%
                        </span>
                        {node.estimatedHours ? (
                          <span>{node.estimatedHours} hrs</span>
                        ) : null}
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>

        {selectedTopic && (
          <section className="info-panel">
            <button className="close-panel-btn" onClick={() => setSelectedTopic(null)}>✕</button>

            <div className="info-header">
              <div className="pill-label">Topic</div>
              <h2 className="info-title">{selectedTopic.label}</h2>
              <div className={`topic-level topic-level-${selectedTopic.level}`}>
                {selectedTopic.level}
              </div>
            </div>
            <div className="info-panel-body">
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
                <h3>Confidence</h3>
                <div className="confidence-panel">
                  <div className="confidence-score">
                    {selectedTopic.confidenceScore ?? "Not assessed yet"}
                    {selectedTopic.confidenceScore != null ? "%" : ""}
                  </div>
                  <div className={`confidence-chip confidence-chip-${selectedTopic.confidenceLabel}`}>
                    {selectedTopic.confidenceLabel}
                  </div>
                </div>
              </div>

              <div className="info-block">
                <h3>Summary</h3>
                <div className="info-scroll info-scroll-summary">
                  {loadingSummary ? (
                    <p className="muted-text">Generating a simple explanation…</p>
                  ) : (
                    <p className="body-text">{summary}</p>
                  )}
                </div>
              </div>

              <div className="info-block">
                <h3>Resources</h3>
                <div className="resource-filters">
                  <select
                    className="filter-select"
                    value={resourceFilters.pricing}
                    onChange={(e) =>
                      setResourceFilters((prev) => ({
                        ...prev,
                        pricing: e.target.value,
                      }))
                    }
                  >
                    <option value="any">Any price</option>
                    <option value="free">Free only</option>
                    <option value="paid">Paid only</option>
                  </select>
                  <select
                    className="filter-select"
                    value={resourceFilters.resourceType}
                    onChange={(e) =>
                      setResourceFilters((prev) => ({
                        ...prev,
                        resourceType: e.target.value,
                      }))
                    }
                  >
                    <option value="any">Any type</option>
                    <option value="video">Video</option>
                    <option value="article">Article</option>
                    <option value="documentation">Documentation</option>
                    <option value="course">Course</option>
                  </select>
                  <select
                    className="filter-select"
                    value={resourceFilters.difficulty}
                    onChange={(e) =>
                      setResourceFilters((prev) => ({
                        ...prev,
                        difficulty: e.target.value,
                      }))
                    }
                  >
                    <option value="any">Any level</option>
                    <option value="beginner">Beginner</option>
                    <option value="intermediate">Intermediate</option>
                    <option value="advanced">Advanced</option>
                  </select>
                </div>
                <div className="info-scroll info-scroll-resources">
                  {materials.length > 0 ? (
                    <>
                      {loadingMaterials ? (
                        <p className="muted-text">Finding more matching resources…</p>
                      ) : null}
                      <ul className="resource-list">
                        {materials.map((m, i) => (
                          <li key={i} className="resource-item">
                            <div className="resource-main">
                              <a href={m.url} target="_blank" rel="noreferrer">
                                {m.title}
                              </a>
                            </div>
                            <span className="resource-tag">{m.type}</span>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : loadingMaterials ? (
                    <p className="muted-text">Finding resources…</p>
                  ) : (
                    <p className="muted-text">
                      No resources match the current filters. Try a broader type,
                      level, or price selection.
                    </p>
                  )}
                </div>
              </div>

              <div className="info-block">
                <h3>I'm confused about…</h3>
                <textarea
                  className="textarea"
                  placeholder="Type your confusion here. PathLearn will fix your RoadMap."
                  value={confusionText}
                  onChange={(e) => setConfusionText(e.target.value)}
                />
                <button className="secondary-button" onClick={fixConfusion}>
                  Fix my roadmap
                </button>
              </div>

              <div className="info-block">
                <h3>Quick quiz</h3>
                {!topicQuiz ? (
                  <button className="secondary-button" onClick={startTopicQuiz}>
                    {loadingTopicQuiz ? "Preparing quiz..." : "Start topic quiz"}
                  </button>
                ) : (
                  <div className="quiz-stack">
                    {topicQuiz.questions.map((question, index) => (
                      <div key={question.id || index} className="quiz-card">
                        <div className="quiz-question">
                          {index + 1}. {question.question}
                        </div>
                        <div className="quiz-options">
                          {question.options.map((option, optionIndex) => (
                            <label key={optionIndex} className="quiz-option">
                              <input
                                type="radio"
                                name={`topic-${index}`}
                                checked={topicQuizAnswers[index] === optionIndex}
                                onChange={() =>
                                  setTopicQuizAnswers((prev) =>
                                    prev.map((value, currentIndex) =>
                                      currentIndex === index ? optionIndex : value
                                    )
                                  )
                                }
                              />
                              <span>{option}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    ))}
                    <button className="primary-button" onClick={submitTopicQuiz}>
                      {submittingTopicQuiz ? "Submitting..." : "Submit quiz"}
                    </button>
                  </div>
                )}

                {topicQuizResult ? (
                  <div className="quiz-result">
                    <div className="diagnostic-score">{topicQuizResult.score_percent}% score</div>
                    <div className={`confidence-chip confidence-chip-${topicQuizResult.confidence_label}`}>
                      confidence {topicQuizResult.confidence_label}
                    </div>
                    {topicQuizResult.weak_subskills?.length ? (
                      <div className="subskill-list">
                        {topicQuizResult.weak_subskills.map((subskill) => (
                          <span key={subskill} className="subskill-chip">{subskill}</span>
                        ))}
                      </div>
                    ) : null}
                    <p className="muted-text">
                      Retake recommended in about {topicQuizResult.recommended_retake_in_days} day(s).
                    </p>
                    {topicQuizResult.roadmap_updated ? (
                      <p className="muted-text">
                        Low score detected, so the roadmap inserted reinforcement topics before this step.
                      </p>
                    ) : (
                      <p className="muted-text">
                        Confidence updated for this topic based on your quiz performance.
                      </p>
                    )}
                    {topicQuizResult.review?.length ? (
                      <div className="quiz-review-list">
                        {topicQuizResult.review.map((item, index) => (
                          <div key={item.id || index} className={`quiz-review-card ${item.is_correct ? "quiz-review-correct" : "quiz-review-wrong"}`}>
                            <div className="quiz-review-question">
                              {index + 1}. {item.question}
                            </div>
                            <div className="quiz-review-meta">
                              <span className="resource-tag">{item.subskill}</span>
                              <span className="resource-tag">{item.difficulty}</span>
                            </div>
                            <p className="muted-text">
                              {item.is_correct ? "Correct." : `Correct answer: ${item.correct_answer || "Not available"}`}
                            </p>
                            <p className="body-text">{item.explanation}</p>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {topicQuizResult.attempt_history?.length ? (
                      <div className="attempt-history">
                        <h4 className="attempt-history-title">Recent attempts</h4>
                        {topicQuizResult.attempt_history.map((attempt, index) => (
                          <div key={`${attempt.created_at}-${index}`} className="attempt-history-row">
                            <span>{new Date(attempt.created_at).toLocaleDateString()}</span>
                            <span>{attempt.score_percent}%</span>
                            <span>{attempt.confidence_score}% confidence</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
