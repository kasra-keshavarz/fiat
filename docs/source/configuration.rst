Configuration
=============

This page documents the minimum and recommended configurations required to run a
FIAT iterative testing workflow. Currently, only recipes for the MESH model and the
Ostrich optimizer programs are available, so we focus on these components for 
this introduction. 

The following details the specifications need to construct
the inputs for the `fiatmodel.core.Calibration` object.

.. note::
    The package currenlty provides Python interfaces for end users. In 
    future releases, a command-line interface (CLI) may be provided for
    convenience.

Overview
--------

To start a calibration workflow you instantiate ``Calibration`` with:

- ``calibration_software``: name of the optimizer backend (e.g., ``"ostrich"``).
  The relavant recipe for the model must be available with FIAT.
- ``model_software``: name of the hydrologic model backend (e.g., ``"mesh"``).
  The relavant recipe for the model must be available with FIAT.
- ``calibration_config``: dictionary configuring the optimizer run and
  experiment details.
- ``model_config``: dictionary configuring the model instance and parameter(s)
  search space.
- ``observations``: list of observed time series and associated metadata.

Each of these entries are specified in the following.

``calibration_config``
----------------------

This entry is generically a Python dictionary specifying configuration
for the iterative testing experiments. Typical information needed includes:

#. Configured instance of a model to be tested,
#. Optimization-specific settings such as algorithm choice, random seed,
   and stopping criteria, etc.,
#. Objective function definitions mapping observations to metrics, and
#. Time windows for iterative testing of the chosen hydrological model(s).

The following entries provide necessary information for the package. In brief:

- ``instance_path``: absolute or relative path (string) where the calibration
  experiment and working files will be generated. Must be writable and will be
  created if missing.
- ``random_seed``: integer seed used to initialize the optimizer’s pseudo-
  random sequence for reproducibility.
- ``algorithm``: string identifier used to select the optimization algorithm
  . The keyword depends on the optimization algorithms supported by the chosen
  ``calibration_software``. For example, in Ostrich, users can use
  ``"ParallelDDS"`` to selected the Parallel DDS algorithm.
- ``algorithm_specs``: dictionary of algorithm-specific settings. Keys and
  value types depend on the chosen algorithm and also optimization software;
  typical examples for the Ostrich Parallel DDS algorithm are:

  - ``PerturbationValue``: float in ``(0, 1]`` controlling parameter
    perturbation scale. The recommended value is ``0.2``.
  - ``MaxIteration``: positive integer controlling the iteration budget.
  - ``UseRandomParamValue``: No value is needed for this option so it is
    set to ``None`` to instruct Ostrich (the optimizer in this case) to 
    utilize random initial values.

.. note::
    If ``None`` is provided for an algorithm specification, no value will be
    written to the optimizer's configuration file for that key. However, the key
    will still be present in the configuration file by itself.

- ``spinup_start``: string-formatted timestamp (e.g., ``"YYYY-MM-DD HH:MM:SS"``)
  indicating the start of a spinup period prior to the calibration window(s).
  The model will be run from this date to the start of the first calibration
  date to allow for state initialization. Observations are not used during
  this period.
- ``dates``: list of one or more date range dictionaries defining calibration
  windows. Each item has:

  - ``start``: string-formatted timestamp (e.g., ``"YYYY-MM-DD HH:MM:SS"``)
  - ``end``: string-formatted timestamp (same format as ``start``)

  All observations used during calibration must cover these ranges at the
  specified sampling frequency.
- ``objective_functions``: mapping of observations to metrics. The outer keys
  correspond to observation ``name``s (e.g., ``"QO"`` for discharge in MESH). Each value is
  a dictionary mapping a metric name to a list of weighted observation terms.
  Example structure:


.. code-block:: python

    {"QO": {"kge_2012": ["-1 * alaska_72"]}}


In the objective function defined above, ``QO`` represents the observation type for
discharge in MESH for which the desired metric(s) will be computed after successful
iteration runs. 

The metric ``kge_2012`` refers to the 2012 formulation of the Kling-Gupta
Efficiency (KGE) metric. The keyword **MUST** match the supported metric names
available in the `HydroErr <https://hydroerr.readthedocs.io/en/stable/list_of_metrics.html#functions>`_ Python package.

Within the list value of the ``kge_2012`` key, each string represents a special
objective function value that will be fed to the optimizer. In this case,
``"-1 * alaska_72"`` indicates that the observation named ``"alaska_72"``
(defined in the ``observations`` entry) will be used to compute the KGE metric
and the result will be multiplied by ``-1`` to convert the maximization problem
of KGE into a minimization problem for the optimizer. In this example, the optimizer
is seleceted to be Ostrich which only supports minimization problems.

One can define one or multiple objective functions for iterative testing of 
the model. Multiple observation terms can also be combined within a metric
by adding more strings to the list value. For example:

.. code-block:: python

    {"QO":
        {
            "kge_2012": [
                "-1 * alaska_72",
                "-1 * alaska_73"
            ],
            "nse": [
                "-0.5 * alaska_72 + -0.5 * alaska_73"
            ]
        }
    }

In the example above, the KGE 2012 metric is computed for two observations
(``alaska_72`` and ``alaska_73``) separately while the NSE metric is computed
as a weighted combination of both observations (with equal weights of ``0.5``).
Therefore, 3 objective function values will be reported to the optimizer
per iteration in this case.

.. warning::

    If end user wishes to define multiple objective functions, the
    testing/optimization algorithm must be able to handle multi-objective
    problems.


``model_config``
----------------

This dictionary specifies details pertaining to model instance and parameter
bounds.

- ``instance_path``: path (string) to the model instance directory. For MESH,
  this should contain required model input files and will be used as a template
  for generated runs.
- ``parameter_bounds``: dictionary defining the search space per parameter
  group. The schema of this dictionary is dependant on the hydrological model
  of choice. For example, expected keys for the MESH model include:

  - ``"class"``: mapping MESH GRU to parameter bounds
  - ``"hydrology"``: mapping MESH GRU to parameter bounds
  - ``"routing"``: mapping MESH river class to parameter bounds

  For the ``class`` and ``hydrology`` groups, the integer keys
  are **MID values** (Mosaic Identifier) as defined in the model's
  ``MESH_parameters_CLASS.ini`` file. The MID appears on the second
  hydrology line of each GRU block (e.g., ``… 5 Temp_sub-_broa_deci_fore``
  where ``5`` is the MID). These identifiers are typically non-contiguous
  (e.g., ``1, 2, 5, 6, 8, 10, 14, …``) and directly correspond to
  the column headers printed in the GRU-dependent parameter section of
  ``MESH_parameters_hydrology.ini``. For the ``routing`` group, integer keys
  reference river class identifiers (0-based).
  For more information, refer to the `MESH model documentation <https://mesh-model.atlassian.net/wiki/spaces/USER/overview?mode=global>`_
  and the `MESHFlow workflow guide <https://mesh-workflow.readthedocs.io/en/latest/>`_.

  **Standard format (single-vegetation GRU)**

  When each GRU represents a single vegetation type, the bounds for each
  computational unit are a flat dictionary mapping parameter names to
  ``[min, max]`` pairs:

  .. code-block:: python

     "parameter_bounds": {
         "class": {
             1: {"sdep": [0.5, 4.0], "fcan": [0.1, 1.0]},
             2: {"lnz0": [-5.0, 1.0]},
         },
         "hydrology": {
             1: {"zsnl": [0.03, 0.6]},
         },
         "routing": {
             1: {"r1n": [0.001, 2.0], "r2n": [0.001, 2.0]},
         },
     }

  Here, ``1`` and ``2`` under ``"class"`` are MID values from the
  ``MESH_parameters_CLASS.ini`` file (not sequential indices). Each
  parameter name (e.g., ``"sdep"``, ``"fcan"``) maps to its lower and upper
  calibration bounds.

  **Mixed-vegetation format (multiple vegetation types per GRU)**

  A single GRU can contain multiple vegetation types (e.g., needleleaf and
  broadleaf forest coexisting in a mixed-forest tile). In this case, replace
  the flat dictionary with a **list of dictionaries**, where each dictionary
  represents one vegetation component and must include a ``"class"`` key
  identifying its type:

  .. code-block:: python

     "class": {
         4: [
             {
                 "class": "needleleaf",
                 "fcan": [0.1, 0.8],
                 "lnz0": [-5.0, 1.0],
                 "sdep": [0.5, 4.0],
             },
             {
                 "class": "broadleaf",
                 "fcan": [0.2, 0.9],
                 "lnz0": [-3.0, 2.0],
                 "sdep": [1.0, 3.0],
             },
         ],
     }

  The ``"class"`` key must match the vegetation type name used by MESH/CLASS
  (e.g., ``"needleleaf"``, ``"broadleaf"``, ``"crop"``, ``"grassland"``,
  ``"urban"``).

  Parameters in CLASS fall into two categories, which are handled differently:

  - **Vegetation-specific parameters** (``fcan``, ``lamx``, ``lnz0``, ``lamn``,
    ``alvc``, ``cmas``, ``alic``, ``root``, ``rsmn``, ``qa50``, ``vpda``,
    ``vpdb``, ``psga``, ``psgb``): these are tied to a specific vegetation
    column in the CLASS file. Each dictionary in the list provides independent
    bounds for its vegetation type. In the example above, ``fcan`` is calibrated
    with bounds ``[0.1, 0.8]`` for needleleaf and ``[0.2, 0.9]`` for broadleaf,
    producing two separate optimizer parameters (e.g., ``_4FCAN_NEEDLELEAF``
    and ``_4FCAN_BROADLEAF``).

  - **GRU-level parameters** (e.g., ``sdep``, ``sand1``, ``clay1``, ``drn``,
    and all other soil, hydrology, and prognostic parameters): these apply to
    the entire GRU regardless of vegetation type. If the same GRU-level
    parameter appears in multiple dictionaries in the list, FIAT takes the
    **widest range** (union) across all provided bounds --- i.e., the minimum
    of the lower bounds and the maximum of the upper bounds. In the example
    above, ``sdep`` appears in both dicts with bounds ``[0.5, 4.0]`` and
    ``[1.0, 3.0]``, so the effective calibration range becomes ``[0.5, 4.0]``
    (a single optimizer parameter ``_4SDEP``). If a GRU-level parameter
    appears in only one dictionary, those bounds are used directly. If it does
    not appear in any dictionary, it is excluded from calibration.

  .. note::

     The mixed-vegetation format only applies to the ``"class"`` parameter
     group. The ``"hydrology"`` and ``"routing"`` groups always use the
     standard flat-dictionary format.

  .. warning::

     The bounds format **must** match the actual GRU type parsed from the
     ``MESH_parameters_CLASS.ini`` file. FIAT validates this at preparation
     time:

     - If a **list of dictionaries** (mixed-vegetation format) is provided
       for a GRU that contains only a single vegetation type, a
       ``ValueError`` is raised.
     - Conversely, if a **flat dictionary** (single-vegetation format) is
       provided for a GRU that actually contains multiple vegetation types,
       a ``ValueError`` is raised.

     Ensure your bounds format matches each GRU's vegetation structure as
     defined in the model instance.

  You are free to combine both formats in the same ``parameter_bounds``
  dictionary. For instance, GRU 1 can use the standard single-dict format
  while GRU 4 uses the list-of-dicts format for its mixed vegetation:

  .. code-block:: python

     "parameter_bounds": {
         "class": {
             1: {"sdep": [0.5, 4.0]},  # single-veg GRU
             4: [                       # mixed-veg GRU
                 {"class": "needleleaf", "fcan": [0.1, 0.8], "sdep": [0.5, 4.0]},
                 {"class": "broadleaf", "fcan": [0.2, 0.9], "sdep": [1.0, 3.0]},
             ],
         },
         "hydrology": {1: {"zsnl": [0.03, 0.6]}},
         "routing": {1: {"r1n": [0.001, 2.0], "r2n": [0.001, 2.0]}},
     }
- ``executable``: absolute (or relative) path to the model executable used in runs
  (e.g., ``"sa_mesh"``). If a bare name is given, ensure it is discoverable via
  ``PATH`` or handled by the workflow’s staging logic.

  **Logarithmic internal sampling (optional)**

  Any bounds entry may optionally include a **third element** selecting the
  internal sampling scale that Ostrich applies to the parameter during
  optimization (the ``txOst`` field in the Ostrich manual, §2.7). Supported
  values are:

  - ``"none"`` (default): uniform sampling in native units,
  - ``"log10"``: Ostrich samples internally in base-10 log space,
  - ``"ln"``: Ostrich samples internally in natural-log space.

  The bounds themselves stay in **native (real-number) units** — only
  Ostrich's internal search space is transformed. This is useful for
  parameters whose plausible range spans several orders of magnitude (e.g.,
  MESH's ``FLZ``):

  .. code-block:: python

     "parameter_bounds": {
         "hydrology": {
             15: {"flz":  [1e-8, 1e-1, "log10"],
                  "zpls": [0.02, 0.6]},
         },
     }

  Validation rules:

  - Log-scale requires **strictly positive** bounds (``min > 0``).
  - For mixed-vegetation GRU-level parameters, the scale must be consistent
    across all vegetation entries.
  - The soil ratio-constrained parameters (``clay1``–``clay3`` and
    ``sand1``–``sand3``) must use ``"none"``; log transforms are not
    compatible with the tied-ratio constraint.

``observations``
----------------

FIAT supports two input modes for observations:

#. A path to a NetCDF file (``.nc`` or ``.nc4``), or
#. A list of per-station/per-unit entry dictionaries.

Input mode: NetCDF file
^^^^^^^^^^^^^^^^^^^^^^^

- Provide a file path string to a NetCDF dataset with a time coordinate named
  ``time`` and variables representing observed types (e.g., ``QO``).
- Units: Each observed variable must define a physical unit (via attribute)
  compatible with `Pint` so FIAT can quantify the dataset. The unit must be
  an attribute of the variable in the NetCDF file.
- Frequency: Include a ``freq`` variable to explicitly convey the sampling
  interval. Missing timestamps are common in observations, so do not rely on
  inference; specify the frequency explicitly for robust behavior.

The dimension and coordinate structure of the NetCDF file must align with the
model's output structure for the corresponding observation types. For MESH,
this typically involves dimensions for ``time`` and ``subbasin``, with
coordinates for observation names and frequencies as needed.

Input mode: list of entries
^^^^^^^^^^^^^^^^^^^^^^^^^^^

List of observation definitions providing time series and metadata. Each item is
an entry dictionary with the following fields:

- ``name``: unique string identifier for the observation (used in objective
  function expressions).
- ``type``: string observation type understood by the model–optimizer linkage.
  The type should match the output of model and the observation; for example
  use ``"QO"`` to refer to MESH's discharge.
- ``timeseries``: a ``pandas.Series`` indexed by time (e.g., hourly) containing
  observed values. Can be prepared from ``xarray`` via
  ``xr.open_dataset(...)[var].to_series()``. It can also be an ordered
  sequence (list or tuple) of values with a corresponding time index. Further
  examples are provided below.
- ``unit``: string physical unit of the observation values (e.g., ``"m^3/s"``).
- ``computational_unit``: string specifying the model computational unit
  (e.g., ``"subbasin"``) matching the model routing output structure.
- ``computational_unit_id``: integer identifier of the target computational
  unit (e.g., subbasin ID) consistent with the model’s domain.
- ``freq``: string frequency alias (e.g., ``"1h"``) describing the regular
  sampling interval of the supplied time series. The series will be validated or
  resampled to this interval as required by the workflow. Since missing data
  are inherent to observed records, specifying the frequency explicitly is
  important for robust behavior.

Alignment and units
^^^^^^^^^^^^^^^^^^^

- Time alignment: Entries are aligned on the union of all timestamps across
  entries. Missing values become ``NaN`` prior to unit conversion.
- Units handling: For each observation ``type`` (e.g., ``QO``) all entries are
  converted to a common unit using the first encountered unit for that type.
  Incompatible units raise a dimensionality error from Pint.

Computational unit semantics
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- The ``computational_unit`` must match the model's aggregation dimension name
  (e.g., ``"subbasin"``). The ``computational_unit_id`` identifies the specific
  element along that dimension.
- The assembled observations dataset has dimensions ``time`` and the chosen
  computational unit kind (e.g., ``subbasin``) and includes coordinates for
  ``name`` and ``freq`` per computational unit.

Supported types and example mapping
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``type`` refers to the observational equivalence of a model output variable.
  For MESH, ``QO`` denotes routed streamflow. Use the type keywords that match
  your model–optimizer recipe.

Resulting dataset
^^^^^^^^^^^^^^^^^

- The ``observations`` property returns an ``xarray.Dataset`` with one data
  variable per observed ``type``. Values carry units via the Pint accessor and
  can be used directly by the evaluation scripts.

Validation checklist
--------------------

- Paths exist and are writable: ``calibration_config.instance_path`` and
  ``model_config.instance_path``.
- Algorithm selection and specs match Ostrich capabilities.
- Observation series cover the provided calibration date ranges at the declared
  frequency and have time-aware indexes.
- Parameter bounds are numeric, ordered as ``[min, max]``, and within model-
  sensible ranges for each GRU/class.
- Model executable is available on the system and callable by the workflow.

Minimal instantiation pattern
-----------------------------

Below is the minimal structure you should provide (values are illustrative):

.. code-block:: python

   from fiatmodel import Calibration

   calibration_config = {
       "instance_path": "/path/to/calibration/workdir/",
       "random_seed": 12345,
       "algorithm": "ParallelDDS",
       "algorithm_specs": {
           "PerturbationValue": 0.2,
           "MaxIteration": 10_000,
       },
       "spinup_start": "1992-12-01 00:00:00",
       "dates": [
           {"start": "1995-01-01 00:00:00", "end": "2005-12-31 23:00:00"},
       ],
       "objective_functions": {
           "QO": {"kge_2012": ["-1 * MY_OBS"]},
       },
   }

   model_config = {
       "instance_path": "/path/to/mesh/instance/",
       "parameter_bounds": {
           "class": {1: {"sdep": [0.5, 4.0]}},
           "hydrology": {1: {"zsnl": [0.03, 0.6]}},
           "routing": {1: {"r1n": [0.001, 2.0], "r2n": [0.001, 2.0]}},
       },
       "executable": "sa_mesh",
   }

   observations = [
       {
           "name": "MY_OBS",
           "type": "QO",
           "timeseries": my_pandas_series,  # pandas.Series with datetime index
           "unit": "m^3/s",
           "computational_unit": "subbasin",
           "computational_unit_id": 38,
           "freq": "1h",
       }
   ]

   c = Calibration(
       calibration_software="ostrich",
       model_software="mesh",
       calibration_config=calibration_config,
       model_config=model_config,
       observations=observations,
   )

   c.prepare(output_path=calibration_config["instance_path"])  # stage workflow

Refer to the :doc:`examples` page for a complete, runnable setup derived from
the repository Notebook.

MESH parameter inequality constraints
-------------------------------------

Some MESH vegetation parameters are physically constrained relative to one
another — for example, the minimum leaf-area index ``LAMN`` must never
exceed the maximum ``LAMX``. A pure-uniform Ostrich sampler has no built-in
awareness of such orderings, so roughly half the samples on the product
rectangle :math:`[\text{LAMN}_{\min}, \text{LAMN}_{\max}] \times
[\text{LAMX}_{\min}, \text{LAMX}_{\max}]` can fall in the infeasible
triangle where ``LAMN > LAMX``. Running MESH on those pairs is at best
wasteful and at worst produces non-physical states.

FIAT enforces the ordering automatically through two complementary
mechanisms, both wired into the generated Ostrich input file. **No changes
to the evaluation script are required.**

Where constraints are declared
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Constraints live in a plain-Python dict in
``src/fiatmodel/models/mesh/constraints.py``. Add a new constraint by
appending one line to the appropriate group dict:

.. code-block:: python

   CONSTRAINTS = {
       "class": {
           "lamn <= lamx": {
               "cost_factor": 1.0e6,  # APM weight for the penalty term
               "clamp": True,         # substitute max(lower, upper) into MESH
           },
           # Future examples (not currently active):
           # "rsmn <= rsmx": {"cost_factor": 1.0e6, "clamp": True},
       },
       # "hydrology": {
       #     "zsnl <= zpls": {"cost_factor": 1.0e6, "clamp": True},
       # },
   }

The expression string (``"lamn <= lamx"``) accepts ``<``, ``<=``, ``>``, and
``>=``. FIAT canonicalizes every entry into ``lower <= upper`` form.

The two mechanisms
~~~~~~~~~~~~~~~~~~

Given calibration bounds like ``{"lamn": [0.0, 5.0], "lamx": [1.0, 10.0]}``
on some vegetation class, FIAT compiles the following into the Ostrich
input file at ``prepare()`` time:

**1. Clamp chain (tied parameters) — what MESH actually runs on.**

A short chain of Ostrich ``wsum`` / ``dist`` tied parameters builds the
clamped upper bound:

.. math::

   \text{LAMX}_{\text{eff}} \;=\; \text{LAMX} + \max\bigl(0,\;
   \text{LAMN} - \text{LAMX}\bigr) \;=\; \max(\text{LAMN},\,\text{LAMX})

The MESH ``CLASS.ini`` template receives ``LAMX_EFF`` instead of the raw
``LAMX``, so the model never runs on a physically infeasible
``(LAMN, LAMX)`` pair — even when the optimizer samples one. The user does
not see or configure this substitution; it happens entirely inside
``model.py`` via ``substituted_templated_parameters``.

**2. APM penalty (constraints block) — what biases the search.**

Ostrich still samples the *raw* ``LAMX`` from its declared bounds, so on
its own the clamp would make the objective perfectly flat across the
infeasible triangle (every ``(LAMN, LAMX)`` with ``LAMN > LAMX`` collapses
to the same ``(LAMN, LAMN)`` MESH run). That flatness would waste function
evaluations and confuse gradient-aware optimizers. To fix this, FIAT also
emits a tied response variable ``DIFF = LAMN − LAMX`` and a GCOP
``BeginConstraints`` entry that adds

.. math::

   P \;=\; \text{CF} \cdot \max\bigl(0,\;\text{LAMN} - \text{LAMX}\bigr)

to the objective whenever the pair is infeasible. With
``cost_factor = 1.0e6``, the penalty dwarfs any realistic KGE/NSE score,
producing a steep wall that steers the optimizer back into the feasible
half-plane.

Effect on the response surface — *yes, the evaluation is intentionally biased*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the user-visible trade-off to be aware of:

* **On the feasible half-plane (LAMN ≤ LAMX),** the response surface is
  unchanged — ``P = 0`` and MESH runs with the sampled ``LAMX``.
* **On the infeasible half-plane (LAMN > LAMX),** the surface is
  deliberately distorted in two stacked ways:

  1. The model is run on ``max(LAMN, LAMX) = LAMN`` rather than on the
     sampled ``LAMX``. The KGE/NSE reported for those samples therefore
     reflects a point on the feasible boundary, not the point Ostrich
     actually sampled.
  2. A large additive penalty ``CF · (LAMN − LAMX)`` is added on top.

Together, the infeasible half-plane shows a steep, monotone slope away
from the ``LAMN = LAMX`` diagonal, with an artificially large cost. Any
best-parameter set reported by Ostrich must land on the feasible side (by
construction of the penalty), so the final answer is not biased; but the
trajectory and the per-iteration log do contain these penalized,
clamp-substituted evaluations and should not be mistaken for honest
evaluations of the raw sampled pair.

If you need the raw, unpenalized surface for diagnostic purposes (e.g.,
sensitivity analysis), temporarily set ``"clamp": False`` and lower
``cost_factor`` toward zero — but understand that MESH will then be asked
to run on infeasible inputs.

Scenario coverage
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Lower calibrated?
     - Upper calibrated?
     - Behavior
   * - Yes
     - Yes
     - Emit clamp chain + APM penalty (as above).
   * - Yes
     - No (fixed in ``.ini``)
     - Validate that the lower's calibration max does not exceed the
       fixed upper; raise ``ValueError`` otherwise.
   * - No (fixed in ``.ini``)
     - Yes
     - Validate that the upper's calibration min is at least the fixed
       lower; raise ``ValueError`` otherwise.
   * - No
     - No
     - Silent no-op.

Worked example — ``LAMN ≤ LAMX`` on class 1
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With ``parameter_bounds = {"class": {1: {"lamn": [0.0, 5.0],
"lamx": [1.0, 10.0]}}}``, ``prepare()`` appends the following to
``ostIn.txt``:

.. code-block:: text

   BeginTiedParams
     # Ordered-pair inequality clamp chains (e.g., LAMX_EFF = max(LAMN, LAMX))
     # name          np   pname1      pname2      pname3      pname4      type    format
     _1LAMN_LAMX_ABSD   4  _1LAMN  _1LAMN  _1LAMX  _1LAMN  dist  free
     # name          np   pname1      pname2      type    c1     c2     format
     _1LAMN_LAMX_DIFF   2  _1LAMN               _1LAMX               wsum  1.0  -1.0  free
     _1LAMN_LAMX_SHIFT  2  _1LAMN_LAMX_DIFF     _1LAMN_LAMX_ABSD     wsum  0.5   0.5  free
     _1LAMX_EFF         2  _1LAMX               _1LAMN_LAMX_SHIFT    wsum  1.0   1.0  free
   EndTiedParams

   BeginTiedRespVars
     # Signed differences used by the APM penalty constraints below.
     # name          np   pname1      pname2      type    c1     c2
     _1LAMN_LAMX_DIFFRV  2  _1LAMN  _1LAMX  wsum  1.0  -1.0
   EndTiedRespVars

   BeginConstraints
     # name                       type     CF            lwr          upr    resp
     _1_LAMN_LT_LAMX  general  1.000000E+06  -1.0E99  0.0  _1LAMN_LAMX_DIFFRV
   EndConstraints

Line-by-line:

- ``_1LAMN_LAMX_ABSD`` uses Ostrich's ``dist`` type with both
  y-coordinates set equal to ``LAMN`` so the y-term cancels, leaving
  ``|LAMN − LAMX|``. This avoids needing a separate constant-zero helper.
- ``_1LAMN_LAMX_DIFF`` is the signed difference ``LAMN − LAMX`` via
  ``wsum``.
- ``_1LAMN_LAMX_SHIFT`` is ``(DIFF + ABSD) / 2 = max(0, LAMN − LAMX)``.
- ``_1LAMX_EFF`` adds that shift back to the sampled ``LAMX``, yielding
  ``max(LAMN, LAMX)`` — this is the name the MESH ``CLASS.ini`` actually
  references.
- ``_1LAMN_LAMX_DIFFRV`` is a duplicate of ``DIFF`` as a **tied response
  variable**. Ostrich's ``BeginConstraints`` block can only reference
  response variables, not tied parameters — this is the reason the same
  quantity is declared twice.
- ``_1_LAMN_LT_LAMX`` is the APM penalty itself. ``lwr = -1.0E99`` means
  "no penalty when ``DIFF`` is negative" (feasible); ``upr = 0.0`` means
  "penalty applies for any positive ``DIFF``"; ``CF = 1.0E6`` is the
  weight taken from ``cost_factor``.

For a list-form ``class`` unit with multiple vegetation classes, every
field is suffixed with the class name (e.g., ``_6LAMX_EFF_NEEDLELEAF``)
and the four lines above are repeated per class.

.. note::

   Because Ostrich still samples the raw ``LAMX`` from its declared bounds,
   the raw iteration log should not be read as the value MESH actually saw.
   The "effective" value MESH received is ``max(LAMN, LAMX)``, and the
   reported objective on infeasible samples also includes the APM penalty.

