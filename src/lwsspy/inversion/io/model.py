import os
import numpy as np


def write_model(m, modldir, it, ls=None):
    """Takes in model vector, modldirectory, iteration and linesearch number
    and write model to modl directory.

    Parameters
    ----------
    m : ndarray
        modelvector
    modldir : str
        model directory
    it : int
        iteration number
    ls : int, optional
        linesearch number
    """

    # Create filename that contains both iteration and linesearch number
    if ls is not None:
        fname = f"m_it{it:05d}_ls{ls:05d}.npy"
    else:
        fname = f"m_it{it:05d}.npy"

    file = os.path.join(modldir, fname)
    np.save(file, m)


def read_model(modldir, it, ls=None):
    """Reads model vector

    Parameters
    ----------
    modldir : str
        model directory
    it : int
        iteration number
    ls : int, optional
        linesearch number

    Returns
    -------
    ndarray
        model vector
    """
    if ls is not None:
        fname = f"m_it{it:05d}_ls{ls:05d}.npy"
    else:
        fname = f"m_it{it:05d}.npy"
    file = os.path.join(modldir, fname)
    m = np.load(file)
    return m