✓ active - 11 loci · 11 memory · 11 corpus

✗ QUESTION LINT - 1 unanswerable of 457:
✗ [Pilorus-loci, Pilorus-scc] questions[2] (loci) 'How long is the recorded history of Pilorus?': expect_key '*/world_year_span' is AMBIGUOUS - 230 other seed documents match this query at least as well (query-term overlap 1). The expectation exists and is reachable, but the query cannot SINGLE IT OUT: it names a category, and the answer key names one member of it. hit_rate@10 is capped below 1 no matter how good the store is. This scores exactly like a dead store - it is a dataset defect. Add a term that only the intended document carries.
⚠ [Nazgundvorn-scc] questions[22] (corpus) 'What is the favorite color of Nazgundvorn, and what does Nazgundvorn recall about need to return a favor?': expect_ref 'nazgundvorn_784_near_0' is CROWDED - 10 other documents tie it on query-term overlap (1). Still reachable within k=10, but the ranking is a coin-flip between them and the score will look noisy.

✗ 2 AMBIGUOUS -- answer EXISTS and is REACHABLE, but the query can't single it out:
✗  "*/world_year_span" (expect_key) -- 230 other docs match this query just as well -- How long is the recorded history of Pilorus?
✗  "*/world_year_span" (expect_key) -- 230 other docs match this query just as well -- How long is the recorded history of Pilorus?
✗  The store will retrieve CORRECTLY and still score near zero. On the dashboard that is
✗  indistinguishable from a dead store, a broken embedder, and a missing hop.
✗  Do NOT tune anything. The query names a category; the answer key names one member of it.
✗  Add a term only the intended document carries.