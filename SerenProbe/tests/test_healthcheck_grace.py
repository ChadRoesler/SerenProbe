"""A vector Loci downloads its embedder before it serves anything. Without a
start_period, compose counts that download as failure and kills the container -
which then comes up perfectly healthy, moments too late. Observed live on
mycelium-loci-v: /health answering in 0ms while compose insisted it was unhealthy."""
from seren_probe.core.topology import compile_topology
from seren_probe.core.topology_emit import emit_compose


def _emit(vector: bool):
    flags = ["vector"] if vector else []
    t = compile_topology({"ProbeConfig": {"StartingPort": 7600,
        "Corpus": {"CorpusCount": 1, "CorpusConfigs": [
            {"Name": "c", "Port": 7604, "Stores": [{"Store": "l"}, {"Store": "m"}]}]},
        "Loci": {"LociCount": 1, "LociConfigs": [
            {"Name": "l", "Port": 7601, "Flags": flags}]},
        "Memory": {"MemoryCount": 1, "MemoryConfigs": [{"Name": "m", "Port": 7600}]}}})
    return emit_compose(t).compose["services"]


def test_vector_loci_gets_a_long_start_period():
    svc = _emit(vector=True)["l"]
    assert svc["healthcheck"]["start_period"] == "300s"
    assert "SEREN_LOCI_EMBEDDING_MODEL" in svc["environment"]   # it IS the vector node


def test_plain_loci_gets_the_short_one():
    svc = _emit(vector=False)["l"]
    assert svc["healthcheck"]["start_period"] == "20s"
    assert "SEREN_LOCI_EMBEDDING_MODEL" not in svc["environment"]


def test_every_service_has_a_start_period_at_all():
    """The bug was its total ABSENCE - the clock started the instant the
    container did. Never ship a healthcheck without a grace window."""
    for name, svc in _emit(vector=True).items():
        assert "start_period" in svc["healthcheck"], f"{name} has no start_period"
