"""HELIX — OMNIX's bioinformatics domain capability.

A local corpus of real PubMed literature, a retrieval index over it, and an
answer layer that grounds every claim in a citable paper. The design goal is
that a question is answered in under a second when it can be answered from the
corpus structure alone, and in a few seconds when it needs a model — never by
asking a model to recall bioinformatics from its weights.
"""

from .topics import TOPICS, BY_KEY, Topic  # noqa: F401
