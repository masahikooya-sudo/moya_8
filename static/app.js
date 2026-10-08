"use strict";

const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const send = document.getElementById("send");
const welcome = log.querySelector(".welcome");

/** API に送る会話履歴 ({role, content}) */
let history = [];
let busy = false;

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** 最小限の Markdown 表示(見出し・箇条書き・番号リスト・太字・リンク・出典番号) */
function renderMarkdown(md, sources) {
  const byNum = new Map(sources.map((s) => [s.number, s]));
  const inline = (text) =>
    escapeHtml(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[^"(>])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>')
      .replace(/\[(\d+)\]/g, (m, n) => {
        const s = byNum.get(Number(n));
        return s ? `<a class="cite" href="${escapeHtml(s.url)}" target="_blank" rel="noopener" title="${escapeHtml(s.title)}">[${n}]</a>` : m;
      });

  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\s*[-*・]\s+(.*)$/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^#{1,6}\s+(.*)$/))) {
      closeList(); out.push(`<h3>${inline(m[1])}</h3>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList(); out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("");
}

function renderSources(text, sources) {
  const cited = new Set([...text.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1])));
  const used = sources.filter((s) => cited.has(s.number));
  if (!used.length) return "";
  const items = used
    .map((s) => `<li value="${s.number}"><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title)}</a></li>`)
    .join("");
  return `<div class="sources">参照したFAQ<ol>${items}</ol></div>`;
}

function addMessage(role, html) {
  if (welcome) welcome.remove();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = html;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  send.disabled = true;
  addMessage("user", escapeHtml(question));
  history.push({ role: "user", content: question });

  const el = addMessage("assistant typing", "");
  let text = "";
  let sources = [];
  let failed = null;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `エラーが発生しました (${res.status})`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "sources") sources = ev.sources;
        else if (ev.type === "delta") {
          text += ev.text;
          el.innerHTML = renderMarkdown(text, sources);
          log.scrollTop = log.scrollHeight;
        } else if (ev.type === "error") failed = ev.message;
      }
    }
  } catch (e) {
    failed = e.message;
  }

  el.classList.remove("typing");
  if (failed) {
    el.classList.add("error");
    el.innerHTML = (text ? renderMarkdown(text, sources) : "") + `<p>${escapeHtml(failed)}</p>`;
    history.pop(); // 失敗した質問は履歴から外し、再送できるようにする
  } else {
    el.innerHTML = renderMarkdown(text, sources) + renderSources(text, sources);
    history.push({ role: "assistant", content: text });
  }
  log.scrollTop = log.scrollHeight;
  busy = false;
  send.disabled = false;
  input.focus();
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value;
  input.value = "";
  input.style.height = "";
  ask(q);
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", () => {
  input.style.height = "";
  input.style.height = `${input.scrollHeight}px`;
});

document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));

document.getElementById("reset").addEventListener("click", () => {
  if (busy) return;
  history = [];
  log.innerHTML = "";
  input.focus();
});
