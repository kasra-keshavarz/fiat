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
  
  - ``"class"``: mapping MESH GRU to ``{param_name: [min, max]}``
  - ``"hydrology"``: mapping MESH GRU to ``{param_name: [min, max]}``
  - ``"routing"``: mapping MESH river class to ``{param_name: [min, max]}``
  As can be seen, for the ``class`` and ``hydrology`` groups, the integer keys
  reference MESH GRU identifiers, as the base computational unit, while for the
  ``routing`` group, the integer keys typically reference river class identifiers.
  For more information, refer to the `MESH model documentation <https://mesh-model.atlassian.net/wiki/spaces/USER/overview?mode=global>`_
  and the `MESHFlow workflow guide <https://mesh-workflow.readthedocs.io/en/latest/>`_.
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
