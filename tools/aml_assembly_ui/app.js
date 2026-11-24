import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const S = {
  sid: null, model: null, meshes: null, stats: null,
  objects: {}, pivots: {}, selected: null, autoTimer: null,
  pendingStep: null, actualEls: {},
};

/* ---------------------------------------------------------------- 3D */
let renderer, scene, camera, controls;

function init3D() {
  const box = document.getElementById('viewport');
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  box.appendChild(renderer.domElement);
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x141a23);
  camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100);
  camera.position.set(1.6, 1.4, 1.6);
  controls = new OrbitControls(camera, renderer.domElement);
  scene.add(new THREE.GridHelper(4, 40, 0x2a3547, 0x1c2431));
  const axes = new THREE.AxesHelper(0.25); axes.position.y = 0.001;
  scene.add(axes);
  scene.add(new THREE.HemisphereLight(0xdfe8f5, 0x202833, 0.9));
  const dir = new THREE.DirectionalLight(0xffffff, 1.1);
  dir.position.set(2, 3, 2); scene.add(dir);
  const resize = () => {
    const w = box.clientWidth, h = box.clientHeight;
    renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(box); resize();
  (function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
}

function clear3D() {
  for (const id of Object.keys(S.objects)) {
    const o = S.objects[id];
    o.root.parent && o.root.parent.remove(o.root);
    o.mesh && o.mesh.geometry.dispose();
  }
  S.objects = {}; S.pivots = {}; S.selected = null;
}

function buildAssembly() {
  clear3D();
  const comps = S.model.components, byId = {};
  const jointsByComp = {};
  for (const j of S.model.joints) (jointsByComp[j.component] = jointsByComp[j.component] || []).push(j);
  for (const c of comps) {
    const root = new THREE.Object3D(); root.name = c.id;
    root.position.fromArray(c.frame.pos);
    if (c.frame.quat) root.quaternion.fromArray(c.frame.quat);
    // 同组件多关节（打包模式 DAE 溯源）→ 链式 pivot（第 k 级沿 z 提升 0.35k，与 scene.xml 一致）
    const jl = jointsByComp[c.id] || [];
    let node = root;
    jl.forEach((j, k) => {
      const pivot = new THREE.Object3D();
      pivot.position.fromArray(j.anchor.map((v, i) => v + (k > 0 && i === 2 ? 0.35 * k : 0)));
      const payload = new THREE.Object3D();
      payload.position.copy(pivot.position).multiplyScalar(-1);
      node.add(pivot); pivot.add(payload);
      S.pivots[j.name] = pivot;
      const ax = new THREE.Vector3().fromArray(j.axis).normalize();
      node.add(new THREE.ArrowHelper(ax, new THREE.Vector3(), 0.12, 0x37b3ff, 0.03, 0.015));
      node = payload;
    });
    let mesh = null;
    const md = S.meshes && S.meshes[c.id];
    if (md && md.vertices.length) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(md.vertices.flat(), 3));
      geo.setIndex(md.faces.flat());
      geo.computeVertexNormals();
      mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
        color: 0xc8734d, metalness: .35, roughness: .55,
        emissive: 0x000000 }));
      node.add(mesh);
    } else if (c.parent !== null) {
      mesh = new THREE.Mesh(new THREE.CapsuleGeometry(.05, .22, 4, 12),
        new THREE.MeshStandardMaterial({ color: 0x6a7280, metalness: .3, roughness: .6 }));
      mesh.position.y = .12; node.add(mesh);
    }
    S.objects[c.id] = { root, payload: node, mesh, comp: c };
    byId[c.id] = S.objects[c.id];
  }
  for (const c of comps) {
    const o = byId[c.id];
    if (c.parent && byId[c.parent]) byId[c.parent].payload.add(o.root);
    else scene.add(o.root);
  }
  // 自动取景：相机对准装配包络（模型可能高于原点）
  const box = new THREE.Box3();
  for (const o of Object.values(S.objects)) box.expandByObject(o.root);
  if (!box.isEmpty()) {
    const center = box.getCenter(new THREE.Vector3());
    const size = Math.max(box.getSize(new THREE.Vector3()).length(), 0.3);
    controls.target.copy(center);
    camera.position.set(center.x + size * .75, center.y + size * .55, center.z + size * .9);
    camera.near = size / 100; camera.far = Math.max(100, size * 20);
    camera.updateProjectionMatrix();
  }
  document.getElementById('hud').textContent =
    `组件 ${comps.length} · 关节 ${S.model.joints.length} · mesh ${S.stats.meshes}` +
    ` · body=${S.stats.nbody} joint=${S.stats.njnt}`;
}

function applyState(state) {
  if (!state) return;
  for (const j of S.model.joints) {
    const pivot = S.pivots[j.name];
    if (pivot && state.qpos_deg && state.qpos_deg[j.name] !== undefined) {
      const ax = new THREE.Vector3().fromArray(j.axis).normalize();
      pivot.quaternion.setFromAxisAngle(ax, THREE.MathUtils.degToRad(state.qpos_deg[j.name]));
    }
  }
  document.getElementById('seq').textContent = state.seq;
  document.getElementById('tsim').textContent = state.t_s;
  for (const [name, el] of Object.entries(S.actualEls))
    if (el && state.actuals[name] !== undefined) el.textContent = state.actuals[name];
}

function select(id) {
  for (const [k, o] of Object.entries(S.objects))
    o.mesh && o.mesh.material.emissive.setHex(k === id ? 0x1d6aa3 : 0x000000);
  S.selected = id;
  document.querySelectorAll('.tnode').forEach(n =>
    n.classList.toggle('sel', n.dataset.id === id));
  const c = S.objects[id] && S.objects[id].comp;
  const d = document.getElementById('detail');
  if (!c) { d.textContent = '点击组件查看详情'; return; }
  const q = v => v === null || v === undefined ? '-' : JSON.stringify(v);
  d.innerHTML = `<b>${c.name}</b> (${c.id})<br>` +
    `role: ${c.role || '-'}<br>` +
    `frame pos: ${c.frame.pos.join(', ')}  rpy: ${c.frame.rpy_deg.join(', ')}<br>` +
    `world pos: ${c.world.pos.map(v => v.toFixed(3)).join(', ')}<br>` +
    `geom: ${c.geom_uri ? c.geom_uri.split('/').pop() : '（无显式几何）'}<br>` +
    `joints: ${c.joints.join(', ') || '-'}<br>` +
    `signals: ${c.signals.join(', ') || '-'}`;
}

/* ---------------------------------------------------------------- 面板 */
function buildChips() {
  const m = S.model, s = S.stats;
  const chip = (k, v, cls = '') => `<span class="chip ${cls}">${k} <b>${v}</b></span>`;
  document.getElementById('chips').innerHTML =
    chip('组件', m.components.length) + chip('关节', m.joints.length) +
    chip('信号', m.signals.length) + chip('接线', m.links.length) +
    chip('mesh', s.meshes) + chip('body', s.nbody) + chip('actuator', s.nu) +
    chip('sensor', s.nsensor) +
    chip('编译', s.compile ? '过' : '失败', s.compile ? 'ok' : 'err') +
    chip('ST', S.stGenerated ? '生成' : '无');
}

function buildTree() {
  const el = document.getElementById('tree');
  const byParent = {};
  for (const c of S.model.components)
    (byParent[c.parent] = byParent[c.parent] || []).push(c);
  let html = '';
  const walk = (list, depth) => {
    for (const c of list) {
      const tags = [];
      if (c.joints.length) tags.push(`<span class="tag jtag">⟳${c.joints.length}</span>`);
      if (c.signals.length) tags.push(`<span class="tag">IO ${c.signals.length}</span>`);
      if (c.geom_uri) tags.push(`<span class="tag">▣</span>`);
      html += `<div class="tnode" data-id="${c.id}" style="padding-left:${6 + depth * 14}px">` +
        `${'└ '}${c.name} <span class="tag">${c.id}</span>${tags.join('')}</div>`;
      walk(byParent[c.id] || [], depth + 1);
    }
  };
  walk(byParent[null] || [], 0);
  el.innerHTML = html;
  el.querySelectorAll('.tnode').forEach(n =>
    n.addEventListener('click', () => select(n.dataset.id)));
}

function buildIO() {
  const rows = S.model.signals.map(s => {
    const bind = s.source_id ? `${s.component_id}#${s.source_id}` :
      `<span style="color:var(--warn)">未绑定</span>`;
    return `<tr class="${s.dir === 'input' ? 'in' : 'out'}">
      <td title="${s.description || ''}">${s.name}</td>
      <td>${s.dir === 'input' ? '↧ in' : '↥ out'}</td><td>${s.type}</td>
      <td>${s.address}</td><td>${s.scale ?? '-'}</td>
      <td>${s.range ? s.range.join('…') : '-'}</td>
      <td>${s.role || '-'}</td><td>${bind}</td></tr>`;
  }).join('');
  document.getElementById('ioTable').innerHTML =
    `<tr><th>name</th><th>dir</th><th>type</th><th>addr</th><th>scale</th>
     <th>range</th><th>role</th><th>binding</th></tr>` + rows;
  document.getElementById('links').innerHTML = S.model.links
    .map(l => `<div>${l.a.name} ⟷ ${l.b.name}</div>`).join('') ||
    '<div style="color:var(--dim)">（无接线）</div>';
  const pend = S.model.pending;
  document.getElementById('pendingBadge').textContent = pend.length || '';
  document.getElementById('pendingList').innerHTML = pend
    .map(p => `<li>${p}</li>`).join('') || '<li style="border-color:var(--ok)">无待确认项</li>';
}

function buildControls() {
  const wrap = document.getElementById('controls');
  S.actualEls = {};
  const targets = S.model.signals
    .filter(s => s.role === 'target' && s.source_id)
    .sort((a, b) => (a.source_id || '').localeCompare(b.source_id || ''));
  if (!targets.length) { wrap.innerHTML = '<div class="empty">无绑定的 target 信号</div>'; return; }
  wrap.innerHTML = '';
  for (const t of targets) {
    const rng = t.range || [-1000, 1000];
    const deg = v => (v * (t.scale || 1)).toFixed(2);
    const row = document.createElement('div'); row.className = 'jrow';
    row.innerHTML = `
      <div class="jhead"><span>${t.source_id} <span style="color:var(--dim)">${t.name}</span></span>
        <span class="act" data-act="${t.name.replace('target', 'actual')}">-</span></div>
      <input type="range" min="${rng[0]}" max="${rng[1]}" step="1" value="0">
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--dim)">
        <span>raw <b class="rv">0</b> → ${deg(1) === '1.00' ? '' : deg(1) + '°/LSB'}</span>
        <span class="dv">0.00°</span></div>`;
    const slider = row.querySelector('input');
    const rv = row.querySelector('.rv'), dv = row.querySelector('.dv');
    const act = row.querySelector('.act');
    if (act) S.actualEls[t.name.replace('target', 'actual')] = act;
    slider.addEventListener('input', () => {
      rv.textContent = slider.value; dv.textContent = deg(+slider.value) + '°';
      queueStep({ [t.name]: +slider.value }, 12);
    });
    wrap.appendChild(row);
  }
  document.getElementById('btnTest').disabled = false;
}

function buildDownloads() {
  const names = ['scene.xml', 'mapping.json', 'io_test.xml', 'io_test.st',
    'plc_test_contract.json', 'modbus.json', 'report.json'];
  document.getElementById('downloads').innerHTML = names
    .map(n => `<a href="/api/download?sid=${S.sid}&name=${n}">${n}</a>`).join('');
}

/* ---------------------------------------------------------------- API */
let stepTimer = null;
function queueStep(targets, steps) {
  S.pendingStep = { targets: { ...(S.pendingStep?.targets || {}), ...targets }, steps };
  if (stepTimer) return;
  stepTimer = setTimeout(async () => {
    stepTimer = null;
    const p = S.pendingStep; S.pendingStep = null;
    if (!p || !S.sid) return;
    try {
      const r = await fetch(`/api/step?sid=${S.sid}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(p) }).then(r => r.json());
      if (r.ok) applyState(r.state);
    } catch (e) { setStatus('步进失败: ' + e.message, true); }
  }, 60);
}

async function runDemo(mode) {
  const rc = mode === 'robotcell';
  setStatus(rc
    ? 'RobotCell 装配中（106 个真实 DAE 网格，首次需构建缓存，约 1-3 分钟）…'
    : '示例组装中（配置器 → 装配 → 闭环）…');
  try {
    const r = await fetch('/api/demo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }) }).then(r => r.json());
    if (r.ok) await loadModel(r.sid, r.summary);
    else setStatus('示例失败: ' + r.errors.join('; '), true);
  } catch (e) { setStatus('示例失败: ' + e.message, true); }
}

async function loadModel(sid, summary) {
  S.sid = sid;
  const r = await fetch('/api/model?sid=' + sid).then(r => r.json());
  if (!r.ok) { setStatus('模型载入失败', true); return; }
  S.model = r.model; S.meshes = r.meshes; S.stats = r.stats;
  S.stGenerated = r.st_generated;
  buildChips(); buildTree(); buildIO(); buildAssembly(); buildControls();
  buildDownloads();
  document.getElementById('btnTest').disabled = !S.stats.compile;
  const a = r.acceptance;
  renderAcceptance(a);
  select(S.model.components[0] && S.model.components[0].id);
  setStatus(`装配完成 —— ${summary.components} 组件 / ${summary.joints} 关节 / ` +
    `${summary.signals} 信号；mujoco body=${summary.nbody}；IO 验收 ${summary.acceptance_ok ? '过' : '未过/未测'}`);
}

function renderAcceptance(a) {
  const el = document.getElementById('testResult'), badge = document.getElementById('testBadge');
  if (!a) { el.innerHTML = '<div class="empty">未运行</div>'; badge.textContent = ''; return; }
  badge.textContent = a.ok ? '过' : '不过'; badge.className = 'badge ' + (a.ok ? 'ok' : 'err');
  el.innerHTML = a.joints.map(j => `
    <div>${j.pass ? '<span class="pass">✔</span>' : '<span class="fail">✘</span>'}
      <b>${j.joint}</b> cmd ${j.cmd_deg}° → ${j.reached_deg}°
      （err ${j.error_deg}° / tol ${j.tolerance_deg}°，settle ${j.settle_s ?? '超时'}s，
      raw→feedback ${j.cmd_raw}→${j.feedback_raw}）</div>`).join('') +
    `<div class="mode">${a.mode}；容差 ${a.tolerances.angle_deg}°/${a.tolerances.timeout_s}s；seq=${a.final_seq}</div>`;
}

function setStatus(msg, err) {
  const f = document.getElementById('status');
  f.textContent = msg; f.className = err ? 'err' : '';
}

/* ---------------------------------------------------------------- UI 绑定 */
function initUI() {
  document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('on'));
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    document.getElementById('tab-' + b.dataset.tab).classList.add('on');
  }));
  document.getElementById('btnDemo').addEventListener('click', () => runDemo('parts'));
  document.getElementById('btnDemoRc').addEventListener('click', () => runDemo('robotcell'));
  document.getElementById('fileInput').addEventListener('change', async e => {
    if (!e.target.files.length) return;
    const fd = new FormData();
    for (const f of e.target.files) fd.append(f.name, f);
    setStatus('上传装配中…');
    try {
      const r = await fetch('/api/upload', { method: 'POST', body: fd }).then(r => r.json());
      if (r.ok) await loadModel(r.sid, r.summary);
      else setStatus('装配失败: ' + r.errors.join('; '), true);
    } catch (e) { setStatus('上传失败: ' + e.message, true); }
    e.target.value = '';
  });
  document.getElementById('btnTest').addEventListener('click', async () => {
    if (!S.sid) return;
    setStatus('IO 验收运行中…');
    try {
      const r = await fetch('/api/test?sid=' + S.sid, { method: 'POST' }).then(r => r.json());
      if (r.ok) { renderAcceptance(r.acceptance);
        setStatus(`IO 验收${r.acceptance.ok ? '通过' : '未通过'}（` +
          `${r.acceptance.joints.filter(j => j.pass).length}/${r.acceptance.joints.length} 关节）`); }
      else setStatus('验收失败: ' + r.errors.join('; '), true);
    } catch (e) { setStatus('验收失败: ' + e.message, true); }
  });
  document.getElementById('autoRun').addEventListener('change', e => {
    if (S.autoTimer) { clearInterval(S.autoTimer); S.autoTimer = null; }
    if (e.target.checked)
      S.autoTimer = setInterval(() => queueStep({}, 8), 120);
  });
}

init3D(); initUI();
