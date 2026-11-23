"""Errors the adapter raises on its own behalf."""

from __future__ import annotations

__all__ = [
    "ForwardCallFailed",
    "ForwardCallNotFound",
    "ForwardCallPagingFailed",
    "ForwardInfrahubError",
    "ForwardLoadError",
    "ForwardSafetyError",
    "InvalidConfiguration",
]


class ForwardInfrahubError(Exception):
    """Base for every error the package raises.

    It does not derive from the Forward SDK's base error: the SDK's errors are translated into
    the package's where the SDK is entered, so code catching one does not catch the other.
    """


class InvalidConfiguration(ForwardInfrahubError):
    """A mapping, setting or package that cannot be used."""


class ForwardCallFailed(ForwardInfrahubError):
    """The SDK failed a call to Forward, or refused to make one. Message and cause are the SDK's."""


class ForwardCallNotFound(ForwardCallFailed):
    """Forward has nothing at the path or id asked for."""


class ForwardCallPagingFailed(ForwardCallFailed):
    """Forward's rows could not be paged to completion, so the query may be sharded."""


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
