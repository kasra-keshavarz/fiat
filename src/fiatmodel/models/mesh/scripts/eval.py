#!/usr/bin/env python
# coding: utf-8

# data manipulation libraries
import pandas as pd
import numpy as np
import xarray as xr

# built-in libraries
from itertools import product
import os
import subprocess
import sys

# import HydroErr
import HydroErr as he


# Here, we calibrate the models based on the `NSE` metric.

# define calibration model
# basic instructions
basin_name = 'wolf'
model = 'mesh'
gauges = ['Wolf Creek at Alaska Highway (WCAH)']
obj_func = 'nse'
# start and end date for the calibration period
cal_start = '1995-01-01T00:00:00'
cal_end = '2010-12-31T23:00:00'


# paths
home = os.getenv('HOME')
obs_path = f'{home}/Documents/github-repos/research-basin-benchmarking/1-model-setup/3-observations/wolf-creek-research-basin/post-processed-gauge-data/wolf-creek-gauge-data.nc'

# read observed data
obs = xr.open_dataset(obs_path)

if model == 'mesh':
    # run MESH
    command = (
    'ml restore scimods; '
    f'cd ./{model}; '
    './sa_mesh;'
    )
    result = subprocess.run(
        command,
        capture_output=True,
        shell=True)
    # specify the output file
    sim_path = f'./{model}/results/QO_H.csv'

    # read the simulated results
    try:
        sim = pd.read_csv(
            sim_path,
            header=None,
            index_col=0,
            parse_dates=True,
            date_format='%Y/%m/%d %H:%M:%S.000'
        )
    except: # if model crashes, so sim_path becomes corrupt
        with open(f"./{model}/results/kge_2012.csv", "w") as file:
            # write a terrible metric value 
            # to indicate model crash
            # OSTRICH assumes a minimization problem
            file.write("2000\n")
            sys.exit()

    # drainage database path
    ddb_path = f'./{model}/MESH_drainage_database.nc'
    ddb = xr.open_dataset(
        ddb_path,
    )
elif model == 'summa':
    pass
elif model == 'hype':
    pass

# finding correspondence between MESH Rank and subbasin id
basin_to_rank = ddb.Rank.to_pandas().to_dict()
# reversing the dictionary above
rank_to_basin = {int(k):v for (v, k) in basin_to_rank.items()}

# drop columns with `NA` values (fictitious outlet basins)
sim.dropna(axis=1, inplace=True)

# change the column values from rank to subbasin id
sim.columns = pd.Series(sim.columns).replace(rank_to_basin)

# assign appropriate names
sim.columns.name = 'LINKNO'
sim.index.name = 'time'
sim.name = 'sim'

# create a xarray.Dataset
sim_ds = sim.stack().to_xarray().to_dataset(name='sim')

# select the gauge values form the `obs'
obs_ts = obs[f'discharge'].sel(gauge_name=gauges).to_pandas()
# select the sub-basin where the gauge is located
link_no = obs['LINKNO'].sel(gauge_name=gauges)
gauge_to_link = link_no.to_pandas().to_dict() # mapping
link_to_gauge = {v: k for (k, v) in gauge_to_link.items()}
# given the known link_no value, select the proper simulation ts
sim_ts = sim_ds['sim'].sel(LINKNO=link_no).to_pandas()
# change the column names so both DataFrames have matching columns
sim_ts.columns = pd.Series(sim_ts.columns).replace(link_to_gauge)

# revert back to xarray.Datasets for clarity of analysis
sim_ts_ds = sim_ts.stack().to_xarray().to_dataset(name='sim')
obs_ts_ds = obs_ts.stack().to_xarray().to_dataset(name='obs')

# create joint dataset
ds = xr.merge([sim_ts_ds, obs_ts_ds])
# remove na values how=any!
ds = ds.dropna(dim='time', how='any')

for g in gauges:
    # define dataframes
    sim_df = ds['sim'].sel(gauge_name=g, time=slice(cal_start, cal_end))
    obs_df = ds['obs'].sel(gauge_name=g, time=slice(cal_start, cal_end))
    
    # calculate metrics - minimization problem
    kge = -1 * (he.kge_2012(sim_df, obs_df))

with open(f"./{model}/results/kge_2012.csv", "w") as file:
    file.write(f"{kge}\n")  # Using an f-string to format the float

