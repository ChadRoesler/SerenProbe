"""
seren_probe.knob_caps
=====================
Capability guard for regrade knobs.

A knob the SCC IGNORES produces identical rows for every combo. On the dashboard
that is indistinguishable from a real ceiling - the exact ambiguity that cost
hours on the mycelium set, where inert knobs were the CORRECT result and the
ceiling was retrieval. So the harness refuses to sweep a knob it cannot confirm
the SCC implements. Fail loud; never emit a flatline that reads as a finding.
"""
from __future__ import annotations

from .topology import KNOBS_NEEDING_CAPABILITY


class KnobUnsupported(Exception):
    """The SCC can't do a knob we were told to sweep. We refuse to sweep it anyway.

    Sweeping a knob the SCC ignores produces IDENTICAL rows for every combo - an
    inert knob, which is indistinguishable on the dashboard from a real ceiling.
    That ambiguity is a lie the harness would be telling. Fail loud instead.
    """


def assert_knobs_supported(info: dict, knobs) -> None:
    """Refuse to sweep a knob the SCC doesn't advertise.

    An SCC that publishes `supported_knobs` in GET /stores is checked against it.
    An SCC that publishes nothing is trusted for the BASELINE knobs (they've always
    worked) but NOT for capability knobs like `hops` - for those, silence means
    'not implemented', and a silent inert sweep is exactly the failure mode we
    refuse to ship.
    """
    advertised = info.get("supported_knobs") if isinstance(info, dict) else None
    wanted = [k for k in knobs if k in KNOBS_NEEDING_CAPABILITY]
    if not wanted:
        return
    if advertised is None:
        raise KnobUnsupported(
            f"this SCC does not advertise `supported_knobs` in GET /stores, so it cannot "
            f"confirm it implements {wanted}. Sweeping anyway would produce identical rows "
            f"for every combo - an INERT knob that looks exactly like a real retrieval "
            f"ceiling. Upgrade the SCC (it must advertise the knob), or drop {wanted} from "
            f"CorpusRegrades.")
    missing = [k for k in wanted if k not in set(advertised)]
    if missing:
        raise KnobUnsupported(
            f"SCC advertises {sorted(advertised)} but the regrade sweeps {missing}, which it "
            f"does NOT implement. Refusing to sweep - the rows would be identical and you'd "
            f"read it as a ceiling. See docs/SCC-MULTIHOP.md.")
