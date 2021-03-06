# Copyright 2018 D-Wave Systems Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Kerberos hybrid sampler runs 3 sampling branches in parallel. In each iteration,
best results from tabu search and simulated annealing are combined with best
results from QPU sampling a subproblem.
"""

import dimod
from hybrid.samplers import (
    QPUSubproblemAutoEmbeddingSampler,
    SimulatedAnnealingSubproblemSampler,
    InterruptableTabuSampler)
from hybrid.decomposers import IdentityDecomposer, EnergyImpactDecomposer
from hybrid.composers import SplatComposer
from hybrid.core import State
from hybrid.flow import RacingBranches, ArgMin, Loop
from hybrid.utils import random_sample


class KerberosSampler(dimod.Sampler):
    """An opinionated dimod-compatible hybrid asynchronous decomposition sampler
    for problems of arbitrary structure and size.

    Examples:
        This example solves a two-variable Ising model.

        >>> import dimod
        >>> response = KerberosSampler().sample_ising(
        ...                     {'a': -0.5, 'b': 1.0}, {('a', 'b'): -1})
        >>> response.data_vectors['energy']
        array([-1.5, -1.5, -1.5, -1.5, -1.5, -1.5, -1.5, -1.5, -1.5, -1.5])

    """

    properties = None
    parameters = None
    runnable = None

    def __init__(self):
        self.parameters = {
            'init_sample': [],
            'max_iter': [],
            'convergence': [],
            'num_reads': [],
            'sa_reads': [],
            'sa_sweeps': [],
            'qpu_reads': [],
            'max_subproblem_size': []
        }
        self.properties = {}

    def sample(self, bqm, init_sample=None, max_iter=100, convergence=10, num_reads=1,
            sa_reads=1, sa_sweeps=1000, qpu_reads=100, qpu_sampler=None, max_subproblem_size=50):
        """Run Tabu search, Simulated annealing and QPU subproblem sampling (for
        high energy impact problem variables) in parallel and return the best
        samples.

        Args:
            bqm (:obj:`~dimod.BinaryQuadraticModel`):
                Binary quadratic model to be sampled from.
            init_sample (:class:`~dimod.SampleSet`, callable, ``None``):
                Initial sample set (or sample generator) used for each "read".
                Use a random sample for each read by default.
            max_iter (int):
                Number of iterations in the hybrid algorithm.
            convergence (int):
                Number of iterations with no improvement that terminates sampling.
            num_reads (int):
                Number of reads. Each sample is the result of a single run of the
                hybrid algorithm.
            sa_reads (int):
                Number of reads in the simulated annealing branch.
            sa_sweeps (int):
                Number of sweeps in the simulated annealing branch.
            qpu_reads (int):
                Number of reads in the QPU branch.
            qpu_sampler (:class:`dimod.Sampler`, optional, default=DWaveSampler()):
                Quantum sampler such as a D-Wave system.
            max_subproblem_size (int):
                Maximum size of the subproblem selected in the QPU branch.

        Returns:
            :obj:`~dimod.SampleSet`: A `dimod` :obj:`.~dimod.SampleSet` object.

        """

        if callable(init_sample):
            init_state_gen = lambda: State.from_sample(init_sample(), bqm)
        elif init_sample is None:
            init_state_gen = lambda: State.from_sample(random_sample(bqm), bqm)
        elif isinstance(init_sample, dimod.SampleSet):
            init_state_gen = lambda: State.from_sample(init_sample, bqm)
        else:
            raise TypeError("'init_sample' should be a SampleSet or a SampleSet generator")

        subproblem_size = min(len(bqm), max_subproblem_size)

        iteration = RacingBranches(
            InterruptableTabuSampler(),
            IdentityDecomposer()
                | SimulatedAnnealingSubproblemSampler(num_reads=sa_reads, sweeps=sa_sweeps)
                | SplatComposer(),
            EnergyImpactDecomposer(max_size=subproblem_size, min_diff=subproblem_size//2)
                | QPUSubproblemAutoEmbeddingSampler(num_reads=qpu_reads, qpu_sampler=qpu_sampler)
                | SplatComposer(),
        ) | ArgMin()
        self.runnable = Loop(iteration, max_iter=max_iter, convergence=convergence)

        samples = []
        energies = []
        for _ in range(num_reads):
            init_state = init_state_gen()
            final_state = self.runnable.run(init_state)
            # the best sample from each run is one "read"
            ss = final_state.result().samples
            ss.change_vartype(bqm.vartype, inplace=True)
            samples.append(ss.first.sample)
            energies.append(ss.first.energy)

        return dimod.SampleSet.from_samples(samples, vartype=bqm.vartype, energy=energies)
