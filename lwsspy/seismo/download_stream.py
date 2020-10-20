from typing import Union, Tuple
from obspy import UTCDateTime, Stream, Inventory
from obspy.clients.fdsn import Client


def download_stream(origintime: UTCDateTime, duration: float = 7200,
                    network: Union[str, None] = "IU,II",
                    station: Union[str, None] = None,
                    location: Union[str, None] = "00",
                    channel: Union[str, None] = "BH*",
                    starttimeoffset: float = 0.0,
                    endtimeoffset: float = 0.0
                    ) -> Tuple[Stream, Inventory]:
    """Function to download data for a seismic section

    Parameters
    ----------
    origintime : UTCDateTime
        origintime of an earthquake
    duration : float, optional
        length of download in seconds, by default 7200
    network : str or None, optional
        Network restrictions, by default "IU,II"
    station : str or None, optional
        station restrictions, by default None
    location : str or None, optional
        location restrictions, by default "00"
    channel : str or None, optional
        channel restrictions, by default "BH*"
    starttimeoffset : float, optional
        set startime to later or earlier, by default 0.0
    endtimeoffset : float, optional
        set endtime to earlier or later, by default 0.0

    Returns
    -------
    Tuple[Stream, Inventory]
        tuple with a stream and an inventory
    """

    # Get times
    starttime = origintime + starttimeoffset
    endtime = origintime + duration + endtimeoffset

    # main program
    client = Client("IRIS")

    # Download the data
    st = client.get_waveforms(network, station, location, channel,
                              starttime, endtime)
    inv = client.get_stations(network=network, station=station,
                              location=location, channel=channel,
                              starttime=starttime, endtime=endtime,
                              level="response")

    return st, inv
