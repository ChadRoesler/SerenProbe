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
          renderEvalPlaceholder(ds.loci, ds.memory, ds.corpus);
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
          + `were dropped rather than shown with a caveat. Run \u25b6 Evaluate to score this one.</span></div>`;
      }
      info.textContent = 'no results for this topology';
      return;
    }
    let html = '<div class="copy-row"><button class="btn-sm copy-btn" onclick="copyEval(this)">\u{1F4CB} Copy results</button></div>';
    if (data.restored) {
      // Scored-in-this-process and read-back-off-disk are different confidence
      // levels, so the table says which it is instead of presenting a rehydrated
      // run as live. The actionable half matters more: it tells you NOT to re-run.
      html += '<div class="note"><span class="label">\u21ba restored:</span> '
            + 'these numbers were scored in an earlier session and read back from disk'
            + (data.restored_at ? ' (' + escapeHtml(String(data.restored_at).replace('T', ' ').slice(0, 16)) + ')' : '')
            + '. <span class="muted">This pod has already been evaluated - go straight to '
            + '\u2699 Regrades. Re-run \u25b6 Evaluate only if the seed or the questions changed.</span> '
            + '<button class="btn-sm" onclick="clearEvalResults()">\u2715 Clear</button></div>';
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
      html += `<tr class="${rowcls}"><td class="sname">${escapeHtml(name)}${badge}</td>`
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

function startProgressPolling() {
  stopProgressPolling();
  _progressTimer = setInterval(async () => {
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
  }, 1000);
}

function stopProgressPolling() {
  if (_progressTimer) {
    clearInterval(_progressTimer);
    _progressTimer = null;
  }
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
    stopProgressPolling();
    btn.textContent = orig;
    btn.disabled = false;
  }
}

async function runEval() {
  const btn = document.getElementById('run-eval');
  const orig = btn.textContent;
  btn.textContent = 'running…';
  btn.disabled = true;
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
      await refreshEval();
    } else {
      alert('Eval failed: ' + (data && data.error || 'unknown'));
    }
  } catch (e) {
    alert('Eval error: ' + (e.message || e));
  } finally {
    stopProgressPolling();
    btn.textContent = orig;
    btn.disabled = false;
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
    + `<button class="btn-xsm" title="capture just this corpus" onclick="runCapture('${escapeHtml(name)}')">\u{1F4F8}</button>`
    + ` <button class="btn-xsm" title="regrade just this corpus" onclick="runRegrade('${escapeHtml(name)}')">\u2699</button></span>`;
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
  let h = Object.keys(_rgResults).length
    ? '<div class="copy-row"><button class="btn-sm copy-btn" onclick="copyRegrade(this)">\u{1F4CB} Copy sweep</button></div>'
    : '';
  h += `<div class="note"><span class="label">\u2699 Regrade plan</span> - `
        + `${escapeHtml(swept + ' of ' + p.corpus_count + ' corpora')}, `
        + `${escapeHtml(String(p.total_combos || 0))} combos total. `
        + `<button class="btn-sm" onclick="runCapture()">\u{1F4F8} Capture all</button> `
        + `<span class="muted">Capture freezes each corpus's member-store candidates to disk; `
        + `\u2699 then replays pure-fusion sets from that file without touching a container. `
        + `Hops sets always run live. Per-row buttons scope either to one corpus.</span></div>`;
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
    const [plan, cap] = await Promise.all([
      api('/eval/regrade/plan'),
      api('/eval/capture/status').catch(() => null),
    ]);
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
  return '<div class="copy-row"><button class="btn-sm copy-btn" onclick="copyRegrade(this)">\u{1F4CB} Copy sweep</button></div>' + h;
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
              + `<span class="muted">\u{1F4F8} Capture again to refresh the file.</span></div>`;
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

// PLACEHOLDER ROWS. Right after docker_start/docker_adopt hand back the topology's
// store names, the Eval tab has nothing to show yet -- no seed has run, no eval has
// scored anything -- but "loading…" (or a blank table) looks like the app is stuck,
// not like the fleet just came up. Render every known store immediately with "-" in
// every column so the operator sees the shape of the topology right away; refreshEval()
// overwrites this with real numbers the moment /eval/seed or /eval/run actually finishes.
function renderEvalPlaceholder(loci, memory, corpus) {
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
  if (info) info.textContent = `${names.length} stores · not yet seeded/evaluated`;
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
      renderEvalPlaceholder(data.loci, data.memory, data.corpus);
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
        if (typeof renderEvalPlaceholder === 'function') renderEvalPlaceholder(d.loci, d.memory, d.corpus);
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
