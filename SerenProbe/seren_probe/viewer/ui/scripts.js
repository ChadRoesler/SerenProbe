/* ── SerenProbe Viewer - Eval Dashboard ────────────────────────────────
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
let currentRegrade = null;

// ── auth'd raw fetch ────────────────────────────────────────────────
// api() carries the bearer token for us, but it throws on non-2xx and swallows the
// response body -- so callers that need to READ a 400/401/501 body (compile errors,
// the SCC-not-installed hint, the adopt offer) drop to raw fetch(). Every one of
// those MUST attach the token itself, or Meninges' bearer_auth_middleware 401s it.
// The adopt banner shipped WITHOUT this and died quietly for exactly that reason:
// a 401 body has no `adoptable` key, so the guard read it as "nothing to adopt"
// and returned. Use this instead of bare fetch() for any authed endpoint.
function authFetch(url, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers);
  const tok = (typeof getToken === 'function') ? getToken() : '';
  if (tok) headers['Authorization'] = 'Bearer ' + tok;
  return fetch(url, Object.assign({}, opts, { headers }));
}

// ---- Copy to clipboard -----------------------------------------------------
//
// navigator.clipboard IS UNDEFINED OUTSIDE A SECURE CONTEXT. `localhost` counts as
// secure; `http://192.168.x.x` does NOT -- and opening the viewer from another box on
// the LAN is the normal way to use this thing. So the execCommand path below is not
// legacy cruft to be cleaned up later: on the box you will most often be reading a
// 6000-line lint from, it is the ONLY path that works. Delete it and the button dies
// silently on exactly the machine that needed it.
function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top = '-1000px';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

function flashBtn(btn, ok) {
  if (!btn) return;
  if (!btn.dataset.orig) btn.dataset.orig = btn.textContent;
  btn.textContent = ok ? '\u2713 copied' : '\u2717 copy failed';
  btn.classList.toggle('copied', !!ok);
  setTimeout(() => {
    btn.textContent = btn.dataset.orig;
    btn.classList.remove('copied');
  }, 1400);
}

function copyToClipboard(text, btn) {
  if (!text) { flashBtn(btn, false); return; }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(
      () => flashBtn(btn, true),
      () => flashBtn(btn, fallbackCopy(text)));
  } else {
    flashBtn(btn, fallbackCopy(text));
  }
}

// Markdown pipe table. Chosen over TSV on purpose: these numbers get pasted into a
// chat window to be read by a human or a model, and a markdown table survives that trip
// intact. TSV collapses into mush the moment anything reflows it.
function mdTable(headers, rows) {
  const esc = (s) => String(s == null ? '' : s).replace(/\|/g, '\\|');
  const out = ['| ' + headers.map(esc).join(' | ') + ' |',
               '| ' + headers.map(() => '---').join(' | ') + ' |'];
  for (const r of rows) out.push('| ' + r.map(esc).join(' | ') + ' |');
  return out.join('\n');
}

const _f3 = (x) => (x == null ? '' : Number(x).toFixed(3));

function evalMarkdown(data) {
  if (!data || !data.stores) return '';
  const qc = data.question_count || data.query_count || 0;
  let out = [`## SerenProbe eval - ${qc} questions, k=${data.k || 10}`];
  if (data.date) out.push(`_${data.date}_`);
  out.push('');

  for (const n of (data.ground_truth || [])) out.push(`> GROUND TRUTH: ${n}`);
  if (data.seed_skipped) out.push(`> SEED SKIPPED: ${data.seed_skipped}`);
  if (data.resolve_warnings) for (const w of data.resolve_warnings) out.push(`> WARN: ${w}`);
  if (data.ground_truth || data.seed_skipped || data.resolve_warnings) out.push('');

  const rows = [];
  for (const [name, m] of Object.entries(data.stores)) {
    const a = m.aggregate || m;
    const flags = [];
    if (m.negative_test) flags.push('negative');
    if (m.is_catchall) flags.push('catch-all');
    if (m.empty_count) flags.push(`empty ${m.empty_passes}/${m.empty_count}`);
    if (m.quiet_count) {
      let q = `quiet ${m.quiet_passes}/${m.quiet_count}`;
      if (m.quiet_margin != null) q += ` (margin ${_f3(m.quiet_margin)})`;
      flags.push(q);
    }
    // same vacuous-NDCG suppression as the table -- a paste that repeats the lie is
    // worse than the lie, because it travels.
    const ndcg = m.negative_test ? '-' : _f3(a.ndcg);
    rows.push([name, m.kind || '', m.question_count == null ? '' : m.question_count,
               _f3(a.hit_rate), _f3(a.mrr), _f3(a.precision), _f3(a.recall),
               ndcg, _f3(a.iou), _f3(a.prec_omega), flags.join(', ')]);
  }
  out.push(mdTable(['Store', 'Kind', 'Qs', 'HR', 'MRR', 'P@k', 'R@k', 'NDCG', 'IoU', 'P-\u03a9', 'Flags'], rows));

  const d = data.docket;
  if (d && d.columns && d.columns.length) {
    out.push('', '### SCC docket - with vs without edges');
    out.push(mdTable(['Edges', 'SCC', 'Coverage', 'Density', 'Recall'],
      d.columns.map(c => [
        c.flavor === 'vector' ? 'with edges' : c.flavor === 'lexical' ? 'without edges' : (c.label || c.flavor),
        c.name, _f3(c.docket_coverage), _f3(c.docket_density), _f3(c.recall)])));
    for (const dl of (d.deltas || [])) {
      out.push('', `**\u0394 edges (with - without)** on ${dl.with_edges} vs ${dl.without_edges}: `
        + `coverage ${_f3(dl.docket_coverage)} \u00b7 density ${_f3(dl.docket_density)} \u00b7 recall ${_f3(dl.recall)}`);
    }
  }
  // Quiet leaks are the headline failure on a multi-tenant run: not "did it find the
  // answer" but "did a store that should never have known, know". Never leave them off
  // the paste -- a copy that drops the bad news is a copy that lies.
  const leaks = [];
  for (const [name, m] of Object.entries(data.stores))
    for (const q of (m.quiet_leaks || [])) leaks.push([name, q]);
  if (leaks.length) {
    out.push('', `### Quiet-test LEAKS (${leaks.length})`);
    out.push(mdTable(['Store', 'Query it should NOT have answered'], leaks));
  }
  return out.join('\n');
}

function regradeMarkdown(data) {
  if (!data || !data.corpora) return '';
  let out = [`## Regrade sweep - ${data.question_count || 0} corpus questions, sort: ${data.sort_by || 'docket_coverage'}`, ''];
  for (const c of data.corpora) {
    out.push(`### ${c.corpus} (${c.flavor}) - baseline: ${c.baseline || '-'}`);
    const rows = [];
    for (const s of (c.sets || [])) {
      const m = s.metrics || {}, p = s.params || {};
      const knobs = Object.entries(p).map(([k, v]) => `${k}=${v}`).join(' ') || '-';
      rows.push([`**${s.name}**`, knobs, _f3(m.ndcg), _f3(m.docket_coverage), _f3(m.recall), _f3(m.mrr)]);
      // EVERY combo, not just the winner. A sweep that reports only max() is
      // unfalsifiable -- the curve IS the result. Same rule for the paste as for the
      // screen: never drop the rows that would disprove you.
      for (const cb of (s.combos || [])) {
        const cm = cb.metrics || {}, cp = cb.params || {};
        const ck = Object.entries(cp).map(([k, v]) => `${k}=${v}`).join(' ') || 'current';
        rows.push([`\u21b3`, ck, _f3(cm.ndcg), _f3(cm.docket_coverage), _f3(cm.recall), _f3(cm.mrr)]);
      }
    }
    out.push(mdTable(['Set', 'Knobs', 'nDCG', 'Coverage', 'Recall', 'MRR'], rows), '');
  }
  return out.join('\n');
}

function copyEval(btn) { copyToClipboard(evalMarkdown(currentEval), btn); }
function copyRegrade(btn) { copyToClipboard(regradeMarkdown(currentRegrade), btn); }
function copyLint(btn) {
  const el = document.getElementById('probeconfig-result');
  copyToClipboard(el ? el.textContent : '', btn);
}

// ---- Tab switching ---------------------------------------------------------
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
    const qc = data.question_count || data.query_count || 0;
    info.textContent = `${qc} questions · k=${data.k || 10}`;
    let html = '<div class="copy-row"><button class="btn-sm copy-btn" onclick="copyEval(this)">\u{1F4CB} Copy results</button></div>';
    // GROUND TRUTH. Loud, at the top, above the numbers -- because a number graded
    // against a MISSING answer key is not a low score, it is a non-score, and the two
    // are indistinguishable on a dashboard. orkrail-mem read HR 0.083 for a full day
    // while the store answered its queries at rank 1. I emitted this note and then
    // forgot to render it, which is the same class of mistake one layer up: the
    // harness knew, and didn't say.
    if (data.ground_truth && data.ground_truth.length) {
      for (const n of data.ground_truth) {
        const bad = /WARNING|MISSING|could NOT/i.test(n);
        html += `<div class="note ${bad ? 'err' : ''}">`
              + `<span class="label">${bad ? '⚠ ground truth:' : '✓ ground truth:'}</span> ${escapeHtml(n)}</div>`;
      }
    }
    if (data.seed_skipped) {
      html += `<div class="note"><span class="label">seed skipped:</span> `
            + `<span class="muted">${escapeHtml(data.seed_skipped)}</span></div>`;
    }
    if (data.live_import && Object.keys(data.live_import).length) {
      const parts = [];
      for (const [lname, r] of Object.entries(data.live_import)) {
        if (r.kind === 'loci') parts.push(`${escapeHtml(lname)} ← ${r.facts} facts`);
        else parts.push(`${escapeHtml(lname)} ← ${r.short}/${r.near}/${r.long} short/near/long`);
      }
      html += `<div class="note"><span class="label">⟵ live import:</span> ${parts.join(' · ')} `
            + `<span class="muted">- real data copied into the container, read-only on the live store. `
            + `Synthetic questions won't match imported data; use this to explore retrieval on real data.</span></div>`;
    }
    html += '<table class="store-table eval-table"><tr><th>Store</th><th>HR</th><th>MRR</th><th>P@k</th><th>R@k</th><th>NDCG</th><th>IoU</th><th>P-Ω</th><th></th></tr>';
    for (const [name, m] of Object.entries(data.stores || {})) {
      const a = m.aggregate || m;   // metrics live under .aggregate (snapshot shape)
      const f = (x) => (a[x] || 0).toFixed(3);
      let tail = '', rowcls = '';
      if (m.negative_test) {
        // a decoy store PASSES by staying quiet - any relevant hit is a leak
        const leaked = (a.hit_rate || 0) > 0 || (a.docket_coverage || 0) > 0;
        tail = leaked ? '<span class="neg-flag leaked">✗ leaked</span>'
                      : '<span class="neg-flag quiet">✓ stayed quiet</span>';
        rowcls = leaked ? 'neg-leaked' : 'neg-quiet';
      } else if (m.is_catchall) {
        tail = '<span class="catch-tag">catch-all</span>';
      }
      if (m.empty_count) {
        // no-answer (expect_empty) questions the store stayed quiet on
        const allpass = (m.empty_pass_rate || 0) >= 0.999;
        const passes = (m.empty_passes != null) ? m.empty_passes : Math.round((m.empty_pass_rate || 0) * m.empty_count);
        tail += ` <span class="empty-flag ${allpass ? 'empty-ok' : 'empty-leak'}" title="no-answer questions the store stayed quiet on">∅ ${passes}/${m.empty_count}</span>`;
      }
      // QUIET (quiet_in) questions: this store must NOT surface the answer. On a
      // multi-tenant run this is the headline, not a footnote -- "did Zara's store answer
      // Zara's question" is table stakes; "did Thorn's store stay out of it" is the product.
      if (m.quiet_count) {
        const allpass = (m.quiet_rate || 0) >= 0.999;
        const marg = (m.quiet_margin != null) ? ` \u00b7 margin ${Number(m.quiet_margin).toFixed(3)}` : '';
        const tip = `questions this store must NOT answer - pass = it did not surface the answer${marg}`;
        tail += ` <span class="quiet-flag ${allpass ? 'quiet-ok' : 'quiet-leak'}" title="${escapeHtml(tip)}">`
              + `\u{1F910} ${m.quiet_passes}/${m.quiet_count}</span>`;
      }
      const badge = m.negative_test ? ' <span class="neg-badge">negative</span>' : '';
      // NDCG IS VACUOUS ON A DECOY. metrics._ndcg returns 1.0 when `relevant` is empty --
      // and a decoy's relevant set is empty on EVERY question, by design. So the decoy
      // posts a perfect 1.000 and outranks every real store in the table. The regrade tab
      // already warns about this in prose ("a combo that finds nothing looks flawless")
      // and sorts by coverage to dodge it; the main table was still printing the number
      // with a straight face. A metric that flatters a HOLE is worse than no metric.
      const ndcgCell = m.negative_test
        ? '<span class="muted" title="vacuous: NDCG is 1.0 when the relevant set is empty, and a decoy has no relevant docs by design">\u2014</span>'
        : f('ndcg');
      html += `<tr class="${rowcls}"><td class="sname">${escapeHtml(name)}${badge}</td>`
            + `<td class="mval">${f('hit_rate')}</td><td class="mval">${f('mrr')}</td><td class="mval">${f('precision')}</td><td class="mval">${f('recall')}</td>`
            + `<td class="mval">${ndcgCell}</td><td class="mval">${f('iou')}</td><td class="mval">${f('prec_omega')}</td><td>${tail}</td></tr>`;
    }
    html += '</table>';
    html += renderDocket(data.docket);
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

// ── SCC docket comparison (with vs without edges) ──────────────────
// Renders data.docket (attached by run_topology_evaluation) as a side-by-side
// coverage/density/recall table + the with−without-edges delta. "Edges" = the
// vector/semantic flavor of the Loci an SCC fans. Returns '' when there are no
// SCC columns, so it no-ops cleanly on loci/memory-only reports.
function renderDocket(d) {
  if (!d || !d.columns || !d.columns.length) return '';
  const f3 = (v) => (v == null ? '-' : Number(v).toFixed(4));
  const sg = (v) => (v == null ? '-' : (v >= 0 ? '+' : '') + Number(v).toFixed(4));
  let h = '<div class="note"><span class="label">SCC Docket - with vs without edges</span><br>';
  const meta = [];
  if (d.question_count != null) meta.push(d.question_count + ' questions');
  if (d.k != null) meta.push('k=' + d.k);
  if (d.corpus_size != null) meta.push('corpus_size=' + d.corpus_size);
  meta.push('flavor: ' + (d.flavor_source || '?'));
  h += `<span class="label">edges = the vector/semantic flavor of the Loci an SCC fans.</span> ${escapeHtml(meta.join(' · '))}</div>`;
  h += '<table class="store-table"><tr><th>Edges</th><th>SCC</th><th>Coverage</th><th>Density</th><th>Recall</th></tr>';
  for (const c of d.columns) {
    const tag = c.flavor === 'vector' ? 'with edges'
              : c.flavor === 'lexical' ? 'without edges' : (c.label || c.flavor);
    h += `<tr><td>${escapeHtml(tag)}</td><td class="sname">${escapeHtml(c.name)}</td>`
       + `<td class="mval">${f3(c.docket_coverage)}</td>`
       + `<td class="mval">${f3(c.docket_density)}</td>`
       + `<td class="mval">${f3(c.recall)}</td></tr>`;
  }
  h += '</table>';
  for (const dl of (d.deltas || [])) {
    const cov = dl.docket_coverage;
    const cls = cov == null ? '' : (cov > 0.0005 ? 'delta-pos' : (cov < -0.0005 ? 'delta-neg' : ''));
    h += `<div class="docket-delta ${cls}"><span class="dl-label">Δ edges (with − without)</span> `
       + `coverage ${sg(dl.docket_coverage)} · density ${sg(dl.docket_density)} · recall ${sg(dl.recall)}</div>`;
    if (cov != null) {
      let v;
      if (cov > 0.0005) v = `edges ADDED ${sg(cov)} docket coverage`;
      else if (cov < -0.0005) v = `edges COST ${sg(cov)} docket coverage`;
      else v = 'edges made no meaningful coverage difference';
      h += `<div class="docket-verdict ${cls}">→ ${escapeHtml(v)} (${escapeHtml(dl.with_edges)} vs ${escapeHtml(dl.without_edges)})</div>`;
    }
  }
  if (d.note) h += `<div class="docket-meta">note: ${escapeHtml(d.note)}</div>`;
  return h;
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

// ── Regrade sweep (CorpusRegrades) ────────────────────────────
// Rolls the ProbeConfig's CorpusRegrades sets against each corpus (capture-once /
// replay-many SCC fusion sweep) and renders best-per-set + Δ-vs-baseline.
function renderRegrade(data) {
  if (!data) return '';
  if (data.note && (!data.corpora || !data.corpora.length))
    return `<div class="note"><span class="label">regrade:</span> ${escapeHtml(data.note)}</div>`;
  const f3 = (v) => (v == null ? '-' : Number(v).toFixed(3));
  const sg = (v) => (v == null ? '' : (v > 0.0005 ? '+' : '') + Number(v).toFixed(3));
  const dcls = (v) => (v == null ? '' : (v > 0.0005 ? 'delta-pos' : (v < -0.0005 ? 'delta-neg' : '')));
  let h = `<div class="note"><span class="label">Regrade sweep</span> - winning config per set (bold) with EVERY combo it measured beneath it, Δ vs baseline. ${escapeHtml((data.question_count || 0) + ' corpus questions · sort: ' + (data.sort_by || 'docket_coverage'))}`
        + ` <span class="muted">Read <b>coverage</b>, not ndcg: on a corpus store the ground truth is derived from the hits, and ndcg scores an empty result as a perfect 1.000 - a combo that finds nothing looks flawless.</span></div>`;
  for (const c of data.corpora) {
    h += `<div class="regrade-corpus"><b class="rg-name">${escapeHtml(c.corpus)}</b> <span class="muted">(${escapeHtml(c.flavor)}) · baseline: ${escapeHtml(c.baseline || '-')}</span></div>`;
    h += '<table class="store-table regrade-table"><tr><th>Set</th><th>nDCG</th><th>Coverage</th><th>Recall</th><th>MRR</th><th>Δ</th><th>best knobs</th></tr>';
    for (const s of c.sets) {
      const m = s.metrics || {}, d = s.delta || {}, isBase = s.name === c.baseline;
      const dparts = [];
      for (const [k, lbl] of [['docket_coverage', 'cov'], ['ndcg', 'ndcg']])
        if (d[k] != null && Math.abs(d[k]) > 0.0005) dparts.push(`<span class="${dcls(d[k])}">${lbl} ${sg(d[k])}</span>`);
      const dcell = isBase ? '<span class="muted">baseline</span>' : (dparts.join(' ') || '<span class="muted">-</span>');
      const p = s.params || {}, knobs = Object.keys(p).length ? Object.entries(p).map(([k, v]) => `${k}=${v}`).join(' ') : '-';
      h += `<tr class="${isBase ? 'rg-base' : 'rg-win'}"><td class="k">${escapeHtml(s.name)}</td>`
         + `<td class="mval">${f3(m.ndcg)}</td><td class="mval">${f3(m.docket_coverage)}</td>`
         + `<td class="mval">${f3(m.recall)}</td><td class="mval">${f3(m.mrr)}</td>`
         + `<td>${dcell}</td><td class="rg-knobs">${escapeHtml(knobs)}</td></tr>`;

      // EVERY combo the sweep measured, not just the winner. A sweep that reports
      // only max() is UNFALSIFIABLE: "hops=1 won" cannot be distinguished from
      // "hops=2 moved something the sort metric couldn't see." The curve IS the
      // result. Never hide the rows that would disprove you.
      for (const cb of (s.combos || [])) {
        const cm = cb.metrics || {}, cd = cb.delta || {}, cp = cb.params || {};
        const ck = Object.keys(cp).length ? Object.entries(cp).map(([k, v]) => `${k}=${v}`).join(' ') : 'current';
        const cparts = [];
        for (const [k, lbl] of [['docket_coverage', 'cov'], ['ndcg', 'ndcg']])
          if (cd[k] != null && Math.abs(cd[k]) > 0.0005) cparts.push(`<span class="${dcls(cd[k])}">${lbl} ${sg(cd[k])}</span>`);
        h += `<tr class="rg-combo"><td class="k rg-knobs">↳ ${escapeHtml(ck)}</td>`
           + `<td class="mval">${f3(cm.ndcg)}</td><td class="mval">${f3(cm.docket_coverage)}</td>`
           + `<td class="mval">${f3(cm.recall)}</td><td class="mval">${f3(cm.mrr)}</td>`
           + `<td>${cparts.join(' ') || '<span class="muted">-</span>'}</td><td></td></tr>`;
      }
    }
    h += '</table>';
  }
  return '<div class="copy-row"><button class="btn-sm copy-btn" onclick="copyRegrade(this)">\u{1F4CB} Copy sweep</button></div>' + h;
}

async function runRegrade() {
  const btn = document.getElementById('run-regrade');
  if (!btn) return;
  const orig = btn.textContent;
  btn.textContent = 'sweeping…';
  btn.disabled = true;
  const host = document.getElementById('regrade-body');
  if (host) host.innerHTML = '<div class="loading">capturing + sweeping…</div>';
  try {
    // raw fetch (not api()) so we can read the detail body on a 501/400 -
    // the SCC-not-installed hint, the no-CorpusRegrades message, etc.
    const headers = { 'Content-Type': 'application/json' };
    const tok = (typeof getToken === 'function') ? getToken() : '';
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    const resp = await fetch('/eval/regrade', { method: 'POST', headers, body: '{}' });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      currentRegrade = data.results;
      if (host) host.innerHTML = renderRegrade(data.results);
    } else {
      const detail = (data && data.detail != null) ? data.detail : data;
      const msg = (typeof detail === 'string') ? detail
                : (detail && detail.errors ? detail.errors.join('; ')
                   : `regrade failed (HTTP ${resp.status})`);
      if (host) host.innerHTML = `<div class="empty">${escapeHtml(msg)}</div>`;
    }
  } catch (e) {
    if (host) host.innerHTML = `<div class="empty">${escapeHtml(e.message || e)}</div>`;
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
      html += `<div class="topo-source">source: ${escapeHtml(srcLabel)} - edit it in the <a href="#" class="tab-link" data-tab="config">Config</a> tab</div>`;
      html += '<div class="topo-nodes">';
      for (const n of s.loci)   html += `<span class="node-chip loci">${escapeHtml(n)}</span>`;
      for (const n of s.memory) html += `<span class="node-chip memory">${escapeHtml(n)}</span>`;
      for (const n of s.corpus) html += `<span class="node-chip corpus">${escapeHtml(n)}</span>`;
      html += '</div>';
    } else {
      html += '<div class="empty">No ProbeConfig loaded - set one in the Config tab.</div>';
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
    if (data.mode === 'topology') {
      // config-driven fleet: a compose project of per-node containers
      const rc = data.running_count, tot = data.service_total;
      const cnt = tot ? `${rc}/${tot} containers up` : 'containers up';
      if (data.running) {
        info.textContent = `topology "${escapeHtml(data.project_name)}" · ${cnt}`;
        let html = '<table class="kv-table"><tr><th>Store</th><th>Host URL</th></tr>';
        for (const [name, url] of Object.entries(data.stores || {})) {
          html += `<tr><td class="k">${escapeHtml(name)}</td><td class="v">${escapeHtml(url)}</td></tr>`;
        }
        html += '</table>';
        if (data.services && data.services.length) {
          html += '<div class="note"><span class="label">containers:</span> '
                + data.services.map(s => `${escapeHtml(s.name)} <span class="muted">(${escapeHtml(s.state || '?')})</span>`).join(' · ')
                + '</div>';
        }
        body.innerHTML = html;
      } else {
        info.textContent = `topology "${escapeHtml(data.project_name)}" · stopped`;
        body.innerHTML = '<div class="empty">Topology isn\'t running. Click ▶ Start to launch it.</div>';
      }
      return;
    }
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

    // ── ProbeConfig (topology) - what ▶ Start spins up ──
    const pcYaml = pc.yaml || '';
    const pcSource = pc.active ? 'uploaded (active)' : 'shipped example';
    let html = '<div class="docker-config-section">';
    html += '<details open>';
    html += `<summary><b>ProbeConfig</b> - topology <span class="muted">source: ${escapeHtml(pcSource)}</span></summary>`;
    html += '<div class="docker-config-body">';
    html += `<div class="note"><span class="label">tip:</span> declare X Loci / Y Memory / Z Corpus, then Set Active to make it the topology <b>▶ Start</b> builds.</div>`;
    html += '<div class="upload-form">';
    html += `<textarea id="probeconfig-yaml" rows="14" spellcheck="false">${escapeHtml(pcYaml)}</textarea>`;
    html += '<input type="file" id="probeconfig-file" accept=".yml,.yaml,.txt" style="display:none" onchange="loadProbeConfigFile(this)"/>';
    html += '<div class="ctrls-row">';
    html += '<button class="btn" onclick="uploadProbeConfig()">✓ Validate &amp; Set Active</button>';
    html += `<button class="btn-sm" onclick="document.getElementById('probeconfig-file').click()">📁 Load file…</button>`;
    html += '<button class="btn-sm" onclick="refreshConfig()">\u21bb Reload active</button>';
    // The lint can run to thousands of lines on a multi-tenant topology. Reading that
    // through a scrollbox and then SCREENSHOTTING it is not a workflow.
    html += '<button class="btn-sm copy-btn" onclick="copyLint(this)">\u{1F4CB} Copy validation</button>';
    html += '</div>';
    html += '<div class="config-save-result" id="probeconfig-result"></div>';
    html += '</div></div></details></div>';

    // ── Store endpoints - manual eval targeting (legacy) ──
    const fields = [
      ['memory_url', 'SerenMemory'],
      ['loci_nv_url', 'Loci · no-vec'],
      ['loci_v_url', 'Loci · vec'],
      ['scc_nv_url', 'SCC · no-vec'],
      ['scc_v_url', 'SCC · vec'],
      ['capture_path', 'Capture path'],
    ];
    html += '<div class="docker-config-section">';
    html += '<details>';
    html += '<summary><b>Store endpoints</b> - manual targets <span class="muted">(advanced)</span></summary>';
    html += '<div class="docker-config-body">';
    html += '<div class="note"><span class="label">note:</span> point the eval at already-running stores. Starting a topology above repoints the eval at those instead.</div>';
    html += '<div class="upload-form">';
    for (const [key, label] of fields) {
      const val = cfg[key] == null ? '' : String(cfg[key]);
      html += `<label>${escapeHtml(label)}:
        <input id="cfg-${key}" type="text" value="${escapeHtml(val)}"/></label>`;
    }
    html += '<button class="btn" onclick="saveConfig()">💾 Save store config</button>';
    html += '<div class="config-save-result" id="config-save-result"></div>';
    html += '</div></div></details></div>';

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
      let msg = `✓ active - ${counts}`;
      if (data.hot_swap) msg += `\n⚡ ${data.hot_swap}`;
      if (data.warnings && data.warnings.length) msg += '\n⚠ ' + data.warnings.join('\n⚠ ');
      // Reachability lint: can these questions actually be ANSWERED by the corpus
      // they'll be graded against? An expectation the seed never contains is
      // unanswerable at ANY fusion setting - and on the dashboard it looks exactly
      // like a retrieval failure. Surface it here, before a container ever starts.
      const L = data.lint;
      if (L && L.skipped) {
        msg += `\n· lint skipped: ${L.skipped}`;
      } else if (L) {
        const nq = L.checked || 0;
        if (L.errors && L.errors.length) {
          msg += `\n\n✗ QUESTION LINT - ${L.errors.length} unanswerable of ${nq}:`;
          msg += '\n✗ ' + L.errors.join('\n✗ ');
        } else {
          msg += `\n✓ question lint: all ${nq} questions are answerable by the corpus`;
        }
        if (L.warnings && L.warnings.length) msg += '\n⚠ ' + L.warnings.join('\n⚠ ');
        if (L.multihop && L.multihop.length) {
          msg += `\n\nℹ ${L.multihop.length} RAILED 2-hop(s) - a bridge doc exists, so hops:2 SHOULD reach these:`;
          for (const m of L.multihop)
            msg += `\nℹ  "${m.expects}" on ${m.holder} - ${m.query}`;
        }
        if (L.unbridged && L.unbridged.length) {
          msg += `\n\n⚠ ${L.unbridged.length} UNBRIDGED - no rail exists; UNANSWERABLE at any hop depth (dataset defect):`;
          for (const u of L.unbridged)
            msg += `\n⚠  "${u.expects}" on ${u.holder} - ${u.query}`;
          msg += '\n⚠  Nothing (no knob, no hop) will move these. Regenerate the question or add the linking fact.';
        }
        // AMBIGUOUS -- Tier 4, the one that cost us a whole day. The expectation
        // EXISTS and IS reachable; the query just can't SINGLE IT OUT. The store
        // retrieves perfectly and scores near zero, which on a dashboard is
        // indistinguishable from a dead store, a broken embedder, and a missing hop.
        if (L.ambiguous && L.ambiguous.length) {
          msg += `\n\n✗ ${L.ambiguous.length} AMBIGUOUS -- answer EXISTS and is REACHABLE, but the query can't single it out:`;
          for (const a of L.ambiguous)
            msg += `\n✗  "${a.expects}" (${a.kind}) -- ${a.rivals} other docs match this query just as well -- ${a.query}`;
          msg += '\n✗  The store will retrieve CORRECTLY and still score near zero. On the dashboard that is';
          msg += '\n✗  indistinguishable from a dead store, a broken embedder, and a missing hop.';
          msg += '\n✗  Do NOT tune anything. The query names a category; the answer key names one member of it.';
          msg += '\n✗  Add a term only the intended document carries.';
        }
        // UNREACHABLE -- Tier 5. The question DECLARES the depth it needs; no config
        // you can run goes that deep. Scores zero in every row of every sweep, and
        // flat rows read as a retrieval ceiling. This is the one that started it all.
        if (L.unreachable && L.unreachable.length) {
          msg += `\n\n✗ ${L.unreachable.length} UNREACHABLE - the question declares a traversal depth nothing you can run reaches:`;
          for (const u of L.unreachable)
            msg += `\n✗  needs hops=${u.needs}, deepest reachable is hops=${u.max} - ${u.query}`;
          msg += '\n✗  Every combo in the sweep asks these at a depth they cannot be answered from.';
          msg += '\n✗  They score ZERO in every row, and flat rows read as a retrieval CEILING.';
          msg += '\n✗  The knob is not inert. It was never turned far enough.';
          msg += '\n✗  Sweep hops deeper in a CorpusRegrades set, and confirm the SCC advertises it in GET /stores.';
        }
        if ((L.multihop && L.multihop.length) || (L.unbridged && L.unbridged.length)) {
          msg += '\nℹ  Reshaping knobs (rrf_k/floor/weight) stay INERT on all of these - correct, not a bug.';
        }
        if (L.notes && L.notes.length) {
          // the live-import / decoy skip notes (the per-expectation ones are covered above)
          for (const n of L.notes)
            if (n.indexOf('SKIPPED') >= 0 || n.indexOf('decoy') === 0) msg += `\n· ${n}`;
        }
      }
      setResult(result, (L && L.errors && L.errors.length) ? 'err' : 'ok', msg);
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

function loadProbeConfigFile(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    const ta = document.getElementById('probeconfig-yaml');
    if (ta) ta.value = e.target.result;
    setResult(document.getElementById('probeconfig-result'), '',
              `loaded ${file.name} - review, then Validate & Set Active`);
  };
  reader.onerror = function() {
    setResult(document.getElementById('probeconfig-result'), 'err', 'could not read ' + file.name);
  };
  reader.readAsText(file);
  input.value = '';  // reset so re-selecting the same file re-fires change
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
      if (result) { result.className = 'config-save-result ok'; result.textContent = '✓ saved - eval stores repointed'; }
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
  const runRegradeBtn = document.getElementById('run-regrade');
  if (runRegradeBtn) runRegradeBtn.addEventListener('click', runRegrade);

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

  // ADOPT: the containers outlive the app process. Restarting SerenProbe used to
  // orphan a perfectly healthy fleet - app.state forgot it, so the operator rebuilt
  // and reseeded (an hour, on a big corpus) for nothing. If a pod is actually up
  // (verified by `compose ps`, not just a state file), offer to attach to it.
  offerAdoption();
});

async function offerAdoption() {
  // Render into #adopt-host: a node OUTSIDE every .view, and the ONLY thing that
  // writes to it is this function. Previously the bar was prepended into
  // #docker-body -- which sits inside the HIDDEN Docker view on load, and which
  // refreshDocker() wipes with `innerHTML` the instant you open that tab. The bar
  // was created invisible and then deleted by the click that would have shown it.
  // If you ever move this, move it somewhere no render function owns.
  const host = document.getElementById('adopt-host');
  if (!host) { console.warn('[adopt] #adopt-host missing from body.html'); return; }

  let a;
  try {
    const r = await authFetch('/docker/adoptable');
    if (!r.ok) {
      // LOUDLY. A silent bail here is what hid this bug: a 401/500 body simply
      // has no `adoptable` key, so the old guard read the failure as "nothing to
      // adopt" and returned without a trace. Never fail quiet on a check whose
      // whole job is to tell you something exists.
      console.warn('[adopt] GET /docker/adoptable -> HTTP ' + r.status
                   + (r.status === 401 ? ' (bearer token missing or wrong)' : ''));
      return;
    }
    a = await r.json();
  } catch (err) {
    console.warn('[adopt] could not reach /docker/adoptable:', err);
    return;
  }
  if (!a || !a.adoptable) return;

  const bar = document.createElement('div');
  bar.className = 'note adopt-note';
  const seeded = a.seeded ? 'stores already SEEDED' : 'stores not yet seeded';
  bar.innerHTML =
    `<span class="label">⚡ running topology found:</span> `
    + `<b>${escapeHtml(a.project_name)}</b> - ${a.running_count}/${a.service_total} containers up, ${seeded}. `
    + `<span class="muted">Adopt it instead of rebuilding + reseeding.</span> `
    + `<button class="btn" id="adopt-btn">Use running containers</button>`;
  host.replaceChildren(bar);

  document.getElementById('adopt-btn').addEventListener('click', async (e) => {
    e.target.disabled = true;
    e.target.textContent = 'Adopting…';
    try {
      const r = await authFetch('/docker/adopt', { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        bar.className = 'note adopt-note ok';
        bar.textContent = `✓ adopted ${d.adopted} - ${d.note}`;
        // The Docker view may already have rendered "nothing running"; force both
        // panels to re-read now that app.state actually has the fleet attached.
        if (typeof refreshDocker === 'function') refreshDocker();
        if (typeof refreshEval === 'function') refreshEval();
      } else {
        bar.className = 'note adopt-note err';
        bar.textContent = `✗ adopt failed: ${d.detail || d.error || ('HTTP ' + r.status)}`;
      }
    } catch (err) {
      bar.className = 'note adopt-note err';
      bar.textContent = `✗ adopt failed: ${err}`;
    }
  });
}
