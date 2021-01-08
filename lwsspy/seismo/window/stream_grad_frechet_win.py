import numpy as np
from obspy import Stream


def stream_grad_frechet_win(data: Stream, synt: Stream, dsyn: Stream) -> float:
    """Computes the gradient of a the least squares cost function wrt. 1
    parameter given its forward computation and the frechet derivative.
    The stats object of the Traces in the stream _*must*_ contain both
    `windows` and the `tapers` attributes!

    Parameters
    ----------
    data : Stream
        data
    synt : Stream
        synthetics
    dsyn : Stream
        frechet derivatives

    Returns
    -------
    float
        gradient

    Last modified: Lucas Sawade, 2020.09.28 19.00 (lsawade@princeton.edu)
    """

    x = 0.0

    for tr in data:
        network, station, component = (
            tr.stats.network, tr.stats.station, tr.stats.component)

        # Get the trace sampling time
        dt = tr.stats.delta
        d = tr.data

        try:
            s = synt.select(network=network, station=station,
                            component=component)[0].data
            dsdz = dsyn.select(network=network, station=station,
                               component=component)[0].data

            for win, tap in zip(tr.stats.windows, tr.stats.tapers):
                wsyn = s[win.left:win.right]
                wobs = d[win.left:win.right]
                wdsdz = dsdz[win.left:win.right]
                x += np.sum((wsyn - wobs) * wdsdz * tap) * dt

        except Exception as e:
            print(f"Error - Gradient - {network}.{station}.{component}: {e}")

    return x