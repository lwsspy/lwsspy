"""
This is the first script to enable depth iterations for the
CMTSOLUTTION depth.
"""
# %% Create inversion directory

# Internal
from obspy.core import event
from obspy.core.utcdatetime import UTCDateTime
from lwsspy.seismo.source import CMTSource
import lwsspy as lpy

# External
from typing import Callable, Union, Optional, List
import os
import shutil
import datetime
from copy import deepcopy
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_pdf import PdfPages
from itertools import repeat
from obspy import read, read_events, Stream, Trace
import multiprocessing.pool as mpp
import _pickle as cPickle
from .process_classifier import ProcessParams
import logging

lpy.updaterc(rebuild=False)


# Main parameters
window_dict = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "body.window.yml")

SPECFEM = "/scratch/gpfs/lsawade/MagicScripts/specfem3d_globe"
specfem_dict = {
    "bin": "link",
    "DATA": {
        "Par_file": "file",
    },
    "DATABASES_MPI": "link",
    "OUTPUT_FILES": "dir"
}
invdir = '/home/lsawade/lwsspy/invdir_real'
datadir = os.path.join(invdir, "Data")
scriptdir = os.path.dirname(os.path.abspath(__file__))

# %% Get Model CMT
processdict = lpy.read_yaml_file(os.path.join(scriptdir, "process.yml"))


download_dict = dict(
    network=",".join(['CU', 'G', 'GE', 'IC', 'II', 'IU', 'MN']),
    channel="BH*",
    location="00",
)

conda_activation = "source /usr/licensed/anaconda3/2020.7/etc/profile.d/conda.sh && conda activate lwsspy"
compute_node_login = "lsawade@traverse.princeton.edu"
bash_escape = "source ~/.bash_profile"
parameter_check_list = ['depth_in_m', "time_shift", 'latitude', 'longitude',
                        "m_rr", "m_tt", "m_pp", "m_rt", "m_rp", "m_tp"]
nosimpars = ["time_shift", "half_duration"]
hypo_pars = ['depth_in_m', "time_shift", 'latitude', 'longitude']
mt_params = ["m_rr", "m_tt", "m_pp", "m_rt", "m_rp", "m_tp"]

pardict = dict(
    time_shift=dict(scale=1.0, pert=None),
    depth_in_m=dict(scale=1000.0, pert=None)
)


class GCMT3DInversion:

    # parameter_check_list: list = [
    #     'm_rr', 'm_tt', 'm_pp', 'm_rt', 'm_rp', 'm_tp',
    #     'latitude', 'longitude', 'depth_in_m', 'time_shift', 'hdur'
    # ]
    parameter_check_list: list = parameter_check_list

    nosimpars: list = nosimpars

    def __init__(
            self,
            cmtsolutionfile: str,
            databasedir: str,
            specfemdir: str,
            processdict: dict = processdict,
            pardict: dict = pardict,
            zero_trace: bool = False,
            # zero_energy: bool = False,
            duration: float = 10800.0,
            starttime_offset: float = -50.0,
            endtime_offset: float = 50.0,
            download_data: bool = True,
            node_login: Optional[str] = None,
            conda_activation: str = conda_activation,
            bash_escape: str = bash_escape,
            download_dict: dict = download_dict,
            damping: float = 0.001,
            hypo_damping: float = 0.001,
            weighting: bool = True,
            normalize: bool = True,
            overwrite: bool = False,
            launch_method: str = "srun -n6 --gpus-per-task=1",
            process_func: Callable = lpy.process_stream,
            window_func: Callable = lpy.window_on_stream,
            multiprocesses: int = 20,
            loglevel: int = logging.DEBUG,
            log2stdout: bool = True,
            log2file: bool = True,
            start_label: Optional[str] = None,
            no_init: bool = False,
            MPIMODE: bool = False):

        self.MPIMODE = MPIMODE

        if self.MPIMODE:
            from mpi4py import MPI
            self.comm = MPI.COMM_WORLD
            self.rank = self.comm.Get_rank()
            self.size = self.comm.Get_size()

        else:
            self.comm = None
            self.rank = None
            self.size = None

        if self.MPIMODE is False or self.rank == 0:

            # CMTSource
            self.cmtsource = lpy.CMTSource.from_CMTSOLUTION_file(
                cmtsolutionfile)
            self.cmt_out = deepcopy(self.cmtsource)
            self.xml_event = read_events(cmtsolutionfile)[0]

            # File locations
            self.databasedir = os.path.abspath(databasedir)
            self.cmtdir = os.path.join(
                self.databasedir, self.cmtsource.eventname)

            if start_label is not None:
                start_label = "_" + start_label
            else:
                start_label = "_gcmt"
            self.cmt_in_db = os.path.join(
                self.cmtdir, self.cmtsource.eventname + start_label)
            self.overwrite: bool = overwrite
            self.download_data = download_data

            # Simulation stuff
            self.specfemdir = specfemdir
            self.specfem_dict = specfem_dict
            self.launch_method = launch_method.split()

            # Processing parameters
            self.processdict = processdict
            self.process_func = process_func
            self.window_func = window_func
            self.duration = duration
            self.duration_in_m = np.ceil(duration/60.0)
            self.simulation_duration = np.round(self.duration_in_m * 1.02)
            self.multiprocesses = multiprocesses
            self.sumfunc = lambda results: Stream(results)

            # Inversion dictionary
            self.pardict = pardict

            # Download parameters
            self.starttime_offset = starttime_offset
            self.endtime_offset = endtime_offset
            self.download_dict = download_dict

            # Compute Node does not have internet
            self.conda_activation = conda_activation
            self.node_login = node_login
            self.bash_escape = bash_escape

            # Inversion parameters:
            self.nsim = 1
            self.__get_number_of_forward_simulations__()
            self.not_windowed_yet = True
            self.zero_trace = zero_trace
            # self.zero_energy = zero_energy
            self.damping = damping
            self.hypo_damping = hypo_damping
            self.normalize = normalize
            self.weighting = weighting
            self.weights_rtz = dict(R=1.0, T=1.0, Z=1.0)

            # Initialize data dictionaries
            self.data_dict: dict = dict()
            self.synt_dict: dict = dict()
            self.zero_window_removal_dict: dict = dict()

            # Logging
            self.loglevel = loglevel
            self.log2stdout = log2stdout
            self.log2file = log2file

            # Basic Checks
            self.__basic_check__()

            # Initialize
            self.init()

            # Set iteration number
            self.iteration = 0

        if self.MPIMODE:
            self.comm.Barrier()

    def __basic_check__(self):

        # Check Parameter dict for wrong parameters
        for _par in self.pardict.keys():
            if _par not in self.parameter_check_list:
                raise ValueError(
                    f"{_par} not supported at this point. \n"
                    f"Available parameters are {self.parameter_check_list}")

        # If one moment tensor parameter is given all must be given.
        if any([_par in self.pardict for _par in mt_params]):
            checklist = [
                _par for _par in mt_params if _par in self.pardict]
            if not all([_par in checklist for _par in mt_params]):
                raise ValueError("If one moment tensor parameter is to be "
                                 "inverted. All must be inverted.\n"
                                 "Update your pardict")
            else:
                self.moment_tensor_inv = True
        else:
            self.moment_tensor_inv = False

        # Check zero trace condition
        if self.zero_trace:
            if self.moment_tensor_inv is False:
                raise ValueError("Can only use Zero Trace condition "
                                 "if inverting for Moment Tensor.\n"
                                 "Update your pardict.")

    def __setup_logger__(self):

        # create logger
        self.logger = logging.getLogger(
            f"GCMT3D-{self.cmtsource.eventname}")
        self.logger.setLevel(self.loglevel)
        self.logger.handlers = []

        # stop propagting to root logger
        self.logger.propagate = False

        # create formatter
        formatter = lpy.CustomFormatter()

        # Add file logger if necessary
        if self.log2file:
            fh = logging.FileHandler(self.logfile, mode='w+')
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)

        # Add stdout logger
        if self.log2stdout:
            sh = logging.StreamHandler()
            sh.setLevel(self.loglevel)
            sh.setFormatter(formatter)
            self.logger.addHandler(sh)

        # Make sure not multiple handlers are created
        self.logger.handler_set = True

        # Starting the log
        lpy.log_bar(
            f"GCMT3D LOG: {self.cmtsource.eventname}",
            plogger=self.logger.info)

    def adapt_processdict(self):
        # Logging
        lpy.log_action(
            "Adapting processing dictionary", plogger=self.logger.debug)

        # Get Process parameters
        PP = ProcessParams(
            self.cmtsource.moment_magnitude, self.cmtsource.depth_in_m)
        proc_params = PP.determine_all()

        # Adjust the process dictionary
        for _wave, _process_dict in proc_params.items():
            if _wave in self.processdict:
                # Adjust weight or drop wave altogether
                if _process_dict['weight'] == 0.0 \
                        or _process_dict['weight'] is None:
                    self.processdict.popitem(_wave)
                    continue

                else:
                    self.processdict[_wave]['weight'] = _process_dict["weight"]

                # Adjust pre_filt
                self.processdict[_wave]['process']['pre_filt'] = \
                    [1.0/x for x in _process_dict["filter"]]

                # Adjust trace length depending on the duration
                # given to the class
                self.processdict[_wave]['process']['relative_endtime'] = \
                    _process_dict["relative_endtime"]
                if self.processdict[_wave]['process']['relative_endtime'] > self.duration:
                    self.processdict[_wave]['process']['relative_endtime'] = self.duration

                # Adjust windowing config
                for _windict in self.processdict[_wave]["window"]:
                    _windict["config"]["min_period"] = _process_dict["filter"][3]
                    _windict["config"]["max_period"] = _process_dict["filter"][0]

        # Remove unnecessary wavetypes
        popkeys = []
        for _wave in self.processdict.keys():
            if _wave not in proc_params:
                popkeys.append(_wave)
        for _key in popkeys:
            self.processdict.pop(_key, None)

        # Dump the processing file in the cmt directory
        lpy.log_action(
            "Writing it to file", plogger=self.logger.debug)
        lpy.write_yaml_file(
            self.processdict, os.path.join(self.cmtdir, "process.yml"))

    def init(self):

        # Initialize directory
        self.__initialize_dir__()

        # Set up the Logger, so that progress is monitored
        self.__setup_logger__()
        lpy.log_section(
            "Setting up the directories and Waveform dicts",
            plogger=self.logger.info)

        # Fix process dict
        self.adapt_processdict()

        # This has to happen after the processdict is adapted since the
        # process dict is used to create the dictionaries
        self.__initialize_waveform_dictionaries__()

        # Get observed data and process data
        if self.download_data:
            with lpy.Timer(plogger=self.logger.info):
                self.__download_data__()

        # Initialize model vector
        self.__init_model_and_scale__()

    def __initialize_dir__(self):

        # Subdirectories
        self.datadir = os.path.join(self.cmtdir, "data")
        self.waveformdir = os.path.join(self.datadir, "waveforms")
        self.stationdir = os.path.join(self.datadir, "stations")
        self.syntdir = os.path.join(self.cmtdir, "synt")
        self.logfile = os.path.join(
            self.cmtdir, self.cmtsource.eventname + ".log")

        # Create subsynthetic directories
        self.synt_syntdir = os.path.join(self.syntdir, "cmt")
        self.synt_pardirs = dict()
        for _par in self.pardict.keys():
            self.synt_pardirs[_par] = os.path.join(self.syntdir, _par)

        # Create database directory if doesn't exist
        self.__create_dir__(self.databasedir)

        # Create entry directory
        self.__create_dir__(self.cmtdir, overwrite=self.overwrite)

        # Create CMT solution
        if os.path.exists(self.cmt_in_db) is False:
            self.cmtsource.write_CMTSOLUTION_file(self.cmt_in_db)
        else:
            check_cmt = lpy.CMTSource.from_CMTSOLUTION_file(self.cmt_in_db)
            if check_cmt != self.cmtsource:
                raise ValueError('Already have a CMTSOLUTION, '
                                 'but it is different from the input one.')

        # Create data directory
        self.__create_dir__(self.datadir)

        # Simulation directory are created as part of the prep simulations
        # routine

    def __init_model_and_scale__(self):

        # Update the scale parameter for the moment tensor inversion
        # depending on the original size of the moment tensor
        if self.moment_tensor_inv:
            for _par, _dict in self.pardict.items():
                if _par in mt_params:
                    _dict["scale"] = self.cmtsource.M0

        # Check whether Mrr, Mtt, Mpp are there for zero trace condition
        # It's important to note here that the zero_trace_array in the following
        # part is simply the gradient of the constraint with repspect to the
        # model parameters. For the zero_energy constraint, we explicitly have
        # have to compute this gradient from measurements, and is therefore
        # "missing" here
        if self.zero_trace:  # and not self.zero_energy:

            self.zero_trace_array = np.array([1.0 if _par in ['m_rr', 'm_tt', 'm_pp'] else 0.0
                                              for _par in self.pardict.keys()])
            self.zero_trace_index_array = np.where(
                self.zero_trace_array == 1.)[0]
            self.zero_trace_array = np.append(self.zero_trace_array, 0.0)

        # elif self.zero_trace and self.zero_energy:

        #     self.zero_trace_array = np.array([1.0 if _par in ['m_rr', 'm_tt', 'm_pp'] else 0.0
        #                                       for _par in self.pardict.keys()])
        #     self.zero_trace_index_array = np.where(
        #         self.zero_trace_array == 1.)[0]
        #     self.zero_trace_array = np.append(self.zero_trace_array, 0.0)
        #     self.zero_trace_array = np.append(self.zero_trace_array, 0.0)

        # damping settings
        if self.damping > 0.0:
            # Do nothing as damping is easy to handle
            pass

        elif self.hypo_damping > 0.0:
            # Figure out where to dampen!
            self.hypo_damp_array = np.array([1.0 if _par in hypo_pars else 0.0
                                             for _par in self.pardict.keys()])
            self.hypo_damp_index_array = np.where(
                self.hypo_damp_array == 1.)[0]

            self.logger.debug("Hypocenter-Damping Indeces:")
            self.logger.debug(self.hypo_damp_index_array)

        # Get the model vector given the parameters to invert for
        self.model = np.array(
            [getattr(self.cmtsource, _par) for _par in self.pardict.keys()])
        self.init_model = 1.0 * self.model
        self.pars = [_par for _par in self.pardict.keys()]

        # Create scaling vector
        self.scale = np.array([10**lpy.magnitude(getattr(self.cmtsource, _par))
                               if _par not in mt_params else _dict['scale']
                               for _par, _dict in self.pardict.items()])

        self.scaled_model = self.model/self.scale
        self.init_scaled_model = 1.0 * self.scaled_model

    def __initialize_waveform_dictionaries__(self):

        for _wtype in self.processdict.keys():
            self.data_dict[_wtype] = Stream()
            self.synt_dict[_wtype] = dict()
            self.synt_dict[_wtype]["synt"] = Stream()

            for _par in self.pardict.keys():
                self.synt_dict[_wtype][_par] = Stream()

    def process_data(self):

        if self.MPIMODE is False or self.rank == 0:
            lpy.log_section(
                "Loading and processing the data",
                plogger=self.logger.info)
            t = lpy.Timer(plogger=self.logger.info)
            t.start()

        self.__load_data__()
        self.__process_data__()

        if self.MPIMODE is False or self.rank == 0:
            t.stop()

    def process_synt(self):

        if self.MPIMODE is False or self.rank == 0:
            lpy.log_section(
                "Loading and processing the modeled data",
                plogger=self.logger.info)
            t = lpy.Timer(plogger=self.logger.info)
            t.start()

        self.__load_synt__()
        self.__process_synt__()

        if self.MPIMODE is False or self.rank == 0:
            t.stop()

    def get_windows(self):

        # Prepare all simulations
        self.__prep_simulations__()
        self.__write_sources__()

        # Run first set of simulations
        self.__run_simulations__()

        # Process all syntheticss
        self.process_all_synt()

        # Copy the initial synthetics
        self.copy_init_synt()

        # Window the data
        self.__window__()

        # Prep next set of simulations
        with lpy.Timer(plogger=self.logger.info):
            self.__prep_simulations__()

        if self.MPIMODE is False or self.rank == 0:
            self.not_windowed_yet = False

    def copy_init_synt(self):

        if self.MPIMODE is False or self.rank == 0:
            # Copy the initial waveform dictionary
            self.synt_dict_init = deepcopy(self.synt_dict)

        if self.MPIMODE:
            self.comm.Barrier()

    def __compute_weights__(self):

        if self.MPIMODE is False or self.rank == 0:

            # Computing the weights
            lpy.log_bar("Computing Weights", plogger=self.logger.info)

            # Weight dictionary
            self.weights = dict()
            self.weights["event"] = [
                self.cmtsource.latitude, self.cmtsource.longitude]

            waveweightdict = dict()
            for _i, (_wtype, _stream) in enumerate(self.data_dict.items()):

                # Dictionary to keep track of the sum in each wave type.
                waveweightdict[_wtype] = 0

                # Get wave type weight from process.yml
                self.weights[_wtype] = dict()
                waveweight = self.processdict[_wtype]["weight"]
                self.weights[_wtype]["weight"] = deepcopy(waveweight)

                # Create dict to access traces
                RTZ_traces = dict()
                for _component, _cweight in self.weights_rtz.items():

                    # Copy compnent weight to dictionary
                    self.weights[_wtype][_component] = dict()
                    self.weights[_wtype][_component]["weight"] = deepcopy(
                        _cweight)

                    # Create reference
                    RTZ_traces[_component] = []

                    # Only add ttraces that have windows.
                    for _tr in _stream:
                        if _tr.stats.component == _component \
                                and len(_tr.stats.windows) > 0:
                            RTZ_traces[_component].append(_tr)

                    # Get locations
                    latitudes = []
                    longitudes = []
                    for _tr in RTZ_traces[_component]:
                        latitudes.append(_tr.stats.latitude)
                        longitudes.append(_tr.stats.longitude)
                    latitudes = np.array(latitudes)
                    longitudes = np.array(longitudes)

                    # Save locations into dict
                    self.weights[_wtype][_component]["lat"] = deepcopy(
                        latitudes)
                    self.weights[_wtype][_component]["lon"] = deepcopy(
                        longitudes)

                    # Get azimuthal weights for the traces of each component
                    if len(latitudes) > 1 and len(longitudes) > 2:
                        azi_weights = lpy.azi_weights(
                            self.cmtsource.latitude,
                            self.cmtsource.longitude,
                            latitudes, longitudes, nbins=12, p=0.5)

                        # Save azi weights into dict
                        self.weights[_wtype][_component]["azimuthal"] = \
                            deepcopy(azi_weights)

                        # Get Geographical weights
                        gw = lpy.GeoWeights(latitudes, longitudes)
                        _, _, ref, _ = gw.get_condition()
                        geo_weights = gw.get_weights(ref)

                        # Save geo weights into dict
                        self.weights[_wtype][_component]["geographical"] = \
                            deepcopy(geo_weights)

                        # Compute Combination weights.
                        weights = (azi_weights * geo_weights)
                        weights /= np.sum(weights)/len(weights)
                        self.weights[_wtype][_component]["combination"] = \
                            deepcopy(weights)

                    # Figuring out weighting for 2 events does not make sense
                    # There is no relative clustering.
                    elif len(latitudes) == 2 and len(longitudes) == 2:
                        self.weights[_wtype][_component]["azimuthal"] = \
                            [0.5, 0.5]
                        self.weights[_wtype][_component]["geographical"] = \
                            [0.5, 0.5]
                        self.weights[_wtype][_component]["combination"] = \
                            [0.5, 0.5]
                        weights = [0.5, 0.5]

                    elif len(latitudes) == 1 and len(longitudes) == 1:
                        self.weights[_wtype][_component]["azimuthal"] = [1.0]
                        self.weights[_wtype][_component]["geographical"] = \
                            [1.0]
                        self.weights[_wtype][_component]["combination"] = [1.0]
                        weights = [1.0]
                    else:
                        self.weights[_wtype][_component]["azimuthal"] = []
                        self.weights[_wtype][_component]["geographical"] = []
                        self.weights[_wtype][_component]["combination"] = []
                        weights = []

                    # Add weights to traces
                    for _tr, _weight in zip(RTZ_traces[_component], weights):
                        _tr.stats.weights = _cweight * _weight
                        waveweightdict[_wtype] += np.sum(_cweight * _weight)

            # Normalize by component and aximuthal weights
            for _i, (_wtype, _stream) in enumerate(self.data_dict.items()):
                # Create dict to access traces
                RTZ_traces = dict()

                for _component, _cweight in self.weights_rtz.items():
                    RTZ_traces[_component] = []
                    for _tr in _stream:
                        if _tr.stats.component == _component \
                                and "weights" in _tr.stats:
                            RTZ_traces[_component].append(_tr)

                    self.weights[_wtype][_component]["final"] = []
                    for _tr in RTZ_traces[_component]:
                        _tr.stats.weights /= waveweightdict[_wtype]

                        self.weights[_wtype][_component]["final"].append(
                            deepcopy(_tr.stats.weights))

            with open(os.path.join(self.cmtdir, "weights.pkl"), "wb") as f:
                cPickle.dump(deepcopy(self.weights), f)

    def process_all_synt(self):

        if self.MPIMODE is False or self.rank == 0:
            # Logging
            lpy.log_section(
                "Loading and processing all modeled data",
                plogger=self.logger.info)
            t = lpy.Timer(plogger=self.logger.info)
            t.start()

        self.__load_synt__()
        self.__load_synt_par__()
        self.__process_synt__()
        self.__process_synt_par__()

        if self.MPIMODE is False or self.rank == 0:
            t.stop()

    def __get_number_of_forward_simulations__(self):

        # For normal forward synthetics
        self.nsim = 1

        # Add one for each parameters that requires a forward simulation
        for _par in self.pardict.keys():
            if _par not in self.nosimpars:
                self.nsim += 1

    def __download_data__(self):

        # Setup download times depending on input...
        # Maybe get from process dict?
        starttime = self.cmtsource.cmt_time + self.starttime_offset
        endtime = self.cmtsource.cmt_time + self.duration \
            + self.endtime_offset

        lpy.log_bar("Data Download", plogger=self.logger.info)

        if self.node_login is None:
            lpy.download_waveforms_to_storage(
                self.datadir, starttime=starttime, endtime=endtime,
                **self.download_dict)

        else:
            from subprocess import Popen, PIPE
            download_cmd = (
                f"download-data "
                f"-d {self.datadir} "
                f"-s {starttime} "
                f"-e {endtime} "
                f"-N {self.download_dict['network']} "
                f"-C {self.download_dict['channel']} "
                f"-L {self.download_dict['location']}"
            )

            login_cmd = ["ssh", "-T", self.node_login]
            comcmd = f"""
            {self.conda_activation}
            {download_cmd}
            """

            lpy.log_action(
                f"Logging into {' '.join(login_cmd)} and downloading",
                plogger=self.logger.info)
            self.logger.debug(f"Command: \n{comcmd}\n")

            with Popen(["ssh", "-T", self.node_login],
                       stdin=PIPE, stdout=PIPE, stderr=PIPE,
                       universal_newlines=True) as p:
                output, error = p.communicate(comcmd)

            if p.returncode != 0:
                self.logger.error(output)
                self.logger.error(error)
                self.logger.error(p.returncode)
                raise ValueError("Download not successful.")

    def __load_data__(self):

        if self.MPIMODE is False or self.rank == 0:
            lpy.log_action("Loading the data", plogger=self.logger.info)

            # Load Station data
            self.stations = lpy.read_inventory(
                os.path.join(self.stationdir, "*.xml"))

            # Load seismic data
            self.data = read(os.path.join(self.waveformdir, "*.mseed"))
            self.raw_data = self.data.copy()
            # Populate the data dictionary.
            for _wtype, _ in self.data_dict.items():
                self.data_dict[_wtype] = self.data.copy()

        if self.MPIMODE:
            self.comm.Barrier()

    def __process_data__(self):

        # Process each wavetype.
        if self.MPIMODE is False or self.rank == 0:
            wtypes = list(self.data_dict.keys())
        else:
            wtypes = None

        if self.MPIMODE:
            wtypes = self.comm.bcast(wtypes, root=0)

        for _wtype in wtypes:
            if self.MPIMODE is False or self.rank == 0:
                lpy.log_action(
                    f"Processing data for {_wtype}",
                    plogger=self.logger.info)

                # Call processing function and processing dictionary
                starttime = self.cmtsource.cmt_time \
                    + self.processdict[_wtype]["process"]["relative_starttime"]
                endtime = self.cmtsource.cmt_time \
                    + self.processdict[_wtype]["process"]["relative_endtime"]

                # Process dict
                processdict = deepcopy(self.processdict[_wtype]["process"])

                processdict.pop("relative_starttime")
                processdict.pop("relative_endtime")
                processdict["starttime"] = starttime
                processdict["endtime"] = endtime
                processdict["inventory"] = self.stations
                processdict.update(dict(
                    remove_response_flag=True,
                    event_latitude=self.cmtsource.latitude,
                    event_longitude=self.cmtsource.longitude,
                    geodata=True)
                )

            if self.MPIMODE:
                # Initialize multiprocessing Class
                PC = lpy.MPIProcessStream()

                # Populate with stream and process
                if PC.rank == 0:
                    PC.get_stream_and_processdict(
                        self.data_dict[_wtype], processdict)

                # Process traces
                PC.process()

                # Copy to the data dictionary
                if PC.rank == 0:
                    self.data_dict[_wtype] = deepcopy(PC.processed_stream)

                # Prohibit runaway (just in case)
                self.comm.Barrier()

            elif self.multiprocesses < 1:
                self.data_dict[_wtype] = self.process_func(
                    self.data_dict[_wtype], **processdict)
            else:
                lpy.log_action(
                    f"Processing in parallel using {self.multiprocesses} cores",
                    plogger=self.logger.debug)
                self.data_dict[_wtype] = lpy.multiprocess_stream(
                    self.data_dict[_wtype], processdict)

    def __load_synt__(self):

        if self.MPIMODE is False or self.rank == 0:

            # if self.specfemdir is not None:
            # Load forward data
            lpy.log_action("Loading forward synthetics",
                           plogger=self.logger.info)
            temp_synt = read(os.path.join(
                self.synt_syntdir, "OUTPUT_FILES", "*.sac"))

            for _wtype in self.processdict.keys():
                self.synt_dict[_wtype]["synt"] = temp_synt.copy()

        if self.MPIMODE:
            self.comm.Barrier()

    def __load_synt_par__(self):

        if self.MPIMODE is False or self.rank == 0:
            # Load frechet data
            lpy.log_action("Loading parameter synthetics",
                           plogger=self.logger.info)
            for _par, _pardirs in self.synt_pardirs.items():
                lpy.log_action(f"    {_par}", plogger=self.logger.info)

                if _par in self.nosimpars:
                    temp_synt = read(os.path.join(
                        self.synt_syntdir, "OUTPUT_FILES", "*.sac"))
                else:
                    # Load foward/perturbed data
                    temp_synt = read(os.path.join(
                        _pardirs, "OUTPUT_FILES", "*.sac"))

                # Populate the wavetype Streams.
                for _wtype, _ in self.data_dict.items():
                    self.synt_dict[_wtype][_par] = temp_synt.copy()

            del temp_synt

        if self.MPIMODE:
            self.comm.Barrier()

    def __process_synt__(self, no_grad=False):

        if self.MPIMODE:
            parallel = False

        elif self.multiprocesses > 1:
            parallel = True
            p = mpp.Pool(processes=self.multiprocesses)
            lpy.log_action(
                f"Processing in parallel using {self.multiprocesses} cores",
                plogger=self.logger.debug)
        else:
            parallel = False

        if self.MPIMODE is False or self.rank == 0:
            wtypes = list(self.processdict.keys())
        else:
            wtypes = None

        if self.MPIMODE:
            wtypes = self.comm.bcast(wtypes, root=0)

        # Process each wavetype.
        for _wtype in wtypes:

            if self.MPIMODE is False or self.rank == 0:
                # Call processing function and processing dictionary
                starttime = self.cmtsource.cmt_time \
                    + self.processdict[_wtype]["process"]["relative_starttime"]
                endtime = self.cmtsource.cmt_time \
                    + self.processdict[_wtype]["process"]["relative_endtime"]

                # Process dict
                processdict = deepcopy(self.processdict[_wtype]["process"])
                processdict.pop("relative_starttime")
                processdict.pop("relative_endtime")
                processdict["starttime"] = starttime
                processdict["endtime"] = endtime
                processdict["inventory"] = self.stations
                processdict.update(dict(
                    remove_response_flag=False,
                    event_latitude=self.cmtsource.latitude,
                    event_longitude=self.cmtsource.longitude)
                )
                lpy.log_action(
                    f"Processing {_wtype}/synt: "
                    f"{len(self.synt_dict[_wtype]['synt'])} waveforms",
                    plogger=self.logger.info)

            if self.MPIMODE:
                # Initialize multiprocessing Class
                PC = lpy.MPIProcessStream()

                # Populate with stream and process
                if PC.rank == 0:
                    PC.get_stream_and_processdict(
                        self.synt_dict[_wtype]["synt"], processdict)

                # Process traces
                PC.process()

                # Copy to the data dictionary
                if PC.rank == 0:
                    self.synt_dict[_wtype]["synt"] = deepcopy(
                        PC.processed_stream)

                # Prohibit runaway traces
                self.comm.Barrier()

            elif parallel:
                self.synt_dict[_wtype]["synt"] = lpy.multiprocess_stream(
                    self.synt_dict[_wtype]["synt"], processdict)
            else:
                self.synt_dict[_wtype]["synt"] = self.process_func(
                    self.synt_dict[_wtype]["synt"], self.stations,
                    **processdict)

        if self.MPIMODE:
            self.comm.Barrier()

        if parallel:
            p.close()

    def __process_synt_par__(self):

        if self.MPIMODE:
            parallel = False
        elif self.multiprocesses > 1:
            parallel = True
            p = mpp.Pool(processes=self.multiprocesses)
            lpy.log_action(
                f"Processing in parallel using {self.multiprocesses} cores",
                plogger=self.logger.debug)
        else:
            parallel = False

        if self.MPIMODE is False or self.rank == 0:
            wtypes = list(self.processdict.keys())
            parkeys = list(self.pardict.keys())
            parsubdicts = list(self.pardict.values())
        else:
            wtypes = None
            parkeys = None
            parsubdicts = None

        if self.MPIMODE:
            wtypes = self.comm.bcast(wtypes, root=0)

        for _wtype in wtypes:

            if self.MPIMODE is False or self.rank == 0:
                # Call processing function and processing dictionary
                starttime = self.cmtsource.cmt_time \
                    + self.processdict[_wtype]["process"]["relative_starttime"]
                endtime = self.cmtsource.cmt_time \
                    + self.processdict[_wtype]["process"]["relative_endtime"]

                # Process dict
                processdict = deepcopy(self.processdict[_wtype]["process"])
                processdict.pop("relative_starttime")
                processdict.pop("relative_endtime")
                processdict["starttime"] = starttime
                processdict["endtime"] = endtime
                processdict.update(dict(
                    remove_response_flag=False,
                    event_latitude=self.cmtsource.latitude,
                    event_longitude=self.cmtsource.longitude)
                )

            # Process each wavetype.
            for _par, _parsubdict in zip(parkeys, parsubdicts):

                if self.MPIMODE is False or self.rank == 0:
                    lpy.log_action(
                        f"Processing {_wtype}/{_par}: "
                        f"{len(self.synt_dict[_wtype][_par])} waveforms",
                        plogger=self.logger.info)

                if _par in self.nosimpars:
                    if self.MPIMODE is False or self.rank == 0:
                        self.synt_dict[_wtype][_par] = \
                            self.synt_dict[_wtype]["synt"].copy()

                else:
                    if self.MPIMODE:
                        # Initialize multiprocessing Class
                        PC = lpy.MPIProcessStream()

                        # Populate with stream and process
                        if PC.rank == 0:
                            PC.get_stream_and_processdict(
                                self.synt_dict[_wtype]["synt"], processdict)

                        # Process traces
                        PC.process()

                        # Copy to the data dictionary
                        if PC.rank == 0:
                            self.synt_dict[_wtype]["synt"] = deepcopy(
                                PC.processed_stream)

                        # Prohibit runaway traces
                        self.comm.Barrier()

                    elif parallel:
                        self.synt_dict[_wtype][_par] = self.sumfunc(
                            lpy.starmap_with_kwargs(
                                p, self.process_func,
                                zip(self.synt_dict[_wtype]
                                    [_par], repeat(self.stations)),
                                repeat(processdict),
                                len(self.synt_dict[_wtype][_par]))).copy()
                    else:
                        self.synt_dict[_wtype][_par] = self.process_func(
                            self.synt_dict[_wtype][_par], self.stations,
                            **processdict)

                if self.MPIMODE is False or self.rank == 0:

                    # divide by perturbation value and scale by scale length
                    if _parsubdict["pert"] is not None:
                        if _parsubdict["pert"] != 1.0:
                            lpy.stream_multiply(
                                self.synt_dict[_wtype][_par],
                                1.0/_parsubdict["pert"])

                    # Compute frechet derivative with respect to time
                    if _par == "time_shift":
                        self.synt_dict[_wtype][_par].differentiate(
                            method='gradient')
                        lpy.stream_multiply(self.synt_dict[_wtype][_par], -1.0)
                    if _par == "depth_in_m":
                        lpy.stream_multiply(
                            self.synt_dict[_wtype][_par], 1.0/1000.0)

        if self.MPIMODE:
            self.comm.Barrier()

        if parallel:
            p.close()

    def __window__(self):

        # Process each wavetype.
        if self.MPIMODE is False or self.rank == 0:
            wtypes = list(self.data_dict.keys())
        else:
            wtypes = None

        if self.MPIMODE:
            wtypes = self.comm.bcast(wtypes, root=0)

        # Debug flag
        debug = True if self.loglevel >= 20 else False

        for _wtype in wtypes:

            if self.MPIMODE is False or self.rank == 0:
                lpy.log_action(f"Windowing {_wtype}", plogger=self.logger.info)
                window_dicts = self.processdict[_wtype]["window"]
            else:
                window_dicts = None

            if self.MPIMODE:
                window_dicts = self.comm.bcast(window_dicts, root=0)

            # Loop over window dictionary
            for window_dict in window_dicts:

                if self.MPIMODE is False or self.rank == 0:

                    # Wrap window dictionary
                    wrapwindowdict = dict(
                        station=self.stations,
                        event=self.xml_event,
                        config_dict=window_dict,
                        _verbose=debug
                    )

                # Serial or Multiprocessing
                if self.MPIMODE:

                    # Initialize multiprocessing Class
                    WC = lpy.MPIWindowStream()

                    # Populate with stream and process
                    if WC.rank == 0:
                        WC.get_streams_and_windowdict(
                            self.data_dict[_wtype],
                            self.synt_dict[_wtype]["synt"],
                            wrapwindowdict)

                    # Process traces
                    WC.window()

                    # Copy to the data dictionary
                    if WC.rank == 0:
                        self.data_dict[_wtype] = deepcopy(
                            WC.processed_stream)

                    # Prohibit runaway traces
                    self.comm.Barrier()

                if self.multiprocesses <= 1:
                    self.window_func(
                        self.data_dict[_wtype],
                        self.synt_dict[_wtype]["synt"],
                        **wrapwindowdict)
                else:

                    self.data_dict[_wtype] = lpy.multiwindow_stream(
                        self.data_dict[_wtype],
                        self.synt_dict[_wtype]["synt"],
                        wrapwindowdict, nprocs=self.multiprocesses)

            if len(self.processdict[_wtype]["window"]) > 1:
                lpy.log_action(
                    f"Merging {_wtype}windows", plogger=self.logger.info)
                self.merge_windows(
                    self.data_dict[_wtype],
                    self.synt_dict[_wtype]["synt"])

            # After each trace has windows attached continue
            lpy.add_tapers(self.data_dict[_wtype], taper_type="tukey",
                           alpha=0.25, verbose=debug)

            # Some traces aren't even iterated over..
            for _tr in self.data_dict[_wtype]:
                if "windows" not in _tr.stats:
                    _tr.stats.windows = []

        if self.MPIMODE:
            self.comm.Barrier()

    def merge_windows(self, data_stream: Stream, synt_stream: Stream):

        for obs_tr in data_stream:
            try:
                synt_tr = synt_stream.select(
                    station=obs_tr.stats.station,
                    network=obs_tr.stats.network,
                    component=obs_tr.stats.component)[0]
            except Exception as e:
                self.logger.warning(e)
                self.logger.warning(
                    "Couldn't find corresponding synt for "
                    f"obsd trace({obs_tr.id}): {e}")
                continue
            if len(obs_tr.stats.windows) > 1:
                obs_tr.stats.windows = lpy.merge_trace_windows(obs_tr, synt_tr)

    def optimize(self, optim: lpy.Optimization):

        try:
            if self.zero_trace:
                model = np.append(deepcopy(self.scaled_model), 1.0)
            else:
                model = deepcopy(self.scaled_model)
            optim_out = optim.solve(optim, model)
            self.model = deepcopy(optim.model)
            return optim_out
        except Exception as e:
            print(e)
            return optim

    def __prep_simulations__(self):

        if self.MPIMODE is False or self.rank == 0:

            lpy.log_action("Prepping simulations", plogger=self.logger.info)
            # Create forward directory
            if self.specfemdir is not None:
                lpy.createsimdir(self.specfemdir, self.synt_syntdir,
                                 specfem_dict=self.specfem_dict)
            else:
                self.__create_dir__(self.syntdir)

            # Create one directory synthetics and each parameter
            for _par, _pardir in self.synt_pardirs.items():
                if _par not in self.nosimpars:
                    if self.specfemdir is not None:
                        lpy.createsimdir(self.specfemdir, _pardir,
                                         specfem_dict=self.specfem_dict)
                    else:
                        self.__create_dir__(_pardir)

            # Write stations file
            lpy.inv2STATIONS(
                self.stations, os.path.join(self.synt_syntdir, "DATA", "STATIONS"))

            # Update Par_file depending on the parameter.
            syn_parfile = os.path.join(self.synt_syntdir, "DATA", "Par_file")
            syn_pars = lpy.read_parfile(syn_parfile)
            syn_pars["USE_SOURCE_DERIVATIVE"] = False

            # Adapt duration
            syn_pars["RECORD_LENGTH_IN_MINUTES"] = self.simulation_duration

            # Write Stuff to Par_file
            lpy.write_parfile(syn_pars, syn_parfile)

            # Do the same for the parameters to invert for.
            for _par, _pardir in self.synt_pardirs.items():

                # Half duration an time-shift don't need extra simulations
                if _par not in self.nosimpars:

                    # Write stations file
                    lpy.inv2STATIONS(
                        self.stations, os.path.join(_pardir, "DATA", "STATIONS"))

                    # Update Par_file depending on the parameter.
                    dsyn_parfile = os.path.join(_pardir, "DATA", "Par_file")
                    dsyn_pars = lpy.read_parfile(dsyn_parfile)

                    # Set data parameters and  write new parfiles
                    locations = ["latitude", "longitude", "depth_in_m"]
                    if _par in locations:
                        dsyn_pars["USE_SOURCE_DERIVATIVE"] = True
                        if _par == "depth_in_m":
                            # 1 for depth
                            dsyn_pars["USE_SOURCE_DERIVATIVE_DIRECTION"] = 1
                        elif _par == "latitude":
                            # 2 for latitude
                            dsyn_pars["USE_SOURCE_DERIVATIVE_DIRECTION"] = 2
                        else:
                            # 3 for longitude
                            dsyn_pars["USE_SOURCE_DERIVATIVE_DIRECTION"] = 3
                    else:
                        dsyn_pars["USE_SOURCE_DERIVATIVE"] = False

                    # Adapt duration
                    dsyn_pars["RECORD_LENGTH_IN_MINUTES"] = self.simulation_duration

                    # Write Stuff to Par_file
                    lpy.write_parfile(dsyn_pars, dsyn_parfile)

        if self.MPIMODE:
            self.comm.Barrier()

    def __update_cmt__(self, model):

        if self.MPIMODE is False or self.rank == 0:
            cmt = deepcopy(self.cmtsource)
            for _par, _modelval in zip(self.pars, model * self.scale):
                setattr(cmt, _par, _modelval)
            self.cmt_out = cmt

        if self.MPIMODE:
            self.comm.Barrier()

    def __write_sources__(self):

        if self.MPIMODE is False or self.rank == 0:
            # Update cmt solution with new model values
            cmt = deepcopy(self.cmtsource)
            for _par, _modelval in zip(self.pars, self.model):
                setattr(cmt, _par, _modelval)

            # Writing synthetic CMT solution
            lpy.log_action("Writing Synthetic CMTSOLUTION",
                           plogger=self.logger.info)
            cmt.write_CMTSOLUTION_file(os.path.join(
                self.synt_syntdir, "DATA", "CMTSOLUTION"))

            # For the perturbations it's slightly more complicated.
            for _par, _pardir in self.synt_pardirs.items():

                if _par not in ["time_shift", "half_duration"]:
                    # Write source to the directory of simulation
                    lpy.log_action(
                        f"Writing Frechet CMTSOLUTION for {_par}",
                        plogger=self.logger.info)

                    if self.pardict[_par]["pert"] is not None:
                        # Perturb source at parameter
                        cmt_pert = deepcopy(cmt)

                        # If parameter a part of the tensor elements then set the
                        # rest of the parameters to 0.
                        tensorlist = ['m_rr', 'm_tt', 'm_pp',
                                      'm_rt', 'm_rp', 'm_tp']
                        if _par in tensorlist:
                            for _tensor_el in tensorlist:
                                if _tensor_el != _par:
                                    setattr(cmt_pert, _tensor_el, 0.0)
                                else:
                                    setattr(cmt_pert, _tensor_el,
                                            self.pardict[_par]["pert"])
                        else:
                            # Get the parameter to be perturbed
                            to_be_perturbed = getattr(cmt_pert, _par)

                            # Perturb the parameter
                            to_be_perturbed += self.pardict[_par]["pert"]

                            # Set the perturb
                            setattr(cmt_pert, _par, to_be_perturbed)

                        cmt_pert.write_CMTSOLUTION_file(os.path.join(
                            _pardir, "DATA", "CMTSOLUTION"))
                    else:
                        cmt.write_CMTSOLUTION_file(os.path.join(
                            _pardir, "DATA", "CMTSOLUTION"))

        if self.MPIMODE:
            self.comm.Barrier()

    def __run_simulations__(self):

        if self.MPIMODE is False or self.rank == 0:

            t = lpy.Timer(plogger=self.logger.info)
            t.start()

            lpy.log_action("Submitting all simulations",
                           plogger=self.logger.info)

            # Initialize necessary commands
            cmd_list = self.nsim * [[*self.launch_method, './bin/xspecfem3D']]

            cwdlist = [self.synt_syntdir]
            cwdlist.extend(
                [_pardir for _par, _pardir in self.synt_pardirs.items()
                 if _par not in self.nosimpars])
            lpy.run_cmds_parallel(cmd_list, cwdlist=cwdlist)

            t.start()

        if self.MPIMODE:
            self.comm.Barrier()

    def __run_forward_only__(self):

        # Initialize necessary commands
        lpy.log_action(
            "Submitting forward simulation", plogger=self.logger.info)
        cmd_list = [[*self.launch_method, './bin/xspecfem3D']]
        cwdlist = [self.synt_syntdir]
        lpy.run_cmds_parallel(cmd_list, cwdlist=cwdlist)

    def __run_parameters_only__(self):

        # Initialize necessary commands
        lpy.log_action(
            "Submitting parameter simulations", plogger=self.logger.info)
        cmd_list = (self.nsim - 1) * \
            [[*self.launch_method, './bin/xspecfem3D']]

        cwdlist = []
        cwdlist.extend(
            [_pardir for _par, _pardir in self.synt_pardirs.items()
                if _par not in self.nosimpars])
        lpy.run_cmds_parallel(cmd_list, cwdlist=cwdlist)

    def forward(self, model):

        if self.MPIMODE is False or self.rank == 0:
            # Update model
            if self.zero_trace:
                self.model = model[:-1] * self.scale
                self.scaled_model = model[:-1]
            else:
                self.model = model * self.scale
                self.scaled_model = model

            # Write sources for next iteration
            self.__write_sources__()

            # Run forward simulation
            self.__run_forward_only__()

        if self.MPIMODE:
            self.comm.Barrier()

        # Process synthetic only
        self.process_synt()

    def compute_cost_gradient(self, model):

        if self.MPIMODE is False or self.rank == 0:
            # Update model
            self.model = model * self.scale
            self.scaled_model = model

            # Write sources for next iteration
            self.__write_sources__()

            # Run the simulations
            with lpy.Timer(plogger=self.logger.info):
                self.__run_simulations__()

        if self.MPIMODE:
            self.comm.Barrier()

        # Get streams
        self.process_all_synt()

        # Window Data
        if self.not_windowed_yet:
            self.__window__()
            self.not_windowed_yet = False

        return self.__compute_cost__(), self.__compute_gradient__() * self.scale

    def compute_cost_gradient_hessian(self, model):

        # Update model
        if self.zero_trace:  # and not self.zero_energy:
            mu = model[-1]
            self.model = model[:-1] * self.scale
            self.scaled_model = model[:-1]
        else:
            self.model = model * self.scale
            self.scaled_model = model

        # elif self.zero_trace and self.zero_energy:
        #     mu = model[-2:]
        #     self.model = model[:-2] * self.scale
        #     self.scaled_model = model[:-2]
        # elif not self.zero_trace and self.zero_energy:
        #     mu = model[-1]
        #     self.model = model[:-1] * self.scale
        #     self.scaled_model = model[:-1]

        # Write sources for next iteration
        self.__write_sources__()

        # Run the simulations
        if self.iteration == 0:
            pass
        else:
            with lpy.Timer(plogger=self.logger.info):
                self.__run_simulations__()

            # Get streams
            self.process_all_synt()

        # Window Data
        if self.not_windowed_yet:
            self.__window__()
            self.not_windowed_yet = False

        # Evaluate
        cost = self.__compute_cost__()
        g, h = self.__compute_gradient_and_hessian__()

        # Get normalization factor at the first iteration
        if self.iteration == 0:
            self.cost_norm = cost
            self.iteration = 1

        # Normalize the cost using the first cost calculation
        cost /= self.cost_norm

        # Compute the log energy cost grad and hessian
        # if self.zero_energy:
        #     c_log = self.__compute_cost_log__()
        #     g_log, h_log = self.__compute_gradient_and_hessian_log__()

        self.logger.debug("Raw")
        self.logger.debug(f"C: {cost}")
        self.logger.debug("G:")
        self.logger.debug(g.flatten())
        self.logger.debug("H")
        self.logger.debug(h.flatten())

        # Scaling of the cost function
        g *= self.scale
        h = np.diag(self.scale) @ h @ np.diag(self.scale)

        # Scaling the log energy cost grad and hessian
        # if self.zero_energy:
        #     g_log *= self.scale
        #     h_log = np.diag(self.scale) @ h_log @ np.diag(self.scale)

        self.logger.debug("Scaled")
        self.logger.debug(f"C: {cost}")
        self.logger.debug("G:")
        self.logger.debug(g.flatten())
        self.logger.debug("H")
        self.logger.debug(h.flatten())

        if self.damping > 0.0:
            factor = self.damping * np.max(np.abs((np.diag(h))))
            self.logger.debug(f"f: {factor}")
            modelres = self.scaled_model - self.init_scaled_model
            self.logger.debug(f"Model Residual: {modelres.flatten()}")
            self.logger.debug(f"Cost Before: {cost}")
            # cost += factor/2 * np.sum(modelres**2)
            self.logger.debug(f"Cost After: {cost}")
            g += factor * modelres
            h += factor * np.eye(len(self.model))

            self.logger.debug("Damped")
            self.logger.debug(f"C: {cost}")
            self.logger.debug("G:")
            self.logger.debug(g.flatten())
            self.logger.debug("H")
            self.logger.debug(h.flatten())

        elif self.hypo_damping > 0.0:
            # Only get the hessian elements of the hypocenter
            hdiag = np.diag(h)[self.hypo_damp_index_array]
            self.logger.debug("HypoDiag:")
            self.logger.debug(hdiag)
            self.logger.debug("HypoDamping:")
            self.logger.debug(self.hypo_damping)
            factor = self.hypo_damping * np.max(np.abs((hdiag)))
            self.logger.debug(f"f: {factor}")
            modelres = self.scaled_model - self.init_scaled_model
            self.logger.debug(f"Model Residual: {modelres.flatten()}")
            self.logger.debug(f"Cost Before: {cost}")
            # cost += factor/2 * np.sum(modelres**2)
            self.logger.debug(f"Cost After: {cost}")
            g += factor * modelres
            h += factor * np.diag(self.hypo_damp_array)

            self.logger.debug("Hypocenter-Damped")
            self.logger.debug(f"C: {cost}")
            self.logger.debug("G:")
            self.logger.debug(g.flatten())
            self.logger.debug("H")
            self.logger.debug(h.flatten())

            # Add zero trace condition
        if self.zero_trace:  # and not self.zero_energy:
            m, n = h.shape
            hz = np.zeros((m+1, n+1))
            hz[:-1, :-1] = h
            hz[:, -1] = self.zero_trace_array
            hz[-1, :] = self.zero_trace_array
            h = hz
            g = np.append(g, 0.0)
            g[-1] = np.sum(self.scaled_model[self.zero_trace_index_array])

        # elif self.zero_trace and self.zero_energy:
        #     m, n = h.shape
        #     hz = np.zeros((m+2, n+2))
        #     hz[:-2, :-2] = h + 1 * h_log
        #     hz[:, -2] = self.zero_trace_array
        #     hz[-2, :] = self.zero_trace_array
        #     hz[:-2, -1] = g_log
        #     hz[-1, :-2] = g_log
        #     h = hz
        #     g = np.append(g, 0.0)
        #     g = np.append(g, 0.0)
        #     g[-2] = np.sum(self.scaled_model[self.zero_trace_index_array])
        #     g[-1] = c_log

        # elif not self.zero_trace and self.zero_energy:
        #     m, n = h.shape
        #     hz = np.zeros((m+1, n+1))
        #     hz[:-1, :-1] = h
        #     hz[:-1, -1] = g_log
        #     hz[-1, :-1] = g_log
        #     h = hz
        #     g = np.append(g, 0.0)
        #     g[-1] = c_log

            # Show stuf when debugging
            self.logger.debug("Constrained:")
            self.logger.debug(f"C: {cost}")
            self.logger.debug("G:")
            self.logger.debug(g.flatten())
            self.logger.debug("H")
            self.logger.debug(h.flatten())

        return cost, g, h

    def __compute_cost__(self):

        cost = 0
        for _wtype in self.processdict.keys():

            cgh = lpy.CostGradHess(
                data=self.data_dict[_wtype],
                synt=self.synt_dict[_wtype]["synt"],
                verbose=True if self.loglevel >= 20 else False,
                normalize=self.normalize,
                weight=self.weighting)
            cost += cgh.cost() * self.processdict[_wtype]["weight"]
        return cost

    def __compute_residuals__(self):

        residuals = dict()
        for _wtype in self.processdict.keys():

            cgh = lpy.CostGradHess(
                data=self.data_dict[_wtype],
                synt=self.synt_dict[_wtype]["synt"],
                verbose=True if self.loglevel >= 20 else False,
                normalize=self.normalize,
                weight=False)
            residuals[_wtype] = cgh.residuals()

        with open(os.path.join(self.cmtdir, "residuals.pkl"), "wb") as f:
            cPickle.dump(deepcopy(residuals), f)

        return residuals

    def __compute_gradient__(self):

        gradient = np.zeros_like(self.model)

        for _wtype in self.processdict.keys():
            # Get all perturbations
            dsyn = list()
            for _i, _par in enumerate(self.pardict.keys()):
                dsyn.append(self.synt_dict[_wtype][_par])

            # Create costgradhess class to computte gradient
            cgh = lpy.CostGradHess(
                data=self.data_dict[_wtype],
                synt=self.synt_dict[_wtype]["synt"],
                dsyn=dsyn,
                verbose=True if self.loglevel >= 20 else False,
                normalize=self.normalize,
                weight=self.weighting)

            gradient += cgh.grad() * self.processdict[_wtype]["weight"]

        return gradient

    def __compute_gradient_and_hessian__(self):

        gradient = np.zeros_like(self.model)
        hessian = np.zeros((len(self.model), len(self.model)))

        for _wtype in self.processdict.keys():

            # Get all perturbations
            dsyn = list()
            for _i, _par in enumerate(self.pardict.keys()):
                dsyn.append(self.synt_dict[_wtype][_par])

            # Create costgradhess class to computte gradient
            cgh = lpy.CostGradHess(
                data=self.data_dict[_wtype],
                synt=self.synt_dict[_wtype]["synt"],
                dsyn=dsyn,
                verbose=True if self.loglevel >= 20 else False,
                normalize=self.normalize,
                weight=self.weighting)

            tmp_g, tmp_h = cgh.grad_and_hess()
            gradient += tmp_g * self.processdict[_wtype]["weight"]
            hessian += tmp_h * self.processdict[_wtype]["weight"]

        self.logger.debug("M, G, H:")
        self.logger.debug(self.model)
        self.logger.debug(gradient.flatten())
        self.logger.debug(hessian.flatten())

        return gradient, hessian

    def __compute_cost_log__(self):

        cost = 0
        for _wtype in self.processdict.keys():

            cgh = lpy.CostGradHessLogEnergy(
                data=self.data_dict[_wtype],
                synt=self.synt_dict[_wtype]["synt"],
                verbose=True if self.loglevel >= 20 else False,
                weight=self.weighting)
            cost += cgh.cost() * self.processdict[_wtype]["weight"]
        return cost

    def __compute_gradient_and_hessian_log__(self):

        gradient = np.zeros_like(self.model)
        hessian = np.zeros((len(self.model), len(self.model)))

        for _wtype in self.processdict.keys():

            # Get all perturbations
            dsyn = list()
            for _i, _par in enumerate(self.pardict.keys()):
                dsyn.append(self.synt_dict[_wtype][_par])

            # Create costgradhess class to computte gradient
            cgh = lpy.CostGradHessLogEnergy(
                data=self.data_dict[_wtype],
                synt=self.synt_dict[_wtype]["synt"],
                dsyn=dsyn,
                verbose=True if self.loglevel >= 20 else False,
                weight=self.weighting)

            tmp_g, tmp_h = cgh.grad_and_hess()
            gradient += tmp_g * self.processdict[_wtype]["weight"]
            hessian += tmp_h * self.processdict[_wtype]["weight"]

        self.logger.debug("M, G, H:")
        self.logger.debug(self.model)
        self.logger.debug(gradient.flatten())
        self.logger.debug(hessian.flatten())

        return gradient, hessian

    def misfit_walk_depth(self):

        # Start the walk
        lpy.log_bar("Misfit walk: Depth", plogger=self.logger.info)

        scaled_depths = np.arange(
            self.cmtsource.depth_in_m - 10000,
            self.cmtsource.depth_in_m + 10100, 1000)/1000.0
        cost = np.zeros_like(scaled_depths)
        grad = np.zeros((*scaled_depths.shape, 1))
        hess = np.zeros((*scaled_depths.shape, 1, 1))
        dm = np.zeros((*scaled_depths.shape, 1))

        for _i, _dep in enumerate(scaled_depths):

            lpy.log_section(
                f"Computing CgH for: {_dep} km",
                plogger=self.logger.info)

            with lpy.Timer(plogger=self.logger.info):
                c, g, h = self.compute_cost_gradient_hessian(
                    np.array([_dep]))
                lpy.log_action(
                    f"\n     Iteration for {_dep} km done.",
                    plogger=self.logger.info)
            cost[_i] = c
            grad[_i, :] = g
            hess[_i, :, :] = h

        # Get the Gauss newton step
        for _i in range(len(scaled_depths)):
            dm[_i, :] = np.linalg.solve(
                hess[_i, :, :], -grad[_i, :])

        plt.switch_backend("pdf")
        plt.figure(figsize=(12, 4))
        # Cost function
        ax = plt.subplot(141)
        plt.plot(cost, scaled_depths, label="Cost")
        plt.legend(frameon=False, loc='upper right')
        plt.xlabel("Cost")
        plt.ylabel("Depth [km]")

        ax = plt.subplot(142, sharey=ax)
        plt.plot(np.squeeze(grad), scaled_depths, label="Grad")
        plt.legend(frameon=False, loc='upper right')
        plt.xlabel("Gradient")
        ax.tick_params(labelleft=False, labelright=False)

        ax = plt.subplot(143, sharey=ax)
        plt.plot(np.squeeze(hess), scaled_depths, label="Hess")
        plt.legend(frameon=False, loc='upper right')
        plt.xlabel("G.-N. Hessian")
        ax.tick_params(labelleft=False, labelright=False)

        ax = plt.subplot(144, sharey=ax)
        plt.plot(np.squeeze(dm), scaled_depths, label="Step")
        plt.legend(frameon=False, loc='upper right')
        plt.xlabel("$\\Delta$m [km]")
        ax.tick_params(labelleft=False, labelright=False)

        plt.savefig(self.cmtdir + "/misfit_walk_depth.pdf")

        # Start the walk
        lpy.log_bar("DONE.", plogger=self.logger.info)

    def misfit_walk_depth_times(self):
        """Pardict containing an array of the walk parameters.
        Then we walk entirely around the parameter space."""

        # if len(pardict) > 2:
        #     raise ValueError("Only two parameters at a time.")

        # depths = np.arange(self.cmtsource.depth_in_m - 10000,
        #                    self.cmtsource.depth_in_m + 10100, 1000)
        # times = np.arange(-10.0, 10.1, 1.0)
        depths = np.arange(self.cmtsource.depth_in_m - 5000,
                           self.cmtsource.depth_in_m + 5100, 1000)
        times = np.arange(self.cmtsource.time_shift - 5.0,
                          self.cmtsource.time_shift + 5.1, 1.0)
        t, z = np.meshgrid(times, depths)
        cost = np.zeros(z.shape)
        grad = np.zeros((*z.shape, 2))
        hess = np.zeros((*z.shape, 2, 2))
        dm = np.zeros((*z.shape, 2))

        for _i, _dep in enumerate(depths):
            for _j, _time in enumerate(times):

                c, g, h = self.compute_cost_gradient_hessian(
                    np.array([_dep, _time]))
                cost[_i, _j] = c
                grad[_i, _j, :] = g
                hess[_i, _j, :, :] = h

        # Get the Gauss newton step
        damp = 0.001
        for _i in range(z.shape[0]):
            for _j in range(z.shape[1]):
                dm[_i, _j, :] = np.linalg.solve(
                    hess[_i, _j, :, :] + damp * np.diag(np.ones(2)), - grad[_i, _j, :])
        plt.switch_backend("pdf")
        extent = [np.min(t), np.max(t), np.min(z), np.max(z)]
        aspect = (np.max(t) - np.min(t))/(np.max(z) - np.min(z))
        plt.figure(figsize=(11, 6.5))

        # Get minimum
        ind = np.unravel_index(np.argmin(cost, axis=None), cost.shape)

        # Cost
        ax1 = plt.subplot(3, 4, 9)
        plt.imshow(cost, interpolation=None, extent=extent, aspect=aspect)
        lpy.plot_label(ax1, r"$\mathcal{C}$", dist=0)
        plt.plot(times[ind[0]], depths[ind[1]], "*")
        c1 = plt.colorbar()
        c1.ax.tick_params(labelsize=7)
        c1.ax.yaxis.offsetText.set_fontsize(7)
        ax1.axes.invert_yaxis()
        plt.ylabel(r'$z$')
        plt.xlabel(r'$t$')

        # Gradient
        ax2 = plt.subplot(3, 4, 6, sharey=ax1)
        plt.imshow(grad[:, :, 1], interpolation=None,
                   extent=extent, aspect=aspect)
        c2 = plt.colorbar()
        c2.ax.tick_params(labelsize=7)
        c2.ax.yaxis.offsetText.set_fontsize(7)
        ax2.tick_params(labelbottom=False)
        lpy.plot_label(ax2, r"$g_{\Delta t}$", dist=0)

        ax3 = plt.subplot(3, 4, 10, sharey=ax1)
        plt.imshow(grad[:, :, 0], interpolation=None,
                   extent=extent, aspect=aspect)
        c3 = plt.colorbar()
        c3.ax.tick_params(labelsize=7)
        c3.ax.yaxis.offsetText.set_fontsize(7)
        ax3.tick_params(labelleft=False)
        lpy.plot_label(ax3, r"$g_z$", dist=0)
        plt.xlabel(r'$\Delta t$')

        # Hessian
        ax4 = plt.subplot(3, 4, 3, sharey=ax1)
        plt.imshow(hess[:, :, 0, 1], interpolation=None,
                   extent=extent, aspect=aspect)
        c4 = plt.colorbar()
        c4.ax.tick_params(labelsize=7)
        c4.ax.yaxis.offsetText.set_fontsize(7)
        ax4.tick_params(labelbottom=False)
        lpy.plot_label(ax4, r"$\mathcal{H}_{z,\Delta t}$", dist=0)

        ax5 = plt.subplot(3, 4, 7, sharey=ax1)
        plt.imshow(hess[:, :, 1, 1], interpolation=None,
                   extent=extent, aspect=aspect)
        c5 = plt.colorbar()
        c5.ax.tick_params(labelsize=7)
        c5.ax.yaxis.offsetText.set_fontsize(7)
        ax5.tick_params(labelleft=False, labelbottom=False)
        lpy.plot_label(ax5, r"$\mathcal{H}_{\Delta t,\Delta t}$", dist=0)

        ax6 = plt.subplot(3, 4, 11, sharey=ax1)
        plt.imshow(hess[:, :, 0, 0], interpolation=None,
                   extent=extent, aspect=aspect)
        c6 = plt.colorbar()
        c6.ax.tick_params(labelsize=7)
        c6.ax.yaxis.offsetText.set_fontsize(7)
        ax6.tick_params(labelleft=False)
        lpy.plot_label(ax6, r"$\mathcal{H}_{z,z}$", dist=0)
        plt.xlabel(r'$\Delta t$')

        # Gradient/Hessian
        ax7 = plt.subplot(3, 4, 8, sharey=ax1)
        plt.imshow(dm[:, :, 1], interpolation=None,
                   extent=extent, aspect=aspect)
        c7 = plt.colorbar()
        c7.ax.tick_params(labelsize=7)
        c7.ax.yaxis.offsetText.set_fontsize(7)
        ax7.tick_params(labelleft=False, labelbottom=False)
        lpy.plot_label(ax7, r"$\mathrm{d}\Delta$", dist=0)

        ax8 = plt.subplot(3, 4, 12, sharey=ax1)
        plt.imshow(dm[:, :, 0], interpolation=None,
                   extent=extent, aspect=aspect)
        c8 = plt.colorbar()
        c8.ax.tick_params(labelsize=7)
        c8.ax.yaxis.offsetText.set_fontsize(7)
        ax8.tick_params(labelleft=False)
        lpy.plot_label(ax8, r"$\mathrm{d}z$", dist=0)
        plt.xlabel(r'$\Delta t$')

        plt.subplots_adjust(hspace=0.2, wspace=0.15)
        plt.savefig(self.cmtdir + "/SyntheticCostGradHess.pdf")

    def plot_data(self, outputdir="."):
        plt.switch_backend("pdf")
        for _wtype in self.processdict.keys():
            with PdfPages(os.path.join(outputdir, f"data_{_wtype}.pdf")) as pdf:
                for obsd_tr in self.data_dict[_wtype]:
                    fig = plot_seismograms(obsd_tr, cmtsource=self.cmtsource,
                                           tag=_wtype)
                    pdf.savefig()  # saves the current figure into a pdf page
                    plt.close(fig)

                    # We can also set the file's metadata via the PdfPages object:
                d = pdf.infodict()
                d['Title'] = f"{_wtype.capitalize()}-Wave-Data-PDF"
                d['Author'] = 'Lucas Sawade'
                d['Subject'] = 'Trace comparison in one pdf'
                d['Keywords'] = 'seismology, moment tensor inversion'
                d['CreationDate'] = datetime.datetime.today()
                d['ModDate'] = datetime.datetime.today()

    def save_seismograms(self):

        outdir = os.path.join(self.cmtdir, "output")
        obsddir = os.path.join(outdir, "observed")
        syntdir = os.path.join(outdir, "synthetic")
        syntdir_init = os.path.join(outdir, "synthetic_init")
        stations = os.path.join(outdir, "STATIONS.xml")

        # Make directories
        if os.path.exists(outdir) is False:
            os.makedirs(outdir)
        if os.path.exists(obsddir) is False:
            os.makedirs(obsddir)
        if os.path.exists(syntdir) is False:
            os.makedirs(syntdir)

        # Write out stations
        self.stations.write(stations, format="STATIONXML")

        # Write processed data
        for _wtype, _stream in self.data_dict.items():

            filename = os.path.join(obsddir, f"{_wtype}_stream.pkl")
            with open(filename, 'wb') as f:
                cPickle.dump(_stream, f)

        # Write processed synthetics
        # Note that you have to run an extra siumulation the right model
        # to get the accruate
        for _wtype in self.synt_dict.keys():

            filename = os.path.join(syntdir, f"{_wtype}_stream.pkl")
            with open(filename, 'wb') as f:
                cPickle.dump(self.synt_dict[_wtype]["synt"], f)

        # Write processed initial synthetics
        if hasattr(self, "synt_dict_init"):
            if os.path.exists(syntdir_init) is False:
                os.makedirs(syntdir_init)
            for _wtype in self.synt_dict_init.keys():
                filename = os.path.join(syntdir_init, f"{_wtype}_stream.pkl")
                with open(filename, 'wb') as f:
                    cPickle.dump(self.synt_dict_init[_wtype]["synt"], f)

    def write_measurements(
            self, data: dict, synt: dict, post_fix: str = None):

        def get_toffset(
                tsample: int, dt: float, t0: UTCDateTime, origin: UTCDateTime) -> float:
            """Computes the time of a sample with respect to origin time

            Parameters
            ----------
            tsample : int
                sample on trace
            dt : float
                sample spacing
            t0 : UTCDateTime
                time of the first sample
            origin : UTCDateTime
                origin time

            Returns
            -------
            float
                Time relative to origin time
            """

            # Second on trace
            trsec = (tsample*dt)
            return (t0 + trsec) - origin

        # Normalize by component and aximuthal weights

        def get_measurements_and_windows(
                obs: Stream, syn: Stream, event: CMTSource):

            windows = dict()

            # Create dict to access traces
            for _component in ["R", "T", "Z"]:
                windows[_component] = dict()
                windows[_component]["id"] = []
                windows[_component]["dt"] = []
                windows[_component]["starttime"] = []
                windows[_component]["endtime"] = []
                windows[_component]["nsamples"] = []
                windows[_component]["latitude"] = []
                windows[_component]["longitude"] = []
                windows[_component]["distance"] = []
                windows[_component]["azimuth"] = []
                windows[_component]["back_azimuth"] = []
                windows[_component]["nshift"] = []
                windows[_component]["time_shift"] = []
                windows[_component]["maxcc"] = []
                windows[_component]["dlna"] = []
                windows[_component]["L1"] = []
                windows[_component]["L2"] = []
                windows[_component]["dL1"] = []
                windows[_component]["dL2"] = []
                windows[_component]["trace_energy"] = []
                windows[_component]["L1_Power"] = []
                windows[_component]["L2_Power"] = []

                for _tr in obs:
                    if _tr.stats.component == _component \
                            and "windows" in _tr.stats:

                        d = _tr.data
                        try:
                            network, station, component = (
                                _tr.stats.network, _tr.stats.station,
                                _tr.stats.component)
                            s = syn.select(
                                network=network, station=station,
                                component=component)[0].data
                        except Exception as e:
                            self.logger.warning(
                                f"{network}.{station}..{component}")
                            self.logger.error(e)
                            continue

                        trace_energy = 0
                        for win in _tr.stats.windows:
                            # Get window data
                            wd = d[win.left:win.right]
                            ws = s[win.left:win.right]

                            # Infos
                            dt = _tr.stats.delta
                            npts = _tr.stats.npts
                            winleft = get_toffset(
                                win.left, dt, win.time_of_first_sample,
                                event.origin_time)
                            winright = get_toffset(
                                win.right, dt, win.time_of_first_sample,
                                event.origin_time)

                            # Populate the dictionary
                            windows[_component]["id"].append(_tr.id)
                            windows[_component]["dt"].append(dt)
                            windows[_component]["starttime"].append(winleft)
                            windows[_component]["endtime"].append(winright)
                            windows[_component]["latitude"].append(
                                _tr.stats.latitude
                            )
                            windows[_component]["longitude"].append(
                                _tr.stats.longitude
                            )
                            windows[_component]["distance"].append(
                                _tr.stats.distance
                            )
                            windows[_component]["azimuth"].append(
                                _tr.stats.azimuth
                            )
                            windows[_component]["back_azimuth"].append(
                                _tr.stats.back_azimuth
                            )

                            # Measurements
                            max_cc_value, nshift = lpy.xcorr(wd, ws)

                            # Get fixed window indeces.
                            istart, iend = win.left, win.right
                            istart_d, iend_d, istart_s, iend_s = lpy.correct_window_index(
                                istart, iend, nshift, npts)
                            wd_fix = d[istart_d:iend_d]
                            ws_fix = s[istart_s:iend_s]

                            powerl1 = lpy.power_l1(wd, ws)
                            powerl2 = lpy.power_l2(wd, ws)
                            norm1 = lpy.norm1(wd)
                            norm2 = lpy.norm2(wd)
                            dnorm1 = lpy.dnorm1(wd, ws)
                            dnorm2 = lpy.dnorm2(wd, ws)
                            dlna = lpy.dlna(wd_fix, ws_fix)
                            trace_energy += norm2

                            windows[_component]["L1"].append(norm1)
                            windows[_component]["L2"].append(norm2)
                            windows[_component]["dL1"].append(dnorm1)
                            windows[_component]["dL2"].append(dnorm2)
                            windows[_component]["dlna"].append(dlna)
                            windows[_component]["L1_Power"].append(powerl1)
                            windows[_component]["L2_Power"].append(powerl2)
                            windows[_component]["nshift"].append(nshift)
                            windows[_component]["time_shift"].append(
                                nshift * dt
                            )
                            windows[_component]["maxcc"].append(
                                max_cc_value
                            )
                        # Create array with the energy
                        windows[_component]["trace_energy"].extend(
                            [trace_energy]*len(_tr.stats.windows))

            return windows

        window_dict = dict()

        for _wtype, _obs_stream in data.items():

            # Get corresponding Synthetic data
            _syn_stream = synt[_wtype]["synt"]

            window_dict[_wtype] = get_measurements_and_windows(
                _obs_stream, _syn_stream, self.cmtsource)

        # Create output file
        filename = "measurements"
        if post_fix is not None:
            filename += "_" + post_fix
        filename += ".pkl"

        outfile = os.path.join(self.cmtdir, filename)
        with open(outfile, "wb") as f:
            cPickle.dump(window_dict, f)

        return window_dict

    def plot_station(self, network: str, station: str, outputdir="."):
        plt.switch_backend("pdf")
        # Get station data
        for _wtype in self.processdict.keys():
            try:
                obsd = self.data_dict[_wtype].select(
                    network=network, station=station)
                synt = self.synt_dict[_wtype]["synt"].select(
                    network=network, station=station)
            except Exception as e:
                self.logger.warning(
                    f"Could load station {network}{station} -- {e}")
            # Plot PDF for each wtype
            with PdfPages(os.path.join(outputdir, f"{network}.{station}_{_wtype}.pdf")) as pdf:
                for component in ["Z", "R", "T"]:
                    try:
                        obsd_tr = obsd.select(
                            station=station, network=network,
                            component=component)[0]
                        synt_tr = synt.select(
                            station=station, network=network,
                            component=component)[0]
                    except Exception as err:
                        self.logger.warning(f"Couldn't find obs or syn for NET.STA.COMP:"
                                            f" {network}.{station}.{component} -- {err}")
                        continue

                    fig = plot_seismograms(obsd_tr, synt_tr, self.cmtsource,
                                           tag=_wtype)
                    pdf.savefig()  # saves the current figure into a pdf page
                    plt.close(fig)

                    # We can also set the file's metadata via the PdfPages object:
                d = pdf.infodict()
                d['Title'] = f"{_wtype.capitalize()}-Wave-PDF"
                d['Author'] = 'Lucas Sawade'
                d['Subject'] = 'Trace comparison in one pdf'
                d['Keywords'] = 'seismology, moment tensor inversion'
                d['CreationDate'] = datetime.datetime.today()
                d['ModDate'] = datetime.datetime.today()

    def plot_station_der(self, network: str, station: str, outputdir="."):
        plt.switch_backend("pdf")
        # Get station data
        for _wtype in self.processdict.keys():
            # Plot PDF for each wtype
            with PdfPages(os.path.join(
                    outputdir,
                    f"{network}.{station}_{_wtype}_derivatives.pdf")) as pdf:
                for _par in self.synt_dict[_wtype].keys():
                    if _par != "synt":
                        try:
                            synt = self.synt_dict[_wtype][_par].select(
                                network=network, station=station)
                        except Exception as e:
                            self.logger.warning(f"Could load station "
                                                f"{network}{station} -- {e}")
                        for component in ["Z", "R", "T"]:
                            try:
                                synt_tr = synt.select(
                                    station=station, network=network,
                                    component=component)[0]
                            except Exception as err:
                                self.logger.warning(
                                    f"Couldn't find obs or syn "
                                    f"for NET.STA.COMP:"
                                    f" {network}.{station}.{component} "
                                    f"-- {err}")
                                continue

                            fig = plot_seismograms(
                                synt_tr, cmtsource=self.cmtsource,
                                tag=f"{_wtype.capitalize()}-{_par.capitalize()}")
                            pdf.savefig()  # saves the current figure into a pdf page
                            plt.close(fig)

                    # We can also set the file's metadata via the PdfPages object:
                d = pdf.infodict()
                d['Title'] = f"{_wtype.capitalize()}-Wave-PDF"
                d['Author'] = 'Lucas Sawade'
                d['Subject'] = 'Trace comparison in one pdf'
                d['Keywords'] = 'seismology, moment tensor inversion'
                d['CreationDate'] = datetime.datetime.today()
                d['ModDate'] = datetime.datetime.today()

    def plot_windows(self, outputdir="."):
        plt.switch_backend("pdf")
        for _wtype in self.processdict.keys():
            self.logger.info(f"Plotting {_wtype} waves")
            with PdfPages(os.path.join(outputdir, f"windows_{_wtype}.pdf")) as pdf:
                for obsd_tr in self.data_dict[_wtype]:
                    try:
                        synt_tr = self.synt_dict[_wtype]["synt"].select(
                            station=obsd_tr.stats.station,
                            network=obsd_tr.stats.network,
                            component=obsd_tr.stats.component)[0]
                    except Exception as err:
                        self.logger.warning(err)
                        self.logger.warning(
                            "Couldn't find corresponding synt for "
                            f"obsd trace({obsd_tr.id}): {err}")
                        continue

                    fig = plot_seismograms(
                        obsd_tr, synt_tr, cmtsource=self.cmtsource, tag=_wtype)
                    pdf.savefig()  # saves the current figure into a pdf page
                    plt.close(fig)

                    # We can also set the file's metadata via the PdfPages object:
                d = pdf.infodict()
                d['Title'] = f"{_wtype.capitalize()}-Wave-PDF"
                d['Author'] = 'Lucas Sawade'
                d['Subject'] = 'Trace comparison in one pdf'
                d['Keywords'] = 'seismology, moment tensor inversion'
                d['CreationDate'] = datetime.datetime.today()
                d['ModDate'] = datetime.datetime.today()

    def plot_final_windows(self, outputdir="."):
        plt.switch_backend("pdf")
        for _wtype in self.processdict.keys():
            self.logger.info(f"Plotting {_wtype} waves")
            with PdfPages(os.path.join(outputdir, f"final_windows_{_wtype}.pdf")) as pdf:
                for obsd_tr in self.data_dict[_wtype]:
                    try:
                        synt_tr = self.synt_dict[_wtype]["synt"].select(
                            station=obsd_tr.stats.station,
                            network=obsd_tr.stats.network,
                            component=obsd_tr.stats.component)[0]
                        init_synt_tr = self.synt_dict_init[_wtype]["synt"].select(
                            station=obsd_tr.stats.station,
                            network=obsd_tr.stats.network,
                            component=obsd_tr.stats.component)[0]
                    except Exception as err:
                        self.logger.warning(
                            "Couldn't find corresponding synt for "
                            f"obsd trace({obsd_tr.id}): {err}")
                        continue

                    fig = plot_seismograms(
                        obsd_tr, init_synt_tr, synt_tr, self.cmtsource,
                        tag=_wtype)
                    pdf.savefig()  # saves the current figure into a pdf page
                    plt.close(fig)

                    # We can also set the file's metadata via the PdfPages object:
                d = pdf.infodict()
                d['Title'] = f"{_wtype.capitalize()}-Wave-PDF"
                d['Author'] = 'Lucas Sawade'
                d['Subject'] = 'Trace comparison in one pdf'
                d['Keywords'] = 'seismology, moment tensor inversion'
                d['CreationDate'] = datetime.datetime.today()
                d['ModDate'] = datetime.datetime.today()

    @ staticmethod
    def __create_dir__(dir, overwrite=False):
        if os.path.exists(dir) is False:
            os.mkdir(dir)
        else:
            if overwrite:
                shutil.rmtree(dir)
                os.mkdir(dir)
            else:
                pass


def plot_seismograms(obsd: Trace, synt: Union[Trace, None] = None,
                     syntf: Union[Trace, None] = None,
                     cmtsource: Union[lpy.CMTSource, None] = None,
                     tag: Union[str, None] = None):
    station = obsd.stats.station
    network = obsd.stats.network
    channel = obsd.stats.channel
    location = obsd.stats.location

    trace_id = f"{network}.{station}.{location}.{channel}"

    # Times and offsets computed individually, since the grid search applies
    # a timeshift which changes the times of the traces.
    if cmtsource is None:
        offset = 0
    else:
        offset = obsd.stats.starttime - cmtsource.cmt_time
        if isinstance(synt, Trace):
            offset_synt = synt.stats.starttime - cmtsource.cmt_time
        if isinstance(syntf, Trace):
            offset_syntf = syntf.stats.starttime - cmtsource.cmt_time

    times = [offset + obsd.stats.delta * i for i in range(obsd.stats.npts)]
    if isinstance(synt, Trace):
        times_synt = [offset_synt + synt.stats.delta * i
                      for i in range(synt.stats.npts)]
    if isinstance(syntf, Trace):
        times_syntf = [offset_syntf + syntf.stats.delta * i
                       for i in range(syntf.stats.npts)]

    # Figure Setup
    fig = plt.figure(figsize=(15, 5))
    ax1 = plt.subplot(211)
    plt.subplots_adjust(left=0.075, right=0.925, top=0.95)

    ax1.plot(times, obsd.data, color="black", linewidth=0.75,
             label="Obs")
    if isinstance(synt, Trace):
        ax1.plot(times_synt, synt.data, color="red", linewidth=0.75,
                 label="Syn")
    if isinstance(syntf, Trace):
        ax1.plot(times_syntf, syntf.data, color="blue", linewidth=0.75,
                 label="New Syn")
    scaleabsmax = 1.25*np.max(np.abs(obsd.data))
    ax1.set_xlim(times[0], times[-1])
    ax1.set_ylim(-scaleabsmax, scaleabsmax)
    ax1.legend(loc='upper right', frameon=False, ncol=3, prop={'size': 11})
    ax1.tick_params(labelbottom=False, labeltop=False)

    # Setting top left corner text manually
    if isinstance(tag, str):
        label = f"{trace_id}\n{tag.capitalize()}"
    else:
        label = f"{trace_id}"
    lpy.plot_label(ax1, label, location=1, dist=0.005, box=False)

    # plot envelope
    ax2 = plt.subplot(212)
    obsenv = lpy.envelope(obsd.data)
    ax2.plot(times, obsenv, color="black",
             linewidth=1.0, label="Obs")
    if isinstance(synt, Trace):
        ax2.plot(times, lpy.envelope(synt.data), color="red", linewidth=1,
                 label="Syn")
    if isinstance(syntf, Trace):
        ax2.plot(times, lpy.envelope(syntf.data), color="blue", linewidth=1,
                 label="New Syn")
    envscaleabsmax = 1.25*np.max(np.abs(obsenv))
    ax2.set_xlim(times[0], times[-1])
    ax2.set_ylim(0, envscaleabsmax)
    ax2.set_xlabel("Time [s]", fontsize=13)
    lpy.plot_label(ax2, "Envelope", location=1, dist=0.005, box=False)
    if isinstance(synt, Trace):
        try:
            for win in obsd.stats.windows:
                left = times[win.left]
                right = times[win.right]
                re1 = Rectangle((left, ax1.get_ylim()[0]), right - left,
                                ax1.get_ylim()[1] - ax1.get_ylim()[0],
                                color="blue", alpha=0.25, zorder=-1)
                ax1.add_patch(re1)
                re2 = Rectangle((left, ax2.get_ylim()[0]), right - left,
                                ax2.get_ylim()[1] - ax2.get_ylim()[0],
                                color="blue", alpha=0.25, zorder=-1)
                ax2.add_patch(re2)
        except Exception as e:
            print(e)

    return fig


def bin():

    import sys
    import argparse

    # Get arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(dest='event', help='CMTSOLUTION file',
                        type=str)
    parser.add_argument('-i', '--inputfile', dest='inputfile',
                        help='Input file location',
                        required=False, type=str, default=None)
    parser.add_argument('-d', '--download-only', dest='download_only',
                        help='Input file location',
                        required=False, type=bool, default=False)
    args = parser.parse_args()

    cmtsolutionfile = args.event
    inputfile = args.inputfile
    download_only = args.download_only

    if download_only:
        MPIMODE = False

    if MPIMODE:
        try:
            from mpi4py import MPI
            MPIMODE = True

        except ImportError as e:
            print(e)
            MPIMODE = False

    if MPIMODE:
        comm = MPI.COMM_WORLD
        rank = comm.Get_rank()
        size = comm.Get_size()
    else:
        comm = None
        rank = None
        size = None

    print(f"Hello 1 from rank {rank}")

    # Get Input parameters
    if inputfile is None:
        inputdict = lpy.smart_read_yaml(
            os.path.join(scriptdir, "input.yml"),
            mpi_mode=MPIMODE, comm=comm)
    else:
        inputdict = lpy.smart_read_yaml(inputfile)

    print(f"Hello 2 from rank {rank}")

    # Get process params
    if inputdict["processparams"] is None:
        processdict = lpy.smart_read_yaml(
            os.path.join(scriptdir, "process.yml"),
            mpi_mode=MPIMODE, comm=comm)
    else:
        processdict = lpy.smart_read_yaml(
            inputdict["processparams"],
            mpi_mode=MPIMODE, comm=comm)

    print(f"Hello 3 from rank {rank}")

    # Set params
    pardict = inputdict["parameters"]
    database = inputdict["database"]
    specfem = inputdict["specfem"]
    launch_method = inputdict["launch_method"]
    download_data = inputdict["download_data"]
    hypo_damping = inputdict["hypo_damping"]
    damping = inputdict["damping"]
    duration = inputdict["duration"]
    overwrite = inputdict["overwrite"]
    zero_trace = inputdict["zero_trace"]
    start_label = inputdict["start_label"]
    solution_label = inputdict["solution_label"]

    if download_only:
        download_data = True

    print(f"Hello 4 from rank {rank}")

    gcmt3d = GCMT3DInversion(
        cmtsolutionfile,
        databasedir=database,
        specfemdir=specfem,
        pardict=pardict,
        processdict=processdict,
        download_data=download_data,
        zero_trace=zero_trace,
        duration=duration,
        overwrite=overwrite,
        launch_method=launch_method,
        damping=damping,
        hypo_damping=hypo_damping,
        start_label=start_label,
        multiprocesses=38,
        MPIMODE=MPIMODE)

    if download_only:
        return

    gcmt3d.process_data()
    gcmt3d.get_windows()
    gcmt3d.__compute_weights__()
    return
    if gcmt3d.MPIMODE is False or gcmt3d.rank == 0:
        optim_list = []
        t = lpy.Timer(plogger=gcmt3d.logger.info)
        t.start()

        # Gauss Newton Optimization Structure
        lpy.log_bar("GN", plogger=gcmt3d.logger.info)
        optim_gn = lpy.Optimization("gn")
        optim_gn.logger = gcmt3d.logger.info
        optim_gn.compute_cost_and_grad_and_hess = gcmt3d.compute_cost_gradient_hessian

        # Set attributes depending on the optimization input parameters
        for key, val in inputdict["optimization"].items():
            setattr(optim_gn, key, val)

        # Run optimization
        with lpy.Timer(plogger=gcmt3d.logger.info):
            optim_out = gcmt3d.optimize(optim_gn)
            lpy.log_action("DONE with Gauss-Newton.",
                           plogger=gcmt3d.logger.info)

        # Update model and write model
        if gcmt3d.zero_trace:
            gcmt3d.__update_cmt__(optim_out.model[:-1])
        else:
            gcmt3d.__update_cmt__(optim_out.model)

        # Write model to file
        gcmt3d.cmt_out.write_CMTSOLUTION_file(
            f"{gcmt3d.cmtdir}/{gcmt3d.cmt_out.eventname}_{solution_label}")

        optim_list.append(deepcopy(optim_out))

    # Stuff for L-Curves
    # Get model related things to save
    if gcmt3d.zero_trace:
        init_model = optim_out.model_ini[:-1]
        model = optim_out.model[:-1]
        modelnorm = np.sqrt(np.sum(optim_out.model[:-1]**2))
        dmnorm = np.sqrt(np.sum((optim_out.model[:-1])**2))
        modelhistory = optim_out.msave[:-1, :]
        hessianhistory = optim_out.hsave
        scale = gcmt3d.scale[:-1]

        # Fix its shape
        hessianhistory = hessianhistory.reshape(
            (optim_out.n, optim_out.n, optim_out.nb_mem))[:-1, :-1, :]

    else:
        init_model = optim_out.model_ini
        model = optim_out.model
        modelnorm = np.sqrt(np.sum(optim_out.model**2))
        dmnorm = np.sqrt(np.sum(optim_out.model**2))
        modelhistory = optim_out.msave
        hessianhistory = optim_out.hsave
        scale = gcmt3d.scale

    cost = optim_out.fcost
    fcost_hist = optim_out.fcost_hist
    fcost_init = optim_out.fcost_init

    # Save to npz file
    np.savez(
        os.path.join(gcmt3d.cmtdir, "summary.npz"),
        cost=cost,
        init_model=init_model,
        modelnorm=modelnorm,
        model=model,
        dmnorm=dmnorm,
        modelhistory=modelhistory,
        fcost_hist=fcost_hist,
        fcost_init=fcost_init,
        scale=scale,
        hessianhistory=hessianhistory
    )

    # To be able to output the current model we need to go back and run one
    # iteration with tht current model
    gcmt3d.forward(optim_out.model)

    # Then compute and save the measurements
    gcmt3d.write_measurements(
        gcmt3d.data_dict, gcmt3d.synt_dict_init, post_fix="before")
    gcmt3d.write_measurements(
        gcmt3d.data_dict, gcmt3d.synt_dict, post_fix="after")
    gcmt3d.plot_final_windows(outputdir=gcmt3d.cmtdir)

    try:
        gcmt3d.save_seismograms()
    except Exception as e:
        print(e)

    # # Write PDF
    plt.switch_backend("pdf")
    # lpy.plot_model_history(
    #     optim_list,
    #     list(pardict.keys()),  # "BFGS-R" "BFGS",
    #     outfile=f"{gcmt3d.cmtdir}/InversionHistory.pdf")
    lpy.plot_optimization(
        optim_list,
        outfile=f"{gcmt3d.cmtdir}/misfit_reduction_history.pdf")
