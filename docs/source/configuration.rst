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
- ``allow_date_mismatch``: boolean (default ``False``). When ``True``, the
  calibration will proceed even if the requested date range extends beyond the
  available simulation or observation time span. Missing time steps are filled
  with ``NaN`` and objective function metrics compute on the overlapping valid
  data. Use this when observations become available partway through a desired
  calibration window.
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
- ``reservoir_key``: optional string selecting how ``parameter_bounds["reservoir"]``
  units are identified. Defaults to ``"ireach"``.

  - ``"ireach"`` (aliases: ``"reach"``, ``"wf_res"``, ``"ireach_num"``): keys are
    MESH reach numbers (``WF_RES`` / last field on each lake line).
  - ``"name"`` (aliases: ``"reservoir_id"``, ``"id"``, ``"reservoir_name"``): keys
    are the name / reservoir-id field (``a12`` column; often MESHFlow's
    ``reservoir_id``).

- ``parameter_bounds``: dictionary defining the search space per parameter
  group. The schema of this dictionary is dependant on the hydrological model
  of choice. For example, expected keys for the MESH model include:

  - ``"class"``: mapping MESH GRU to parameter bounds
  - ``"hydrology"``: mapping MESH GRU to parameter bounds
  - ``"routing"``: mapping MESH river class to parameter bounds
  - ``"reservoir"``: mapping reservoir units to parameter bounds

  For the ``class`` and ``hydrology`` groups, the integer keys
  are **MID values** (Mosaic Identifier) as defined in the model's
  ``MESH_parameters_CLASS.ini`` file. The MID appears on the second
  hydrology line of each GRU block (e.g., ``… 5 Temp_sub-_broa_deci_fore``
  where ``5`` is the MID). These identifiers are typically non-contiguous
  (e.g., ``1, 2, 5, 6, 8, 10, 14, …``) and directly correspond to
  the column headers printed in the GRU-dependent parameter section of
  ``MESH_parameters_hydrology.ini``. For the ``routing`` group, integer keys
  reference river class identifiers (0-based). For the ``reservoir`` group,
  keys follow ``reservoir_key``: reach numbers when ``"ireach"``, or name /
  reservoir-id strings when ``"name"``. The reserved key ``"_all"`` expands
  the same parameter bounds onto every lake in the instance (each lake still
  gets its own Ostrich parameters); explicit per-lake entries override
  ``"_all"`` on a per-parameter basis. Calibratable reservoir coefficients are
  ``b1`` and ``b2`` (MESH ``WF_B1`` / ``WF_B2``); location and name fields
  are carried through for MESHFlow re-rendering but are usually left fixed.
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
         # with model_config["reservoir_key"] = "ireach" (default):
         "reservoir": {
             1: {"b1": [0.0, 10.0], "b2": [0.0, 5.0]},
             2: {"b1": [0.0, 10.0]},
         },
         # or apply the same bounds to every lake (per-lake Ostrich params):
         # "reservoir": {
         #     "_all": {"b1": [0.0, 10.0], "b2": [0.0, 5.0]},
         #     3: {"b1": [0.0, 8.0]},  # optional override for reach 3
         # },
         # with model_config["reservoir_key"] = "name":
         # "reservoir": {
         #     "Ghost Lake": {"b1": [0.0, 10.0], "b2": [0.0, 5.0]},
         #     "R-101": {"b1": [0.0, 10.0]},
         # },
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
     group. The ``"hydrology"``, ``"routing"``, and ``"reservoir"`` groups
     always use the standard flat-dictionary format.

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

- ``parameter_initial_values`` *(optional)*: dictionary defining explicit
  starting values for a subset (or all) calibrated parameters.  When omitted,
  every parameter receives a random initial value drawn uniformly from its
  bounds (respecting ``random_seed`` for reproducibility).  When provided,
  only the listed parameters use the user-supplied values; everything else
  still falls back to random.

  The structure mirrors ``parameter_bounds`` exactly:

  .. code-block:: python

     "parameter_initial_values": {
         "class": {
             1: {"sdep": 1.5},
             4: [
                 {"class": "needleleaf", "fcan": 0.5, "lnz0": -2.0},
                 {"class": "broadleaf",  "fcan": 0.3, "lnz0": 0.0},
             ],
         },
         "hydrology": {15: {"zpls": 0.1}},
         "routing": {6: {"flz": 1e-5}},
     }

  Validation rules:

  - Every key (group, unit, parameter, and class when applicable) must
    exist in ``parameter_bounds``.
  - The value must be numeric and lie **inside** the declared bounds (inclusive).
  - Mixed-vegetation GRU-level parameters must be consistent across all
    vegetation entries (same rule as bounds).
  - The structural format (single value vs. per-class dict) must match the
    corresponding bounds format.  A mismatch raises ``TypeError``.

  Log-scale parameters (``log10`` or ``ln``) are formatted in scientific
  notation; linear parameters are formatted with six decimal places.

- ``executable``: absolute (or relative) path to the model executable used in runs
  (e.g., ``"sa_mesh"``). If a bare name is given, ensure it is discoverable via
  ``PATH`` or handled by the workflow’s staging logic.

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
- If ``parameter_initial_values`` is provided, every entry references a key
  that exists in ``parameter_bounds``, the value is numeric, and it lies
  inside the declared bounds.
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
       "reservoir_key": "ireach",  # or "name" / "reservoir_id"
       "parameter_bounds": {
           "class": {1: {"sdep": [0.5, 4.0]}},
           "hydrology": {1: {"zsnl": [0.03, 0.6]}},
           "routing": {1: {"r1n": [0.001, 2.0], "r2n": [0.001, 2.0]}},
           "reservoir": {1: {"b1": [0.0, 10.0], "b2": [0.0, 5.0]}},
       },
       "parameter_initial_values": {       # optional
           "class": {1: {"sdep": 1.5}},    # explicit start for sdep
           "hydrology": {1: {"zsnl": 0.15}},
           "reservoir": {1: {"b1": 0.15, "b2": 0.25}},
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
