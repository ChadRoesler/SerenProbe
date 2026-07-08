/* ── SerenProbe Viewer — Eval Dashboard ────────────────────────────────
 * Tabs: Eval, Docker, Config
 * Orange-tinted accent. Same api() + showTab() shell contract as other
 * Seren viewer UIs.
 * ──────────────────────────────────────────────────────────────────── */

// ── Shell-provided helpers (from seren_meninges viewer baseplate) ───
/* global api, showTab, escapeHtml, getToken */

// ── State ───────────────────────────────────────────────────────────
let currentEval = null;
let currentDocker = null;
let currentConfig = null;

// ── Tab switching ───────────────────────────────────────────────────
// The shell owns show/hide via showTab() (toggles the active .tabbar .tab and
// the .view whose id === the tab) and auto-activates the first tab on load. We
// just lazy-load each view's data the first time it opens.
const _loaded = {};
function lazyLoad(id) {
  if (!id || _loaded[id]) return;
  _loaded[id] = true;
  if (id === 'eval') refreshEval();
  else if (id === 'docker') { refreshDocker(); refreshDockerConfig(); }
  else if (id === 'config') refreshConfig();
}

// ── Eval ────────────────────────────────────────────────────────────
async function refreshEval() {
  const body = document.getElementById('eval-body');
  if (!body) return;
  body.innerHTML = '<div class="loading">loading…</div>';
  try {
    const data = await api('/eval/results');
    currentEval = data;
    const info = document.getElementById('eval-info');
    const qc = data.query_count || 0;
    info.textContent = `${qc} queries · k=${data.k || 10}`;
    let html = '<table class="kv-table"><tr><th>Store</th><th>HR</th><th>MRR</th><th>P@k</th><th>R@k</th><th>NDCG</th><th>IoU</th><th>P-Ω</th></tr>';
    for (const [name, m] of Object.entries(data.stores || {})) {
      const hr = (m.hit_rate || 0).toFixed(3);
      const mr = (m.mrr || 0).toFixed(3);
      const pr = (m.precision || 0).toFixed(3);
      const re = (m.recall || 0).toFixed(3);
      const nd = (m.ndcg || 0).toFixed(3);
      const io = (m.iou || 0).toFixed(3);
      const po = (m.prec_omega || 0).toFixed(3);
      html += `<tr><td class="k">${escapeHtml(name)}</td><td>${hr}</td><td>${mr}</td><td>${pr}</td><td>${re}</td><td>${nd}</td><td>${io}</td><td>${po}</td></tr>`;
    }
    html += '</table>';
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

async function runEval() {
  const btn = document.getElementById('run-eval');
  const orig = btn.textContent;
  btn.textContent = 'running…';
  btn.disabled = true;
  try {
    const data = await api('/eval/run', { method: 'POST' });
    if (data && data.ok) {
      await refreshEval();
    } else {
      alert('Eval failed: ' + (data && data.error || 'unknown'));
    }
  } catch (e) {
    alert('Eval error: ' + (e.message || e));
  } finally {
    btn.textContent = orig;
    btn.disabled = false;
  }
}

// ── Docker config management ───────────────────────────────────────
async function refreshDockerConfig() {
  const body = document.getElementById('docker-config-body');
  const summary = document.getElementById('docker-config-summary');
  if (!body) return;
  try {
    const [cfg, pc] = await Promise.all([
      api('/docker/config').catch(() => ({})),
      api('/docker/probeconfig').catch(() => ({})),
    ]);
    const di = cfg.docker || {};
    const installed = di.installed ? '✓ Docker installed' : '✗ Docker not found';
    const running = di.running ? 'daemon running' : 'daemon DOWN';
    if (summary) summary.textContent = `${installed} · ${running}`;

    let html = '<div class="config-section">';
    const s = pc.summary;
    const srcLabel = pc.active ? 'uploaded' : 'shipped example';
    html += '<div class="topo-summary">';
    if (pc.errors && pc.errors.length) {
      html += '<div class="status-pill warn">active ProbeConfig is INVALID</div>';
      html += '<ul class="error-list">';
      for (const e of pc.errors) html += `<li>${escapeHtml(e)}</li>`;
      html += '</ul>';
    } else if (s) {
      const counts = `${s.loci.length} loci · ${s.memory.length} memory · ${s.corpus.length} corpus`;
      html += `<div class="status-pill ok">active topology: ${escapeHtml(counts)}</div>`;
      html += `<div class="topo-source">source: ${escapeHtml(srcLabel)} — edit it in the <a href="#" class="tab-link" data-tab="config">Config</a> tab</div>`;
      html += '<div class="topo-nodes">';
      for (const n of s.loci)   html += `<span class="node-chip loci">${escapeHtml(n)}</span>`;
      for (const n of s.memory) html += `<span class="node-chip memory">${escapeHtml(n)}</span>`;
      for (const n of s.corpus) html += `<span class="node-chip corpus">${escapeHtml(n)}</span>`;
      html += '</div>';
    } else {
      html += '<div class="empty">No ProbeConfig loaded — set one in the Config tab.</div>';
    }
    if (pc.warnings && pc.warnings.length) {
      html += '<ul class="warn-list">';
      for (const w of pc.warnings) html += `<li>⚠ ${escapeHtml(w)}</li>`;
      html += '</ul>';
    }
    html += '</div>';
    html += '<div class="note"><span class="label">▶ Start</span> compiles this topology into a docker-compose and builds each service from the shipped Dockerfiles. Bring-your-own prebuilt images via the start request\'s image_overrides (advanced).</div>';
    html += '</div>';
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

// ── Docker environment ─────────────────────────────────────────────
async function refreshDocker() {
  const body = document.getElementById('docker-body');
  if (!body) return;
  body.innerHTML = '<div class="loading">loading…</div>';
  try {
    const data = await api('/docker/status');
    currentDocker = data;
    const info = document.getElementById('docker-info');
    if (data.managed && data.running) {
      const cid = data.id || '?';
      info.textContent = `container ${escapeHtml(cid)} running · ports mapped`;
      let html = '<table class="kv-table"><tr><th>Property</th><th>Value</th></tr>';
      for (const [k, v] of Object.entries(data)) {
        const vs = typeof v === 'object' ? JSON.stringify(v) : String(v);
        html += `<tr><td class="k">${escapeHtml(k)}</td><td class="v">${escapeHtml(vs)}</td></tr>`;
      }
      html += '</table>';
      body.innerHTML = html;
    } else if (data.managed && !data.running) {
      info.textContent = 'container exists but is stopped';
      body.innerHTML = '<div class="empty">Container exists but is not running. Click ▶ Start to relaunch.</div>';
    } else {
      info.textContent = 'no container';
      body.innerHTML = '<div class="empty">No Docker environment running. Click ▶ Start to launch one.</div>';
    }
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

async function dockerStart() {
  const btn = document.getElementById('docker-start');
  const orig = btn.textContent;
  btn.textContent = 'building…';
  btn.disabled = true;
  try {
    const data = await api('/docker/start', { method: 'POST' });
    if (data && data.ok) {
      await refreshDocker();
    } else {
      alert('Docker start failed: ' + (data && data.error || 'unknown'));
    }
  } catch (e) {
    alert('Docker error: ' + (e.message || e));
  } finally {
    btn.textContent = orig;
    btn.disabled = false;
  }
}

async function dockerStop() {
  const btn = document.getElementById('docker-stop');
  const orig = btn.textContent;
  btn.textContent = 'stopping…';
  btn.disabled = true;
  try {
    const data = await api('/docker/stop', { method: 'POST' });
    if (data && data.ok) {
      await refreshDocker();
    } else {
      alert('Docker stop failed: ' + (data && data.error || 'unknown'));
    }
  } catch (e) {
    alert('Docker error: ' + (e.message || e));
  } finally {
    btn.textContent = orig;
    btn.disabled = false;
  }
}

async function dockerRunEval() {
  const btn = document.getElementById('docker-run-eval');
  const orig = btn.textContent;
  btn.textContent = 'running…';
  btn.disabled = true;
  try {
    const data = await api('/docker/run-eval', { method: 'POST' });
    if (data && data.ok) {
      alert('Eval complete! Results loaded. Switch to the Eval tab to view.');
      await refreshDocker();
    } else {
      alert('Docker run-eval failed: ' + (data && data.error || 'unknown'));
    }
  } catch (e) {
    alert('Docker error: ' + (e.message || e));
  } finally {
    btn.textContent = orig;
    btn.disabled = false;
  }
}

// ── Init ────────────────────────────────────────────────────────────
async function refreshConfig() {
  const body = document.getElementById('config-body');
  if (!body) return;
  body.innerHTML = '<div class="loading">loading…</div>';
  try {
    // topology (ProbeConfig) + legacy store endpoints, fetched in parallel
    const [cfg, pc] = await Promise.all([
      api('/eval/config').catch(() => ({})),
      api('/docker/probeconfig').catch(() => ({})),
    ]);
    currentConfig = cfg;
    const info = document.getElementById('config-info');
    if (info) info.textContent = `${cfg.stores || 5} stores · v${cfg.version || '?'}`;

    // ── ProbeConfig (topology) — what ▶ Start spins up ──
    const pcYaml = pc.yaml || '';
    const pcSource = pc.active ? 'uploaded (active)' : 'shipped example';
    let html = '<div class="config-section"><h4>ProbeConfig — topology</h4>';
    html += `<div class="note"><span class="label">source:</span> ${escapeHtml(pcSource)} — declare X Loci / Y Memory / Z Corpus, then Set Active to make it the topology <b>▶ Start</b> builds.</div>`;
    html += '<div class="upload-form">';
    html += `<textarea id="probeconfig-yaml" rows="14" spellcheck="false">${escapeHtml(pcYaml)}</textarea>`;
    html += '<div class="ctrls-row">';
    html += '<button class="btn" onclick="uploadProbeConfig()">✓ Validate &amp; Set Active</button>';
    html += '<button class="btn-sm" onclick="refreshConfig()">↻ Reload active</button>';
    html += '</div>';
    html += '<div class="config-save-result" id="probeconfig-result"></div>';
    html += '</div></div>';

    // ── Store endpoints — manual eval targeting (legacy) ──
    const fields = [
      ['memory_url', 'SerenMemory'],
      ['loci_nv_url', 'Loci · no-vec'],
      ['loci_v_url', 'Loci · vec'],
      ['scc_nv_url', 'SCC · no-vec'],
      ['scc_v_url', 'SCC · vec'],
      ['capture_path', 'Capture path'],
    ];
    html += '<div class="config-section"><h4>Store endpoints — manual targets</h4>';
    html += '<div class="note"><span class="label">note:</span> point the eval at already-running stores. Starting a topology above repoints the eval at those instead.</div>';
    html += '<div class="upload-form">';
    for (const [key, label] of fields) {
      const val = cfg[key] == null ? '' : String(cfg[key]);
      html += `<label>${escapeHtml(label)}:
        <input id="cfg-${key}" type="text" value="${escapeHtml(val)}"/></label>`;
    }
    html += '<button class="btn" onclick="saveConfig()">💾 Save store config</button>';
    html += '<div class="config-save-result" id="config-save-result"></div>';
    html += '</div></div>';

    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

async function uploadProbeConfig() {
  const ta = document.getElementById('probeconfig-yaml');
  const result = document.getElementById('probeconfig-result');
  if (!ta) return;
  const yamlText = ta.value;
  if (!yamlText.trim()) { setResult(result, 'err', 'ProbeConfig is empty.'); return; }
  setResult(result, '', 'validating…');
  try {
    // raw fetch (not api()) so we can read the 400 body's compile errors
    const headers = { 'Content-Type': 'application/json' };
    const tok = (typeof getToken === 'function') ? getToken() : '';
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    const resp = await fetch('/docker/probeconfig', {
      method: 'POST', headers, body: JSON.stringify({ probe_config: yamlText }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      const s = data.summary || {};
      const counts = `${(s.loci || []).length} loci · ${(s.memory || []).length} memory · ${(s.corpus || []).length} corpus`;
      let msg = `✓ active — ${counts}`;
      if (data.warnings && data.warnings.length) msg += '\n⚠ ' + data.warnings.join('\n⚠ ');
      setResult(result, 'ok', msg);
    } else {
      const detail = (data && data.detail) || data || {};
      const errs = detail.errors || [];
      const warns = detail.warnings || [];
      let msg = errs.length ? ('✗ ' + errs.join('\n✗ ')) : (`validation failed (HTTP ${resp.status})`);
      if (warns.length) msg += '\n⚠ ' + warns.join('\n⚠ ');
      setResult(result, 'err', msg);
    }
  } catch (e) {
    setResult(result, 'err', 'error: ' + (e.message || e));
  }
}

function setResult(el, cls, text) {
  if (!el) return;
  el.className = 'config-save-result' + (cls ? ' ' + cls : '');
  el.textContent = text;
}

async function saveConfig() {
  const keys = ['memory_url', 'loci_nv_url', 'loci_v_url', 'scc_nv_url', 'scc_v_url', 'capture_path'];
  const payload = {};
  for (const k of keys) {
    const el = document.getElementById('cfg-' + k);
    if (el) payload[k] = el.value.trim();
  }
  const result = document.getElementById('config-save-result');
  try {
    const data = await api('/eval/config', {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { 'Content-Type': 'application/json' },
    });
    if (data && data.ok) {
      if (result) { result.className = 'config-save-result ok'; result.textContent = '✓ saved — eval stores repointed'; }
    } else {
      if (result) { result.className = 'config-save-result err'; result.textContent = 'save failed: ' + ((data && data.error) || 'unknown'); }
    }
  } catch (e) {
    if (result) { result.className = 'config-save-result err'; result.textContent = 'save error: ' + (e.message || e); }
  }
}

document.addEventListener('DOMContentLoaded', function() {
  // Wire run buttons
  const runEvalBtn = document.getElementById('run-eval');
  if (runEvalBtn) runEvalBtn.addEventListener('click', runEval);

  // Wire Docker buttons
  const dockerStartBtn = document.getElementById('docker-start');
  if (dockerStartBtn) dockerStartBtn.addEventListener('click', dockerStart);
  const dockerStopBtn = document.getElementById('docker-stop');
  if (dockerStopBtn) dockerStopBtn.addEventListener('click', dockerStop);
  const dockerRunEvalBtn = document.getElementById('docker-run-eval');
  if (dockerRunEvalBtn) dockerRunEvalBtn.addEventListener('click', dockerRunEval);

  // Wire tab clicks (both .tab buttons and .tab-link cross-nav anchors)
  document.querySelectorAll('.tab, .tab-link').forEach(el => {
    el.addEventListener('click', function(e) {
      e.preventDefault();
      const tab = this.getAttribute('data-tab');
      if (!tab) return;
      showTab(tab);        // shell: toggles the active .tabbar .tab + .view by id
      lazyLoad(tab);
    });
  });

  // Wire refresh-all
  const refreshBtn = document.getElementById('refresh-all');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() {
      refreshEval();
      refreshDocker();
      refreshDockerConfig();
      refreshConfig();
    });
  }

  // Wire per-panel refresh buttons
  document.querySelectorAll('[data-refresh]').forEach(el => {
    el.addEventListener('click', function() {
      const target = this.getAttribute('data-refresh');
      if (target === 'eval') refreshEval();
      else if (target === 'docker') { refreshDocker(); refreshDockerConfig(); }
      else if (target === 'config') refreshConfig();
    });
  });

  // the shell activates the first .view on DOMContentLoaded; load its data too
  lazyLoad(document.querySelector('.tabbar .tab')?.dataset?.tab);
});
