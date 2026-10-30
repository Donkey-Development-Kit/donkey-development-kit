"""``classify``: the refusal classifier, and the registry that lets a customer's
own gateway policy become a typed exception (BG §2.2, #210).

A custom (PDK) policy emits its own refusal shape, which the built-in rules in
``core/errors`` cannot know. ``classify.register(match=..., raises=...)`` maps
one such shape to a :class:`~donkey_kit.core.errors.PolicyViolation` subclass
that carries the customer's own remediation. Nothing is enforced client-side:
the gateway still decides, and the SDK only names the decision.

The rules that keep a registration honest are checked at ``register()`` time,
never deferred to the first refusal:

* the match names a 4xx ``status`` (not 401, always auth; not 429, always a
  rate limit) and at least one discriminator;
* the minimal response the spec describes (:meth:`PolicyRegistration.example`)
  is run through the built-in rules, and a spec one of them claims is rejected
  by name: a verified shape always wins, so it could never match;
* a spec discriminated only by a captured upstream provider error value is
  rejected, since it would turn a provider mistake into a policy refusal;
* the exception defines its own remediation (BG §1.2) and a slug that no
  built-in ``donkey.policy.type`` value uses.

The registry is process-wide because ``classify()`` runs without a ``Donkey``
(in the transport, in ``classify(err.response)`` bridging, in ``donkey
doctor``) and every path must classify the same way. It is an immutable tuple
replaced on each change, so the hot path takes no lock. ``core/errors`` reaches
it through a one-slot hook this module fills at import, after every built-in
policy rule and before the upstream-envelope rule.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from ._policy_spec import POLICY_SLUGS, PolicyRegistration, condition_key, holds, validated
from ._response import ResponseLike
from .errors import (
    _REGISTERED_MATCHER,
    DonkeyError,
    PolicyViolation,
    _Shape,
)
from .errors import classify as _classify_response

__all__ = ["Classifier", "PolicyRegistration", "classify", "policy_type_slug"]

_log = logging.getLogger(__name__)


def policy_type_slug(error: DonkeyError) -> str | None:
    """The :data:`~donkey_kit.core.telemetry.DONKEY_POLICY_TYPE` value for a
    classified refusal, or ``None`` for a non-policy error (auth / upstream /
    transport) that carries no governance allow-or-refuse decision.

    A registered type records its slug verbatim (#210), e.g.
    ``"acme_salary_block"``, so spans and gateway logs share one string."""
    if isinstance(error, PolicyViolation) and any(
        reg.raises is type(error) and reg.policy == error.policy for reg in _registry
    ):
        return error.policy
    for kind, slug in POLICY_SLUGS:
        if isinstance(error, kind):
            return slug
    return None


class Classifier:
    """``classify``: call it to map an error response to a typed
    :class:`~donkey_kit.core.errors.DonkeyError`; register a custom policy's
    refusal shape on it (BG §2.2, #210).

    ``donkey_kit.classify`` and ``Donkey.classify`` are the one process-wide
    instance, so a registration applies to every client, including clients
    built before it. ``classify(response)`` is unchanged.

    Docs: https://docs.donkey-kit.dev/custom-policies
    """

    def __call__(
        self,
        response: ResponseLike,
        *,
        correlation_id: str | None = None,
        call_id: str | None = None,
    ) -> DonkeyError:
        """Map an HTTP error response to a specific exception; see
        :func:`donkey_kit.core.errors.classify`."""
        return _classify_response(response, correlation_id=correlation_id, call_id=call_id)

    def register(
        self, *, match: Mapping[str, object], raises: type[PolicyViolation]
    ) -> PolicyRegistration:
        """Type a custom policy's refusal as ``raises``.

        Args:
            match: ``status`` (a 4xx other than 401 and 429) plus at least one
                of ``error_type`` (the nested error ``type``, or Gemini's
                ``status``), ``error_code`` (the nested ``code``, as a string),
                ``headers`` (``{name: exact value}``, names case-insensitive)
                and ``body`` (``{dotted.path: str | int | bool |
                re.Pattern}``). Every condition must hold.
            raises: A :class:`PolicyViolation` subclass that defines its own
                ``remediation``. Its ``policy`` is the slug; when it sets none,
                the ``error_type`` or ``error_code`` value is.

        Returns:
            The stored :class:`PolicyRegistration`.

        Raises:
            TypeError: An unknown ``match`` key, a value of the wrong type, or a
                ``raises`` that is not registrable.
            ValueError: The spec is a status-only, shadowed, contradictory,
                provider-value-only or duplicate match, or its slug is invalid,
                built-in or used by another class.
        """
        global _registry
        reg = validated(match, raises)
        for existing in _registry:
            if condition_key(existing) == condition_key(reg):
                raise ValueError(
                    f"Duplicate: {existing.raises.__name__} is already registered "
                    "for this exact match."
                )
            if existing.policy == reg.policy and existing.raises is not raises:
                raise ValueError(
                    f"Slug {reg.policy!r} is already used by {existing.raises.__name__}; "
                    "set a distinct policy on the class so donkey.policy.type "
                    "names one type."
                )
        _registry = (*_registry, reg)
        return reg

    def unregister(self, raises: type[PolicyViolation]) -> tuple[PolicyRegistration, ...]:
        """Remove every registration that raises ``raises``; returns the removed
        entries (empty when there were none)."""
        global _registry
        removed = tuple(reg for reg in _registry if reg.raises is raises)
        _registry = tuple(reg for reg in _registry if reg.raises is not raises)
        return removed

    def registered(self) -> tuple[PolicyRegistration, ...]:
        """The current registrations, oldest first, as frozen value objects."""
        return _registry

    def __repr__(self) -> str:
        return f"<donkey_kit.classify: {len(_registry)} registered>"


#: The process-wide classifier: ``donkey_kit.classify`` and ``Donkey.classify``.
classify = Classifier()

_registry: tuple[PolicyRegistration, ...] = ()
# Tie sets already logged, so an ambiguous shape warns once, not per refusal.
_reported_ties: set[frozenset[PolicyRegistration]] = set()


def _restore(registrations: tuple[PolicyRegistration, ...]) -> None:
    """Replace the registry wholesale: ``donkey_kit._testing`` snapshot/restore."""
    global _registry
    _registry = registrations
    _reported_ties.clear()


def _reset_for_tests() -> None:
    """Empty the registry (``tests/conftest.py``, #750)."""
    _restore(())


def _match_registered(s: _Shape) -> DonkeyError | None:
    """The registered shape with the most conditions that all hold on ``s``. A
    tie is never settled by registration order: it is the honest generic
    refusal, naming the competitors, and is logged once."""
    hits = [reg for reg in _registry if holds(reg, s)]
    if not hits:
        return None
    best = max(reg.conditions for reg in hits)
    top = [reg for reg in hits if reg.conditions == best]
    if len(top) == 1:
        reg = top[0]
        message = f"Request refused by custom policy {reg.policy} ({s.status})."
        return reg.raises(message, policy=reg.policy, **s.kw)
    names = ", ".join(f"{reg.raises.__name__} ({reg.policy})" for reg in top)
    tie = frozenset(top)
    if tie not in _reported_ties:
        _reported_ties.add(tie)
        _log.warning(
            "classify: %d registrations match a %d refusal with %d conditions each: %s",
            len(top),
            s.status,
            best,
            names,
        )
    return PolicyViolation(
        f"Request refused by a custom policy; {len(top)} registrations match it "
        f"equally ({s.status}).",
        policy="unknown",
        remediation=(
            f"These classify.register() entries match this refusal with {best} "
            f"condition(s) each: {names}. A tie is never settled by registration "
            "order. Add a condition to the one that should win, or unregister the other."
        ),
        **s.kw,
    )


_REGISTERED_MATCHER[:] = [_match_registered]
