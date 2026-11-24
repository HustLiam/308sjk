/* 标准 AML 配置器前端逻辑（原生 JS，无依赖） */
const $ = (id) => document.getElementById(id);
let picked = [];      // File[]
let sid = null;
let questions = [];

/* ---------- ① 上传 ---------- */
const drop = $("drop"), fileInput = $("file");
drop.addEventListener("click", () => fileInput.click());
["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", e => addFiles(e.dataTransfer.files));
fileInput.addEventListener("change", () => addFiles(fileInput.files));

function addFiles(list) {
  for (const f of list) if (!picked.some(x => x.name === f.name)) picked.push(f);
  renderFiles();
}
function renderFiles() {
  $("filelist").innerHTML = picked.map(f =>
    `<li>${f.name} <span class="hint">${(f.size / 1024).toFixed(1)}KB</span></li>`).join("");
  $("btnUpload").disabled = picked.length === 0;
}
$("btnUpload").addEventListener("click", async () => {
  const fd = new FormData();
  picked.forEach(f => fd.append("files", f, f.name));
  $("uploadMsg").textContent = "上传中…";
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  const j = await r.json();
  if (!r.ok) { $("uploadMsg").textContent = j.error || "上传失败"; return; }
  sid = j.sid;
  $("uploadMsg").textContent = `已接收 ${j.files.length} 个文件，审查中…`;
  analyze();
});

/* ---------- ② 审查问答 ---------- */
async function analyze() {
  const r = await fetch("/api/analyze", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sid })
  });
  const j = await r.json();
  $("step2").classList.remove("hidden");
  if (!j.ok) {
    $("questions").innerHTML = j.errors.map(e => `<div class="err">${esc(e)}</div>`).join("");
    $("btnAssemble").classList.add("hidden");
    return;
  }
  questions = j.questions || [];
  $("llmBadge").textContent = j.llm_used ? "审查者：deepseek 真实 LLM" :
    "审查者：Mock 规则引擎（LLM 不可用降级）";
  $("llmBadge").className = "badge" + (j.llm_used ? " llm" : "");
  const c = j.classification;
  $("clsView").innerHTML =
    `识别：${c.base_aml ? "基座AML ✓" : "散装"} · 关系树${c.parts_files.includes("tree") ? "✓" : "缺"} · ` +
    `几何 ${c.geometry.length} 个 · io.json ${c.io ? "✓" : "缺"} · 可选件 [${c.optional.join(", ") || "无"}]` +
    (c.unknown.length ? ` · 未识别：${c.unknown.join(", ")}` : "");
  renderQuestions();
}

function renderQuestions() {
  const box = $("questions");
  if (!questions.length) {
    box.innerHTML = `<p class="okline">无缺失项——可直接生成。</p>`;
    return;
  }
  box.innerHTML = questions.map((q, i) => {
    const opts = (q.options || []).map(o =>
      `<button class="opt${q.default === o ? " sel" : ""}" data-q="${i}" data-v="${esc(o)}">${esc(o)}</button>`
    ).join("");
    return `<div class="q">
      <div class="ask">${esc(q.ask)}</div>
      <div class="apply">apply: ${esc(q.apply || "-")}</div>
      <div class="opts">${opts}
        <input type="text" placeholder="或输入答案（回车确认）" data-q="${i}">
      </div></div>`;
  }).join("");
  box.querySelectorAll(".opt").forEach(b => b.addEventListener("click", () => {
    box.querySelectorAll(`.opt[data-q="${b.dataset.q}"]`).forEach(x => x.classList.remove("sel"));
    b.classList.add("sel");
  }));
  box.querySelectorAll("input[type=text]").forEach(inp => inp.addEventListener("change", () => {
    inp.dataset.custom = inp.value;
  }));
}

/* ---------- ③ 组装 ---------- */
$("btnAssemble").addEventListener("click", async () => {
  const answers = {};
  questions.forEach((q, i) => {
    const sel = document.querySelector(`.opt[data-q="${i}"].sel`);
    const inp = document.querySelector(`input[data-q="${i}"]`);
    const v = (inp && inp.dataset.custom) || (sel && sel.dataset.v) || q.default || "";
    answers[q.id] = { question: q.ask, apply: q.apply, answer: v };
  });
  $("asmMsg").textContent = "组装中…";
  const r = await fetch("/api/assemble", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sid, answers })
  });
  const j = await r.json();
  $("step3").classList.remove("hidden");
  if (!j.ok) {
    $("result").innerHTML =
      (j.errors || []).map(e => `<div class="err">${esc(e)}</div>`).join("") ||
      `<div class="err">组装失败</div>`;
    $("dl").classList.add("hidden");
    return;
  }
  renderReport(j.report, j.download);
});

function renderReport(rep, dlUrl) {
  const v = rep.verify || {};
  const rt = v.roundtrip || {};
  const xsd = v.xsd || {};
  const st = v.stats || {};
  const rows = [
    ["信号 / 端子 / 接线",
      `${rep.assembly.signals} / ${rep.assembly.terminals} / ${rep.assembly.links}`],
    ["组件 / 接口 / DAE 引用", `${st.components} / ${st.interfaces} / ${st.dae_refs}`],
    ["CAEX 2.15 XSD", xsd.ok === undefined ? "跳过" : (xsd.ok ? '<span class="okline">通过</span>' : '<span class="badline">未过</span>')],
    ["⓪ 解析回读逐字一致",
      rt.ok ? `<span class="okline">${rt.io_readback}/${rt.expected} 一致</span>`
            : `<span class="badline">${rt.io_readback}/${rt.expected}（${(rt.mismatches || []).concat(rt.missing || []).join("；") || "见详情"}）</span>`],
    ["自动补充", (rep.normalized || []).join("；") || "无"],
    ["审查警告", (rep.audit_warnings || []).length + " 条"],
  ];
  $("result").innerHTML =
    `<p class="okline">标准 AML 生成成功</p>` +
    `<table class="rep">${rows.map(r2 => `<tr><td>${r2[0]}</td><td>${r2[1]}</td></tr>`).join("")}</table>`;
  $("dl").classList.remove("hidden");
  $("dl").href = dlUrl;
}

$("btnRestart").addEventListener("click", () => location.reload());

function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
