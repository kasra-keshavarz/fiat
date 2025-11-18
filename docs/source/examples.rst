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