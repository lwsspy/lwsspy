import matplotlib.pyplot as plt
import cartopy
from cartopy.mpl.ticker import LongitudeFormatter, LatitudeFormatter
from cartopy.crs import PlateCarree, Mollweide


def plot_map(fill=True, zorder=-10, projection=PlateCarree(),
             labelstopright: bool = True, labelsbottomleft: bool = True):
    """Plots map into existing axes.

    Parameters
    ----------
    fill : bool, optional
        fills the continents in light gray, by default True
    zorder : int, optional
        zorder of the map, by default -10
    projection : cartopy.crs.projection, optional
        projection to be used for the map.
    labelstopright : bool, optional
        flag to turn on or off the ticks
    labelsbottomleft : bool, optional
        flag to turn on or off the ticks

    Returns
    -------
    matplotlib.pyplot.Axes
        Axes in which the map was plotted

    Notes
    -----

    :Author:
        Lucas Sawade (lsawade@princeton.edu)

    :Last Modified:
        2020.01.07 16.30


    """

    ax = plt.gca()

    # Set xticks Should be automated, but I just don't know how rn
    ax.set_xticks([-180, -150, -120, -90, -60, -30, 0, 30,
                   60, 90, 120, 150, 180], crs=projection)
    ax.set_yticks([-90, -60, -30, 0, 30, 60, 90], crs=projection)

    # Put lables all around
    if projection is not Mollweide():

        # Set label formatter
        ax.xaxis.set_major_formatter(cartopy.mpl.ticker.LongitudeFormatter())
        ax.yaxis.set_major_formatter(cartopy.mpl.ticker.LatitudeFormatter())

        ax.tick_params(
            labeltop=labelstopright, labelright=labelstopright,
            labelbottom=labelsbottomleft, labelleft=labelsbottomleft)

    # Set gridlines
    # gl = ax.gridlines(draw_labels=False, linewidth=1, color='lightgray',
    #
    #     alpha=0.5, linestyle='-', zorder=-1.5)

    # Add land
    if fill:
        ax.add_feature(cartopy.feature.LAND, zorder=zorder, edgecolor='black',
                       linewidth=0.5, facecolor=(0.9, 0.9, 0.9))
    else:
        ax.add_feature(cartopy.feature.LAND, zorder=zorder, edgecolor='black',
                       linewidth=0.5, facecolor=(0, 0, 0, 0))

    return ax
