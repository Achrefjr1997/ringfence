"""Billing provider seam (T-7.3).

The gateway meters usage locally (``BillingStore``); a provider mirrors it
to whoever actually invoices.  ``NullBilling`` is the default -- metering
runs, nothing is sent anywhere.  ``StripeBilling`` (Stripe Meters API,
gated on ``RF_STRIPE_SECRET_KEY``) lands in a follow-up and slots in here
without touching the call sites.
"""

from __future__ import annotations

import logging
from typing import Protocol

from packages.billing.meter import Metric

log = logging.getLogger("ringfence.billing")


class BillingProvider(Protocol):
    def ensure_customer(self, tenant: str, *, email: str, name: str) -> str | None:
        """Return the provider's customer id, creating it if needed."""
        ...

    def report_usage(self, tenant: str, metric: Metric, quantity: float, *, ts: float) -> None: ...

    def portal_url(self, tenant: str) -> str | None:
        """A link the tenant admin can use to manage payment / see invoices."""
        ...


class NullBilling:
    """No external billing.  Local metering still runs."""

    def ensure_customer(self, tenant: str, *, email: str, name: str) -> str | None:
        return None

    def report_usage(self, tenant: str, metric: Metric, quantity: float, *, ts: float) -> None:
        log.debug(
            "usage (not reported: no billing provider)", extra={"tenant": tenant, "metric": metric}
        )

    def portal_url(self, tenant: str) -> str | None:
        return None
