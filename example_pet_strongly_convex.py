# Copyright 2018 Matthias J. Ehrhardt, University of Cambridge
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.

"""An example of using the SPDHG algorithm to solve a PET reconstruction
problem with a strongly convex total variation prior. We exploit the smoothness
of the data term and the strong convexity of the prior to obtain a linearly
convergent algorithm. We compare different algorithms for this problem and
visualize the results as in [CERS2017].

Note that this example uses the ASTRA toolbox https://www.astra-toolbox.com/.

Reference
---------
[CERS2017] A. Chambolle, M. J. Ehrhardt, P. Richtarik and C.-B. Schoenlieb,
*Stochastic Primal-Dual Hybrid Gradient Algorithm with Arbitrary Sampling
and Imaging Applications*. ArXiv: http://arxiv.org/abs/1706.04957 (2017).
"""

from __future__ import division, print_function
import os
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage.filters import gaussian_filter
import brewer2mpl
import odl
import odl.contrib.datasets.images as images
from stochastic_primal_dual_hybrid_gradient import spdhg, spdhg_pesquet
import misc

# create folder structure and set parameters
folder_out = '.'  # to be changed
filename = 'example_pet_strongly_convex'
nepoch = 100
niter_target = 2000
subfolder = '{}epochs'.format(nepoch)
nvoxelx = 250  # set problem size

filename = '{}_{}x{}'.format(filename, nvoxelx, nvoxelx)

folder_main = '{}/{}'.format(folder_out, filename)
if not os.path.exists(folder_main):
    os.makedirs(folder_main)

folder_today = '{}/{}'.format(folder_main, subfolder)
if not os.path.exists(folder_today):
    os.makedirs(folder_today)

folder_npy = '{}/npy'.format(folder_today)
if not os.path.exists(folder_npy):
    os.makedirs(folder_npy)

# set latex options
matplotlib.rc('text', usetex=False)

# create geometry of operator
X = odl.uniform_discr(min_pt=[-1, -1], max_pt=[1, 1],
                      shape=[nvoxelx, nvoxelx], dtype='float32')

geometry = odl.tomo.parallel_beam_geometry(X, num_angles=200, det_shape=250)
G = odl.BroadcastOperator(*[odl.tomo.RayTransform(X, gi, impl='astra_cpu')
                            for gi in geometry])

# create ground truth
Y = G.range
groundtruth = X.element(images.brain_phantom(shape=X.shape))
clim = [0, 1]
tol_norm = 1.05

# save images and data
file_data = '{}/data.npy'.format(folder_main)
if not os.path.exists(file_data):
    sinogram = G(groundtruth)
    support = X.element(groundtruth.ufuncs.greater(0))
    factors = -G(0.005 / X.cell_sides[0] * support)
    factors.ufuncs.exp(out=factors)

    counts_observed = (factors * sinogram).ufuncs.sum()
    counts_desired = 3e+6
    counts_background = 2e+6

    factors *= counts_desired / counts_observed

    sinogram_support = sinogram.ufuncs.greater(0)
    smoothed_support = Y.element(
        [gaussian_filter(sino_support, sigma=[1, 2 / X.cell_sides[0]])
         for sino_support in sinogram_support])
    background = 10 * smoothed_support + 10
    background *= counts_background / background.ufuncs.sum()
    data = odl.phantom.poisson_noise(factors * sinogram + background,
                                     seed=1807)

    arr = np.empty(3, dtype=object)
    arr[0] = data
    arr[1] = factors
    arr[2] = background
    np.save(file_data, arr)

    misc.save_image(groundtruth, 'groundtruth', folder_main, 1, clim=clim)

    fig2 = plt.figure(2)
    fig2.clf()
    i = 0
    plt.plot((sinogram[i]).asarray()[0], label='G(x)')
    plt.plot((factors[i] * sinogram[i]).asarray()[0], label='factors * G(x)')
    plt.plot(data[i].asarray()[0], label='data')
    plt.plot(background[i].asarray()[0], label='background')
    plt.legend()

    fig2.savefig('{}/components1D.png'.format(folder_main),
                 bbox_inches='tight')

else:
    (data, factors, background) = np.load(file_data, allow_pickle=True)

# data fit
f = odl.solvers.SeparableSum(
    *[misc.KullbackLeiblerSmooth(Yi, yi, ri)
      for Yi, yi, ri in zip(Y, data, background)])

# prior and regularisation parameter
g = misc.TotalVariationNonNegative(X, alpha=5e-2, strong_convexity=5e-1)
g.prox_options['niter'] = 20

# operator
A = odl.BroadcastOperator(*[fi * Gi for fi, Gi in zip(factors, G)])

obj_fun = f * A + g  # objective functional
rho = 0.99  # square root of step size upper bound

# define strong convexity constants
mu_i = [fi.convex_conj.strong_convexity for fi in f]
mu_f = np.min(mu_i)
mu_g = g.strong_convexity

# create target / compute a saddle point
file_target = '{}/target.npy'.format(folder_main)
if not os.path.exists(file_target):
    file_normA = '{}/norms_{}subsets.npy'.format(folder_main, 1)
    if not os.path.exists(file_normA):
        # compute norm of operator
        normA = [tol_norm * A.norm(estimate=True)]

        np.save(file_normA, normA)

    else:
        normA = np.load(file_normA)

    # set step size parameters
    kappa = np.sqrt(1 + normA[0]**2 / (mu_g * mu_f) / rho**2)
    sigma = 1 / ((kappa - 1) * mu_f)
    tau = 1 / ((kappa - 1) * mu_g)
    theta = 1 - 2 / (1 + kappa)

    x_opt, y_opt = X.zero(), Y.zero()  # initialise variables

    # create callback
    callback = (odl.solvers.CallbackPrintIteration(step=10, end=', ') &
                odl.solvers.CallbackPrintTiming(step=10, cumulative=True))

    # compute a saddle point with PDHG and time the reconstruction
    g.prox_options['p'] = None
    odl.solvers.pdhg(x_opt, g, f, A, niter_target, tau, sigma, y=y_opt,
                     theta=theta, callback=callback)

    # compute the subgradients of the saddle point
    subx_opt = -A.adjoint(y_opt)
    suby_opt = A(x_opt)

    # compute the objective function value at the saddle point
    obj_opt = obj_fun(x_opt)

    # save saddle point
    np.save(file_target, (x_opt, y_opt, subx_opt, suby_opt, obj_opt), 
            allow_pickle=True)

    # show saddle point and subgradients
    misc.save_image(x_opt, 'x_saddle', folder_main, 1, clim=clim)
    misc.save_signal(y_opt[0], 'y_saddle[0]', folder_main, 2)
    misc.save_image(subx_opt, 'subx_saddle', folder_main, 3)
    misc.save_signal(suby_opt[0], 'suby_saddle[0]', folder_main, 4)

else:
    (x_opt, y_opt, subx_opt, suby_opt, obj_opt) = np.load(file_target, 
            allow_pickle=True)

# set distances
dist_x = 1 / 2 * odl.solvers.L2NormSquared(X).translated(x_opt)
dist_y = 1 / 2 * odl.solvers.L2NormSquared(Y).translated(y_opt)


class CallbackStore(odl.solvers.Callback):
    """Callback to store function values"""

    def __init__(self, alg, iter_save, iter_plot):
        self.iter_save = iter_save
        self.iter_plot = iter_plot
        self.iter = 0
        self.alg = alg
        self.out = []

    def __call__(self, x, **kwargs):

        if self.iter in self.iter_save:
            obj = obj_fun(x[0])
            dx = dist_x(x[0])
            dy = dist_y(x[1])
            d = dx + dy

            self.out.append({'obj': obj, 'dist': d,
                             'dist_x': dx, 'dist_y': dy})

        if self.iter in self.iter_plot:
            fname = '{}_{}'.format(self.alg, self.iter)
            misc.save_image(x[0], fname, folder_today, 1, clim=clim)

        self.iter += 1


# number of subsets for each algorithm
nsub = {'pdhg': 1, 'spdhg_uni10': 10, 'spdhg_uni50': 50,
        'pesquet_uni10': 10, 'pesquet_uni50': 50}

# number of iterations for each algorithm
niter, iter_save, iter_plot = {}, {}, {}
for alg in nsub.keys():
    niter[alg] = nepoch * nsub[alg]
    iter_save[alg] = range(0, niter[alg] + 1, nsub[alg])
    iter_plot[alg] = list(np.array([10, 20, 30, 40, 100, 300]) * nsub[alg])

# %% --- Run algorithms ---
for alg in ['pdhg', 'spdhg_uni10', 'spdhg_uni50', 'pesquet_uni10',
            'pesquet_uni50']:
    print('======= ' + alg + ' =======')

    # clear variables in order not to use previous instances
    prob, sigma, tau, theta = [None] * 4

    # create lists for subset division
    n = nsub[alg]
    (sub2ind, ind2sub) = misc.divide_1Darray_equally(range(len(A)), n)

    # set random seed so that results are reproducable
    np.random.seed(1807)

    if alg == 'pdhg' or alg[0:5] == 'spdhg':
        file_normA = '{}/norms_{}subsets.npy'.format(folder_main, n)

    elif alg[0:7] == 'pesquet':
        file_normA = '{}/norms_{}subsets.npy'.format(folder_main, 1)

    if not os.path.exists(file_normA):
        A_subsets = [odl.BroadcastOperator(*[A[i] for i in subset])
                     for subset in sub2ind]
        normA = [tol_norm * Ai.norm(estimate=True) for Ai in A_subsets]
        np.save(file_normA, normA)

    else:
        normA = np.load(file_normA)

    # choose parameters for algorithm
    if alg == 'pdhg':
        kappa = np.sqrt(1 + normA[0]**2 / (mu_g * mu_f) / rho**2)
        prob_subset = [1]
        prob = [1] * Y.size
        sigma = [1 / ((kappa - 1) * mu_f)] * Y.size
        tau = 1 / ((kappa - 1) * mu_g)
        theta = 1 - 2 / (1 + kappa)

    elif alg.startswith('spdhg'):
        kappa = [np.sqrt(1 + normAi**2 / (mu_g * mui) / rho**2)
                 for normAi, mui in zip(normA, mu_i)]
        kappa_max = max(kappa)
        prob_subset = [1 / n] * n
        prob = [1 / n] * Y.size
        sigma = [1 / ((kappa_max - 1) * mui) for mui in mu_i]
        tau = 1 / ((n * kappa_max + n - 2) * mu_g)
        theta = 1 - 2 / (n + n * kappa_max)

    elif alg.startswith('pesquet'):
        prob_subset = [1 / n] * n
        prob = [1 / n] * Y.size
        sigma = [rho / normA[0]] * Y.size
        tau = rho / normA[0]

    else:
        assert False, "Parameters not defined"

    # function that selects the indices every iteration
    def fun_select(k):
        return sub2ind[int(np.random.choice(n, 1, p=prob_subset))]

    # initialise variables
    x, y = X.zero(), Y.zero()

    # output function to be used within the iterations
    callback = (odl.solvers.CallbackPrintIteration(step=n, end=', ') &
                odl.solvers.CallbackPrintTiming(step=n, cumulative=True) &
                CallbackStore(alg, iter_save[alg], iter_plot[alg]))

    x, y = X.zero(), Y.zero()  # initialise variables
    callback([x, y])
    g.prox_options['p'] = None

    if alg.startswith('pdhg') or alg.startswith('spdhg'):
        spdhg(x, f, g, A, tau, sigma, niter[alg], prob=prob, y=y,
              fun_select=fun_select, theta=theta, callback=callback)

    elif alg.startswith('pesquet'):
        spdhg_pesquet(x, f, g, A, tau, sigma, niter[alg], y=y,
                      fun_select=fun_select, callback=callback)

    else:
        assert False, "Algorithm not defined"

    np.save('{}/{}_output'.format(folder_npy, alg), (iter_save[alg],
            niter[alg], x, callback.callbacks[1].out, nsub[alg], theta), 
            allow_pickle=True)

# %% --- Analyse and visualise the output ---
algs = ['pdhg', 'spdhg_uni10', 'spdhg_uni50', 'pesquet_uni10', 'pesquet_uni50']

iter_save_v, niter_v, image_v, out_v, nsub_v, theta_v = {}, {}, {}, {}, {}, {}
for a in algs:
    (iter_save_v[a], niter_v[a], image_v[a], out_v[a], nsub_v[a],
     theta_v[a]) = np.load('{}/{}_output.npy'.format(folder_npy, a), 
            allow_pickle=True)

epochs_save = {a: np.array(iter_save_v[a]) / np.float(nsub_v[a]) for a in algs}

out_resorted = {}
for a in algs:
    print('==== ' + a)
    out_resorted[a] = {}
    K = len(iter_save_v[a])

    for meas in out_v[a][0].keys():  # quality measures
        print('    ==== ' + meas)
        out_resorted[a][meas] = np.nan * np.ones(K)

        for k in range(K):  # iterations
            out_resorted[a][meas][k] = out_v[a][k][meas]

    meas = 'obj_rel'
    print('    ==== ' + meas)
    out_resorted[a][meas] = np.nan * np.ones(K)

    for k in range(K):  # iterations
        out_resorted[a][meas][k] = ((out_v[a][k]['obj'] - obj_opt) /
                                    (out_v[a][0]['obj'] - obj_opt))

for a in algs:  # algorithms
    for meas in out_resorted[a].keys():  # quality measures
        for k in range(K):  # iterations
            if out_resorted[a][meas][k] <= 0:
                out_resorted[a][meas][k] = np.nan

fig = plt.figure()
for a in algs:
    misc.save_image(image_v[a], a, folder_today, 1, clim=clim)

markers = plt.Line2D.filled_markers

all_plots = out_resorted[algs[0]].keys()
logy_plot = all_plots

for plotx in ['linx', 'logx']:
    for meas in all_plots:
        print('============ ' + plotx + ' === ' + meas + ' ============')
        fig = plt.figure(1)
        plt.clf()

        if plotx == 'linx':
            if meas in logy_plot:
                for a in algs:
                    x = epochs_save[a]
                    y = out_resorted[a][meas]
                    plt.semilogy(x, y, linewidth=3, label=a)
            else:
                for j, a in enumerate(algs):
                    x = epochs_save[a]
                    y = out_resorted[a][meas]
                    plt.plot(x, y, linewidth=3, marker=markers[j],
                             markersize=7, markevery=.1, label=a)

        elif plotx == 'logx':
            if meas in logy_plot:
                for a in algs:
                    x = epochs_save[a][1:]
                    y = out_resorted[a][meas][1:]
                    plt.loglog(x, y, linewidth=3, label=a)
            else:
                for j, a in enumerate(algs):
                    x = epochs_save[a][1:]
                    y = out_resorted[a][meas][1:]
                    plt.semilogx(x, y, linewidth=3, marker=markers[j],
                                 markersize=7, markevery=.1, label=a)

        plt.title('{} v iteration'.format(meas))
        h = plt.gca()
        h.set_xlabel('epochs')
        plt.legend(loc='best')

        fig.savefig('{}/{}_{}_{}.png'.format(folder_today, filename, plotx,
                    meas), bbox_inches='tight')

# %% --- Prepapare visual output as in [1] ---
# set line width and style
lwidth = 2
lwidth_help = 2
lstyle = '-'
lstyle_help = '--'
# set colors using colorbrewer
bmap = brewer2mpl.get_map('Paired', 'Qualitative', 6)
colors = bmap.mpl_colors
colors.pop(1)
# set latex options
matplotlib.rc('text', usetex=True)
matplotlib.rcParams['text.latex.preamble'] = [r"\usepackage{amsmath}"]

# set font
fsize = 15
font = {'family': 'serif', 'size': fsize}
matplotlib.rc('font', **font)
matplotlib.rc('axes', labelsize=fsize)  # fontsize of x and y labels
matplotlib.rc('xtick', labelsize=fsize)  # fontsize of xtick labels
matplotlib.rc('ytick', labelsize=fsize)  # fontsize of ytick labels
matplotlib.rc('legend', fontsize=fsize)  # legend fontsize

# markers
marker = ('o', 'v', 's', 'p', 'd', 'x', 'x')  # set markers
mevery = [(i / 30., .15) for i in range(20)]  # how many markers to draw
msize = 9  # marker size

algs = ['pdhg', 'spdhg_uni10', 'spdhg_uni50', 'pesquet_uni10', 'pesquet_uni50']
label = ['PDHG', 'SPDHG (10 subsets)', 'SPDHG (50)', 'Pesquet\&Repetti (10)',
         'Pesquet\&Repetti (50)']
fig = []

# draw figures
fig.append(plt.figure(1))
plt.clf()
xlim = [0, 100]
ylim = [1e-8, 5e-1]
meas = 'dist'
for k, a in enumerate(algs):
    x = epochs_save[a]
    y = out_resorted[a][meas] / out_resorted[a][meas][0]
    i = (np.less_equal(x, xlim[1]) & np.greater_equal(x, xlim[0]) &
         np.less_equal(y, ylim[1]) & np.greater_equal(y, ylim[0]))
    plt.semilogy(x[i], y[i], color=colors[k], linestyle=lstyle,
                 marker=marker[k], markersize=msize, markevery=mevery[k],
                 linewidth=lwidth, label=label[k])

plt.gca().set_xlabel('iterations [epochs]')
plt.gca().set_ylabel('relative distance to saddle point')
plt.gca().yaxis.set_ticks(np.logspace(-6, -2, 3))
plt.legend(frameon=False)


fig.append(plt.figure(2))
plt.clf()
ylim = [1e-8, 1]
meas = 'obj_rel'
for k, a in enumerate(algs):
    x = epochs_save[a]
    y = out_resorted[a][meas]
    i = (np.less_equal(x, xlim[1]) & np.greater_equal(x, xlim[0]) &
         np.less_equal(y, ylim[1]) & np.greater_equal(y, ylim[0]))
    plt.semilogy(x[i], y[i], color=colors[k], linestyle=lstyle,
                 linewidth=lwidth, marker=marker[k], markersize=msize,
                 markevery=mevery[k], label=label[k])

plt.gca().set_xlabel('iterations [epochs]')
plt.gca().set_ylabel('relative objective')
plt.gca().yaxis.set_ticks(np.logspace(-7, -1, 3))
plt.legend(frameon=False)

# %%
for i, fi in enumerate(fig):
    fi.savefig('{}/{}_output{}.png'.format(folder_today, filename, i),
               bbox_inches='tight')
