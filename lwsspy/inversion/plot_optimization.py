
import numpy as np
import matplotlib.pyplot as plt
from .optimizer import Optimization


def plot_optimization(optim: Optimization, outfile: str or None = None):
    """Plotting Misfit Reduction

    Parameters
    ----------
    optim : Optimization
        Optimization class
    outfile : str or None, optional
        Define where to save the figure. If None, plot is shown,
        by default None
    """
    # Get values
    c = optim.fcost_hist
    it = np.arange(len(c))

    # Plot values
    plt.figure(figsize=(6, 5))
    ax = plt.axes()
    ax.set_yscale("log")
    plt.plot(it, c, label=optim.type.upper())
    plt.legend(loc=1)
    plt.title(f"{optim.type.upper()} - Misfit Reduction")

    if outfile is not None:
        plt.savefig(outfile, dpi=300)
    else:
        plt.show()
