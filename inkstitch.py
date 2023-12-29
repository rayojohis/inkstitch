# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

import os
import sys
from pathlib import Path
import configparser
import lib.debug_utils as debug_utils

SCRIPTDIR = Path(__file__).parent.absolute()

if len(sys.argv) < 2:
    # no arguments - prevent accidentally running this script
    print("No arguments given, continue without arguments?")
    answer = input("Continue? [y/N] ")
    if answer.lower() != 'y':
        exit(1)

running_as_frozen = getattr(sys, 'frozen', None) is not None  # check if running from pyinstaller bundle

ini = configparser.ConfigParser()
ini.read(SCRIPTDIR / "DEVEL.ini")  # read DEVEL.ini file if exists

# prefer pip installed inkex over inkscape bundled inkex, pip version is bundled with Inkstitch
prefere_pip_inkex = ini.getboolean("LIBRARY","prefer_pip_inkex", fallback=True)  

# check if running from inkscape, given by environment variable
if os.environ.get('INKSTITCH_OFFLINE_SCRIPT', '').lower() in ['true', '1', 'yes', 'y']:
    running_from_inkscape = False
else:
    running_from_inkscape = True

debug_active = bool((gettrace := getattr(sys, 'gettrace')) and gettrace())  # check if debugger is active on startup
debug_type = 'none'
profile_type = 'none'

if not running_as_frozen: # debugging/profiling only in development mode
    # define names of files used by offline Bash script
    bash_file_base = ini.get("DEBUG","bash_file_base", fallback="debug_inkstitch")
    bash_name = Path(bash_file_base).with_suffix(".sh")  # Path object
    bash_svg  = Path(bash_file_base).with_suffix(".svg") # Path object

    # specify debugger type
    # - if script was already started from debugger then don't read debug file
    if not debug_active:
        debug_type = ini.get("DEBUG","debugger", fallback="none")  # debugger type vscode, pycharm, pydevd, none

    # specify profiler type
    profile_type = ini.get("PROFILE","profiler", fallback="none")  # profiler type cprofile, profile, pyinstrument, none

    # process creation of the Bash script
    if running_from_inkscape:
        if ini.getboolean("DEBUG","create_bash_script", fallback=False):  # create script only if enabled in DEVEL.ini
            debug_utils.write_offline_debug_script(SCRIPTDIR, bash_name, bash_svg)
        
        # disable debugger when running from inkscape
        disable_from_inkscape = ini.getboolean("DEBUG","disable_from_inkscape", fallback=False)
        if disable_from_inkscape:
            debug_type = 'none'  # do not start debugger when running from inkscape

    if prefere_pip_inkex and 'PYTHONPATH' in os.environ:
        # see static void set_extensions_env() in inkscape/src/inkscape-main.cpp

        # When running in development mode, we prefer inkex installed by pip, not the one bundled with Inkscape.
        # - move inkscape extensions path to the end of sys.path
        # - we compare PYTHONPATH with sys.path and move PYTHONPATH to the end of sys.path
        #   - also user inkscape extensions path is moved to the end of sys.path - may cause problems?
        #   - path for deprecated-simple are removed from sys.path, will be added later by importing inkex

        # PYTHONPATH to list
        pythonpath = os.environ.get('PYTHONPATH', '').split(os.pathsep)
        # remove pythonpath from sys.path
        sys.path = [p for p in sys.path if p not in pythonpath]
        # remove deprecated-simple, it will be added later by importing inkex
        pythonpath = [p for p in pythonpath if not p.endswith('deprecated-simple')]
        # remove nonexisting paths
        pythonpath = [p for p in pythonpath if os.path.exists(p)]
        # add pythonpath to the end of sys.path
        sys.path.extend(pythonpath)

        # >> should be removed after previous code was tested <<
        # if sys.platform == "darwin":
        #     extensions_path = "/Applications/Inkscape.app/Contents/Resources/share/inkscape/extensions" # Mac
        # else:
        #     extensions_path = "/usr/share/inkscape/extensions" # Linux
        #                                                        # windows ?
        # move inkscape extensions path to the end of sys.path
        # sys.path.remove(extensions_path)
        # sys.path.append(extensions_path)
        # >> ------------------------------------------------- <<

import logging
from argparse import ArgumentParser
from io import StringIO

from lib.exceptions import InkstitchException, format_uncaught_exception

from inkex import errormsg
from lxml.etree import XMLSyntaxError

import lib.debug as debug
from lib import extensions
from lib.i18n import _
from lib.utils import restore_stderr, save_stderr

# file DEBUG exists next to inkstitch.py - enabling debug mode depends on value of debug_type in DEBUG file
if debug_type != 'none':
    debug_file = ini.get("DEBUG","debug_file", fallback="debug.log")
    wait_attach = ini.getboolean("DEBUG","wait_attach", fallback=True) # currently only for vscode
    debug.enable(debug_type, debug_file, wait_attach)
    # check if debugger is really activated
    debug_active = bool((gettrace := getattr(sys, 'gettrace')) and gettrace())

# ignore warnings in releases - see warnings.warn()
if running_as_frozen or not debug_active:
    import warnings
    warnings.filterwarnings('ignore')

# set logger for shapely
logger = logging.getLogger('shapely.geos')  # attach logger of shapely, from ver 2.0.0 all logs are exceptions
logger.setLevel(logging.DEBUG)
shapely_errors = StringIO()                # in memory file to store shapely errors
ch = logging.StreamHandler(shapely_errors)
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

# pop '--extension' from arguments and generate extension class name from extension name
parser = ArgumentParser()
parser.add_argument("--extension")
my_args, remaining_args = parser.parse_known_args()

extension_name = my_args.extension

# example: foo_bar_baz -> FooBarBaz
extension_class_name = extension_name.title().replace("_", "")

extension_class = getattr(extensions, extension_class_name)
extension = extension_class()  # create instance of extension class - call __init__ method

# extension run(), but we differentiate between debug and normal mode
# - in debug or profile mode we run extension or profile extension
# - in normal mode we run extension in try/except block to catch all exceptions and hide GTK spam
if debug_active or profile_type != "none":  # if debug or profile mode
    profile_file_base = ini.get("PROFILE","profile_file_base", fallback="debug_profile")
    profile_path = SCRIPTDIR / profile_file_base  # Path object

    if profile_type == 'none':
        extension.run(args=remaining_args)
    elif profile_type == 'cprofile':
        import cProfile
        import pstats
        profiler = cProfile.Profile()

        profiler.enable()
        extension.run(args=remaining_args)
        profiler.disable()

        profiler.dump_stats(profile_path.with_suffix(".prof"))  # can be read by 'snakeviz -s' or 'pyprof2calltree'
        with open(profile_path, 'w') as stats_file:
            stats = pstats.Stats(profiler, stream=stats_file)
            stats.sort_stats(pstats.SortKey.CUMULATIVE)
            stats.print_stats()
        print(f"profiling stats written to '{profile_path.name}' and '{profile_path.name}.prof'. Use snakeviz to see it.", file=sys.stderr)

    elif profile_type == 'profile':
        import profile
        import pstats
        profiler = profile.Profile()

        profiler.run('extension.run(args=remaining_args)')

        profiler.dump_stats(profile_path.with_suffix(".prof"))  # can be read by 'snakeviz' or 'pyprof2calltree' - seems broken
        with open(profile_path, 'w') as stats_file:
            stats = pstats.Stats(profiler, stream=stats_file)
            stats.sort_stats(pstats.SortKey.CUMULATIVE)
            stats.print_stats()
        print(f"profiling stats written to '{profile_path.name}'", file=sys.stderr)

    elif profile_type == 'pyinstrument':
        import pyinstrument
        profiler = pyinstrument.Profiler()

        profiler.start()
        extension.run(args=remaining_args)
        profiler.stop()

        profile_path = SCRIPTDIR / "profile_stats.html"
        with open(profile_path, 'w') as stats_file:
            stats_file.write(profiler.output_html())
        print(f"profiling stats written to '{profile_path.name}'. Use browser to see it.", file=sys.stderr)

else:   # if not debug nor profile mode
    save_stderr() # hide GTK spam
    exception = None
    try:
        extension.run(args=remaining_args)
    except (SystemExit, KeyboardInterrupt):
        raise
    except XMLSyntaxError:
        msg = _("Ink/Stitch cannot read your SVG file. "
                "This is often the case when you use a file which has been created with Adobe Illustrator.")
        msg += "\n\n"
        msg += _("Try to import the file into Inkscape through 'File > Import...' (Ctrl+I)")
        errormsg(msg)
    except InkstitchException as exc:
        errormsg(str(exc))
    except Exception:
        errormsg(format_uncaught_exception())
        sys.exit(1)
    finally:
        restore_stderr()

        if shapely_errors.tell():
            errormsg(shapely_errors.getvalue())

    sys.exit(0)
