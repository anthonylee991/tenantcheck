"""A `Target` bundles how to reach *some* app shape: how to make two accounts,
and how to discover a record one of them owns. The scanner is generic over
`Target`; each app shape (control twin, Juice Shop, a real submission) is one.

Keeping this an explicit seam is what stops the prober from being welded to the
control app it was first written against.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .accounts import provision_two
from .discovery import discover_invoices
from .http import Client
from .models import ResourceRef, Session

ProvisionFn = Callable[[Client], Awaitable[tuple[Session, Session]]]
DiscoverFn = Callable[[Client, Session], Awaitable[list[ResourceRef]]]


@dataclass(frozen=True)
class Target:
    name: str
    provision: ProvisionFn
    discover: DiscoverFn


def control_target(name: str = "control") -> Target:
    """The reference app shape: /signup + /api/invoices."""
    return Target(name=name, provision=provision_two, discover=discover_invoices)
