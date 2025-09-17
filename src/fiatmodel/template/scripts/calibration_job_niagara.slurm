#!/bin/bash
#SBATCH --job-name=calibration
#SBATCH --time=06:00:00
#SBATCH --ntasks=1000
#SBATCH --nodes=25
#SBATCH --comment="Calibrating MESH for the Bow River at Calgary"
#SBATCH --output=logs/%A_out.txt
#SBATCH --error=logs/%A_err.txt
#SBATCH --mail-user=kasra.keshavarz1@ucalgary.ca
#SBATCH --mail-type=BEGIN,END,FAIL

# folder name
# folder="$(echo $PWD | rev | cut -d '/' -f 1 | rev)"

# model can be 'mesh', 'summa', or 'hype'
model=mesh
mpi=true

# load modules
module restore scimods

# activate custom Python environment
source $SCRATCH/virtual-envs/scienv/bin/activate

# copy the files to the scratch
#temp_path="$SCRATCH/$SLURM_JOBID/${model}_calibration"

#mkdir -p "$temp_path"
#cp -r "$PWD" "$temp_path"

# change the directory to where the scratch is
#cd "$temp_path/${folder}"

# run the script
if [[ "$mpi" == "true" ]]; then
  srun "./OstrichMPI"
elif [[ "$mpi" == "false" ]]; then
  . "./Ostrich"
fi

# run end message
echo "Run ends at: $(date +'%Y-%m-%d %H:%M:%S.000')"
echo ""
