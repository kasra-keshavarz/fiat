.. _gcop_constraints:

Optimization constraints
========================

FIAT supports **optimization constraints** through Ostrich's General-purpose
Constrained Optimization Platform (GCOP). A constraint places bounds on a model
performance metric: whenever the metric falls outside its allowed range,
Ostrich adds a penalty to the system cost. This keeps diagnostics such as bias
within acceptable limits while the optimizer focuses on the primary objective.

Constraints are defined under ``calibration_config['constraints']`` and reuse
the same group structure as ``objective_functions``:

.. code-block:: python

   def pbias(sim, obs):
       """Percent bias (not part of HydroErr)."""
       return 100.0 * (obs - sim).sum() / obs.sum()

   calibration_config = {
       # ... other settings ...
       'objective_functions': {
           'flux': {
               'QO': {'nse': ['-1 * MY_OBS']},
           },
       },
       'constraints': {
           'flux': {
               'QO': {
                   pbias: {
                       'lower': -25.0,
                       'upper': 25.0,
                       'cost_factor': 100,
                       'expressions': ['MY_OBS'],
                   },
               },
           },
       },
   }

In this example the NSE objective is minimized while PBIAS is constrained to
the ``[-25, +25]`` percent "satisfactory" range (Moriasi et al., 2007). For a
complete runnable setup, see
``examples/wolf-creek-calibration-with-constraints.ipynb`` in the repository.

How constraints are enforced
----------------------------

Every iteration, ``eval.py`` computes each constraint metric and writes its
value to a CSV file that Ostrich reads as a *response variable*. Ostrich then
converts any violation into a penalty using the additive penalty method (APM):

.. math::

   P = \begin{cases}
   CF \times (g_{min} - g), & g < g_{min} \\
   CF \times (g - g_{max}), & g > g_{max} \\
   0, & \text{otherwise}
   \end{cases}

   F = C_{SYS} + \sum_i P_i

where :math:`g` is the response variable value, :math:`g_{min}` and
:math:`g_{max}` are the ``lower`` and ``upper`` bounds, :math:`CF` is the
``cost_factor``, :math:`C_{SYS}` is the system cost, and :math:`F` is the
penalized cost reported to the optimizer. Constraint values *inside* the
bounds add no penalty at all.

Configuration schema
--------------------

Each entry under ``constraints`` maps a group name to a flux (or state
variable) and then to a metric specification:

.. code-block:: python

   'constraints': {
       '<group>': {
           '<flux_or_state>': {
               '<metric_name>': {
                   'lower': float,
                   'upper': float,
                   'cost_factor': float,
                   'expressions': str | list[str],
                   'kwargs': dict,  # optional
               },
           },
       },
   }

- ``lower`` / ``upper``: allowed range for the metric value. Both numbers are
  passed to Ostrich as-is.
- ``cost_factor``: multiplier converting the violation magnitude into a
  penalty. Larger values make violations more expensive; ``100`` is a common
  choice for percentage-based metrics.
- ``expressions``: one expression or a list of expressions. Each expression
  becomes an **independent constraint** with its own response variable and CSV
  file, indexed from ``1``.
- ``kwargs`` *(optional)*: keyword arguments forwarded to the metric function
  as ``metric_func(simulated, observed, **kwargs)``. See
  :doc:`configuration` for the full description.

All four keys ``lower``, ``upper``, ``cost_factor``, and ``expressions`` are
required, even when their values are never used by Ostrich (as for helpers —
see below). Missing keys raise a ``ValueError`` when the ``Calibration`` object
is constructed.

The group name **must contain** one of the keywords ``flux``, ``state``,
``state_variable``, ``helper``, or ``custom``. Any other group name — including
a bare flux such as ``'QO'`` — raises a ``ValueError`` at construction time.

Constraint groups
-----------------

Standard groups: ``flux``, ``state``, ``state_variable``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

These are the groups that are actually registered with Ostrich.

- The metric name must be a metric available in the
  `HydroErr <https://hydroerr.readthedocs.io/en/stable/list_of_metrics.html#functions>`_
  package or a user-defined callable (see below).
- The metric is computed for every observation station of the flux/state
  variable.
- Each expression is a `numexpr <https://numexpr.readthedocs.io/>`_ expression
  over the station names, exactly as in ``objective_functions``. A single
  station name such as ``'MY_OBS'`` constrains that station; arithmetic over
  several names (e.g. ``'-0.5 * MY_OBS + -0.5 * OTHER_OBS'``) constrains a
  weighted combination.
- Every expression produces one ``constraint_..._<index>.csv`` file and one
  ``general`` constraint line in ``ostIn.txt``.

Use the model's output variable name exactly (typically upper case, e.g.
``QO``, ``RFF``). ``eval.py`` upper-cases the flux when naming the CSV files
while the Jinja template writes the key verbatim, so a lower-case key would
desynchronize the response variable from the file written by ``eval.py``.

Helper groups: ``helper``
^^^^^^^^^^^^^^^^^^^^^^^^^

Helper constraints compute intermediate values that are **not** sent to
Ostrich: they are not registered as response variables and no CSV is written
for them. They are useful to split a complex diagnostic into named pieces that
a later ``custom`` constraint can combine.

A helper entry behaves in one of two ways:

- If the metric name is a HydroErr metric or a user-defined callable, the
  per-station metric is computed and the value of each expression is appended
  to a list, in expression order.
- Otherwise, the entry is a **derived helper**: each expression may reference
  *previously defined* helper names (bare names, e.g. ``abs(sum(pbias))``) plus
  NumPy and Python built-ins. Helpers must therefore be defined before they are
  used; the namespace is ordered.

Helper values are lists of per-expression values (or NumPy arrays once a
derived helper has been processed), so a derived or custom expression should
aggregate them to a scalar — for example ``abs(sum(pbias))`` or
``np.abs(np.mean(pbias))`` — rather than calling ``abs(pbias)`` directly.

Because helpers never reach Ostrich, their ``lower``, ``upper``, and
``cost_factor`` values are inert. The conventional placeholder is
``'lower': -1e99, 'upper': 1e99, 'cost_factor': 1``.

.. note::

   Constraint helpers are **isolated** from objective-function helpers: the two
   ``helper`` groups cannot cross-reference each other.

Custom groups: ``custom``
^^^^^^^^^^^^^^^^^^^^^^^^^

Custom constraints are written out for Ostrich like standard constraints, but
their expressions reference constraint helpers (and previously defined custom
entries) rather than stations. As with derived helpers, names are written as
bare identifiers and are rewritten internally, and ``kwargs`` are rejected
because there is no metric function to receive them:

.. code-block:: python

   'constraints': {
       'helper': {
           'QO': {
               pbias: {
                   'lower': -1e99,
                   'upper': 1e99,
                   'cost_factor': 1,
                   'expressions': ['MY_OBS'],
               },
           },
       },
       'custom': {
           'QO': {
               'abs_pbias': {
                   'lower': 0.0,
                   'upper': 25.0,
                   'cost_factor': 100,
                   'expressions': ['abs(sum(pbias))'],
               },
           },
       },
   }

User-defined callables
----------------------

Constraint metric names may be Python callables accepting two arguments,
``(simulated, observed)``. As for objective functions, the callable's source
code is serialized into ``etc/eval/eval.json`` and reconstructed by ``eval.py``
at run time. The callable must therefore be self-contained: only NumPy is
available in its execution namespace, and it should not rely on closures or
globally defined helpers.

Callables are useful for diagnostics that HydroErr does not provide, such as
PBIAS in the example above.

Generated artifacts
-------------------

When the workload is prepared, FIAT writes:

- one ``constraint_<group>_<FLUX>_<metric>_<index>.csv`` response-variable
  entry per non-helper constraint expression in the ``BeginResponseVars``
  block of ``ostIn.txt``;
- a matching ``general`` line per expression in the ``BeginConstraints``
  block, with the configured bounds and cost factor;
- ``etc/eval/eval.json`` including the ``constraints`` definitions so
  ``eval.py`` recomputes them every iteration.

If the model output or observations are corrupted, ``eval.py`` writes a large
penalty value (``1e10``) into every objective and constraint CSV so the
offending trial is discarded by the optimizer.

Notes and limitations
---------------------

- Only the ``general`` constraint type is emitted by FIAT.
- Constraint group names are validated when ``Calibration`` is constructed;
  malformed metric specifications (missing keys, non-mapping ``kwargs``) are
  rejected then as well.
- Derived-helper and custom expressions are evaluated at run time and are not
  validated in advance: an expression referencing an unknown name raises a
  ``NameError`` and aborts that evaluation.
- Non-finite station metric values produce a warning; standard-constraint
  expressions that reference an unknown station are skipped.
- Constraint values for state variables use the same machinery as ``flux``
  groups; use whichever output name matches the model output.

.. seealso::

   The :doc:`configuration` page documents the surrounding
   ``calibration_config`` entries and per-metric ``kwargs``, and the
   ``wolf-creek-calibration-with-constraints.ipynb`` notebook under
   ``examples/`` demonstrates the full workflow.
