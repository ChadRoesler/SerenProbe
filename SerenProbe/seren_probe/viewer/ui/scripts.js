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
let currentPc = null;       // the active ProbeConfig as /docker/probeconfig last described it
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
  btn.textContent = ok ? 'Copied' : 'Copy failed';
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
    out.push('', '### SCC docket - vector vs lexical Loci');
    out.push(mdTable(['Loci', 'SCC', 'Coverage', 'Density', 'Recall'],
      d.columns.map(c => [
        _loci(c),
        c.name, _f3(c.docket_coverage), _f3(c.docket_density), _f3(c.recall)])));
    for (const dl of (d.deltas || [])) {
      out.push('', `**\u0394 vector - lexical** on ${dl.vector || dl.with_edges} vs ${dl.lexical || dl.without_edges}: `
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
function copyRegrade(btn) {
  // EVERY corpus swept this session, not just the last one. currentRegrade holds a
  // single response, and with per-corpus buttons that is whichever corpus you
  // happened to run most recently -- so the paste would silently drop the other two.
  const merged = Object.keys(_rgResults).length
    ? { ...(currentRegrade || {}), corpora: Object.values(_rgResults) }
    : currentRegrade;
  copyToClipboard(regradeMarkdown(merged), btn);
}
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
  else if (id === 'runs') refreshRuns();
}

// ── Eval ────────────────────────────────────────────────────────────
async function refreshEval() {
  const body = document.getElementById('eval-body');
  if (!body) return;
  body.innerHTML = '<div class="loading">loading…</div>';
  try {
    const data = await api('/eval/results');
    currentEval = data;
    // No results yet (fresh start/adopt, nothing seeded/scored) -- fall back to
    // placeholder rows sourced from the running topology, rather than an empty
    // table that looks indistinguishable from "the app is broken."
    if (!data.stores || !Object.keys(data.stores).length) {
      try {
        const ds = await api('/docker/status');
        if (ds && (ds.running || ds.managed) && (ds.loci || ds.memory || ds.corpus)) {
          renderEvalPlaceholder(ds.loci, ds.memory, ds.corpus, ds.seeded);
          refreshRegradePlan();   // stage the sweep even before anything is scored
          return;
        }
      } catch (e) { /* fall through to normal empty rendering below */ }
    }
    const info = document.getElementById('eval-info');
    const qc = data.question_count || data.query_count || 0;
    info.textContent = `${qc} questions · k=${data.k || 10}`;
    // SAY THE STATE, don't imply it. An empty table used to be the only signal, and
    // it reads as "never evaluated" whether that is true or the app merely lost the
    // paperwork on restart. Those are different facts and only one of them means re-run.
    if (data.date) {
      info.textContent += ' \u00b7 evaluated ' + String(data.date).replace('T', ' ').slice(0, 16);
    }
    if (data.restored) info.textContent += ' \u00b7 \u21ba restored';
    if (data.run_id) info.textContent += ' \u00b7 run ' + data.run_id;    // the ledger row this table came from
    // A DISCARD is not an empty state. "Nothing has been evaluated" and "an evaluation
    // existed and was thrown away because it no longer describes this pod" are
    // different facts, and rendering the second as the first is the same disease as
    // scoring a failed search as a miss: the operator re-runs an hour of work without
    // ever learning why the old numbers vanished.
    if (data.discarded) {
      const host0 = document.getElementById('eval-body');
      if (host0) {
        host0.innerHTML = `<div class="note err"><span class="label">\u2717 previous results discarded:</span> `
          + `${escapeHtml(data.discarded)}. <span class="muted">They described a different pod, so they `
          + `were dropped rather than shown with a caveat. Run Evaluate to score this one.</span></div>`;
      }
      info.textContent = 'no results for this topology';
      return;
    }
    // Clear rides the SAME row as Copy results (left of it), and only when there's a
    // restored run to clear. copy-row is right-aligned, so Copy results stays at the edge.
    const clearBtn = data.restored
        ? '<button class="btn-sm copy-btn" onclick="clearEvalResults()">Clear results</button>'
      : '';
    let html = '<div class="copy-row">' + clearBtn
             + '<button class="btn-sm copy-btn" onclick="copyEval(this)">Copy results</button></div>';
    if (data.restored) {
      // Scored-in-this-process and read-back-off-disk are different confidence
      // levels, so the table says which it is instead of presenting a rehydrated
      // run as live. The actionable half matters more: it tells you NOT to re-run.
      html += '<div class="note"><span class="label">\u21ba restored:</span> '
            + 'these numbers were scored in an earlier session and read back from disk'
            + (data.restored_at ? ' (' + escapeHtml(String(data.restored_at).replace('T', ' ').slice(0, 16)) + ')' : '')
            + '. <span class="muted">This pod has already been evaluated - go straight to '
            + 'Regrades. Re-run Evaluate only if the seed or the questions changed.</span></div>';
    }
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
    html += '<table class="store-table eval-table"><tr><th>Store</th><th>Status</th><th>HR</th><th>MRR</th><th>P@k</th><th>R@k</th><th>NDCG</th><th>IoU</th><th>P-Ω</th><th></th></tr>';
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
      html += `<tr class="${rowcls}"><td class="sname"><span class="sname-link" data-store="${escapeHtml(name)}" title="click for per-question detail: rank, docket, misses">${escapeHtml(name)}</span>${badge}</td>`
            + `<td class="mval prog-cell" id="prog-${escapeHtml(name)}">done</td>`
            + `<td class="mval">${f('hit_rate')}</td><td class="mval">${f('mrr')}</td><td class="mval">${f('precision')}</td><td class="mval">${f('recall')}</td>`
            + `<td class="mval">${ndcgCell}</td><td class="mval">${f('iou')}</td><td class="mval">${f('prec_omega')}</td><td>${tail}</td></tr>`;
    }
    html += '</table>';
    html += renderDocket(data.docket);
    body.innerHTML = html;
    // Fills #regrade-body with the PLAN, and no-ops once a sweep has actually run.
    // Both exits of refreshEval reach it, so the staging table is present whether the
    // pod is freshly adopted or fully scored.
    refreshRegradePlan();
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

// ── SCC docket comparison (vector vs lexical Loci) ─────────────────
// Renders data.docket (attached by run_topology_evaluation) as a side-by-side
// coverage/density/recall table + the vector - lexical delta: the same corpus
// fanned through a Loci with embeddings and through one without. (Until 22 Oct
// 2026 this said "with vs without edges", which predates the Callosum's real
// edges - satellites, superseded cores, topic neighbours - and read as them.)
// Returns '' when there are no SCC columns, so it no-ops cleanly on
// loci/memory-only reports.
// The flavor named by the viewer, not the stored label: runs recorded before
// 22 Oct 2026 carry 'with edges' / 'without edges' inside them.
function _loci(c) {
  return ({vector: 'vector Loci', lexical: 'lexical Loci', mixed: 'mixed Loci', none: 'no loci'})[c.flavor]
    || c.label || c.flavor;
}

function renderDocket(d) {
  if (!d || !d.columns || !d.columns.length) return '';
  const f3 = (v) => (v == null ? '-' : Number(v).toFixed(4));
  const sg = (v) => (v == null ? '-' : (v >= 0 ? '+' : '') + Number(v).toFixed(4));
  let h = '<div class="note"><span class="label">SCC Docket - vector vs lexical Loci</span><br>';
  const meta = [];
  if (d.question_count != null) meta.push(d.question_count + ' questions');
  if (d.k != null) meta.push('k=' + d.k);
  if (d.corpus_size != null) meta.push('corpus_size=' + d.corpus_size);
  meta.push('flavor: ' + (d.flavor_source || '?'));
  h += `<span class="label">each SCC fans the same Memory with a Loci that has embeddings (vector) or one that does not (lexical).</span> ${escapeHtml(meta.join(' · '))}</div>`;
  h += '<table class="store-table"><tr><th>Loci</th><th>SCC</th><th>Coverage</th><th>Density</th><th>Recall</th></tr>';
  for (const c of d.columns) {
    const tag = _loci(c);
    h += `<tr><td>${escapeHtml(tag)}</td><td class="sname">${escapeHtml(c.name)}</td>`
       + `<td class="mval">${f3(c.docket_coverage)}</td>`
       + `<td class="mval">${f3(c.docket_density)}</td>`
       + `<td class="mval">${f3(c.recall)}</td></tr>`;
  }
  h += '</table>';
  for (const dl of (d.deltas || [])) {
    const cov = dl.docket_coverage;
    const cls = cov == null ? '' : (cov > 0.0005 ? 'delta-pos' : (cov < -0.0005 ? 'delta-neg' : ''));
    h += `<div class="docket-delta ${cls}"><span class="dl-label">Δ vector − lexical</span> `
       + `coverage ${sg(dl.docket_coverage)} · density ${sg(dl.docket_density)} · recall ${sg(dl.recall)}</div>`;
    if (cov != null) {
      let v;
      if (cov > 0.0005) v = `the vector Loci ADDED ${sg(cov)} docket coverage`;
      else if (cov < -0.0005) v = `the vector Loci COST ${sg(cov)} docket coverage`;
      else v = 'vector and lexical made no meaningful coverage difference';
      h += `<div class="docket-verdict ${cls}">→ ${escapeHtml(v)} (${escapeHtml(dl.vector || dl.with_edges)} vs ${escapeHtml(dl.lexical || dl.without_edges)})</div>`;
    }
  }
  if (d.note) h += `<div class="docket-meta">note: ${escapeHtml(d.note)}</div>`;
  return h;
}

// ── Store drill-down modal ──────────────────────────────────────────
// Click a store name -> GET /eval/detail/<store> -> per-question accordion.
// The whole point is to turn "this store scores 0.6" into "THESE questions
// missed, and HERE is what outranked the right answer" -- so the list opens
// sorted worst-first and every docket row flags the ground-truth hit. Detail
// is fetched here, not carried in /eval/results, because a 22-store report's
// worth of dockets is megabytes and that table is polled.
let _qd = { store: null, kind: '', agg: {}, k: 10, qcount: 0, error: null,
            questions: [], filter: 'all', search: '' };

// Severity for worst-first ordering. Lower = shown higher. A real miss is the
// thing you came to see; a rank-1 hit is the thing you can skip; "no ground
// truth" is informational, not failure, so it sinks BELOW the passes.
function _qdSeverity(q) {
  if (q.unscored) return 0;                 // never asked (search failed) - loudest
  if (!q.has_gt)  return 5;                  // unscorable - not a miss, park it low
  if (!q.hit)     return 1;                  // real miss
  if (!q.within_k) return 2;                 // found, but past the k cutoff
  if (q.rank > 1) return 3;                   // found in-k but not rank 1
  return 4;                                   // rank 1
}

function _qdFlag(q) {
  if (q.unscored) return { cls: 'nogt',  label: '⚠ not asked' };
  if (!q.has_gt)  return { cls: 'nogt',  label: '– no truth' };
  if (!q.hit)     return { cls: 'miss',  label: '✗ miss' };
  if (q.rank === 1) return { cls: 'hit', label: '✓ #1' };
  if (q.within_k) return { cls: 'near',  label: '#' + q.rank };
  return { cls: 'miss', label: '✗ #' + q.rank + ' (>k)' };
}

function _qdMatchesFilter(q) {
  const f = _qd.filter;
  if (f === 'miss') return q.unscored || (q.has_gt && (!q.hit || !q.within_k));
  if (f === 'hit')  return !q.unscored && q.has_gt && q.hit && q.within_k;
  if (f === 'nogt') return !q.unscored && !q.has_gt;
  return true;                                // 'all'
}

function renderQdList() {
  const list = document.getElementById('qd-list');
  if (!list) return;
  const term = (_qd.search || '').trim().toLowerCase();
  const rows = _qd.questions
    .map((q, i) => ({ q, i }))
    .filter(({ q }) => _qdMatchesFilter(q))
    .filter(({ q }) => !term || (q.query || '').toLowerCase().includes(term)
                    || (q.hits || []).some(h => (h.text || '').toLowerCase().includes(term)));
  // worst-first, then bigger rank (deeper miss) first within a tier
  rows.sort((a, b) => _qdSeverity(a.q) - _qdSeverity(b.q)
                   || ((b.q.rank || 999) - (a.q.rank || 999)));
  if (!rows.length) {
    list.innerHTML = '<div class="qd-empty-docket">no questions match this filter.</div>';
    return;
  }
  let h = '';
  for (const { q, i } of rows) {
    const flag = _qdFlag(q);
    let body;
    if (q.unscored) {
      body = `<div class="qd-body-note">${escapeHtml(q.reason || 'this question was not scored')}.</div>`;
    } else {
      const note = [];
      if (q.has_gt) note.push(`${q.n_relevant} ground-truth doc${q.n_relevant === 1 ? '' : 's'}`);
      else note.push('no resolvable ground truth for this question - a "miss" here is unscorable, not a failure');
      if (q.coverage != null) note.push(`docket coverage ${q.coverage.toFixed(3)}`);
      note.push(`${q.n_hits} hit${q.n_hits === 1 ? '' : 's'} returned`);
      let docket = '';
      if (!q.hits || !q.hits.length) {
        docket = '<div class="qd-empty-docket">store returned nothing.</div>';
      } else {
        q.hits.forEach((hit, hi) => {
          const exp = hit.expected ? ' is-expected' : '';
          const origin = hit.origin ? `<span class="qd-origin">${escapeHtml(hit.origin)}</span>` : '';
          const mark = hit.expected ? '✓ ' : '';
          docket += `<div class="qd-hit${exp}">`
                  + `<span class="qd-rank">${mark}#${hi + 1}</span>`
                  + `<span class="qd-score">${Number(hit.score).toFixed(4)}</span>`
                  + `<span class="qd-htext">${escapeHtml(hit.text || '')}${origin}</span>`
                  + `</div>`;
        });
      }
      body = `<div class="qd-body-note">${escapeHtml(note.join(' · '))}</div>${docket}`;
    }
    h += `<div class="qd-q" data-qi="${i}">`
       + `<div class="qd-qhead">`
       + `<span class="qd-caret seren-chev"></span>`
       + `<span class="qd-flag ${flag.cls}">${flag.label}</span>`
       + `<span class="qd-qtext">${escapeHtml(q.query || '(no query text)')}</span>`
       + `</div>`
       + `<div class="qd-body">${body}</div>`
       + `</div>`;
  }
  list.innerHTML = h;
}

function _qdStats() {
  const el = document.getElementById('qd-stats');
  if (!el) return;
  const a = _qd.agg || {};
  const scored = _qd.questions.filter(q => !q.unscored);
  const miss = scored.filter(q => q.has_gt && (!q.hit || !q.within_k)).length;
  const nogt = scored.filter(q => !q.has_gt).length;
  const parts = [];
  if (a.hit_rate != null) parts.push(`HR <b>${a.hit_rate.toFixed(3)}</b>`);
  if (a.mrr != null) parts.push(`MRR <b>${a.mrr.toFixed(3)}</b>`);
  if (a.docket_coverage != null) parts.push(`cov <b>${a.docket_coverage.toFixed(3)}</b>`);
  parts.push(`<b>${miss}</b> miss / ${scored.length}`);
  if (nogt) parts.push(`${nogt} no-truth`);
  if (_qd.questions.length !== scored.length) parts.push(`${_qd.questions.length - scored.length} not asked`);
  el.innerHTML = parts.join(' · ');
}

function _qdRenderFilters() {
  const el = document.getElementById('qd-filters');
  if (!el) return;
  const opts = [['all', 'All'], ['miss', 'Misses'], ['hit', 'Hits'], ['nogt', 'No-truth']];
  let h = '';
  for (const [key, label] of opts) {
    h += `<button class="qd-fbtn ${_qd.filter === key ? 'active' : ''}" data-qdfilter="${key}">${label}</button>`;
  }
  h += `<input class="qd-search" id="qd-search" type="text" placeholder="filter text…" value="${escapeHtml(_qd.search)}">`;
  el.innerHTML = h;
}

async function openStoreDetail(store) {
  const modal = document.getElementById('qd-modal');
  if (!modal) return;
  _qd = { store, kind: '', agg: {}, k: 10, qcount: 0, error: null,
          questions: [], filter: 'all', search: '' };
  document.getElementById('qd-title').textContent = store;
  document.getElementById('qd-sub').textContent = 'loading…';
  document.getElementById('qd-filters').innerHTML = '';
  document.getElementById('qd-list').innerHTML = '<div class="loading">loading…</div>';
  document.getElementById('qd-stats').innerHTML = '';
  modal.hidden = false;
  try {
    // authFetch (not api): api() throws on non-2xx and eats the body, and a 409
    // "scored before detail existed" / 404 "stale store name" IS the message we
    // want to show, not a generic failure.
    const resp = await authFetch(`/eval/detail/${encodeURIComponent(store)}`);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      let msg = (data && (data.detail || data.error)) || `HTTP ${resp.status}`;
      if (typeof msg !== 'string') msg = JSON.stringify(msg);
      // "Not Found" is Starlette's DEFAULT 404 for an unmatched route -- i.e. the
      // /eval/detail endpoint isn't mounted, which means the probe is still running
      // the build from before this feature. Our own 404 carries a real message, so a
      // bare "Not Found" can only mean stale process. Say THAT, not the framework's word.
      if (resp.status === 404 && /^not found$/i.test(msg.trim())) {
        msg = 'This build of the probe doesn’t have the /eval/detail endpoint yet — '
            + 'restart the probe process (new routes don’t hot-reload), then re-run the eval.';
      }
      document.getElementById('qd-sub').textContent = '';
      document.getElementById('qd-list').innerHTML =
        `<div class="qd-empty-docket">${escapeHtml(msg)}</div>`;
      return;
    }
    _qd.kind = data.kind || '';
    _qd.agg = data.aggregate || {};
    _qd.k = data.k != null ? data.k : 10;
    _qd.qcount = data.question_count != null ? data.question_count : (data.questions || []).length;
    _qd.error = data.error || null;
    _qd.questions = data.questions || [];
    const sub = [_qd.kind, `${_qd.qcount} scored`, `k=${_qd.k}`].filter(Boolean).join(' · ');
    document.getElementById('qd-sub').textContent = _qd.error ? `${sub} · errored: ${_qd.error}` : sub;
    _qdRenderFilters();
    renderQdList();
    _qdStats();
  } catch (e) {
    document.getElementById('qd-list').innerHTML =
      `<div class="qd-empty-docket">Error: ${escapeHtml(e.message || String(e))}</div>`;
  }
}

function closeQd() {
  const modal = document.getElementById('qd-modal');
  if (modal) modal.hidden = true;
}

function qdCopyMarkdown(btn) {
  if (!_qd.store) { copyToClipboard('', btn); return; }
  const a = _qd.agg || {};
  const head = [`## ${_qd.store} — per-question detail`];
  const meta = [];
  if (a.hit_rate != null) meta.push(`HR ${a.hit_rate.toFixed(3)}`);
  if (a.mrr != null) meta.push(`MRR ${a.mrr.toFixed(3)}`);
  if (a.docket_coverage != null) meta.push(`cov ${a.docket_coverage.toFixed(3)}`);
  meta.push(`${_qd.qcount} scored`, `k=${_qd.k}`);
  head.push(meta.join(' · '), '');
  const out = [head.join('\n')];
  // Same worst-first order the modal shows, honouring the active filter so "copy"
  // copies what you're looking at, not a different set than the screen.
  const term = (_qd.search || '').trim().toLowerCase();
  const rows = _qd.questions
    .filter(q => _qdMatchesFilter(q))
    .filter(q => !term || (q.query || '').toLowerCase().includes(term)
              || (q.hits || []).some(h => (h.text || '').toLowerCase().includes(term)));
  rows.sort((x, y) => _qdSeverity(x) - _qdSeverity(y) || ((y.rank || 999) - (x.rank || 999)));
  for (const q of rows) {
    const flag = _qdFlag(q);
    out.push(`### ${flag.label} — ${q.query || '(no query)'}`);
    if (q.unscored) { out.push(`_${q.reason || 'not scored'}_`, ''); continue; }
    const bits = [];
    bits.push(q.has_gt ? `expected: ${q.hit ? 'rank ' + q.rank : 'MISS'} (${q.n_relevant} truth)`
                       : 'no resolvable ground truth');
    if (q.coverage != null) bits.push(`coverage ${q.coverage.toFixed(3)}`);
    out.push(bits.join(' · '));
    if (q.hits && q.hits.length) {
      out.push(mdTable(['#', 'score', 'store', '✓', 'text'],
        q.hits.map((h, i) => [i + 1, Number(h.score).toFixed(4), h.origin || '',
                              h.expected ? '✓' : '', h.text || ''])));
    } else {
      out.push('_store returned nothing_');
    }
    out.push('');
  }
  copyToClipboard(out.join('\n'), btn);
}

// ── Live progress polling ──────────────────────────────────────────
// The /eval/seed and /eval/run requests BLOCK until the whole operation is
// done (run_in_threadpool holds the request open) -- so there is no response
// to read progress FROM. Instead we poll a separate, cheap GET on an interval
// while that request is in flight, and paint whatever X/Y it reports onto the
// matching row's Status cell by store name. Stops itself (clearInterval) the
// moment the caller's await resolves, success or failure.
let _progressTimer = null;

async function clearEvalResults() {
  // The automatic checks catch what they can SEE (topology, reseed, question set).
  // They cannot see a rebuilt image at the same version pin, or edited seed CONTENT
  // behind an unchanged path -- so the operator gets a direct way to say "these are
  // junk" without tearing down a pod to clear a JSON file.
  try {
    await api('/eval/results', { method: 'DELETE' });
  } catch (e) { /* already gone is a fine outcome for a delete */ }
  await refreshEval();
}

function _renderProgressCell(row) {
  if (!row) return '-';
  // Pass the phase through as-is. This used to whitelist eval/regrade and map
  // everything else to 'seed', which silently mislabelled every phase added later
  // -- 'capture' rendered as 'seed', which on this dashboard is an alarming thing
  // to claim about a read-only operation.
  const phase = row.phase || 'seed';
  if (row.done) return `${phase} ${row.current}/${row.total}`;
  return `${phase} ${row.current}/${row.total}\u2026`;
}

// Paint ONE store's finished metrics into its existing row, live, as soon as that
// column is scored -- instead of every result waiting on the slowest column.
//
// Reached via the row's `prog-<name>` cell rather than by giving every metric cell
// its own id: the two renderers (refreshEval + renderEvalPlaceholder) already emit
// that anchor, so this needs no change to either and cannot drift out of sync with
// them. Cell order is Store | Status | HR | MRR | P@k | R@k | NDCG | IoU | P-\u03a9 | tail.
function _paintPartial(name, m) {
  const cell = document.getElementById('prog-' + name);
  if (!cell) return;
  // REGRADE/CAPTURE partials are corpus-shaped ({corpus, sets|error}) and belong to
  // the regrade panel, NOT this table. Without this guard, polling during a regrade
  // fed those shapes to the eval painter, every metric read null, and the corpus
  // rows' REAL eval numbers got overwritten with dashes mid-sweep.
  if (m && m.corpus) return;
  const tr = cell.parentElement;
  if (!tr || !tr.cells || tr.cells.length < 9) return;

  if (m && m.error) {
    // Publish failures the moment they happen. A container that died in wave 1
    // should be visible NOW, not after the corpora finish -- that is most of why
    // partials exist at all.
    if (tr.cells[9]) tr.cells[9].textContent = '\u2717 ' + m.error;
    tr.classList.add('row-error');
    return;
  }
  const a = (m && m.aggregate) || m || {};
  const f = (x) => (a[x] == null ? '\u2013' : Number(a[x]).toFixed(3));
  // Same vacuous-NDCG suppression the final table uses: a decoy's relevant set is
  // empty by design, and a metric that flatters a hole is worse than no metric.
  const ndcg = (m && m.negative_test) ? '\u2014' : f('ndcg');
  const vals = [f('hit_rate'), f('mrr'), f('precision'), f('recall'),
                ndcg, f('iou'), f('prec_omega')];
  for (let i = 0; i < vals.length; i++) {
    if (tr.cells[2 + i]) tr.cells[2 + i].textContent = vals[i];
  }
  tr.classList.add('row-partial');
  tr.title = 'live partial - final numbers land when the whole run finishes';
}

// Latest /eval/progress rows, kept module-level so the REGRADE panel can render a
// Status column from the same registry the eval table's Status column polls. The
// partials carry results; progress carries position. Neither alone answers "is this
// moving", which is the entire question during a multi-hour sweep.
let _lastProgress = {};

// ONE poll tick: paint the latest /eval/progress rows and /eval/partials. Extracted from
// the interval so finalizeProgress() can run exactly one MORE tick after the op resolves.
async function _progressTick() {
  // Two cheap GETs per tick, INDEPENDENTLY guarded. /eval/partials is newer than
  // /eval/progress, so a viewer talking to an older backend must still get its
  // status column -- one failing endpoint may not take the other down with it.
  try {
    const snap = await api('/eval/progress');
    _lastProgress = snap || {};
    for (const [name, row] of Object.entries(snap || {})) {
      const cell = document.getElementById(`prog-${name}`);
      if (cell) cell.textContent = _renderProgressCell(row);
      // Regrade sections carry their own Status cells (per set), repainted here
      // rather than only when a partial lands -- a combo takes minutes, so waiting
      // for the next partial to show movement defeats the point.
      _paintRegradeStatus(name, row);
    }
  } catch (e) { /* transient - next tick will retry */ }
  try {
    const p = await api('/eval/partials');
    for (const [name, snap] of Object.entries((p && p.partials) || {})) {
      // Corpus-shaped snapshots belong to the regrade panel's sections; eval-shaped
      // ones to the eval table. Routed here so each painter stays single-purpose --
      // _paintPartial's own m.corpus guard remains as the backstop.
      if (snap && snap.corpus) _paintRegradePartial(name, snap);
      else _paintPartial(name, snap);
    }
  } catch (e) { /* endpoint absent or transient - status column still works */ }
}

function startProgressPolling() {
  stopProgressPolling();
  _progressTimer = setInterval(_progressTick, 1000);
}

function stopProgressPolling() {
  if (_progressTimer) {
    clearInterval(_progressTimer);
    _progressTimer = null;
  }
}

// One last poll AFTER the blocking op resolves, THEN stop. The backend snaps a store's
// row to current==total, done=true INSIDE the seed/eval request (just before it returns),
// but the interval clears the instant the caller's await resolves -- so without this final
// tick the display freezes on the last mid-op poll (a long-tier memory bar stuck 2 short)
// and never repaints the terminal 'done' state the backend already published. Call this in
// an operation's finally INSTEAD of stopProgressPolling().
async function finalizeProgress() {
  stopProgressPolling();
  try { await _progressTick(); } catch (e) { /* terminal paint is best-effort */ }
}

async function runSeedOnly() {
  const btn = document.getElementById('seed-stores');
  const orig = btn.textContent;
  btn.textContent = 'seeding…';
  btn.disabled = true;
  startProgressPolling();
  try {
    const data = await api('/eval/seed', { method: 'POST' });
    if (data && data.ok) {
      if (data.note) alert(data.note);
    } else {
      alert('Seed failed: ' + (data && data.error || 'unknown'));
    }
  } catch (e) {
    alert('Seed error: ' + (e.message || e));
  } finally {
    await finalizeProgress();   // one last poll so the bar shows done, not stuck mid-seed
    btn.textContent = orig;
    btn.disabled = false;
  }
}

// Reset the CURRENT eval table's cells in place for a re-run: metrics back to '-', the
// Status (prog) cells to 'queued'. Only touches textContent, so the prog-<name> cells the
// poller targets keep their identity - the whole reason this doesn't re-render the table.
function _resetEvalRowsForRun() {
  const body = document.getElementById('eval-body');
  if (body) {
    body.querySelectorAll('td.mval').forEach((td) => {
      td.textContent = td.classList.contains('prog-cell') ? 'queued…' : '–';
    });
  }
  const info = document.getElementById('eval-info');
  if (info) info.textContent = 're-evaluating…';
}

async function runEval() {
  const btn = document.getElementById('run-eval');
  const orig = btn.textContent;
  btn.textContent = 'running…';
  btn.disabled = true;
  // Reset the visible rows IN PLACE so a re-eval obviously re-runs -- blank the metrics,
  // mark Status 'queued'. Do NOT re-render the table: a full innerHTML replace here
  // (renderEvalPlaceholder) swapped out the very prog-<name> cells the poller paints into
  // and killed live updates. Touching only textContent keeps every DOM reference intact.
  _resetEvalRowsForRun();
  // Snapshot the auto-capture choice up front (default on). On a clean eval we then
  // freeze member-store hits so the NEXT regrade replays instantly instead of re-querying
  // live -- the "never forget to hit capture" fix. runCapture itself no-ops gracefully
  // when capture-replay isn't available or nothing's eligible, so this is always safe.
  const wantCapture = !!(document.getElementById('auto-capture') || {}).checked;
  let evalOk = false;
  startProgressPolling();
  try {
    // NO seed:false here. Evaluate is split from the explicit 🌱 Seed button, but
    // /eval/run's own guard (ts_seeded/force_reseed) already refuses to seed a pod
    // twice -- so on a FRESH pod this button must still be allowed to seed once,
    // otherwise there is nothing to score and expect_ref questions fail loudly
    // with "GROUND TRUTH MISSING" even though the topology is perfectly healthy.
    // Forcing seed:false here made Evaluate useless as a first action; 🌱 Seed
    // stays useful for "seed now, evaluate later" and for reseeding via reseed:true.
    const data = await api('/eval/run', { method: 'POST' });
    if (data && data.ok) {
      evalOk = true;
      await refreshEval();
    } else {
      alert('Eval failed: ' + (data && data.error || 'unknown'));
    }
  } catch (e) {
    alert('Eval error: ' + (e.message || e));
  } finally {
    await finalizeProgress();   // one last poll so the bar shows done, not stuck mid-run
    btn.textContent = orig;
    btn.disabled = false;
  }
  // AFTER the eval fully settles (polling stopped, button restored): auto-capture, so its
  // own progress polling doesn't fight the eval's. Only on a clean eval, only if opted in.
  if (evalOk && wantCapture) {
    try { await runCapture(null); } catch (e) { /* non-fatal - the eval numbers still stand */ }
  }
}

// ── Regrade PLAN (staging) ─────────────────────────────────────────
// What a sweep WOULD do, shown before you run it. #regrade-body used to sit empty
// until a sweep FINISHED, which meant the only way to find out whether a corpus was
// even included was to spend the hour and look at what came back. With per-corpus
// CorpusRegrades that got worse: sets are inherited, overridden by name, or opted out
// of, so "is Geography-scc in this?" is no longer answerable by reading the YAML.
//
// Rendered from /eval/regrade/plan, which runs the SAME resolver the sweep uses, so
// the staging table cannot disagree with what actually executes.
// A plan corpus -> the SAME snapshot shape a finished corpus has, so the staging
// view renders through _rgCorpusSection exactly like the results do. This is the
// whole trick behind "no jump": the plan is not a different table, it is the results
// table with nothing in it yet. Metrics are absent (f3 renders '-'), Status reads
// pending, and every row that will exist already exists -- baseline, each set, each
// combo -- so clicking the button FILLS the table instead of replacing it.
//
// A layout that changes shape the moment work starts makes the operator re-find
// everything at exactly the moment they most want to watch one number.
// Results kept PER CORPUS, not as one blob. A single-corpus regrade used to replace
// the entire panel with that corpus's result and set currentRegrade -- which then
// tripped refreshRegradePlan's "results outrank intentions" guard, so the other
// corpora's sections and their buttons never came back until a reload. You could
// regrade one corpus, once, and then had no way to reach the others. The per-corpus
// buttons defeated themselves.
//
// Keyed by name instead, the panel is a MERGE: every corpus renders, showing results
// if it has them and its ready-state if it does not. Sweeping one updates one section
// and leaves the rest exactly as they were.
let _rgResults = {};

// Capture chip + per-corpus buttons. Extracted so a RESULTS section gets the
// identical header a PLAN section does -- otherwise a corpus would lose its own
// buttons the moment it finished, which is the same bug in miniature.
function _rgHeaderExtra(name, capMap) {
  const ce = (capMap || {})[name];
  let capCell;
  if (!ce) capCell = '<span class="muted">no capture</span>';
  else if (ce.stale) capCell = `<span class="cap-stale" title="${escapeHtml(ce.reason || '')}">\u26a0 STALE capture</span>`;
  else capCell = `<span class="cap-fresh" title="captured ${escapeHtml(ce.captured_at || '')}">\u2713 captured ${escapeHtml(String(ce.captured_at || '').replace('T', ' ').slice(5, 16))}</span>`;
  // Names embedded in onclick are safe: corpus names are slug-safe ({name}-scc, no
  // quotes), and escapeHtml covers the rest of the attribute.
  return `${capCell}<span class="rg-actions">`
    + `<button class="btn-sm" title="capture just this corpus" onclick="runCapture('${escapeHtml(name)}')">Capture</button>`
    + ` <button class="btn-sm" title="regrade just this corpus" onclick="runRegrade('${escapeHtml(name)}')">Regrade</button></span>`;
}

function _rgPlanCorpus(c, capMap, running) {
  return {
    corpus: c.corpus,
    flavor: c.flavor || (running ? 'running' : 'ready'),
    baseline: 'current', running: !!running,
    headerExtra: _rgHeaderExtra(c.corpus, capMap),
    // The baseline row FIRST: `current` is a real measured pass, not a header, and
    // omitting it makes the table jump a row when the first result lands.
    sets: [{ name: 'current', metrics: {}, params: {}, delta: {} }].concat(
      (c.sets || []).map(s => ({
        name: s.name + (s.source === 'corpus' ? ' (own)' : ''),
        metrics: {}, delta: {},
        // The knob RANGES stand in for "best knobs" until a winner exists -- the
        // column then swaps ranges for the winning combo, in place.
        params: Object.fromEntries(Object.entries(s.knobs || {})
          .map(([k, v]) => [k, '[' + v.join(',') + ']'])),
        // combo_params comes from the backend's compact_combos. Rebuilding that
        // product in JS would be a second implementation of the rule, and it would
        // rot the first time a knob's semantics changed.
        combos: (s.combo_params || []).map(pp => ({ params: pp, metrics: {}, delta: {} })),
      }))),
  };
}

// Last capture status seen by the plan, so the skeleton can show the same chips
// without a second round-trip at the exact moment a sweep is starting.
let _lastCapMap = {};

function renderRegradePlan(p, cap) {
  if (!p) return '';
  if (p.note) return `<div class="note"><span class="label">regrade:</span> ${escapeHtml(p.note)}</div>`;
  if (!p.corpora || !p.corpora.length) return '';
  const capMap = (cap && cap.corpora) || {};
  const swept = p.swept || 0;
  // The Copy button lives HERE now. It used to be emitted by renderRegrade(), which
  // the merged panel no longer calls -- so results sat on screen with no way to paste
  // them, which is most of what they are for.
  // Capture all + Copy sweep share ONE right-aligned row. Capture all always shows
  // (it's how you prime a sweep); Copy sweep only once there are results to copy.
  const hasResults = Object.keys(_rgResults).length;
  let h = '<div class="copy-row">'
        + '<button class="btn-sm copy-btn" onclick="runCapture()">Capture all</button>'
        + (hasResults ? '<button class="btn-sm copy-btn" onclick="copyRegrade(this)">Copy sweep</button>' : '')
        + '</div>';
  h += `<div class="note"><span class="label">\u2699 Regrade plan</span> - `
        + `${escapeHtml(swept + ' of ' + p.corpus_count + ' corpora')}, `
        + `${escapeHtml(String(p.total_combos || 0))} combos total. `
        + `<span class="muted">Capture freezes each corpus's member-store candidates to disk; `
        + `\u2699 then replays pure-fusion sets from that file without touching a container. `
        + `Hops sets always run live. Per-row buttons scope either to one corpus.</span></div>`;
  h += '<hr class="panel-hr">';
  // ONE RENDERER for plan and results. Everything below just decides WHICH corpora
  // to hand to _rgCorpusSection; the table itself is identical in both views.
  _lastCapMap = capMap;
  const skipped = [];
  for (const c of p.corpora) {
    if (c.skipped) { skipped.push(c); continue; }
    // MERGE: a swept corpus shows its results, an unswept one its ready-state. Both
    // keep their buttons, so any corpus can be re-swept at any time regardless of
    // what else has already run.
    const done = _rgResults[c.corpus];
    h += _rgCorpusSection(done
      ? { ...done, headerExtra: _rgHeaderExtra(c.corpus, capMap) }
      : _rgPlanCorpus(c, capMap, false));
  }
  // Skipped corpora COLLAPSED, grouped by reason. Eleven identical "no top-level
  // CorpusRegrades to inherit" rows is eleven copies of one fact, and they buried
  // the three corpora that actually run. Still shown -- "not swept, and why" is
  // information, and silently omitting a corpus looks the same as forgetting to
  // declare it -- just not eleven times.
  if (skipped.length) {
    const byReason = {};
    for (const c of skipped) {
      const r = c.reason || 'skipped';
      if (!byReason[r]) byReason[r] = [];
      byReason[r].push(c.corpus);
    }
    for (const [reason, names] of Object.entries(byReason)) {
      h += `<div class="note rg-skipped"><span class="label">\u2013 ${names.length} skipped:</span> `
         + `${escapeHtml(reason)} <span class="muted">- ${escapeHtml(names.join(', '))}</span></div>`;
    }
  }
  return h;
}

async function refreshRegradePlan(force) {
  const host = document.getElementById('regrade-body');
  // NO "results outrank intentions" guard any more. It existed because the plan
  // would have clobbered a finished sweep -- which cannot happen now that results
  // live in _rgResults and get merged in on every render. Its only remaining effect
  // was to freeze the whole panel after the first single-corpus sweep.
  if (!host) return;
  try {
    // Plan + capture status together: the staging table's whole job is answering
    // "what would run, and would it run from a trustworthy capture" in one look.
    const [plan, cap, rg] = await Promise.all([
      api('/eval/regrade/plan'),
      api('/eval/capture/status').catch(() => null),
      api('/eval/regrade/results').catch(() => null),
    ]);
    // Rehydrate a still-in-memory sweep after a page reload: _rgResults resets to {}
    // on load, which is what hid the Copy-sweep button and the result tables. Only
    // seed it when it's empty, so an in-session sweep already in progress is never
    // clobbered by a stale read.
    if (rg && rg.corpora && rg.corpora.length && !Object.keys(_rgResults).length) {
      currentRegrade = rg;                                  // what Copy sweep serialises
      for (const c of rg.corpora) if (c && c.corpus) _rgResults[c.corpus] = c;
    }
    host.innerHTML = renderRegradePlan(plan, cap);
  } catch (e) { /* older backend or no topology - leave the space empty */ }
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
  for (const c of data.corpora) h += _rgCorpusSection(c);
  return '<div class="copy-row"><button class="btn-sm copy-btn" onclick="copyRegrade(this)">Copy sweep</button></div>' + h;
}

// ONE corpus's regrade section, wrapped in an id'd div so the live partial painter
// can replace EXACTLY this section as the sweep progresses, without touching its
// siblings. Partial and final snapshots render through this same function, so a
// half-done sweep looks like a finished one that just has fewer rows yet.
function _rgCorpusSection(c) {
  const f3 = (v) => (v == null ? '-' : Number(v).toFixed(3));
  const sg = (v) => (v == null ? '' : (v > 0.0005 ? '+' : '') + Number(v).toFixed(3));
  const dcls = (v) => (v == null ? '' : (v > 0.0005 ? 'delta-pos' : (v < -0.0005 ? 'delta-neg' : '')));
  let h = ``;
  if (c.error) {
    // Failures paint into their own section the moment they publish -- a corpus that
    // died on knob 3 is visible NOW, while the rest keep sweeping around it.
    h += `<div class="regrade-corpus"><b class="rg-name">${escapeHtml(c.corpus)}</b></div>`
       + `<div class="note err"><span class="label">\u2717 failed:</span> ${escapeHtml(c.error)}</div>`;
    return h + '</div>';
  }
  if (c.running) {
    h += `<div class="note"><span class="label">running\u2026</span> <span class="muted">sets fill in as they finish; combo progress is in the eval table's Status column.</span></div>`;
  }
  {
    h += `<div class="regrade-corpus"><b class="rg-name">${escapeHtml(c.corpus)}</b> <span class="muted">(${escapeHtml(c.flavor)}) · baseline: ${escapeHtml(c.baseline || '-')}</span>`
       // Roll-up (X/Y REGRADES) INSIDE the header div, repainted by the poll so it
       // ticks without re-rendering the section under the operator's scroll position.
       + `<span class="rg-rollup" id="rgp-${escapeHtml(c.corpus)}"></span>`
       // Capture chip + per-corpus buttons, supplied by the PLAN renderer. Results
       // sections pass nothing, so one shell serves both views unchanged.
       + `${c.headerExtra || ''}</div>`;
    h += '<table class="store-table regrade-table"><tr><th>Set</th><th>Status</th><th>nDCG</th><th>Coverage</th><th>Recall</th><th>MRR</th><th>Δ</th><th>best knobs</th></tr>';
    for (const s of c.sets) {
      const m = s.metrics || {}, d = s.delta || {}, isBase = s.name === c.baseline;
      // A set with no metrics has not been measured yet (a skeleton row). Its Status
      // cell is where the per-QUESTION counter lands while that set is in flight; a
      // measured one reads done. data-set is how the poll finds the right cell.
      const measured = (m && m.ndcg != null);
      // WHICH ENGINE MEASURED IT, not just "done". A pure-fusion set replayed from a
      // frozen capture and a hops set driven against live containers are different
      // kinds of evidence -- capture rows are bit-reproducible, live rows carry the
      // ~0.02 run-to-run noise floor of real vector search. Reading a 0.01 delta as
      // signal is fine on one and wrong on the other, so the table says which.
      const eng = s.engine === 'capture-replay' ? 'capture'
                : s.engine === 'live' ? 'live' : 'done';
      const stTip = measured
        ? (s.engine === 'capture-replay'
            ? 'replayed from a frozen capture - deterministic, repeats exactly'
            : s.engine === 'live'
              ? 'measured against live containers - carries a ~0.02 noise floor'
              : 'measured')
        : 'not measured yet';
      const stCell = `<td class="mval rg-st" data-set="${escapeHtml(s.name)}" title="${escapeHtml(stTip)}">`
                   + (measured ? eng : '<span class="muted">pending</span>') + '</td>';
      const dparts = [];
      for (const [k, lbl] of [['docket_coverage', 'cov'], ['ndcg', 'ndcg']])
        if (d[k] != null && Math.abs(d[k]) > 0.0005) dparts.push(`<span class="${dcls(d[k])}">${lbl} ${sg(d[k])}</span>`);
      const dcell = isBase ? '<span class="muted">baseline</span>' : (dparts.join(' ') || '<span class="muted">-</span>');
      const p = s.params || {}, knobs = Object.keys(p).length ? Object.entries(p).map(([k, v]) => `${k}=${v}`).join(' ') : '-';
      h += `<tr class="${isBase ? 'rg-base' : 'rg-win'}"><td class="k">${escapeHtml(s.name)}</td>`
         + stCell
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
           + `<td></td><td class="mval">${f3(cm.ndcg)}</td><td class="mval">${f3(cm.docket_coverage)}</td>`
           + `<td class="mval">${f3(cm.recall)}</td><td class="mval">${f3(cm.mrr)}</td>`
           + `<td>${cparts.join(' ') || '<span class="muted">-</span>'}</td><td></td></tr>`;
      }
    }
    h += '</table>';
  }
  return h;
}

// The sweep's SKELETON: every in-scope corpus section rendered empty, from the same
// plan the sweep resolves -- so the table exists at 0% and fills section by section,
// the exact flow Seed and Eval already have. A synthetic corpus snapshot (set names,
// dash metrics, running:true) goes through _rgCorpusSection, so the skeleton is the
// SAME markup as the finished thing, just with nothing in it yet.
async function _renderRegradeSkeleton(corpus) {
  const host = document.getElementById('regrade-body');
  if (!host) return;
  try {
    const plan = await api('/eval/regrade/plan');
    if (!plan || !plan.corpora || !plan.corpora.length) return;
    let h = '<div class="note"><span class="label">\u2699 Regrade sweep</span> '
          + '<span class="muted">running - sections fill in as each corpus lands; '
          + 'combo progress is in the eval table\'s Status column.</span></div>';
    let any = false;
    for (const c of plan.corpora) {
      if (c.skipped) continue;
      any = true;
      // EVERY corpus, not just the target. Filtering to the swept one wiped the
      // others off the panel for the duration of the run -- and if it was a
      // single-corpus sweep, they never came back. The target shows running; the
      // rest keep whatever they already had.
      const isTarget = !corpus || c.corpus === corpus;
      const done = _rgResults[c.corpus];
      h += _rgCorpusSection((done && !isTarget)
        ? { ...done, headerExtra: _rgHeaderExtra(c.corpus, _lastCapMap) }
        : _rgPlanCorpus(c, _lastCapMap, isTarget));
    }
    if (any) host.innerHTML = h;
  } catch (e) { /* older backend / no plan: the plain spinner already showing stands */ }
}

// Replace ONE corpus's section in place as its partial lands. outerHTML swap keeps
// the id (the new section carries the same one), so successive partials for the same
// corpus keep finding their slot. Siblings are never re-rendered -- a finished
// section does not flicker because its neighbour is still sweeping.
function _paintRegradePartial(name, m) {
  const sec = document.getElementById('rg-sec-' + name);
  if (!sec || !m || !m.corpus) return;
  sec.outerHTML = _rgCorpusSection(m);
}

// Paint the two live counters into ONE corpus's regrade section, from the progress
// registry rather than from partials. Two granularities, because they answer
// different questions:
//   roll-up  X/Y REGRADES  -- where in the sweep this corpus is
//   per-set  X/Y QUESTIONS -- whether anything is happening RIGHT NOW
// The coarse counter only moves once per combo, which on a wide fan is once every
// twelve minutes; a table that sits still that long is indistinguishable from a hung
// one, which is the whole reason this exists.
//
// Cell-level updates, never a section re-render: repainting the section every second
// would fight the operator's scroll and wipe any expanded state.
function _paintRegradeStatus(name, row) {
  if (!row || row.phase !== 'regrade') return;
  const roll = document.getElementById('rgp-' + name);
  if (roll) {
    roll.textContent = row.done
      ? ` \u00b7 ${row.current}/${row.total} regrades done`
      : ` \u00b7 regrade ${row.current}/${row.total}\u2026`;
  }
  const sec = document.getElementById('rg-sec-' + name);
  if (!sec) return;
  const d = row.detail || {};
  for (const cell of sec.querySelectorAll('td.rg-st')) {
    const setName = cell.getAttribute('data-set');
    if (d.set && setName === d.set && !row.done) {
      // The in-flight set carries the question counter. Everything else keeps
      // whatever the renderer gave it (done / pending).
      cell.textContent = `q ${d.q_done || 0}/${d.q_total || 0}`;
      cell.classList.add('rg-st-live');
    } else {
      cell.classList.remove('rg-st-live');
    }
  }
}

async function runRegrade(corpus) {
  // WIRED TWO WAYS: the toolbar's addEventListener('click', runRegrade) and the
  // per-row onclick="runRegrade('Characters-scc')". A click handler's first argument
  // is a PointerEvent, and without this guard that event object would go straight
  // into JSON.stringify as {corpus: PointerEvent} -- a 400 on every toolbar click.
  if (typeof corpus !== 'string') corpus = null;
  const btn = document.getElementById('run-regrade');
  if (!btn) return;
  const orig = btn.textContent;
  btn.textContent = 'sweeping…';
  btn.disabled = true;
  const host = document.getElementById('regrade-body');
  if (host) host.innerHTML = '<div class="loading">capturing + sweeping…</div>';
  // SKELETON FIRST, spinner as fallback: the loading div above is replaced by the
  // full empty table the moment the plan answers, and each section fills as its
  // corpus lands. Same preload-then-fill flow Seed and Eval already have.
  await _renderRegradeSkeleton(corpus);
  // Regrade publishes per-corpus progress (phase 'regrade') into the registry the
  // eval table's Status column polls -- corpora exist as rows there, so the combo
  // counter is visible while the sweep runs. The partials painter ignores
  // corpus-shaped snapshots by design (see _paintPartial).
  startProgressPolling();
  try {
    // raw fetch (not api()) so we can read the detail body on a 501/400 -
    // the SCC-not-installed hint, the no-CorpusRegrades message, etc.
    const headers = { 'Content-Type': 'application/json' };
    const tok = (typeof getToken === 'function') ? getToken() : '';
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    const resp = await fetch('/eval/regrade', {
      method: 'POST', headers,
      body: JSON.stringify(corpus ? { corpus } : {}),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      currentRegrade = data.results;      // what \u{1F4CB} Copy sweep serialises
      // MERGE per corpus rather than replacing the panel, so a single-corpus sweep
      // updates its own section and leaves every other corpus (and its buttons)
      // exactly where they were.
      for (const c of (data.results.corpora || [])) {
        if (c && c.corpus) _rgResults[c.corpus] = c;
      }
      let html = '';
      // THE HOLLER. A saved capture that was refused (reseed after capture, changed
      // questions) is called out above the results, with the reason and the fact
      // that a fresh capture was taken instead -- the sweep is still trustworthy,
      // the operator just learns their capture FILE is not.
      const stale = data.results && data.results.stale_captures;
      if (stale && Object.keys(stale).length) {
        const parts = Object.entries(stale).map(([n, r]) => `${escapeHtml(n)} (${escapeHtml(r)})`);
        html += `<div class="note err"><span class="label">\u26a0 stale capture:</span> `
              + `${parts.join(' \u00b7 ')} - excluded; those corpora captured FRESH for this sweep. `
              + `<span class="muted">Capture again to refresh the file.</span></div>`;
      }
      await refreshRegradePlan(true);
      if (host && html) host.innerHTML = html + host.innerHTML;
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
    stopProgressPolling();
    btn.textContent = orig;
    btn.disabled = false;
  }
}

async function runCapture(corpus) {
  // Same PointerEvent guard as runRegrade -- 'Capture all' is a bare onclick today,
  // but the moment anyone wires it as a listener the event object arrives here.
  if (typeof corpus !== 'string') corpus = null;
  const host = document.getElementById('regrade-body');
  if (host) host.innerHTML = `<div class="loading">capturing ${corpus ? escapeHtml(corpus) : 'all eligible corpora'} <span class="muted">(read-only /search pass; the sweep comes later)</span></div>`;
  startProgressPolling();
  try {
    const data = await api('/eval/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(corpus ? { corpus } : {}),
    });
    if (data && data.ok) {
      // Force the plan (and its Capture column) to re-render with the new file.
      // currentRegrade may hold an old sweep, but a capture invalidates the
      // "results outrank intentions" hold: the operator just changed the intentions.
      currentRegrade = null;
      await refreshRegradePlan();
      if (data.note && host && !(data.captured || []).length) {
        host.innerHTML = `<div class="note"><span class="label">capture:</span> ${escapeHtml(data.note)}</div>` + host.innerHTML;
      }
    } else if (host) {
      host.innerHTML = `<div class="empty">capture failed: ${escapeHtml((data && data.error) || 'unknown')}</div>`;
    }
  } catch (e) {
    if (host) host.innerHTML = `<div class="empty">capture error: ${escapeHtml(e.message || e)}</div>`;
  } finally {
    stopProgressPolling();
  }
}

// -- Docker config management -------------------------------------
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
    html += '<div class="note"><span class="label">Start</span> compiles this topology into a docker-compose and builds each service from the shipped Dockerfiles. Bring-your-own prebuilt images via the start request\'s image_overrides (advanced).</div>';
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
        body.innerHTML = '<div class="empty">Topology isn\'t running. Click Start to launch it.</div>';
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
      body.innerHTML = '<div class="empty">Container exists but is not running. Click Start to relaunch.</div>';
    } else {
      info.textContent = 'no container';
      body.innerHTML = '<div class="empty">No Docker environment running. Click Start to launch one.</div>';
    }
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

// PLACEHOLDER ROWS. Right after docker_start/docker_adopt hand back the topology's
// store names, the Eval tab has nothing to show yet -- no seed has run, no eval has
// scored anything -- but "loading…" (or a blank table) looks like the app is stuck,
// not like the fleet just came up. Render every known store immediately with "-" in
// every column so the operator sees the shape of the topology right away; refreshEval()
// overwrites this with real numbers the moment /eval/seed or /eval/run actually finishes.
function renderEvalPlaceholder(loci, memory, corpus, seeded) {
  const body = document.getElementById('eval-body');
  if (!body) return;
  const names = [...(loci || []), ...(memory || []), ...(corpus || [])];
  if (!names.length) return;
  let html = '<table class="store-table eval-table"><tr><th>Store</th><th>Status</th><th>HR</th><th>MRR</th><th>P@k</th><th>R@k</th><th>NDCG</th><th>IoU</th><th>P-Ω</th><th></th></tr>';
  for (const name of names) {
    html += `<tr><td class="sname">${escapeHtml(name)}</td>`
          + `<td class="mval prog-cell" id="prog-${escapeHtml(name)}">-</td>`
          + '<td class="mval">-</td><td class="mval">-</td><td class="mval">-</td><td class="mval">-</td>'
          + '<td class="mval">-</td><td class="mval">-</td><td class="mval">-</td><td></td></tr>';
  }
  html += '</table>';
  body.innerHTML = html;
  const info = document.getElementById('eval-info');
  // Say the TRUE state. This used to hardcode "not yet seeded/evaluated", so a reload of
  // an already-seeded pod lied about it and prompted a needless reseed. `seeded` comes
  // from /docker/status now (a persistent flag that survives an app restart).
  if (info) {
    info.textContent = seeded
      ? `${names.length} stores · already seeded, not yet evaluated — Evaluate to score`
      : `${names.length} stores · not yet seeded/evaluated`;
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
      renderEvalPlaceholder(data.loci, data.memory, data.corpus, data.seeded);
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
    currentPc = pc;
    const info = document.getElementById('config-info');
    if (info) info.textContent = `${cfg.stores || 5} stores · v${cfg.version || '?'}`;

    // ── ProbeConfig (topology) - what ▶ Start spins up ──
    const pcYaml = pc.yaml || '';
    const pcSource = pc.source === 'bundle' ? `bundle ${pc.bundle} \u00b7 ${pc.config_name}${pc.edited ? ' (edited)' : ''} (active)`
                   : (pc.active ? 'uploaded (active)' : 'shipped example');
    let html = '<div class="docker-config-section">';
    html += '<details open>';
    html += `<summary><b>ProbeConfig</b> - topology <span class="muted">source: ${escapeHtml(pcSource)}</span></summary>`;
    html += '<div class="docker-config-body">';
    html += `<div class="note"><span class="label">tip:</span> declare X Loci / Y Memory / Z Corpus, then Set Active to make it the topology <b>Start</b> builds.</div>`;
    html += '<div class="upload-form">';
    html += `<textarea id="probeconfig-yaml" rows="14" spellcheck="false">${escapeHtml(pcYaml)}</textarea>`;
    if (pc.bundle) {
      // DERIVED FROM A BUNDLE. Ticked, Set Active keeps the text tied to the
      // bundle: its paths mean files inside the bundle, and its runs group as
      // "<config> (edited)". Untick to upload it as a plain config.
      html += `<label class="keep-bundle" `
            + `title="Set Active keeps this text tied to the bundle: its paths mean files inside the bundle, and its runs group as '${escapeHtml(pc.config_name)} (edited)'. Untick to upload it as a plain config with paths relative to the Probe's working directory.">`
            + `<input type="checkbox" id="probeconfig-keep-bundle" checked> paths inside bundle ${escapeHtml(pc.bundle)} \u00b7 ${escapeHtml(pc.config_name)}</label>`;
    }
    html += '<div class="ctrls-row">';
    // Under the editor there is one button. Validate, Load file and Reload
    // live in the panel header and nowhere else (20 Oct 2026: "remove
    // all of the lower buttons but the copy validation button").
    // The lint can run to thousands of lines on a multi-tenant topology. Reading that
    // through a scrollbox and then SCREENSHOTTING it is not a workflow.
    html += '<button class="btn-sm copy-btn" onclick="copyLint(this)">Copy validation</button>';
    html += '</div>';
    html += '<div class="config-save-result" id="probeconfig-result"></div>';
    html += '</div></div></details></div>';

    // ── Inquisition bundles - datasets that travel whole (21 Oct 2026) ──
    // Rendered by refreshBundles() into #bundles-body once this shell is in the DOM.
    html += '<div class="docker-config-section">';
    html += '<details open>';
    html += '<summary><b>Bundles</b> - datasets that travel whole <span class="muted" id="bundles-summary">loading\u2026</span></summary>';
    html += '<div class="docker-config-body" id="bundles-body"><div class="loading">loading\u2026</div></div>';
    html += '</details></div>';

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
    html += '<button class="btn" onclick="saveConfig()">Save store config</button>';
    html += '<div class="config-save-result" id="config-save-result"></div>';
    html += '</div></div></details></div>';

    body.innerHTML = html;
    refreshBundles();
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

// What an activation came back with, as the one message both doors show: the
// summary, the hot-swap, the warnings and the whole question lint. Returns
// [cls, text] for setResult. (Factored out of uploadProbeConfig on 21 Oct 2026
// so a bundle's config says the same things when it is activated.)
function _describeActivation(data) {
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
  return [(L && L.errors && L.errors.length) ? 'err' : 'ok', msg];
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
      method: 'POST', headers, body: JSON.stringify(_withBundle({ probe_config: yamlText })),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      // TELL THE EVAL TAB. The hot-swap replaces the running topology's regrade sets
      // server-side, but #regrade-body lives in the OTHER tab and nothing here ever
      // touched it -- so the banner announced "hot-swapped, hit Regrades to roll them"
      // directly above a plan table still listing the sets it had just replaced. A UI
      // that contradicts its own success message is worse than a stale table: it
      // teaches the operator not to believe either one.
      //
      // force=true because the plan's normal guard ("results outrank intentions")
      // pins an old sweep's results -- and a config upload is exactly the moment the
      // intentions became the newer fact.
      if (typeof refreshRegradePlan === 'function') refreshRegradePlan(true);
      const [cls, msg] = _describeActivation(data);
      setResult(result, cls, msg);
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

// The upload body, naming the bundle the editor's text came from when the
// checkbox under the editor is ticked (see refreshConfig).
function _withBundle(body) {
  const keep = document.getElementById('probeconfig-keep-bundle');
  if (keep && keep.checked && currentPc && currentPc.bundle) {
    body.bundle = currentPc.bundle;
    body.config_name = currentPc.config_name;
  }
  return body;
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
  const seedStoresBtn = document.getElementById('seed-stores');
  if (seedStoresBtn) seedStoresBtn.addEventListener('click', runSeedOnly);
  const runEvalBtn = document.getElementById('run-eval');
  if (runEvalBtn) runEvalBtn.addEventListener('click', runEval);
  const runRegradeBtn = document.getElementById('run-regrade');
  if (runRegradeBtn) runRegradeBtn.addEventListener('click', runRegrade);

  // Wire Docker buttons
  const dockerStartBtn = document.getElementById('docker-start');
  if (dockerStartBtn) dockerStartBtn.addEventListener('click', dockerStart);
  const dockerStopBtn = document.getElementById('docker-stop');
  if (dockerStopBtn) dockerStopBtn.addEventListener('click', dockerStop);

  // Wire the ledger's controls (Runs tab)
  const cmpBtn = document.getElementById('runs-compare');
  if (cmpBtn) cmpBtn.addEventListener('click', compareRuns);
  for (const id of ['runs-kind', 'runs-bundle']) {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', refreshRuns);
  }
  const grp = document.getElementById('runs-group');
  if (grp) grp.addEventListener('change', renderRuns);

  // ── Store drill-down modal wiring (all delegated: the rows it targets are
  // rendered dynamically, so one document-level listener beats re-binding on
  // every refresh) ──
  document.addEventListener('click', function(e) {
    const link = e.target.closest('.sname-link');
    if (link && link.dataset.store) { openStoreDetail(link.dataset.store); return; }
    if (e.target.closest('[data-qd-close]')) { closeQd(); return; }
    const fbtn = e.target.closest('[data-qdfilter]');
    if (fbtn) { _qd.filter = fbtn.dataset.qdfilter; _qdRenderFilters(); renderQdList(); return; }
    const qhead = e.target.closest('.qd-qhead');
    if (qhead) { qhead.parentElement.classList.toggle('open'); return; }
    const copyBtn = e.target.closest('#qd-copy');
    if (copyBtn) { qdCopyMarkdown(copyBtn); return; }
  });
  document.addEventListener('input', function(e) {
    if (e.target && e.target.id === 'qd-search') { _qd.search = e.target.value; renderQdList(); }
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      const modal = document.getElementById('qd-modal');
      if (modal && !modal.hidden) closeQd();
    }
  });

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

  // The header ⟳ (header_aside.html) is reload(): the pill and the open
  // panel, not every panel (20 Oct 2026, the family's shape).

  // Wire per-panel refresh buttons
  document.querySelectorAll('[data-refresh]').forEach(el => {
    el.addEventListener('click', function() {
      const target = this.getAttribute('data-refresh');
      if (target === 'eval') refreshEval();
      else if (target === 'docker') { refreshDocker(); refreshDockerConfig(); }
      else if (target === 'config') refreshConfig();
      else if (target === 'runs') refreshRuns();
    });
  });

  // the shell activates the first .view on DOMContentLoaded; load its data too
  lazyLoad(document.querySelector('.tabbar .tab')?.dataset?.tab);
  refreshSummary();

  // ADOPT: the containers outlive the app process. Restarting SerenProbe used to
  // orphan a perfectly healthy fleet - app.state forgot it, so the operator rebuilt
  // and reseeded (an hour, on a big corpus) for nothing. If a pod is actually up
  // (verified by `compose ps`, not just a state file), offer to attach to it.
  offerAdoption();
});

// The header pill: the active ProbeConfig in one line, lit in the accent
// when it is valid, red when it is not, like the Callosum's fan pill and the
// Hippocampus's model pill (20 Oct 2026).
async function refreshSummary() {
  const pill = document.getElementById('status-pill');
  if (!pill) return;
  try {
    const pc = await api('/docker/probeconfig');
    if (pc.errors && pc.errors.length) {
      pill.className = 'head-pill bad';
      pill.textContent = 'config: invalid';
      pill.title = pc.errors.join('\n');
    } else if (pc.summary) {
      const s = pc.summary;
      const n = (s.loci || []).length + (s.memory || []).length + (s.corpus || []).length;
      pill.className = 'head-pill hot';
      pill.textContent = `config: ${n} store${n === 1 ? '' : 's'}`;
      const from = pc.source === 'bundle' ? `bundle ${pc.bundle} · ${pc.config_name}` : (pc.active ? 'uploaded' : 'shipped example');
      pill.title = `${(s.loci || []).length} loci · ${(s.memory || []).length} memory · ${(s.corpus || []).length} corpus · ${from}`;
    } else {
      pill.className = 'head-pill';
      pill.textContent = 'config: none';
      pill.title = 'no ProbeConfig loaded - set one in the Config tab';
    }
  } catch (e) {
    pill.className = 'head-pill bad';
    pill.textContent = 'disconnected';
    pill.title = e.message || String(e);
  }
}

// The header ⟳: the pill and the panel that is open.
function reload() {
  refreshSummary();
  const open = document.querySelector('.view.active')?.id;
  if (open === 'eval') refreshEval();
  else if (open === 'docker') { refreshDocker(); refreshDockerConfig(); }
  else if (open === 'config') refreshConfig();
  else if (open === 'runs') refreshRuns();
}

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
        if (typeof renderEvalPlaceholder === 'function') renderEvalPlaceholder(d.loci, d.memory, d.corpus, d.seeded);
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

// ── Inquisition bundles (.iqz): the dataset travels whole (21 Oct 2026) ──────
// A ProbeConfig's Seed and Questions paths are opened by the Probe PROCESS on
// its own box, so a config sent from the desktop found nothing on the nuc. A
// bundle carries the configs and every file they reference; activating one of
// its configs goes through the same door as Validate & Set Active, with the
// bundle's folder as the base for its paths.
let currentBundles = [];

function _kb(n) {
  n = n || 0;
  return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB';
}

async function refreshBundles() {
  const host = document.getElementById('bundles-body');
  if (!host) return;
  try {
    const data = await api('/bundles');
    currentBundles = data.bundles || [];
  } catch (e) {
    host.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
    return;
  }
  const sum = document.getElementById('bundles-summary');
  if (sum) sum.textContent = `${currentBundles.length} installed`;
  let html = '<div class="note"><span class="label">tip:</span> pack a dataset folder on the box it lives on '
           + '(<code>seren-probe bundle pack FOLDER -o name.iqz</code>), then upload the .iqz here. '
           + '<b>Inspect</b> says what it holds and whether it can be trusted, installing nothing; '
           + '<b>Upload</b> validates, then installs. A bundle is kept by its content and is not thrown away when a pod stops.</div>';
  html += '<div class="ctrls-row">'
        + '<input type="file" id="bundle-file" accept=".iqz,.tar,.tar.gz,.tgz" title="Choose a bundle" onchange="bundleChosen(this)"/>'
        + '<span class="muted bundle-file-name" id="bundle-file-name"></span>'
        + '<button class="btn-sm" onclick="sendBundle(false)">Inspect</button>'
        + '<button class="btn" onclick="sendBundle(true)">Upload &amp; install</button>'
        + '</div>'
        + '<div class="config-save-result" id="bundle-result"></div>';
  if (!currentBundles.length) {
    html += '<div class="empty">no bundles installed yet</div>';
  } else {
    // Design note (22 Oct 2026): bundle, installed, size, runs, configs, then
    // export and trash; each config carries its own play button.
    html += '<table class="store-table bundle-table"><tr><th>Bundle</th><th>Installed</th><th>Size</th><th>Runs</th>'
          + '<th>Configs</th><th></th><th></th></tr>';
    const act = (currentPc && currentPc.bundle) ? currentPc : null;
    for (const b of currentBundles) {
      const cfgs = (b.configs || []).map(c => {
        const on = act && act.bundle === b.id && act.config_name === c.name;
        return `<div class="bundle-config${on ? ' active' : ''}"><span class="bc-name">${escapeHtml(c.name)}</span>`
          + (on ? ` <span class="catch-tag" title="the active ProbeConfig${act.edited ? ', edited in the editor' : ''}">active${act.edited ? ', edited' : ''}</span>` : '')
          + (c.note ? ` <span class="muted bc-note">${escapeHtml(c.note)}</span>` : '')
          + `<button class="btn-sm icon-btn bc-play" onclick="activateBundleConfig('${escapeHtml(b.id)}', '${escapeHtml(c.name)}')" `
          + `title="Make ${escapeHtml(c.name)} the active ProbeConfig" aria-label="Activate ${escapeHtml(c.name)}">${_icon('play')}</button></div>`;
      }).join('');
      html += `<tr><td><b>${escapeHtml(b.name)}</b><div class="muted mono">${escapeHtml(b.id)}</div>`
            + (b.note ? `<div class="muted">${escapeHtml(b.note)}</div>` : '') + '</td>'
            + `<td class="muted nowrap">${escapeHtml(String(b.installed_at || '').replace('T', ' ').slice(0, 16))}</td>`
            + `<td class="muted nowrap">${b.files} files \u00b7 ${_kb(b.bytes)}</td>`
            + `<td>${b.runs || 0}${b.baseline_runs ? ` <span class="muted" title="runs that came with the bundle, as a baseline">(${b.baseline_runs} came with it)</span>` : ''}</td>`
            + `<td class="bc-cell">${cfgs}</td>`
            + `<td><button class="btn-sm icon-btn" onclick="exportBundle('${escapeHtml(b.id)}')" title="Export: download this bundle with every run that names it, to hand on as a baseline" aria-label="Export ${escapeHtml(b.id)}">${_icon('export')}</button></td>`
            + `<td><button class="btn-sm icon-btn danger" onclick="deleteBundle('${escapeHtml(b.id)}', false)" title="Delete this bundle (refused while runs name it)" aria-label="Delete ${escapeHtml(b.id)}">${_icon('trash')}</button></td></tr>`;
    }
    html += '</table>';
  }
  host.innerHTML = html;
}

// The picker is the family's file field, in sight (25 Oct 2026); choosing a file shows
// its size beside it and inspects it at once, which writes nothing.
function bundleChosen(input) {
  const file = input.files && input.files[0];
  const label = document.getElementById('bundle-file-name');
  if (label) label.textContent = file ? _kb(file.size) : '';
  if (file) sendBundle(false);
}

async function sendBundle(install) {
  const input = document.getElementById('bundle-file');
  const result = document.getElementById('bundle-result');
  const file = input && input.files && input.files[0];
  if (!file) { setResult(result, 'err', 'choose an .iqz file first'); return; }
  setResult(result, '', (install ? 'validating and installing ' : 'inspecting ') + file.name + '\u2026');
  try {
    // raw fetch: the body is the archive itself, and a 400 carries the problems
    const resp = await authFetch(install ? '/bundles' : '/bundles/inspect', {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const d = data.detail || {};
      const probs = d.problems || (typeof d === 'string' ? [d] : []);
      setResult(result, 'err', probs.length ? '\u2717 ' + probs.join('\n\u2717 ') : `HTTP ${resp.status}`);
      return;
    }
    const cfgs = (data.configs || []).map(c =>
      `${c.name}: ${(c.loci || []).length} loci \u00b7 ${(c.memory || []).length} memory \u00b7 ${(c.corpus || []).length} corpus`
      + (c.note ? ` - ${c.note}` : '') + (c.errors && c.errors.length ? ' \u2717 ' + c.errors[0] : '')).join('\n  ');
    if (install) {
      setResult(result, 'ok', (data.already ? `\u2713 already installed as ${data.id}` : `\u2713 installed as ${data.id}`)
        + ` - ${data.files} files, ${_kb(data.bytes)}` + (data.note ? `\n${data.note}` : '')
        + (data.runs_imported ? `\n${data.runs_imported} baseline run(s) came with it and are in the ledger: compare yours against them in the Runs tab` : '')
        + (cfgs ? '\n  ' + cfgs : '')
        + '\nActivate one of its configs below to make it the topology Start builds.');
      input.value = '';
      refreshBundles();
    } else {
      setResult(result, data.ok ? 'ok' : 'err',
        (data.ok ? `\u2713 ${data.name} (${data.id}) can be trusted` : '\u2717 refused') + ` - ${data.files} files, ${_kb(data.bytes)}`
        + (data.installed ? ' \u00b7 already installed' : '')
        + (cfgs ? '\n  ' + cfgs : '')
        + (data.problems && data.problems.length ? '\n\u2717 ' + data.problems.join('\n\u2717 ') : ''));
    }
  } catch (e) {
    setResult(result, 'err', 'error: ' + (e.message || e));
  }
}

async function exportBundle(id) {
  const result = document.getElementById('bundle-result');
  setResult(result, '', `packing ${id} with its runs\u2026`);
  try {
    const resp = await authFetch(`/bundles/${encodeURIComponent(id)}/export?runs=true`);
    if (!resp.ok) { setResult(result, 'err', `export failed (HTTP ${resp.status})`); return; }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${id}.iqz`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    setResult(result, 'ok', `\u2713 ${id}.iqz (${_kb(blob.size)}) - the dataset, its configs and every run that names it. `
      + 'Whoever installs it gets your runs as a baseline to compare theirs against.');
  } catch (e) {
    setResult(result, 'err', 'error: ' + (e.message || e));
  }
}

async function activateBundleConfig(id, name) {
  const result = document.getElementById('bundle-result');
  setResult(result, '', `activating ${name} from ${id}\u2026`);
  try {
    const resp = await authFetch(`/bundles/${encodeURIComponent(id)}/activate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config: name }) });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      if (typeof refreshRegradePlan === 'function') refreshRegradePlan(true);   // same reason as the upload
      const [cls, msg] = _describeActivation(data);
      await refreshConfig();                 // the editor now shows the bundled config
      setResult(document.getElementById('probeconfig-result'), cls, `\u2713 ${name} from bundle ${id}\n` + msg);
      refreshSummary();
    } else {
      const detail = (data && data.detail) || {};
      const errs = detail.errors || (typeof detail === 'string' ? [detail] : []);
      setResult(result, 'err', errs.length ? '\u2717 ' + errs.join('\n\u2717 ') : `activate failed (HTTP ${resp.status})`);
    }
  } catch (e) {
    setResult(result, 'err', 'error: ' + (e.message || e));
  }
}

// Clear the active ProbeConfig (22 Oct 2026, "we can't remove a bundle once
// it's loaded"). An uploaded text lives only in this Probe, so that one asks
// first; a bundled one can be activated again from its bundle.
async function clearProbeConfig() {
  if (currentPc && currentPc.source === 'uploaded'
      && !confirm('Clear the uploaded config? It lives only in this Probe, so the text is gone unless you have a copy.')) {
    return false;
  }
  try {
    const resp = await authFetch('/docker/probeconfig', { method: 'DELETE' });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      setResult(document.getElementById('probeconfig-result'), 'err', `clear failed (HTTP ${resp.status})`);
      return false;
    }
    if (typeof refreshRegradePlan === 'function') refreshRegradePlan(true);
    await refreshConfig();
    refreshSummary();
    const c = data.cleared;
    const what = !c ? 'nothing was active' : (c.bundle ? `cleared ${c.config_name} from bundle ${c.bundle}` : 'cleared the uploaded config');
    setResult(document.getElementById('probeconfig-result'), 'ok',
      `\u2713 ${what} - nothing is active; Start would build the shipped example.` + (data.note ? `\n${data.note}` : ''));
    return true;
  } catch (e) {
    setResult(document.getElementById('probeconfig-result'), 'err', 'error: ' + (e.message || e));
    return false;
  }
}

async function deleteBundle(id, force) {
  const result = document.getElementById('bundle-result');
  if (force && !confirm(`Delete bundle ${id} anyway? The active config is cleared with it if it came from here, `
                        + 'runs that name it keep their numbers, and a pod started from it cannot Seed or Evaluate until stopped.')) return;
  try {
    const resp = await authFetch(`/bundles/${encodeURIComponent(id)}` + (force ? '?force=true' : ''), { method: 'DELETE' });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      if (data.config_cleared) { await refreshConfig(); refreshSummary(); }
      else refreshBundles();
      setResult(document.getElementById('bundle-result'), 'ok', `\u2713 deleted ${id}` + (data.note ? ` - ${data.note}` : ''));
      return;
    }
    const detail = data.detail || {};
    const probs = detail.problems || [];
    const reasons = detail.reasons || [];
    if (resp.status === 409) {
      setResult(result, 'err', '\u2717 ' + probs.join('\n\u2717 ') + '\n');
      // the way out for each holder, then the override
      if (reasons.includes('active_config'))
        result.insertAdjacentHTML('beforeend', `<button class="btn-sm" onclick="clearThenDelete('${escapeHtml(id)}')" title="Clear the active config, then delete the bundle">Clear config and delete</button> `);
      if (reasons.includes('running_pod'))
        result.insertAdjacentHTML('beforeend', `<button class="btn-sm" onclick="showTab('docker'); lazyLoad('docker');" title="The pod is stopped from the Docker tab">Go to Docker</button> `);
      result.insertAdjacentHTML('beforeend', `<button class="btn-sm" onclick="deleteBundle('${escapeHtml(id)}', true)">Delete anyway</button>`);
    } else {
      setResult(result, 'err', `delete failed (HTTP ${resp.status})`);
    }
  } catch (e) {
    setResult(result, 'err', 'error: ' + (e.message || e));
  }
}

async function clearThenDelete(id) {
  if (await clearProbeConfig()) deleteBundle(id, false);
}

// ── Runs: the ledger (21 Oct 2026) ───────────────────────────────────────────
// Every eval and regrade sweep is a kept run. Two over the same question hash
// compare per store (every metric both graded) and per question tag, movers
// first; a differing hash is a different experiment and the page says so.
let currentRuns = [];
let runPick = [];          // run ids checked for compare, in click order: A then B
let currentCompare = null;

async function refreshRuns() {
  const body = document.getElementById('runs-body');
  if (!body) return;
  body.innerHTML = '<div class="loading">loading\u2026</div>';
  const kind = document.getElementById('runs-kind')?.value || '';
  const bundle = document.getElementById('runs-bundle')?.value || '';
  try {
    const q = new URLSearchParams({ limit: '200' });
    if (kind) q.set('kind', kind);
    if (bundle) q.set('bundle', bundle);
    const [data, bl] = await Promise.all([
      api('/eval/runs?' + q.toString()),
      api('/bundles').catch(() => ({ bundles: [] })),
    ]);
    currentRuns = data.runs || [];
    _fillBundleFilter(bl.bundles || [], bundle);
    const info = document.getElementById('runs-info');
    if (info) info.textContent = `${data.count} run${data.count === 1 ? '' : 's'} kept`;
    runPick = runPick.filter(id => currentRuns.some(r => r.id === id));
    renderRuns();
  } catch (e) {
    body.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

function _fillBundleFilter(bundles, selected) {
  const sel = document.getElementById('runs-bundle');
  if (!sel) return;
  // the installed bundles plus every bundle a kept run names: a deleted bundle's runs still filter
  const ids = new Set(bundles.map(b => b.id));
  for (const r of currentRuns) if (r.bundle) ids.add(r.bundle);
  let html = '<option value="">any bundle</option>';
  for (const id of [...ids].sort()) html += `<option value="${escapeHtml(id)}"${id === selected ? ' selected' : ''}>${escapeHtml(id)}</option>`;
  sel.innerHTML = html;
}

function _headlineCell(r) {
  // one number per store: hit_rate when this build grades it, else the first metric
  const parts = [];
  for (const [name, m] of Object.entries(r.headline || {})) {
    const k = ('hit_rate' in m) ? 'hit_rate' : Object.keys(m)[0];
    if (k) parts.push(`${escapeHtml(name)} <b>${Number(m[k]).toFixed(3)}</b>`);
  }
  return parts.join(' \u00b7 ') || '<span class="muted">-</span>';
}

function _runTitle(r) {
  const when = String(r.finished_at || '').replace('T', ' ').slice(0, 16);
  const src = r.bundle ? `${r.bundle} \u00b7 ${r.config_name || '?'}${r.config_edited ? ' (edited)' : ''}`
            : (r.config_name || r.project_name || 'uploaded config');
  return `${r.id} - ${r.kind} \u00b7 ${when} \u00b7 ${src}${r.origin === 'imported' ? ' [baseline]' : ''}`;
}

// The group a run belongs to: its bundle and config (the before beside the
// after), or the uploaded config's project when it came through the editor.
function _groupKey(r) {
  if (r.bundle) return `${r.bundle} \u00b7 ${r.config_name || '?'}${r.config_edited ? ' (edited)' : ''}`;
  return r.config_name ? r.config_name : `uploaded \u00b7 ${r.project_name || '?'}`;
}

function renderRuns() {
  const body = document.getElementById('runs-body');
  if (!body) return;
  const cmp = document.getElementById('runs-compare');
  if (cmp) cmp.disabled = runPick.length !== 2;
  if (!currentRuns.length) {
    body.innerHTML = '<div class="empty">no runs kept yet - every Evaluate and Regrades lands here</div>';
    return;
  }
  if (!document.getElementById('runs-group')?.checked) {
    body.innerHTML = _runsTable(currentRuns);
    return;
  }
  // GROUPS: one block per bundle \u00b7 config, newest group first (the runs come
  // newest first, so first appearance is the order). A group lends its latest
  // run to A or B with one click, so before \u2192 after is two clicks and Compare;
  // and a group with two or more runs offers previous \u2192 latest directly.
  const groups = new Map();
  for (const r of currentRuns) {
    const k = _groupKey(r);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  let html = '';
  for (const [k, rs] of groups) {
    const latest = rs[0], prev = rs[1];
    html += `<div class="run-group-head"><b>${escapeHtml(k)}</b> <span class="muted">${rs.length} run${rs.length === 1 ? '' : 's'}</span>`
          + ` <button class="btn-sm" onclick="pickLatest('${escapeHtml(latest.id)}', 0)" title="this config's latest run as A">Latest as A</button>`
          + ` <button class="btn-sm" onclick="pickLatest('${escapeHtml(latest.id)}', 1)" title="this config's latest run as B">Latest as B</button>`
          + (prev ? ` <button class="btn-sm" onclick="pickPair('${escapeHtml(prev.id)}', '${escapeHtml(latest.id)}')" title="the previous run as A, the latest as B, compared">Compare previous to latest</button>` : '')
          + '</div>';
    html += _runsTable(rs);
  }
  body.innerHTML = html;
}

function pickLatest(id, slot) {
  const rest = runPick.filter(x => x !== id);
  runPick = slot === 0 ? [id].concat(rest).slice(0, 2) : rest.slice(0, 1).concat([id]);
  renderRuns();
}

function pickPair(a, b) {
  runPick = [a, b];
  renderRuns();
  compareRuns();
}

function _runsTable(rows) {
  let html = '<table class="store-table runs-table"><tr><th title="check two to compare: first A, then B">A/B</th>'
           + '<th>When</th><th>Kind</th><th>Config</th><th>Questions</th><th>Headline</th><th>Took</th><th></th></tr>';
  for (const r of rows) {
    const pick = runPick.indexOf(r.id);
    const tag = pick < 0 ? '' : (pick === 0 ? 'A' : 'B');
    const when = String(r.finished_at || '').replace('T', ' ').slice(0, 16);
    const cfg = r.bundle ? `${escapeHtml(r.bundle)} \u00b7 ${escapeHtml(r.config_name || '?')}${r.config_edited ? ' (edited)' : ''}`
              : (r.config_name ? escapeHtml(r.config_name) : `<span class="muted">${escapeHtml(r.project_name || 'uploaded')}</span>`);
    html += `<tr class="${tag ? 'picked' : ''}">`
          + `<td><label class="pick"><input type="checkbox" ${pick >= 0 ? 'checked' : ''} onchange="pickRun('${escapeHtml(r.id)}', this.checked)"/> ${tag}</label></td>`
          + `<td><a href="#" class="run-link" onclick="openRun('${escapeHtml(r.id)}'); return false;" title="${escapeHtml(r.id)}">${escapeHtml(when)}</a>`
          + `<div class="muted mono">${escapeHtml(r.id)}</div></td>`
          + `<td>${escapeHtml(r.kind)}${r.knobs && r.knobs.mode ? ` <span class="muted">${escapeHtml(r.knobs.mode)}</span>` : ''}`
          + (r.origin === 'imported' ? ` <span class="catch-tag" title="came with bundle ${escapeHtml(r.imported_from || '')} as a baseline${r.imported_at ? ', imported ' + escapeHtml(String(r.imported_at).replace('T', ' ').slice(0, 16)) : ''}">baseline</span>` : '') + '</td>'
          + `<td>${cfg}</td>`
          + `<td title="question hash ${escapeHtml(r.question_hash || '')}">${r.question_count ?? '?'} <span class="muted mono">${escapeHtml(String(r.question_hash || '').slice(0, 8))}</span></td>`
          + `<td>${_headlineCell(r)}</td>`
          + `<td class="muted">${r.duration_seconds != null ? r.duration_seconds + 's' : '-'}</td>`
          + `<td><button class="btn-sm icon-btn danger" onclick="deleteRun('${escapeHtml(r.id)}')" title="Delete this run" aria-label="Delete run ${escapeHtml(r.id)}">${_icon('trash')}</button></td></tr>`;
  }
  return html + '</table>';
}

function pickRun(id, on) {
  runPick = runPick.filter(x => x !== id);
  if (on) runPick.push(id);
  if (runPick.length > 2) runPick = runPick.slice(-2);
  renderRuns();
}

async function deleteRun(id) {
  if (!confirm(`Delete run ${id}? Its numbers are gone for good.`)) return;
  try {
    const r = await authFetch(`/eval/runs/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!r.ok) { alert(`delete failed (HTTP ${r.status})`); return; }
    refreshRuns();
  } catch (e) {
    alert('delete failed: ' + (e.message || e));
  }
}

function _num(v) {
  if (v == null || v === '') return '<span class="muted">-</span>';
  return Number.isInteger(v) ? String(v) : Number(v).toFixed(3);
}

function _delta(d) {
  if (d == null) return '<span class="muted">-</span>';
  const n = Number(d);
  const cls = n > 1e-9 ? 'up' : (n < -1e-9 ? 'down' : '');
  return `<span class="delta ${cls}">${n > 0 ? '+' : ''}${n.toFixed(3)}</span>`;
}

function _tagTable(byTag, open) {
  const stores = Object.keys(byTag || {});
  if (!stores.length) return '';
  const tags = [...new Set(stores.flatMap(s => Object.keys(byTag[s])))].sort();
  let html = `<details${open ? ' open' : ''}><summary><b>By question tag</b> <span class="muted">share found within k / questions carrying the tag</span></summary>`;
  html += '<table class="store-table"><tr><th>Tag</th>' + stores.map(s => `<th>${escapeHtml(s)}</th>`).join('') + '</tr>';
  for (const t of tags) {
    html += `<tr><td>${escapeHtml(t)}</td>` + stores.map(s => {
      const x = byTag[s][t];
      return x ? `<td>${Number(x.within_k).toFixed(3)} <span class="muted">/${x.n}</span></td>` : '<td class="muted">-</td>';
    }).join('') + '</tr>';
  }
  return html + '</table></details>';
}

async function openRun(id) {
  const host = document.getElementById('compare-body');
  if (!host) return;
  host.innerHTML = '<div class="loading">loading\u2026</div>';
  try {
    const r = await api(`/eval/runs/${encodeURIComponent(id)}`);
    let html = `<div class="panel-hr"></div><h3 class="run-title">${escapeHtml(_runTitle(r))}</h3>`;
    const meta = [];
    if (r.probe_version) meta.push(`probe ${r.probe_version}`);
    for (const [k, v] of Object.entries(r.versions || {})) meta.push(`${k} ${v}`);
    if (r.fingerprint) meta.push(`fingerprint ${String(r.fingerprint).slice(0, 12)}`);
    if (r.seeded_at) meta.push(`seeded ${String(r.seeded_at).replace('T', ' ').slice(0, 16)}`);
    if (r.question_hash) meta.push(`questions ${String(r.question_hash).slice(0, 12)} (${r.question_count ?? '?'})`);
    if (r.note) meta.push(r.note);
    html += `<div class="muted">${escapeHtml(meta.join(' \u00b7 '))}</div>`;
    const stores = Object.keys(r.headline || {});
    const metrics = [...new Set(stores.flatMap(s => Object.keys(r.headline[s])))].sort();
    if (stores.length) {
      html += '<table class="store-table"><tr><th>Metric</th>' + stores.map(s => `<th>${escapeHtml(s)}</th>`).join('') + '</tr>';
      for (const m of metrics) html += `<tr><td>${escapeHtml(m)}</td>` + stores.map(s => `<td>${_num(r.headline[s][m])}</td>`).join('') + '</tr>';
      html += '</table>';
    }
    html += _tagTable(r.by_tag, false);
    host.innerHTML = html;
  } catch (e) {
    host.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

async function compareRuns() {
  if (runPick.length !== 2) return;
  const host = document.getElementById('compare-body');
  if (!host) return;
  host.innerHTML = '<div class="loading">comparing\u2026</div>';
  try {
    const c = await api(`/eval/compare?a=${encodeURIComponent(runPick[0])}&b=${encodeURIComponent(runPick[1])}`);
    currentCompare = c;
    host.innerHTML = renderCompare(c);
  } catch (e) {
    host.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message || e)}</div>`;
  }
}

function renderCompare(c) {
  let html = '<div class="panel-hr"></div>';
  html += '<div class="copy-row"><button class="btn-sm copy-btn" onclick="copyCompare(this)">Copy compare</button></div>';
  html += `<h3 class="run-title">A: ${escapeHtml(_runTitle(c.a))}</h3><h3 class="run-title">B: ${escapeHtml(_runTitle(c.b))}</h3>`;
  if (!c.same_questions) {
    html += '<div class="note err"><span class="label">\u26A0 different question sets:</span> these runs were graded on '
          + 'different questions (the hashes differ), so the deltas describe two experiments, not one change. '
          + '<span class="muted">Editing the questions starts a new lineage on purpose.</span></div>';
  }
  const moved = c.moved || [];
  html += '<div class="note"><span class="label">movers:</span> '
        + (moved.length ? moved.slice(0, 8).map(m => `${escapeHtml(m.store)} ${escapeHtml(m.metric)} ${_delta(m.delta)}`).join(' \u00b7 ')
                        : '<span class="muted">nothing moved</span>') + '</div>';
  for (const [store, row] of Object.entries(c.stores || {})) {
    const metrics = Object.keys(row).sort();
    if (!metrics.length) continue;
    html += `<h4 class="run-store">${escapeHtml(store)}</h4><table class="store-table compare-table"><tr><th>Metric</th><th>A</th><th>B</th><th>\u0394</th></tr>`;
    for (const m of metrics) html += `<tr><td>${escapeHtml(m)}</td><td>${_num(row[m].a)}</td><td>${_num(row[m].b)}</td><td>${_delta(row[m].delta)}</td></tr>`;
    html += '</table>';
  }
  const tagStores = Object.keys(c.by_tag || {});
  if (tagStores.length) {
    html += '<details open><summary><b>By question tag</b> <span class="muted">share found within k, A \u2192 B, biggest movers first</span></summary>';
    for (const store of tagStores) {
      const row = c.by_tag[store];
      const tags = Object.keys(row).sort((x, y) => Math.abs(row[y].delta) - Math.abs(row[x].delta));
      html += `<h4 class="run-store">${escapeHtml(store)}</h4><table class="store-table compare-table"><tr><th>Tag</th><th>n</th><th>A</th><th>B</th><th>\u0394</th></tr>`;
      for (const t of tags) {
        const x = row[t];
        const n = x.n_a === x.n_b ? String(x.n_a) : `${x.n_a}\u2192${x.n_b}`;
        html += `<tr><td>${escapeHtml(t)}</td><td class="muted">${n}</td><td>${_num(x.a)}</td><td>${_num(x.b)}</td><td>${_delta(x.delta)}</td></tr>`;
      }
      html += '</table>';
    }
    html += '</details>';
  }
  return html;
}

function compareMarkdown(c) {
  const sign = (d) => (d > 0 ? '+' : '') + Number(d).toFixed(3);
  const lines = [`# Compare ${c.a.id} \u2192 ${c.b.id}`, '', `- A: ${_runTitle(c.a)}`, `- B: ${_runTitle(c.b)}`,
                 `- same questions: ${c.same_questions ? 'yes' : 'NO (different hashes)'}`, ''];
  for (const [store, row] of Object.entries(c.stores || {})) {
    lines.push(`## ${store}`, '');
    lines.push(mdTable(['metric', 'A', 'B', '\u0394'], Object.keys(row).sort().map(m =>
      [m, Number(row[m].a).toFixed(3), Number(row[m].b).toFixed(3), sign(row[m].delta)])));
    lines.push('');
    const tags = (c.by_tag || {})[store];
    if (tags) {
      lines.push(`### ${store} by tag`, '');
      lines.push(mdTable(['tag', 'n', 'A', 'B', '\u0394'], Object.keys(tags).sort().map(t =>
        [t, String(tags[t].n_b), Number(tags[t].a).toFixed(3), Number(tags[t].b).toFixed(3), sign(tags[t].delta)])));
      lines.push('');
    }
  }
  return lines.join('\n');
}

function copyCompare(btn) {
  if (currentCompare) copyToClipboard(compareMarkdown(currentCompare), btn);
}

// Line icons for the icon buttons (22 Oct 2026: play, export, trash), drawn
// inline in currentColor like Lodestar's power symbol, so they follow the
// theme and never fall back to an emoji font.
function _icon(name) {
  const paths = {
    play: '<path d="M7 4.5v15l12.5-7.5z" fill="currentColor" stroke="none"/>',
    export: '<path d="M12 15V3.5"/><path d="M7.5 8L12 3.5 16.5 8"/><path d="M4.5 13.5v5.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-5.5"/>',
    trash: '<path d="M4 6.5h16"/><path d="M9.5 6.5V4.5h5v2"/><path d="M6.5 6.5l1 13a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4l1-13"/><path d="M10 10.5v6.5M14 10.5v6.5"/>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" `
       + `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths[name] || ''}</svg>`;
}
