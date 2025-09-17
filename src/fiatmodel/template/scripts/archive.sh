#!/bin/bash

# necessary information
# calibration time-stamp
time_stamp="$(date +'%Y-%m-%dT%H.%M.%S')"

# calibration model name
model="mesh"

# archiving path
archive_path="${HOME}/.logs/fiat/archive_${model}_${time_stamp}"

# if archive_path does not exist, then create it
if [[ ! -d "${archive_path}" ]]; then
  mkdir -p "${archive_path}"
fi

# creating archive run directory
best_path="${archive_path}/run_best/"

# defining input variables received from OSTRICH(MPI)
rank="$1"
trial="$2"
counter="$3"
objective_category="$4"

# copy necessary information
if [[ "${model}" == "mesh" ]]; then
    
    # if objective_category is `best', then copy the model to the ${best_path}
    if [[ "${objective_category}" == "best" ]]; then
      target="${best_path}"
    else
      target="${archive_path}/cpu_${rank}/run_${counter}/"
    fi
    
    # make the target directory in case it does not exist
    mkdir -p "$target"

    # echo log message
    echo "model,rank,counter,objective_category" > "${target}/log_${time_stamp}.csv"
    echo "${model},${rank},${counter},${objective_category}" >> "${target}/log_${time_stamp}.csv"

    # make a copy of the *.ini files
    cp ./${model}/*.ini "${target}"

    # make a copy of the *.txt files
    cp ./${model}/*.txt "${target}"

elif [[ "${model}" == "summa" ]]; then
    :

elif [[ "${model}" == "hype" ]]; then
    :
fi
