__all__ = ("data_dict", "scheduler_download_data")

import argparse
import os
import warnings
from shutil import rmtree, unpack_archive

import requests
from requests.exceptions import ConnectionError
from tqdm.auto import tqdm

from .data_sets import data_versions, get_data_dir

DEFAULT_DATA_URL = "https://s3df.slac.stanford.edu/data/rubin/sim-data/rubin_sim_data/"


def data_dict():
    """Creates a `dict` for all data buckets and the tar file they map to.
    To create tar files and follow any sym links, run:
        ``tar -chvzf maf_may_2021.tgz maf``

    Returns
    -------
    result : `dict`
        Data bucket filenames dictionary with keys:
        ``"name"``
            Data bucket name (`str`).
        ``"version"``
            Versioned file name (`str`).
    """
    file_dict = {
        "scheduler": "scheduler_2023_10_16.tgz",
        "site_models": "site_models_2023_10_02.tgz",
        "skybrightness_pre": "skybrightness_pre_2023_10_17.tgz",
        "tests": "tests_2022_10_18.tgz",
    }
    return file_dict


def scheduler_download_data(file_dict=None):
    """Download data."""

    if file_dict is None:
        file_dict = data_dict()
    parser = argparse.ArgumentParser(description="Download data files for rubin_sim package")
    parser.add_argument(
        "--versions",
        dest="versions",
        default=False,
        action="store_true",
        help="Report expected versions, then quit",
    )
    parser.add_argument(
        "-d",
        "--dirs",
        type=str,
        default=None,
        help="Comma-separated list of directories to download",
    )
    parser.add_argument(
        "-f",
        "--force",
        dest="force",
        default=False,
        action="store_true",
        help="Force re-download of data directory(ies)",
    )
    parser.add_argument(
        "--url_base",
        type=str,
        default=DEFAULT_DATA_URL,
        help="Root URL of download location",
    )
    parser.add_argument(
        "--orbits_pre",
        dest="orbits",
        default=False,
        action="store_true",
        help="Include pre-computed orbit files.",
    )
    parser.add_argument(
        "--tdqm_disable",
        dest="tdqm_disable",
        default=False,
        action="store_true",
        help="Turn off tdqm progress bar",
    )
    args = parser.parse_args()

    dirs = args.dirs
    if dirs is None:
        dirs = file_dict.keys()
    else:
        dirs = dirs.split(",")

    data_dir = get_data_dir()
    if not os.path.isdir(data_dir):
        os.mkdir(data_dir)
    version_file = os.path.join(data_dir, "versions.txt")
    versions = data_versions()
    if versions is None:
        versions = {}

    if args.versions:
        print("Versions on disk currently // versions expected for this release:")
        match = True
        for k in file_dict:
            print(f"{k} : {versions.get(k, '')} // {file_dict[k]}")
            if versions.get(k, "") != file_dict[k]:
                match = False
        if match:
            print("Versions are in sync")
            return 0
        else:
            print("Versions do not match")
            return 1

    if not args.orbits:
        dirs = [key for key in dirs if "orbits_precompute" not in key]

    # See if base URL is alive
    url_base = args.url_base
    try:
        r = requests.get(url_base)
        fail_message = f"Could not connect to {args.url_base} or {url_base}. Check sites are up?"
    except ConnectionError:
        print(fail_message)
        exit()
    if r.status_code != requests.codes.ok:
        print(fail_message)
        exit()

    for key in dirs:
        filename = file_dict[key]
        path = os.path.join(data_dir, key)
        if os.path.isdir(path) and not args.force:
            warnings.warn("Directory %s already exists, skipping download" % path)
        else:
            if os.path.isdir(path) and args.force:
                rmtree(path)
                warnings.warn("Removed existing directory %s, downloading new copy" % path)
            # Download file
            url = url_base + filename
            print("Downloading file: %s" % url)
            # Stream and write in chunks (avoid large memory usage)
            r = requests.get(url, stream=True)
            file_size = int(r.headers.get("Content-Length", 0))
            if file_size < 245:
                warnings.warn(f"{url} file size unexpectedly small.")
            # Download this size chunk at a time; reasonable guess
            block_size = 1024 * 1024
            progress_bar = tqdm(total=file_size, unit="iB", unit_scale=True, disable=args.tdqm_disable)
            print(f"Writing to {os.path.join(data_dir, filename)}")
            with open(os.path.join(data_dir, filename), "wb") as f:
                for chunk in r.iter_content(chunk_size=block_size):
                    progress_bar.update(len(chunk))
                    f.write(chunk)
            progress_bar.close()
            # untar in place
            unpack_archive(os.path.join(data_dir, filename), data_dir)
            os.remove(os.path.join(data_dir, filename))
            versions[key] = file_dict[key]

    # Write out the new version info to the data directory
    with open(version_file, "w") as f:
        for key in versions:
            print(key + "," + versions[key], file=f)

    # Write a little table to stdout
    new_versions = data_versions()
    print("Current/updated data versions:")
    for k in new_versions:
        if len(k) <= 10:
            sep = "\t\t"
        else:
            sep = "\t"
        print(f"{k}{sep}{new_versions[k]}")