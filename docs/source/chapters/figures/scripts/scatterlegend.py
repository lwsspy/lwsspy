# %%

from typing import Callable, DefaultDict, Optional, Union
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, Normalize, Colormap
from matplotlib.lines import Line2D

import lwsspy as lpy


# %%


N = 20
x = np.arange(N)
y = 2*np.random.rand(N)-1

cmap = plt.get_cmap('rainbow')
boundaries = np.arange(-1.0, 1.25, 0.25)
norm = BoundaryNorm(boundaries,  cmap.N)


def sizefunc(x): return (5*(1 + x))**2


fig = plt.figure(figsize=(8, 6))
ax = plt.gca()
ax.axis('off')

# Create Axes for the scatter plot
sax = lpy.axes_from_axes(ax, 1, extent=[0.0, 0.25, 0.85, 0.85])
scatter = sax.scatter(x, y, s=sizefunc(np.abs(y)), c=y, cmap=cmap, norm=norm,
                      edgecolor='k', linewidths=0.25)

# Create Axes for the horizontal legend
cax = lpy.axes_from_axes(ax, 1, extent=[0.0, 0.0, 0.85, 0.15])
cax.axis('off')

# Create Horizonat
legend1 = lpy.scatterlegend(
    boundaries, norm=norm, cmap=cmap, sizefunc=sizefunc,
    orientation='h',
    loc='upper center',
    frameon=False,
    fancybox=False,
    title='Random Number Scale'
)
# Create Axes for the vertical legend
cax2 = lpy.axes_from_axes(ax, 1, extent=[0.9, 0.25, 0.1, 0.85])
cax2.axis('off')

legend2 = lpy.scatterlegend(
    boundaries, norm=norm, cmap=cmap, sizefunc=sizefunc,
    orientation='v',
    frameon=False,
    fancybox=False,
    loc='center left',
    borderaxespad=0,
    title='Random #\nScale')
