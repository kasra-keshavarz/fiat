Configuration
=============


This page documents the minimum and recommended configuration required to run a
FIAT calibration workflow with the MESH model and the Ostrich optimizer, based
on the working example in the repository. Use these specifications to construct
the inputs for the `fiatmodel.core.Calibration` class.

Overview
--------

To start a calibration workflow you instantiate ``Calibration`` with:

- ``calibration_software``: name of the optimizer backend (e.g., ``"ostrich"``).
- ``model_software``: name of the hydrologic model backend (e.g., ``"mesh"``).
- ``calibration_config``: dictionary configuring the optimizer run and
    experiment details.
- ``model_config``: dictionary configuring the model instance and parameter
    search space.
- ``observations``: list of observed time series and associated metadata.

Each of the three configuration dictionaries is specified below.

calibration_config
------------------

Dictionary configuring the calibration/optimization experiment.

- ``instance_path``: absolute or relative path (string) where the calibration
    experiment and working files will be generated. Must be writable and will be
    created if missing.
- ``random_seed``: integer seed used to initialize the optimizer’s pseudo-
    random sequence for reproducibility.
- ``algorithm``: string identifier of the Ostrich algorithm
    (e.g., ``"ParallelDDS"``). Refer to Ostrich documentation for the list of
    supported algorithms.
- ``algorithm_specs``: dictionary of algorithm-specific settings. Keys and
    value types depend on the chosen algorithm; typical examples include:

    - ``PerturbationValue``: float in ``(0, 1]`` controlling parameter
        perturbation scale.
    - ``MaxIteration``: positive integer controlling the iteration budget.
    - ``UseRandomParamValue``: optional boolean or ``None`` to use randomized
        initial values.
- ``dates``: list of one or more date range dictionaries defining calibration
    windows. Each item has:

    - ``start``: string-formatted timestamp (e.g., ``"YYYY-MM-DD HH:MM:SS"``)
    - ``end``: string-formatted timestamp (same format as ``start``)

    All observations used during calibration must cover these ranges at the
    specified sampling frequency.
- ``objective_functions``: mapping of variable type → metrics. The outer keys
    correspond to observation types (e.g., ``"QO"`` for discharge). Each value is
    a dictionary mapping a metric name to a list of weighted observation terms.
    Example structure:

    - ``{"QO": {"kge_2012": ["-1 * alaska_72"]}}``

    where a negative sign indicates minimization via negation of a score that is
    otherwise maximized.

model_config
------------

Dictionary configuring the model instance and parameter bounds.

- ``instance_path``: path (string) to the model instance directory. For MESH,
    this should contain required model input files and will be used as a template
    for generated runs.
- ``parameter_bounds``: dictionary defining the search space per parameter
    group. Expected keys for MESH include:
    - ``"class"``: mapping ``int → {param_name: [min, max]}``
    - ``"hydrology"``: mapping ``int → {param_name: [min, max]}``
    - ``"routing"``: mapping ``int → {param_name: [min, max]}``
    The integer keys typically reference GRU/class identifiers; each parameter
    maps to a two-element numeric list specifying inclusive bounds.
- ``executable``: string name or path to the model executable used in runs
    (e.g., ``"sa_mesh"``). If a bare name is given, ensure it is discoverable via
    ``PATH`` or handled by the workflow’s staging logic.

observations
------------

List of observation definitions providing time series and metadata. Each item is
a dictionary with the following fields:

- ``name``: unique string identifier for the observation (used in objective
    function expressions).
- ``type``: string observation type understood by the model–optimizer linkage.
    For MESH discharge, use ``"QO"``.
- ``timeseries``: a ``pandas.Series`` indexed by time (e.g., hourly) containing
    observed values. Can be prepared from ``xarray`` via
    ``xr.open_dataset(...)[var].to_series()``.
- ``unit``: string physical unit of the observation values (e.g., ``"m^3/s"``).
- ``computational_unit``: string specifying the model aggregation unit
    (e.g., ``"subbasin"``) matching the model routing output structure.
- ``computational_unit_id``: integer identifier of the target computational
    unit (e.g., subbasin ID) consistent with the model’s domain.
- ``freq``: string frequency alias (e.g., ``"1h"``) describing the regular
    sampling interval of the supplied time series. The series will be validated or
    resampled to this interval as required by the workflow.

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
           "MaxIteration": 10000,
       },
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
the repository notebook.

