"""The layer that turns provider answers into something a model can reason with.

`core/` produces facts. This produces *context*: the facts assembled, labelled
with how much they should be trusted, and stripped of the fields a model has no
use for. The brief's instruction — "do not simply return raw API responses to
the LLM" — is this package's entire reason to exist.
"""

from __future__ import annotations

__all__ = ["context"]
