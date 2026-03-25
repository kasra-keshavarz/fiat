Examples
========

This page provides a complete example of configuring and preparing a FIAT
calibration workflow using the MESH model and the Ostrich optimizer. The example
below mirrors the repository notebook and can be adapted to your environment.

Load the framework
------------------

.. code-block:: python

   # built-in imports
   import time

   # package imports
   from fiatmodel import Calibration

Define parameter bounds
-----------------------

Parameter bounds define the search space for the optimizer. Each parameter
group (``class``, ``hydrology``, ``routing``) maps integer computational-unit
identifiers to dictionaries of ``{parameter_name: [min, max]}``.

For the ``class`` group, integer keys are GRU identifiers (1-based, matching
the order of GRU blocks in ``MESH_parameters_CLASS.ini``). For ``hydrology``,
keys are also GRU identifiers. For ``routing``, keys are river class
identifiers (1-based, maximum 5).

**Single-vegetation GRUs**

When each GRU contains a single vegetation type, bounds are specified as
a flat dictionary:

.. code-block:: python

   # defining MESH calibration parameter bounds
   class_dict_bounds = {
       1: {
           'sdep': [0.5, 4.0],
       },
       5: {
           'sdep': [0.5, 4.0],
       },
   }

   hydrology_dict_bounds = {
       1: {
           'zsnl': [0.03, 0.6],
       },
       5: {
           'zsnl': [0.03, 0.6],
       },
   }

   routing_dict_bounds = {
       1: {
           'r2n': [0.001, 2.0],
           'r1n': [0.001, 2.0]
       },
       2: {
           'r2n': [0.001, 2.0],
           'r1n': [0.001, 2.0]
       },
       3: {
           'r2n': [0.001, 2.0],
           'r1n': [0.001, 2.0]
       },
       4: {
           'r2n': [0.001, 2.0],
           'r1n': [0.001, 2.0]
       },
       5: {
           'r2n': [0.001, 2.0],
           'r1n': [0.001, 2.0]
       },
   }

**Mixed-vegetation GRUs**

If a GRU contains multiple vegetation types (e.g., a mixed-forest tile with
needleleaf and broadleaf), use a list of dictionaries instead. Each dictionary
must include a ``'class'`` key to identify the vegetation type:

.. code-block:: python

   # mixed-vegetation GRU example
   class_dict_bounds = {
       # GRU 1: single vegetation type (standard format)
       1: {
           'sdep': [0.5, 4.0],
       },
       # GRU 4: mixed vegetation (needleleaf + broadleaf)
       4: [
           {
               'class': 'needleleaf',
               'fcan': [0.1, 0.8],
               'lnz0': [-5.0, 1.0],
               'sdep': [0.5, 4.0],
           },
           {
               'class': 'broadleaf',
               'fcan': [0.2, 0.9],
               'lnz0': [-3.0, 2.0],
               'sdep': [1.0, 3.0],
           },
       ],
   }

In this example:

- ``fcan`` and ``lnz0`` are vegetation-specific parameters, so each
  vegetation type gets its own calibration bounds and optimizer parameter
  (e.g., ``_4FCAN_NEEDLELEAF`` and ``_4FCAN_BROADLEAF``).
- ``sdep`` is a GRU-level parameter (shared across all vegetation types in
  the GRU). Since it appears in both dictionaries, FIAT merges the bounds
  to the widest range: ``min(0.5, 1.0) = 0.5`` and ``max(4.0, 3.0) = 4.0``,
  producing a single optimizer parameter ``_4SDEP`` with bounds
  ``[0.5, 4.0]``.

The vegetation-specific parameters recognized by MESH/CLASS are: ``fcan``,
``lamx``, ``lnz0``, ``lamn``, ``alvc``, ``cmas``, ``alic``, ``root``,
``rsmn``, ``qa50``, ``vpda``, ``vpdb``, ``psga``, and ``psgb``. All other
parameters (soil, hydrology, prognostic) are GRU-level.

Load observations
-----------------

.. code-block:: python

   import xarray as xr
   obs_path = ('/path/to/wolf-creek-gauge-data.nc')

Instantiate ``Calibration``
---------------------------

.. code-block:: python

   c = Calibration(
       calibration_software='ostrich',
       model_software='mesh',
       calibration_config={
           'instance_path': '/path/to/wolf-creek-calibration-instance/',  # where the calibration instance is generated
           'random_seed': int(time.time()),
           'algorithm': 'ParallelDDS',
           'algorithm_specs': {  # refer to Ostrich manual for keys
               'PerturbationValue': 0.2,
               'MaxIteration': 10_000,
               'UseRandomParamValue': None,
           },
           'spinup_start': '1992-12-01 00:00:00',
           'dates': [  # one or more calibration dates
               {
                   'start': '1995-01-01 00:00:00',
                   'end': '2005-12-31 23:00:00',
               },
           ],
           'objective_functions': {
               'QO': {
                   'kge_2012': ['-1 * alaska_72'],
               },
           },
       },
       model_config={
           'instance_path': '/path/to/wolf-creek-mesh-instance/',
           'parameter_bounds': {
               'class': class_dict_bounds,
               'hydrology': hydrology_dict_bounds,
               'routing': routing_dict_bounds,
           },
           'executable': 'sa_mesh',  # ensure available on PATH or via absolute path
       },
       observations=[
           {
               'name': 'alaska_72',
               'type': 'QO',
               'timeseries': xr.open_dataset(obs_path)['discharge'].isel(gauge_name=2).to_series(),
               'unit': 'm^3/s',
               'computational_unit': 'subbasin',
               'computational_unit_id': 38,
               'freq': '1h',
           },
       ],
   )

Prepare the workflow
--------------------

.. code-block:: python

   c.prepare(output_path='/path/to/instance/destination/dir/')

A complete, runnable notebook example is available in the
`examples <https://github.com/kasra-keshavarz/FIATModel/tree/main/examples>`_
directory of this repository.