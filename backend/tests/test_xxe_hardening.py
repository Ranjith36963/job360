"""M18 — feed/ATS XML parsers must be XXE-safe (defusedxml, not stdlib ET).

Every source that parses untrusted remote XML routes it through defusedxml, so
entity-expansion / external-entity (XXE) payloads are rejected at parse time
instead of being processed. Bandit B314 flagged the stdlib `ET.fromstring`
calls; this locks in the defusedxml replacement so they can't regress.
"""

import importlib

import pytest
from defusedxml.common import EntitiesForbidden

# Each of these modules parses untrusted feed XML and must import the
# defusedxml-backed `_safe_fromstring` alias (M18).
_XML_SOURCE_MODULES = [
    "src.sources.ats.personio",
    "src.sources.ats.successfactors",
    "src.sources.feeds.nhs_jobs",
    "src.sources.feeds.realworkfromanywhere",
    "src.sources.feeds.uni_jobs",
    "src.sources.feeds.weworkremotely",
    # biospace / jobs_ac_uk / nhs_jobs_xml / workanywhere were removed
    # 2026-08-10 with their dead upstreams.
]

# A DOCTYPE that declares an entity — the simplest XXE / billion-laughs vector.
# defusedxml forbids entity declarations by default and raises EntitiesForbidden;
# stdlib xml.etree would happily process it.
_XXE_PAYLOAD = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE lolz [<!ENTITY lol "lol">]>'
    "<rss><channel><title>&lol;</title></channel></rss>"
)


@pytest.mark.parametrize("module_path", _XML_SOURCE_MODULES)
def test_source_uses_defusedxml_parser(module_path):
    """Each XML source exposes the defusedxml-backed `_safe_fromstring` alias."""
    mod = importlib.import_module(module_path)
    assert hasattr(mod, "_safe_fromstring"), (
        f"{module_path} must parse untrusted XML via defusedxml (_safe_fromstring), "
        "not stdlib xml.etree.ElementTree.fromstring"
    )


@pytest.mark.parametrize("module_path", _XML_SOURCE_MODULES)
def test_source_parser_rejects_entity_expansion(module_path):
    """An ENTITY-declaration payload is rejected (XXE-safe), not expanded."""
    mod = importlib.import_module(module_path)
    with pytest.raises(EntitiesForbidden):
        mod._safe_fromstring(_XXE_PAYLOAD)
