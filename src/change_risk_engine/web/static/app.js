const API = "/api/v1";

const levelColor = (label) => ({ LOW: "var(--low)", MEDIUM: "var(--medium)", HIGH: "var(--high)", CRITICAL: "var(--critical)" }[label] || "var(--muted)");

async function api(path, options) {
  const res = await fetch(API + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options && options.headers) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.appendChild(child);
  return node;
}

function renderResults(change, assessment) {
  const root = document.getElementById("results");
  root.innerHTML = "";

  const level = assessment.risk_level.toUpperCase();

  const hero = el("div", { class: "score-hero" }, [
    el("div", { class: "number", text: Math.round(assessment.overall_score) }),
    el("div", {}, [
      el("span", { class: `badge ${assessment.risk_level}`, text: level }),
      el("div", { class: "meta", text: `Confidence ${Math.round(assessment.confidence * 100)}%${assessment.requires_approval ? " · Approval required" : ""}` }),
    ]),
  ]);
  root.appendChild(hero);

  if (change) {
    root.appendChild(el("div", { class: "tag-list", style: "margin-bottom:16px" }, [
      el("span", { class: "tag", text: change.title }),
      el("span", { class: `tag ${change.environment === "prod" ? "production" : ""}`, text: change.environment }),
      el("span", { class: "tag", text: change.target_database || "" }),
    ]));
  }

  if (assessment.explanation) {
    root.appendChild(el("div", { class: "section" }, [
      el("h2", { text: "Why" }),
      el("div", { class: "explanation", text: assessment.explanation }),
    ]));
  }

  const factorsSection = el("div", { class: "section" }, [el("h2", { text: "Risk factors" })]);
  const sortedFactors = [...assessment.factors].sort((a, b) => b.score * b.weight - a.score * a.weight);
  for (const f of sortedFactors) {
    const contribution = (f.score * f.weight).toFixed(1);
    const row = el("div", { class: "factor-row" }, [
      el("div", { class: "factor-label" }, [
        el("span", { class: "name", text: f.factor_type.replaceAll("_", " ") }),
        el("span", { class: "contribution", text: `${contribution} (${f.label})` }),
      ]),
      el("div", { class: "factor-bar-track" }, [
        el("div", { class: "factor-bar-fill", style: `width:${f.score}%; background:${levelColor(f.label)}` }),
      ]),
      el("div", { class: "factor-reason", text: f.reason }),
    ]);
    factorsSection.appendChild(row);
  }
  root.appendChild(factorsSection);

  if (assessment.blast_radius) {
    const br = assessment.blast_radius;
    const stats = [
      ["services", br.affected_services.length],
      ["apis", br.affected_apis.length],
      ["pipelines", br.affected_data_pipelines.length],
      ["tables/views", br.affected_tables.length],
      ["critical deps", br.critical_dependencies.length],
      ["scope", br.estimated_scope],
    ];
    const grid = el("div", { class: "stat-grid" });
    for (const [label, value] of stats) {
      grid.appendChild(el("div", { class: "stat-card" }, [
        el("div", { class: "value", text: value }),
        el("div", { class: "label", text: label }),
      ]));
    }
    root.appendChild(el("div", { class: "section" }, [el("h2", { text: "Blast radius" }), grid]));
  }

  if (assessment.recommendations.length) {
    const list = el("div", {});
    for (const r of assessment.recommendations) {
      list.appendChild(el("div", { class: "rec-item" }, [
        el("span", { class: `rec-priority ${r.priority}`, text: r.priority }),
        el("span", { text: r.detail }),
      ]));
    }
    root.appendChild(el("div", { class: "section" }, [el("h2", { text: "Recommendations" }), list]));
  }

  if (assessment.policy_decisions.some((d) => d.triggered)) {
    const list = el("div", {});
    for (const d of assessment.policy_decisions.filter((d) => d.triggered)) {
      list.appendChild(el("div", { class: "policy-item triggered" }, [
        el("span", { class: "sev", text: d.severity }),
        el("span", { text: `${d.policy_id}: ${d.description}` }),
      ]));
    }
    root.appendChild(el("div", { class: "section" }, [el("h2", { text: "Policy decisions" }), list]));
  }

  if (assessment.evidence.length) {
    const list = el("div", {});
    for (const e of assessment.evidence) {
      list.appendChild(el("div", { class: "evidence-item" }, [
        el("span", { class: "source", text: `[${e.source}] ` }),
        el("span", { text: e.description }),
      ]));
    }
    root.appendChild(el("div", { class: "section" }, [el("h2", { text: "Evidence" }), list]));
  }

  if (assessment.uncertainty.length) {
    const list = el("div", {});
    for (const u of assessment.uncertainty) {
      list.appendChild(el("div", { class: "uncertainty-item", text: `${u.description} -- ${u.reason}` }));
    }
    root.appendChild(el("div", { class: "section" }, [el("h2", { text: "Uncertainty" }), list]));
  }
}

function renderError(message) {
  const root = document.getElementById("results");
  root.innerHTML = "";
  root.appendChild(el("div", { class: "error-banner", text: message }));
}

async function loadHistory() {
  const listEl = document.getElementById("history-list");
  try {
    const history = await api("/history?limit=25");
    if (!history.length) {
      listEl.innerHTML = '<div class="empty-state">No assessments yet.</div>';
      return;
    }
    listEl.innerHTML = "";
    for (const item of history) {
      const row = el("div", { class: "history-item" }, [
        el("span", { class: `badge ${item.risk_level}`, text: item.risk_level }),
        el("span", { class: "score", text: `${Math.round(item.overall_score)}` }),
      ]);
      row.addEventListener("click", () => openAssessment(item.id, item.change_id));
      listEl.appendChild(row);
    }
  } catch (err) {
    listEl.innerHTML = `<div class="empty-state">${err.message}</div>`;
  }
}

async function openAssessment(assessmentId, changeId) {
  try {
    const [assessment, change] = await Promise.all([
      api(`/assessments/${assessmentId}`),
      changeId ? api(`/changes/${changeId}`).catch(() => null) : null,
    ]);
    renderResults(change, assessment);
  } catch (err) {
    renderError(err.message);
  }
}

document.getElementById("submit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("submit-btn");
  button.disabled = true;
  button.textContent = "Analyzing...";
  try {
    const change = await api("/changes", {
      method: "POST",
      body: JSON.stringify({
        sql: document.getElementById("sql").value,
        environment: document.getElementById("environment").value,
        target_database: document.getElementById("target-database").value,
        demo: document.getElementById("demo").checked,
      }),
    });
    const assessment = await api("/assessments", {
      method: "POST",
      body: JSON.stringify({ change_id: change.id }),
    });
    renderResults(change, assessment);
    await loadHistory();
  } catch (err) {
    renderError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = "Analyze change";
  }
});

loadHistory();
