"""Canonical name mappings for Ostrich algorithms.

Provides a compact lookup to translate user-provided algorithm names to the
canonical identifiers expected by Ostrich configuration templates. The keys are
lowercase normalized variants commonly found in literature or user inputs.

Attributes
----------
_algorithm_equivalents : dict[str, str]
    Mapping from a normalized algorithm name to the canonical Ostrich
    identifier used in templates and configuration files.

Examples
--------
>>> _algorithm_equivalents['levenberg-marquardt']
'LevMar'
>>> _algorithm_equivalents.get('geneticalgorithm')
'GeneticAlg'
"""

_algorithm_equivalents = {
    'bisectionalgorithm': 'BisectionAlg',
    'fletcher-reeves': 'FletchReevesAlg',
    'levenberg-marquardt': 'LevMar',
    'gml-ms': 'LevMar',
    'gridalgorithm': 'GridAlg',
    'powell': 'PowellAlg',
    'steepest-descent': 'SteepestDescAlg',
    'appso': 'APPSO',
    'particleswarm': 'ParticleSwarm',
    'beers': 'BEERS',
    'binarygeneticalgorithm': 'GeneticAlg',
    'geneticalgorithm': 'GeneticAlg',
    'discretesimulatedannealing': 'SimulatedAlg',
    'simulatedannealing': 'SimulatedAlg',
    'vanderbiltsimulatedannealing': 'SimulatedAlg',
    'discretedds': 'DiscreteDDSAlg',
    'dds': 'DDSAlg',
    'paralleldds': 'ParallelDDSAlg',
    'shuffledcomplexevolution': 'SCEUA',
    'samplingalgorithm': 'SamplingAlg',
    'ddsau': '_DDSAU_Alg',
    'glue': 'GLUE',
    'metropolissampler': 'MetropolisSampler',
    'rejectionsampler': 'RejectionSampler',
    'padds': 'PADDSAlg',
    'parapadds': 'ParallelPADDSAlg',
    'smooth': 'SMOOTH',
}