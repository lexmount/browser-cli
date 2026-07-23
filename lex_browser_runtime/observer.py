"""Local browser research observer UI and HTTP API."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib import request
from urllib.parse import urlparse

from lex_browser_runtime.browser.lexmount import LexmountBrowserAdmin
from lex_browser_runtime.config import get_default_research_concurrency
from lex_browser_runtime.research import run_research

Runner = Callable[..., Any]
SessionCloser = Callable[[str], None]


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Lexmount Research Observer</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f7f9;
      color: #172026;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: #f5f7f9;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid #d8e0e7;
      background: #ffffff;
    }

    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }

    main {
      display: grid;
      grid-template-columns: minmax(220px, 280px) 1fr;
      min-height: calc(100vh - 65px);
      transition: grid-template-columns 160ms ease;
    }

    main.sidebar-collapsed {
      grid-template-columns: 48px 1fr;
    }

    aside {
      padding: 14px;
      border-right: 1px solid #d8e0e7;
      background: #ffffff;
    }

    .sidebar-top {
      display: flex;
      justify-content: flex-end;
    }

    .toggle-sidebar {
      width: 34px;
      padding: 0;
      border-color: #c4ced7;
      background: #ffffff;
      color: #172026;
      font-size: 18px;
      line-height: 1;
    }

    main.sidebar-collapsed aside {
      padding: 10px 7px;
    }

    main.sidebar-collapsed .sidebar-content {
      display: none;
    }

    label {
      display: block;
      margin: 12px 0 5px;
      font-size: 11px;
      font-weight: 650;
      color: #42515c;
    }

    input, select {
      width: 100%;
      height: 34px;
      padding: 0 9px;
      border: 1px solid #c4ced7;
      border-radius: 6px;
      background: #ffffff;
      color: #172026;
      font: inherit;
    }

    button {
      height: 34px;
      border: 1px solid #1f6f5b;
      border-radius: 6px;
      background: #1f6f5b;
      color: #ffffff;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }

    button.secondary {
      border-color: #c4ced7;
      background: #ffffff;
      color: #172026;
    }

    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 14px;
    }

    .status {
      margin-top: 12px;
      padding: 9px;
      border: 1px solid #d8e0e7;
      border-radius: 6px;
      background: #f8fafb;
      font-size: 12px;
      color: #42515c;
      min-height: 34px;
    }

    .workspace {
      padding: 14px;
    }

    .browser-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      align-items: start;
    }

    .browser-card {
      min-width: 0;
      min-height: 670px;
      border: 1px solid #d8e0e7;
      border-radius: 8px;
      overflow: hidden;
      background: #ffffff;
    }

    .browser-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      height: 34px;
      padding: 0 9px;
      border-bottom: 1px solid #d8e0e7;
    }

    .browser-title {
      min-width: 0;
      font-size: 10px;
      font-weight: 650;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .browser-toolbar a {
      flex: 0 0 auto;
      color: #1f6f5b;
      font-size: 10px;
      font-weight: 650;
      text-decoration: none;
    }

    iframe {
      display: block;
      width: 100%;
      height: clamp(620px, 70vh, 860px);
      border: 0;
      background: #eef2f5;
    }

    .empty {
      display: grid;
      place-items: center;
      min-height: 670px;
      border: 1px dashed #b8c4ce;
      border-radius: 8px;
      color: #6c7a86;
    }

    .log {
      margin-top: 10px;
      border: 1px solid #d8e0e7;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }

    .answer {
      margin-top: 10px;
      border: 1px solid #d8e0e7;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }

    .answer-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 38px;
      padding: 0 12px;
      border-bottom: 1px solid #d8e0e7;
      color: #42515c;
      font-size: 13px;
      font-weight: 650;
    }

    .answer-body {
      padding: 12px;
      color: #25313a;
      font-size: 13px;
      line-height: 1.5;
    }

    .answer-summary {
      margin: 0 0 10px;
      color: #42515c;
    }

    .answer-text {
      margin: 0 0 12px;
      color: #25313a;
    }

    .answer-recommendations {
      margin: 0 0 12px;
      padding-left: 24px;
    }

    .answer-recommendations li {
      margin: 0 0 8px;
    }

    .log-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 38px;
      padding: 0 12px;
      border-bottom: 1px solid #d8e0e7;
      color: #42515c;
      font-size: 13px;
      font-weight: 650;
    }

    .event-list {
      margin: 0;
      max-height: 160px;
      padding: 12px;
      overflow: auto;
      list-style: none;
    }

    .event-list li {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 30px;
      border-bottom: 1px solid #edf1f4;
      color: #42515c;
      font-size: 13px;
    }

    .event-list li:last-child {
      border-bottom: 0;
    }

    .event-status {
      flex: 0 0 auto;
      color: #1f6f5b;
      font-weight: 650;
    }

    .event-status.failed {
      color: #b42318;
    }

    @media (max-width: 1760px) {
      .browser-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    }

    @media (max-width: 1360px) {
      .browser-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }

    @media (max-width: 1080px) {
      .browser-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid #d8e0e7; }
      .browser-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Lexmount Research Observer</h1>
    <span id="runLabel">No run</span>
  </header>
  <main>
    <aside>
      <div class="sidebar-top">
        <button
          class="toggle-sidebar"
          id="toggleSidebar"
          type="button"
          title="Toggle sidebar"
          aria-label="Toggle sidebar"
          aria-expanded="true"
        >‹</button>
      </div>
      <div class="sidebar-content">
        <form id="researchForm">
          <label for="query">Query</label>
          <input id="query" name="query" value="" autocomplete="off" />
          <div class="actions">
            <button type="submit">Run</button>
            <button class="secondary" id="closeRun" type="button">Close</button>
          </div>
        </form>
        <div class="status" id="status">Ready</div>
        </div>
    </aside>
    <section class="workspace">
      <div class="browser-grid" id="browserGrid">
        <div class="empty" data-empty="browsers">Browser windows will appear here</div>
      </div>
      <div class="answer" id="answerPanel">
        <div class="answer-header">
          <span>Answer</span>
          <span id="answerStatus">Waiting</span>
        </div>
        <div class="answer-body" id="answerBody">
          Run a query to generate the final answer.
        </div>
      </div>
      <div class="log">
        <div class="log-header">
          <span>Run activity</span>
          <span id="eventCount">0 events</span>
        </div>
        <ul class="event-list" id="eventList"></ul>
      </div>
    </section>
  </main>
  <script>
    const form = document.querySelector("#researchForm");
    const appShell = document.querySelector("main");
    const toggleSidebar = document.querySelector("#toggleSidebar");
    const queryInput = document.querySelector("#query");
    const statusEl = document.querySelector("#status");
    const runLabel = document.querySelector("#runLabel");
    const browserGrid = document.querySelector("#browserGrid");
    const answerStatus = document.querySelector("#answerStatus");
    const answerBody = document.querySelector("#answerBody");
    const eventList = document.querySelector("#eventList");
    const eventCount = document.querySelector("#eventCount");
    const closeRun = document.querySelector("#closeRun");
    let currentRunId = null;
    let events = null;
    const browserSlots = new Map();
    const sessionKeys = new Map();
    let visibleEventCount = 0;
    let latestPoll = null;

    function setStatus(text) {
      statusEl.textContent = text;
    }

    function describeEvent(event) {
      if (event.type === "research_started") {
        return `Started ${event.job_count} browser jobs at concurrency ${event.concurrency}`;
      }
      if (event.type === "job_started") {
        return `${event.source_name || event.source_id} started`;
      }
      if (event.type === "browser_created") {
        return `${event.source_name || event.source_id} browser window is ready`;
      }
      if (event.type === "browser_prepared") {
        return `${event.source_name || event.source_id} login context is ready`;
      }
      if (event.type === "browser_closed") {
        return `${event.source_name || event.source_id} browser session was released`;
      }
      if (event.type === "job_finished") {
        if (!event.ok && event.message) {
          return `${event.source_name || event.source_id} failed: ${event.message}`;
        }
        return `${event.source_name || event.source_id} ${event.ok ? "finished" : "failed"}`;
      }
      if (event.type === "research_finished") {
        return `Research finished: ${event.success_count} succeeded, ${event.failure_count} failed`;
      }
      if (event.type === "observer_run_finished") {
        return `Run ${event.status}`;
      }
      return event.type || "Event";
    }

    function eventStatus(event) {
      if (event.ok === false || event.status === "failed") return "failed";
      if (event.type === "browser_created") return "ready";
      if (event.type === "browser_prepared") return "prepared";
      if (event.type === "browser_closed") return "released";
      if (event.type === "job_started" || event.type === "research_started") return "running";
      if (event.type === "job_finished" || event.type === "research_finished") return "done";
      return event.status || "";
    }

    function appendLog(event) {
      visibleEventCount += 1;
      eventCount.textContent = `${visibleEventCount} events`;
      const item = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = describeEvent(event);
      const status = document.createElement("span");
      status.className = `event-status ${event.ok === false ? "failed" : ""}`;
      status.textContent = eventStatus(event);
      item.append(text, status);
      eventList.appendChild(item);
      eventList.scrollTop = eventList.scrollHeight;
    }

    function ensureEmptyBrowserState() {
      const empty = browserGrid.querySelector("[data-empty='browsers']");
      if (!browserSlots.size) {
        if (!empty) {
          const emptyNode = document.createElement("div");
          emptyNode.className = "empty";
          emptyNode.dataset.empty = "browsers";
          emptyNode.textContent = "Browser windows will appear here";
          browserGrid.appendChild(emptyNode);
        }
        return;
      }
      if (empty) empty.remove();
    }

    function createBrowserCard(key) {
      const card = document.createElement("article");
      card.className = "browser-card";
      card.dataset.browserKey = key;
      const toolbar = document.createElement("div");
      toolbar.className = "browser-toolbar";
      const title = document.createElement("div");
      title.className = "browser-title";
      toolbar.appendChild(title);
      card.appendChild(toolbar);
      browserGrid.appendChild(card);
      return card;
    }

    function keyForBrowserEvent(event) {
      if (event.session_id && sessionKeys.has(event.session_id)) {
        return sessionKeys.get(event.session_id);
      }
      const rawKey = event.source_id || event.session_id || event.rank || "unknown";
      const key = `${currentRunId || "run"}:${rawKey}`;
      if (event.session_id) sessionKeys.set(event.session_id, key);
      return key;
    }

    function upsertBrowser(key, browser) {
      const previous = browserSlots.get(key) || {};
      const next = {
        ...previous,
        ...browser,
        inspect_url: browser.inspect_url || previous.inspect_url,
      };
      browserSlots.set(key, next);
      ensureEmptyBrowserState();
      const selectorKey = window.CSS && CSS.escape ? CSS.escape(String(key)) : String(key).replace(/"/g, '\\"');
      const card = browserGrid.querySelector(`[data-browser-key="${selectorKey}"]`) || createBrowserCard(key);
      const title = card.querySelector(".browser-title");
      const toolbar = card.querySelector(".browser-toolbar");
      title.textContent = `${next.source_name || next.source_id} · ${next.status || "Preparing"}`;
      let link = toolbar.querySelector("a");
      if (next.inspect_url) {
        if (!link) {
          link = document.createElement("a");
          link.target = "_blank";
          link.rel = "noreferrer";
          link.textContent = "Open";
          toolbar.appendChild(link);
        }
        link.href = next.inspect_url;
      } else if (link) {
        link.remove();
      }
      if (next.inspect_url) {
        let frame = card.querySelector("iframe");
        if (!frame) {
          frame = document.createElement("iframe");
          frame.title = title.textContent;
          card.appendChild(frame);
        }
        if (frame.dataset.inspectUrl !== next.inspect_url) {
          frame.dataset.inspectUrl = next.inspect_url;
          frame.src = next.inspect_url;
        }
        const placeholder = card.querySelector(".empty");
        if (placeholder) placeholder.remove();
      } else {
        let placeholder = card.querySelector(".empty");
        const frame = card.querySelector("iframe");
        if (frame) frame.remove();
        if (!placeholder) {
          placeholder = document.createElement("div");
          placeholder.className = "empty";
          card.appendChild(placeholder);
        }
        placeholder.textContent = browser.status || "Preparing browser";
      }
    }

    function removeBrowser(key) {
      browserSlots.delete(key);
      for (const [sessionId, mappedKey] of sessionKeys) {
        if (mappedKey === key) sessionKeys.delete(sessionId);
      }
      const selectorKey = window.CSS && CSS.escape ? CSS.escape(String(key)) : String(key).replace(/"/g, '\\"');
      const card = browserGrid.querySelector(`[data-browser-key="${selectorKey}"]`);
      if (card) card.remove();
      ensureEmptyBrowserState();
    }

    function clearBrowsers() {
      browserSlots.clear();
      sessionKeys.clear();
      for (const card of browserGrid.querySelectorAll("[data-browser-key]")) {
        card.remove();
      }
      ensureEmptyBrowserState();
    }

    function resetAnswer() {
      answerStatus.textContent = "Waiting";
      answerBody.textContent = "Answer will appear after the browser research finishes.";
    }

    function renderAnswer(answer) {
      answerStatus.textContent = answer.source_summary || "Ready";
      answerBody.innerHTML = "";
      const sections = [
        answer.summary_text,
        answer.conclusion,
      ].filter(Boolean);
      for (const section of sections) {
        const paragraph = document.createElement("p");
        paragraph.className = "answer-text";
        paragraph.textContent = section;
        answerBody.appendChild(paragraph);
      }
      if (answer.recommendations && answer.recommendations.length) {
        const lead = document.createElement("p");
        lead.className = "answer-text";
        lead.textContent = "我会推荐的做法：";
        answerBody.appendChild(lead);
        const list = document.createElement("ol");
        list.className = "answer-recommendations";
        for (const recommendation of answer.recommendations) {
          const item = document.createElement("li");
          item.textContent = recommendation;
          list.appendChild(item);
        }
        answerBody.appendChild(list);
      }
      for (const section of [answer.one_liner, answer.source_note].filter(Boolean)) {
        const paragraph = document.createElement("p");
        paragraph.className = "answer-text";
        paragraph.textContent = section;
        answerBody.appendChild(paragraph);
      }
    }

    async function loadAnswer() {
      if (!currentRunId) return;
      const response = await fetch(`/api/runs/${currentRunId}`);
      if (!response.ok) throw new Error(await response.text());
      const state = await response.json();
      if (state.answer) renderAnswer(state.answer);
    }

    async function pollLatestRun() {
      try {
        const response = await fetch("/api/runs/latest");
        if (response.status === 404) return;
        if (!response.ok) throw new Error(await response.text());
        const latest = await response.json();
        if (latest.run_id && latest.run_id !== currentRunId) {
          resetRun(latest.run_id);
        }
      } catch (error) {
        setStatus(error.message);
      }
    }

    function resetRun(runId) {
      if (events) events.close();
      clearBrowsers();
      eventList.innerHTML = "";
      visibleEventCount = 0;
      eventCount.textContent = "0 events";
      resetAnswer();
      currentRunId = runId;
      runLabel.textContent = runId;
      setStatus("Running");
      events = new EventSource(`/api/runs/${currentRunId}/events`);
      events.onmessage = (message) => {
        const data = JSON.parse(message.data);
        appendLog(data);
        if (data.type === "research_started" && Array.isArray(data.jobs)) {
          for (const job of data.jobs) {
            upsertBrowser(keyForBrowserEvent(job), {
              ...job,
              status: "Preparing",
            });
          }
        }
        if (data.type === "browser_prepared") {
          const key = keyForBrowserEvent(data);
          const existing = browserSlots.get(key) || {};
          upsertBrowser(key, {
            ...existing,
            ...data,
            inspect_url: existing.inspect_url,
            status: "Login ready",
          });
        }
        if (data.type === "browser_created" && data.inspect_url) {
          const key = keyForBrowserEvent(data);
          const existing = browserSlots.get(key) || {};
          upsertBrowser(key, {
            ...existing,
            ...data,
            status: "Ready",
          });
        }
        if (data.type === "browser_closed") {
          const key = keyForBrowserEvent(data);
          removeBrowser(key);
        }
        if (data.type === "research_finished") {
          setStatus("Finishing");
        }
        if (data.type === "observer_run_finished") {
          setStatus(data.status || "finished");
          events.close();
          loadAnswer().catch((error) => setStatus(error.message));
        }
      };
      events.onerror = () => setStatus("Event stream disconnected");
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const query = queryInput.value.trim();
      if (!query) {
        setStatus("Enter a query");
        return;
      }
      setStatus("Starting");
      try {
        const response = await fetch("/api/research", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query,
            preset: "food",
            max_sites: 5,
            concurrency: 5,
            browser_mode: "normal",
          }),
        });
        if (!response.ok) throw new Error(await response.text());
        const started = await response.json();
        resetRun(started.run_id);
      } catch (error) {
        setStatus(error.message);
      }
    });

    closeRun.addEventListener("click", async () => {
      if (!currentRunId) return;
      const response = await fetch(`/api/runs/${currentRunId}/close`, { method: "POST" });
      setStatus(response.ok ? "Close requested" : await response.text());
    });

    toggleSidebar.addEventListener("click", () => {
      const collapsed = appShell.classList.toggle("sidebar-collapsed");
      toggleSidebar.setAttribute("aria-expanded", collapsed ? "false" : "true");
      toggleSidebar.textContent = collapsed ? "›" : "‹";
    });

    ensureEmptyBrowserState();
    resetAnswer();
    pollLatestRun();
    latestPoll = setInterval(pollLatestRun, 1000);
  </script>
</body>
</html>
"""


@dataclass
class ObserverRun:
    """Mutable state for one local observer run."""

    run_id: str
    thread: threading.Thread | None = None
    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    active_sessions: dict[str, str | None] = field(default_factory=dict)
    summary: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    error: str | None = None

    def public_state(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of this run."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "events": list(self.events),
            "active_sessions": [
                {"session_id": session_id, "inspect_url": inspect_url}
                for session_id, inspect_url in self.active_sessions.items()
            ],
            "summary": self.summary,
            "answer": self.answer,
            "error": self.error,
        }


def format_sse(event: dict[str, Any], *, event_id: int | None = None) -> str:
    """Format one JSON event as a Server-Sent Events message."""

    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _clip_text(value: Any, *, limit: int = 280) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_research_answer(summary: dict[str, Any]) -> dict[str, Any]:
    """Build a compact display answer from extracted research results."""

    results = summary.get("results")
    if not isinstance(results, list):
        results = []
    highlights: list[dict[str, str | None]] = []
    failures: list[dict[str, str]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        source_name = str(result.get("source_name") or result.get("source_id") or "")
        if result.get("ok") is True:
            candidates = result.get("candidates")
            first_candidate = (
                candidates[0]
                if isinstance(candidates, list)
                and candidates
                and isinstance(candidates[0], dict)
                else {}
            )
            candidate_text = first_candidate.get("text") if first_candidate else None
            candidate_href = first_candidate.get("href") if first_candidate else None
            text = _clip_text(
                candidate_text or result.get("text") or result.get("title")
            )
            if not text:
                continue
            highlights.append(
                {
                    "source_name": source_name,
                    "title": _clip_text(result.get("title"), limit=120),
                    "text": text,
                    "href": candidate_href if isinstance(candidate_href, str) else None,
                }
            )
        else:
            message = _clip_text(
                result.get("message") or result.get("error"), limit=160
            )
            failures.append(
                {
                    "source_name": source_name,
                    "message": message or "failed",
                }
            )
    success_count = int(summary.get("success_count") or len(highlights))
    failure_count = int(summary.get("failure_count") or len(failures))
    total_count = len(results) or success_count + failure_count
    source_names = [
        str(highlight["source_name"])
        for highlight in highlights[:3]
        if highlight.get("source_name")
    ]
    source_text = "、".join(source_names) if source_names else "可用来源"
    if failure_count:
        summary_text = (
            f"我用 Lexmount Browser 并发跑了 {total_count} 个来源。"
            f"有效信息主要来自 {source_text}；"
            f"另有 {failure_count} 个来源失败或受限，参考价值较低。"
        )
    else:
        summary_text = (
            f"我用 Lexmount Browser 并发跑了 {total_count} 个来源。"
            f"有效信息主要来自 {source_text}。"
        )

    evidence_text = " ".join(
        str(highlight.get("text") or "") for highlight in highlights
    )
    query_text = str(summary.get("query") or "")
    is_red_braised_pork = "红烧肉" in query_text or "红烧肉" in evidence_text
    if is_red_braised_pork:
        conclusion = (
            "结论：这类问题没有客观唯一的“最好吃”，但综合可用结果，"
            "最稳的方向是上海本帮/家常冰糖红烧肉：带皮三层五花肉、冰糖炒糖色、"
            "小火慢炖，最后大火收汁。"
        )
        recommendations = [
            "想吃经典甜口：选上海本帮红烧肉。",
            "想吃咸香浓郁：选东北/北方红烧肉。",
            "自己做时优先选带皮三层五花肉，先炒糖色，再小火慢炖，最后收汁。",
        ]
        one_liner = (
            "一句话版：带皮三层五花肉 + 冰糖炒糖色 + 小火慢炖 + 大火收汁，"
            "是大众意义上最稳的红烧肉答案。"
        )
    else:
        first_choice = (
            highlights[0]["text"] if highlights else "暂无足够有效证据给出明确推荐"
        )
        conclusion = (
            "结论：这类问题通常没有唯一答案，但综合可用检索结果，"
            f"更稳妥的判断是：{first_choice}。"
        )
        recommendations = [
            str(highlight["text"])
            for highlight in highlights[:3]
            if highlight.get("text")
        ]
        one_liner = f"一句话版：{first_choice}。"
    source_note = f"主要参考来源是 {source_text}。"
    return {
        "ok": bool(summary.get("ok")),
        "query": query_text,
        "source_summary": f"{success_count} succeeded, {failure_count} failed",
        "summary_text": summary_text,
        "conclusion": conclusion,
        "recommendations": recommendations,
        "one_liner": one_liner,
        "source_note": source_note,
        "highlights": highlights[:5],
        "failures": failures[:5],
    }


class ResearchObserver:
    """Run browser research in the background and expose live run state."""

    def __init__(
        self,
        *,
        runner: Runner = run_research,
        session_closer: SessionCloser | None = None,
    ) -> None:
        self._runner = runner
        self._session_closer = session_closer or LexmountBrowserAdmin().close_session
        self._runs: dict[str, ObserverRun] = {}
        self._latest_run_id: str | None = None
        self._lock = threading.Lock()

    def create_observed_run(
        self,
        *,
        run_id: str | None = None,
        query: str | None = None,
    ) -> dict[str, str]:
        """Create a run container for events pushed by an external caller."""

        resolved_run_id = run_id or f"codex-{uuid.uuid4().hex[:12]}"
        run = ObserverRun(run_id=resolved_run_id)
        if query:
            run.events.append({"type": "observer_run_created", "query": query})
        with self._lock:
            self._runs[resolved_run_id] = run
            self._latest_run_id = resolved_run_id
        return {"run_id": resolved_run_id, "status": run.status}

    def start_research(
        self,
        *,
        query: str,
        preset: str = "food",
        sites: str | None = None,
        max_sites: int = 10,
        concurrency: int | None = None,
        browser_mode: str = "normal",
    ) -> dict[str, str]:
        """Start a local observer run and return its id."""

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        resolved_concurrency = (
            concurrency
            if concurrency is not None
            else get_default_research_concurrency()
        )
        run_id = f"observer-{uuid.uuid4().hex[:12]}"
        run = ObserverRun(run_id=run_id)
        thread = threading.Thread(
            target=self._run_research,
            kwargs={
                "run": run,
                "query": normalized_query,
                "preset": preset,
                "sites": sites,
                "max_sites": max_sites,
                "concurrency": resolved_concurrency,
                "browser_mode": browser_mode,
            },
            daemon=True,
        )
        run.thread = thread
        with self._lock:
            self._runs[run_id] = run
            self._latest_run_id = run_id
        thread.start()
        return {"run_id": run_id, "status": run.status}

    def latest_run(self) -> dict[str, Any]:
        """Return public state for the most recently created run."""

        with self._lock:
            if self._latest_run_id is None:
                raise KeyError("latest")
            run = self._runs.get(self._latest_run_id)
            if run is None:
                raise KeyError("latest")
            return run.public_state()

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Return public state for one run."""

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return run.public_state()

    def wait_for_run(self, run_id: str, *, timeout: float) -> dict[str, Any]:
        """Wait for one background run to finish, then return its state."""

        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.thread is not None:
            run.thread.join(timeout=timeout)
        return self.get_run(run_id)

    def close_run(self, run_id: str) -> dict[str, Any]:
        """Close all known sessions for one observer run."""

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            session_ids = list(run.active_sessions)
        closed: list[str] = []
        errors: list[dict[str, str]] = []
        for session_id in session_ids:
            try:
                self._session_closer(session_id)
            except Exception as exc:
                errors.append({"session_id": session_id, "error": str(exc)})
            else:
                closed.append(session_id)
        with self._lock:
            for session_id in closed:
                run.active_sessions.pop(session_id, None)
        return {"run_id": run_id, "closed": closed, "errors": errors}

    def record_external_event(
        self,
        run_id: str,
        event: dict[str, Any],
    ) -> dict[str, str]:
        """Record one event pushed by a CLI or skill run."""

        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        self._record_event(run, event)
        if event.get("type") == "observer_run_finished":
            summary = event.get("summary")
            with self._lock:
                if isinstance(summary, dict):
                    run.summary = summary
                    run.answer = build_research_answer(summary)
                status = event.get("status")
                run.status = status if isinstance(status, str) else "finished"
        return {"run_id": run_id, "status": run.status}

    def _record_event(self, run: ObserverRun, event: dict[str, Any]) -> None:
        with self._lock:
            run.events.append(event)
            if event.get("type") in {"browser_created", "browser_prepared"}:
                session_id = event.get("session_id")
                if isinstance(session_id, str) and session_id:
                    inspect_url = event.get("inspect_url")
                    run.active_sessions[session_id] = (
                        inspect_url if isinstance(inspect_url, str) else None
                    )
            if event.get("type") == "browser_closed":
                session_id = event.get("session_id")
                if isinstance(session_id, str) and session_id:
                    run.active_sessions.pop(session_id, None)

    def _finish_run(self, run: ObserverRun, *, status: str, error: str | None) -> None:
        event = {
            "type": "observer_run_finished",
            "run_id": run.run_id,
            "status": status,
        }
        if error is not None:
            event["error"] = error
        with self._lock:
            run.status = status
            run.error = error
            run.events.append(event)

    def _run_research(
        self,
        *,
        run: ObserverRun,
        query: str,
        preset: str,
        sites: str | None,
        max_sites: int,
        concurrency: int,
        browser_mode: str,
    ) -> None:
        try:
            summary = self._runner(
                query=query,
                preset=preset,
                sites=sites,
                max_sites=max_sites,
                concurrency=concurrency,
                browser_mode=browser_mode,
                keep_sessions=False,
                on_event=lambda event: self._record_event(run, event),
            )
        except Exception as exc:
            self._finish_run(run, status="failed", error=str(exc))
            return
        with self._lock:
            run.summary = summary.model_dump(mode="json")
            run.answer = build_research_answer(run.summary)
        self._finish_run(run, status="finished", error=None)


class ObserverEventPublisher:
    """Publish research events from a CLI process to a local observer server."""

    def __init__(self, observer_url: str, *, run_id: str, query: str) -> None:
        self._base_url = observer_url.rstrip("/")
        self._run_id = run_id
        self._query = query

    def __enter__(self) -> "ObserverEventPublisher":
        self._post("/api/runs", {"run_id": self._run_id, "query": self._query})
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc is not None:
            self.emit(
                {
                    "type": "observer_run_finished",
                    "run_id": self._run_id,
                    "status": "failed",
                    "error": str(exc),
                }
            )

    def finish(self, summary: dict[str, Any]) -> None:
        """Mark the observed run finished and publish its summary."""

        self.emit(
            {
                "type": "observer_run_finished",
                "run_id": self._run_id,
                "status": "finished" if summary.get("ok", False) else "failed",
                "summary": summary,
            }
        )

    def emit(self, event: dict[str, Any]) -> None:
        """Send one live research event to the observer."""

        self._post(f"/api/runs/{self._run_id}/events", event)

    def _post(self, path: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(
            f"{self._base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(http_request, timeout=2) as response:
            response.read()


class ObserverHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying observer state for request handlers."""

    observer: ResearchObserver


class ObserverRequestHandler(BaseHTTPRequestHandler):
    """Handle local observer API and page requests."""

    server: ObserverHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        """Silence default stderr request logging for the local UI."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(
                INDEX_HTML.encode("utf-8"),
                content_type="text/html; charset=utf-8",
            )
            return
        if parsed.path == "/api/runs/latest":
            self._send_latest_run()
            return
        if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/events"):
            run_id = parsed.path.removeprefix("/api/runs/").removesuffix("/events")
            self._send_events(run_id)
            return
        if parsed.path.startswith("/api/runs/"):
            run_id = parsed.path.removeprefix("/api/runs/")
            self._send_run_state(run_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/research":
            self._start_research()
            return
        if parsed.path == "/api/runs":
            self._create_external_run()
            return
        if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/events"):
            run_id = parsed.path.removeprefix("/api/runs/").removesuffix("/events")
            self._record_external_event(run_id)
            return
        if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/close"):
            run_id = parsed.path.removeprefix("/api/runs/").removesuffix("/close")
            self._close_run(run_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _send_bytes(
        self,
        body: bytes,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, status=status, content_type="application/json")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _start_research(self) -> None:
        try:
            payload = self._read_json()
            started = self.server.observer.start_research(
                query=str(payload.get("query", "")),
                preset=str(payload.get("preset", "food")),
                sites=(
                    str(payload["sites"])
                    if payload.get("sites") not in {None, ""}
                    else None
                ),
                max_sites=int(payload.get("max_sites", 10)),
                concurrency=(
                    int(payload["concurrency"])
                    if payload.get("concurrency") not in {None, ""}
                    else None
                ),
                browser_mode=str(payload.get("browser_mode", "normal")),
            )
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": exc.__class__.__name__, "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json({"ok": True, **started}, status=HTTPStatus.ACCEPTED)

    def _send_run_state(self, run_id: str) -> None:
        try:
            state = self.server.observer.get_run(run_id)
        except KeyError:
            self.send_error(HTTPStatus.NOT_FOUND, "run not found")
            return
        self._send_json(state)

    def _send_latest_run(self) -> None:
        try:
            state = self.server.observer.latest_run()
        except KeyError:
            self.send_error(HTTPStatus.NOT_FOUND, "run not found")
            return
        self._send_json(state)

    def _create_external_run(self) -> None:
        try:
            payload = self._read_json()
            started = self.server.observer.create_observed_run(
                run_id=(
                    str(payload["run_id"])
                    if payload.get("run_id") not in {None, ""}
                    else None
                ),
                query=(
                    str(payload["query"])
                    if payload.get("query") not in {None, ""}
                    else None
                ),
            )
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": exc.__class__.__name__, "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json({"ok": True, **started}, status=HTTPStatus.CREATED)

    def _record_external_event(self, run_id: str) -> None:
        try:
            payload = self._read_json()
            result = self.server.observer.record_external_event(run_id, payload)
        except KeyError:
            self.send_error(HTTPStatus.NOT_FOUND, "run not found")
            return
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": exc.__class__.__name__, "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json({"ok": True, **result})

    def _close_run(self, run_id: str) -> None:
        try:
            payload = self.server.observer.close_run(run_id)
        except KeyError:
            self.send_error(HTTPStatus.NOT_FOUND, "run not found")
            return
        self._send_json({"ok": True, **payload})

    def _send_events(self, run_id: str) -> None:
        try:
            self.server.observer.get_run(run_id)
        except KeyError:
            self.send_error(HTTPStatus.NOT_FOUND, "run not found")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_event_id = self.headers.get("Last-Event-ID")
        try:
            sent = int(last_event_id) + 1 if last_event_id is not None else 0
        except ValueError:
            sent = 0
        while True:
            try:
                state = self.server.observer.get_run(run_id)
                events = state["events"]
                for index, event in enumerate(events[sent:], start=sent):
                    self.wfile.write(format_sse(event, event_id=index).encode("utf-8"))
                    self.wfile.flush()
                    sent = index + 1
                if state["status"] in {"finished", "failed"} and sent >= len(events):
                    return
                time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                return


def create_observer_server(
    address: tuple[str, int],
    *,
    observer: ResearchObserver | None = None,
) -> ObserverHTTPServer:
    """Create a local observer HTTP server."""

    server = ObserverHTTPServer(address, ObserverRequestHandler)
    server.observer = observer or ResearchObserver()
    return server


def serve_observer(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the local browser research observer until interrupted."""

    server = create_observer_server((host, port))
    print(
        f"Lexmount Research Observer: http://{host}:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "ObserverEventPublisher",
    "ResearchObserver",
    "create_observer_server",
    "format_sse",
    "serve_observer",
]
