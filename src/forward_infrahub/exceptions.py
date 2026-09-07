"""Errors the adapter raises on its own behalf."""

from __future__ import annotations

from forward_sdk import ForwardError


class ForwardInfrahubError(ForwardError):
    """Base for errors raised by the adapter rather than by Forward."""


class ForwardLoadError(ForwardInfrahubError):
    """A table could not be loaded, so the whole run is abandoned.

    A partial load would look to DiffSync like the missing rows were deleted
    upstream, so there is no safe way to continue.
    """


class ForwardSafetyError(ForwardInfrahubError):
    """A safety gate refused the run (snapshot completeness, query audit, scope)."""


class ForwardIncrementalError(ForwardInfrahubError):
    """An incremental run could not establish a trustworthy baseline.

    Never downgraded to a silent full load: a diff that quietly becomes a full
    query hides the fact that the baseline was wrong.
    """
