"""Parameter inequality constraints for MESH calibration.

This module declares inequality constraints that must hold between pairs
of MESH parameters during calibration (for example, ``LAMN <= LAMX``).
Constraints are grouped by parameter group (``class``, ``hydrology``,
...) to match the shape of :pyattr:`~fiatmodel.models.mesh.model.MESH.
parameter_bounds`.

Each entry under a group maps an **inequality expression string** to an
options dictionary. The expression has the form ``"<lhs> <op> <rhs>"``
where ``<op>`` is one of ``<``, ``<=``, ``>``, ``>=`` and both sides are
parameter names that appear in MESH's ``.ini`` files
(case-insensitive).

FIAT normalizes every expression to the canonical ordered pair
``lower <= upper`` at :meth:`~fiatmodel.models.mesh.model.MESH.prepare`
time and emits:

* an Ostrich ``BeginTiedParams`` clamp chain that substitutes
  ``max(lower, upper)`` into MESH inputs whenever both parameters are
  being calibrated, so MESH is never asked to run on an infeasible
  pair; and
* an Ostrich ``BeginTiedRespVars`` / ``BeginConstraints`` entry that
  adds a GCOP ``APM`` penalty proportional to the violation magnitude,
  providing a gradient back toward the feasible region.

When only one side of a constraint is calibrated, FIAT cross-checks the
calibration bounds against the fixed value read from the ``.ini`` and
raises :class:`ValueError` if the pair can never be feasible. When
neither side is calibrated, the constraint is a no-op.

To add a new constraint, append an entry under the appropriate group:

.. code-block:: python

    CONSTRAINTS = {
        'class': {
            'lamn <= lamx': {'cost_factor': 1.0e6, 'clamp': True},
            # 'rsmn <= rsmx': {'cost_factor': 1.0e6, 'clamp': True},
        },
    }
"""
from typing import Dict, Iterator, Tuple, Any


# Inequality constraints declared per parameter group.
#
# Keys are parameter groups (``class``, ``hydrology``, ``routing``,
# ...). Values are dicts whose keys are inequality expressions (strings)
# and whose values are option dicts with the following recognized keys:
#
#   cost_factor : float
#       Weight applied by the GCOP ``APM`` penalty function to the
#       violation magnitude. Larger values steer the optimizer away
#       from the infeasible half-space more aggressively.
#   clamp : bool
#       When ``True`` (the default behavior used for ordered-pair
#       constraints) FIAT substitutes ``max(lower, upper)`` into MESH
#       inputs so every sample produces a well-defined model run. When
#       ``False``, only the APM penalty is emitted and MESH receives
#       the raw (possibly infeasible) values.
CONSTRAINTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    'class': {
        'lamn <= lamx': {
            'cost_factor': 1.0e6,
            'clamp': True,
        },
    },
}


# Small positive floor used when the user wrote a strict inequality
# (``<`` or ``>``). Harmless for non-strict ones, and currently not
# applied in the emitted Ostrich config because APM operates on
# non-strict bounds; reserved for future use.
STRICT_EPSILON: float = 1.0e-12


def parse_inequality(expr: str) -> Tuple[str, str, bool]:
    """Parse an inequality expression into its canonical ordered pair.

    The returned pair is always oriented as ``(lower, upper)`` such that
    the semantics are ``lower <= upper`` (or ``lower < upper`` if
    ``strict`` is ``True``).

    Parameters
    ----------
    expr : str
        Inequality expression, for example ``'lamn <= lamx'``.
        Whitespace-tolerant and case-insensitive.

    Returns
    -------
    tuple
        ``(lower, upper, strict)`` with parameter names lowercased.

    Raises
    ------
    ValueError
        If the expression cannot be parsed.
    """
    s = expr.strip().lower()
    # Try two-char operators before single-char ones so ``<=`` is not
    # mis-parsed as ``<``.
    for op in ('<=', '>=', '<', '>'):
        if op in s:
            left, _, right = s.partition(op)
            left, right = left.strip(), right.strip()
            if not left or not right:
                raise ValueError(
                    f"Unparseable constraint expression: {expr!r}"
                )
            strict = op in ('<', '>')
            if op in ('<', '<='):
                return left, right, strict
            return right, left, strict
    raise ValueError(f"Unparseable constraint expression: {expr!r}")


def iter_constraints(
    group: str,
) -> Iterator[Tuple[str, str, bool, Dict[str, Any]]]:
    """Yield canonical ``(lower, upper, strict, opts)`` for a group.

    Parameters
    ----------
    group : str
        Parameter group name (e.g. ``'class'``).

    Yields
    ------
    tuple
        ``(lower, upper, strict, opts)`` for each inequality declared
        under the given group. Yields nothing if the group has no
        entries in :data:`CONSTRAINTS`.
    """
    for expr, opts in CONSTRAINTS.get(group, {}).items():
        lower, upper, strict = parse_inequality(expr)
        yield lower, upper, strict, opts
