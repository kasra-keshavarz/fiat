#!/usr/bin/env python3

"""Evaluation utilities for MESH models.

To make the evaluation as flexible and lightweight as possible, we use
an individual script to perform the evaluation. This script is
dynamically generated based on the FIAT configuration.
"""

# built-in imports
import importlib
import subprocess
import os
import re
import json

# external imports
import xarray as xr
import HydroErr
import numexpr as ne

# default environment
my_env = os.environ.copy()

# MESH-specific import
import meshflow as mf

# Precompile regexes for speed/readability
_INT_RE = re.compile(r'^[-+]?\d+$')
_FLOAT_RE = re.compile(
    r"""^[-+]?(                # optional sign
        (?:\d+\.\d*|\d*\.\d+)  # something with a decimal point
        (?:[eE][-+]?\d+)?      # optional exponent
        |
        \d+[eE][-+]?\d+        # or integer with exponent (e.g. 1e6)
    )$""",
    re.X
)

# default environment
my_env = os.environ.copy()

def _parse_numeric_string(s: str):
    """
    Try to interpret a numeric-looking string as int or float.
    Return the converted number, or the original string if not numeric.
    """
    if _INT_RE.match(s):
        # Keep as int if it fits typical Python int (Python int is unbounded anyway)
        return int(s)
    if _FLOAT_RE.match(s):
        # Anything with decimal point or exponent
        return float(s)
    return s  # not numeric-looking

def _convert_numeric_strings(obj):
    """
    Recursively walk lists/dicts and convert numeric-like strings.
    """
    if isinstance(obj, dict):
        return {k: _convert_numeric_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_numeric_strings(v) for v in obj]
    if isinstance(obj, str):
        return _parse_numeric_string(obj.strip())
    return obj  # leaves int, float, bool, None, etc. untouched

def _make_object_hook():
    def object_hook(d):
        for k, v in d.items():
            d[k] = _convert_numeric_strings(v)  # reuse earlier function
        return d
    return object_hook

if "__name__" == "__main__":
    # read the `json` configuration file
    with open("./etc/eval/eval.json", "r") as f:
        eval_config = json.load(f, object_hook=_make_object_hook())

    # read the observation file
    observations = xr.open_dataset('./etc/observations/observations.nc')

    # files to be read
    root_file_path = os.path.join('./etc', 'eval')
    # first adding parameters
    file_paths = {k: os.path.join(root_file_path, v + '.json')
                  for k, v in eval_config.get('parameters').items()}
    # then adding others
    file_paths.update({k: os.path.join(root_file_path, v + '.nc')
                       for k, v in eval_config.get('others').items()})
    
    # read the files in each file_paths and generate MESH input parameters
    mesh_inputs = {}
    for param_name, file_path in file_paths.items():
        if file_path.endswith('.json'):
            with open(file_path, 'r') as f:
                mesh_inputs[param_name] = json.load(f, object_hook=_make_object_hook())
        else:
            raise ValueError(f"Unsupported file format for {file_path}")
        
    # use meshflow to generate the parameter files
    # class
    class_file = mf.utility.render_class_template(
        class_case=mesh_inputs['case_entry'],
        class_info=mesh_inputs['info_entry'],
        class_grus=mesh_inputs['class']
    )
    # hydrology
    hydrology_file = mf.utility.render_hydrology_template(
        routing_params=mesh_inputs['routing'],
        hydrology_params=mesh_inputs['hydrology'],
    )
    # apply changes to the MESH instance
    with open(os.path.join(eval_config['model_instance_path'], "MESH_parameters_CLASS.ini"), "w", encoding="utf-8") as f:
        f.write(class_file)
    with open(os.path.join(eval_config['model_instance_path'], "MESH_parameters_hydrology.ini"), "w", encoding="utf-8") as f:
        f.write(hydrology_file)

    # run the MESH model
    subprocess.run(
        eval_config['model_executable'],
        cwd=eval_config['model_instance_path'],
        check=True,
        env=my_env)

    # first read the time-series of obs/sim for
    #      each element in the `obs` file
    simulations = xr.open_mfdataset(
        os.path.join(
            eval_config['model_instance_path'],
            eval_config['results_path'],
            f) for f in eval_config['output_files']
        )
    
    # resampling the time-series matching the observations time-step
    simulations = simulations.resample(time=).mean

    # extract names for the `observations` - can be hard-coded
    station_ids = observations.subbasin.to_numpy().tolist()
    station_names = observations.name.to_numpy().tolist()

    # evaluate each objective function
    of_values = {}

    for flux, metrics in eval_config.get('objective_functions').items():
        sims = {}
        obs = {}
        # start populating of_values
        of_values[flux] = {}
        # assign simulation results for the selected flux
        for st in station_ids:
            # sims dictionary
            sims[observations['name'].sel(subbasin=st).to_numpy().tolist()] = simulations[flux].sel(subbasin=st).to_series()
            # same for obs dictionary
            obs[observations['name'].sel(subbasin=st).to_numpy().tolist()] = observations[flux].sel(subbasin=st).to_series()
        # metric (for example, kge_2012), and ofs (list of individual objective functions
        for metric, ofs in metrics.items():
            # add elements to `of_values`
            of_values[flux][metric] = []
            # calculate the metric value
            he_metric = getattr(HydroErr, metric)
            metric_dict = {}
            for name in obs.keys():
                metric_dict[name] = he_metric(sims[name], obs[name])

            for idx, of in enumerate(ofs): # a list of objective functions
                result = ne.evaluate(of, local_dict=metric_dict)
                of_values[flux][metric] = result

                # write the of results to a .csv file (with only a single element)
                with open(
                    os.path.join(
                        './etc',
                        'eval',
                        f'{flux.upper()}_{metric}_{idx}.csv',
                    ),
                    'w',
                ) as f:
                    f.write(f'{result}')
