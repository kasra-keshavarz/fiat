'''
Limited dictionaries with names consistent among other "calibration"
methods.
'''

_algorithm_names = {
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
    '': '',
}