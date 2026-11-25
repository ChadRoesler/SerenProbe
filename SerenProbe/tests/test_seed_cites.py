"""
Loci seed items can say what they rest on (24 Nov 2026).

A dataset that wants to measure citation edges (the Callosum bringing a fact's
reasons along) has to be able to seed citations. Pinned here:

- `cites` takes a bare key (same project), a project/key, or {on, why}
- a fact citing itself, or a cites that is not a list, is an error
- seeding writes every fact first and the citations after, since a citation
  needs both ends live and the fact it rests on may come later in the file
- a citation the store refuses is a note on the seed, never a failed seed
"""
from __future__ import annotations

import pytest

from seren_probe.core.seed_dataset import SeedError, _seed_one_store, load_seed_items


def _items(tmp_path, text):
    f = tmp_path / "loci.yaml"
    f.write_text(text, encoding="utf-8")
    return load_seed_items(str(f), "loci")


def test_cites_parse_bare_crossed_and_with_a_why(tmp_path):
    [title, civ] = _items(tmp_path, """
- project: w
  key: lurg.title
  value: Sovereign
  cites: [lurg.civ, other/thok.capital, {address: w/lurg.age, why: old enough to rule}, {on: x/y.z}]
- {project: w, key: lurg.civ, value: Thoknakhkrug}
""")
    # the last one is YAML's trap: `on:` arrives as the key True, and is still understood
    assert title.cites == [("w", "lurg.civ", None), ("other", "thok.capital", None),
                           ("w", "lurg.age", "old enough to rule"), ("x", "y.z", None)]
    assert civ.cites == []


@pytest.mark.parametrize("cites,why", [("[lurg.title]", "cannot rest on itself"),
                                       ("lurg.civ", "must be a list"),
                                       ("[{why: no address}]", "needs an address")])
def test_bad_cites_are_errors(tmp_path, cites, why):
    with pytest.raises(SeedError) as e:
        _items(tmp_path, f"- {{project: w, key: lurg.title, value: Sovereign, cites: {cites}}}\n")
    assert any(why in err for err in e.value.errors)


def test_facts_land_first_then_the_citations(tmp_path):
    items = _items(tmp_path, """
- {project: w, key: lurg.title, value: Sovereign, cites: [lurg.civ]}
- {project: w, key: lurg.civ, value: Thoknakhkrug}
""")
    calls = []

    def post(url, path, body):
        calls.append((path, body.get("key"), body.get("on_key")))
        return {"ok": True}
    p = _seed_one_store("la", items, "http://loci", "loci", post)
    assert [c[0] for c in calls] == ["/fact", "/fact", "/fact/cite"]
    assert calls[-1] == ("/fact/cite", "lurg.title", "lurg.civ")
    assert p.cites == 1 and p.cites_refused == []


def test_a_refused_citation_is_a_note_not_a_failure(tmp_path):
    items = _items(tmp_path, "- {project: w, key: lurg.title, value: Sovereign, cites: [nowhere.key]}\n")

    def post(url, path, body):
        if path == "/fact/cite":
            raise RuntimeError("404 no live fact for project='w' key='nowhere.key'")
        return {"ok": True}
    p = _seed_one_store("la", items, "http://loci", "loci", post)
    assert p.count == 1 and p.cites == 0
    assert len(p.cites_refused) == 1 and "nowhere.key" in p.cites_refused[0]
