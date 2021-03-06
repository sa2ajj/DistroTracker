# Copyright 2013 The Distro Tracker Developers
# See the COPYRIGHT file at the top-level directory of this distribution and
# at http://deb.li/DTAuthors
#
# This file is part of Distro Tracker. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution and at http://deb.li/DTLicense. No part of Distro Tracker,
# including this file, may be copied, modified, propagated, or distributed
# except according to the terms contained in the LICENSE file.
"""Utilities for processing Debian package information."""
from __future__ import unicode_literals
from pts.core.utils.email_messages import (
    name_and_address_from_string as parse_address,
    names_and_addresses_from_string as parse_addresses
)
from django.conf import settings
from django.utils.encoding import force_bytes

from debian import deb822
from pts.core.utils import extract_tar_archive
from pts.core.models import Repository
from pts.core.models import Architecture

import os
import apt
import shutil
import apt_pkg
import subprocess


def extract_vcs_information(stanza):
    """
    Extracts the VCS information from a package's Sources entry.

    :param stanza: The ``Sources`` entry from which to extract the VCS info.
        Maps ``Sources`` key names to values.
    :type stanza: dict

    :returns: VCS information regarding the package. Contains the following keys:
        type[, browser, url]
    :rtype: dict
    """
    vcs = {}
    for key, value in stanza.items():
        key = key.lower()
        if key == 'vcs-browser':
            vcs['browser'] = value
        elif key.startswith('vcs-'):
            vcs['type'] = key[4:]
            vcs['url'] = value
    return vcs


def extract_dsc_file_name(stanza):
    """
    Extracts the name of the .dsc file from a package's Sources entry.

    :param stanza: The ``Sources`` entry from which to extract the VCS info.
        Maps ``Sources`` key names to values.
    :type stanza: dict

    """
    for file in stanza.get('files', []):
        if file.get('name', '').endswith('.dsc'):
            return file['name']

    return None


def extract_information_from_sources_entry(stanza):
    """
    Extracts information from a ``Sources`` file entry and returns it in the
    form of a dictionary.

    :param stanza: The raw entry's key-value pairs.
    :type stanza: Case-insensitive dict
    """
    binaries = [
        binary.strip()
        for binary in stanza['binary'].split(',')
    ]
    entry = {
        'version': stanza['version'],
        'homepage': stanza.get('homepage', ''),
        'priority': stanza.get('priority', ''),
        'section': stanza.get('section', ''),
        'architectures': stanza['architecture'].split(),
        'binary_packages': binaries,
        'maintainer': parse_address(stanza['maintainer']),
        'uploaders': parse_addresses(stanza.get('uploaders', '')),
        'standards_version': stanza.get('standards-version', ''),
        'vcs': extract_vcs_information(stanza),
        'dsc_file_name': extract_dsc_file_name(stanza),
        'directory': stanza.get('directory', ''),
    }

    return entry


def extract_information_from_packages_entry(stanza):
    """
    Extracts information from a ``Packages`` file entry and returns it in the
    form of a dictionary.

    :param stanza: The raw entry's key-value pairs.
    :type stanza: Case-insensitive dict
    """
    entry = {
        'version': stanza['version'],
        'short_description': stanza.get('description', ''),
    }

    return entry


class SourcePackageRetrieveError(Exception):
    pass


class AptCache(object):
    """
    A class for handling cached package information.
    """
    DEFAULT_MAX_SIZE = 1 * 1024 ** 3  # 1 GiB
    QUILT_FORMAT = '3.0 (quilt)'

    class AcquireProgress(apt.progress.base.AcquireProgress):
        """
        Instances of this class can be passed to :meth:`apt.cache.Cache.update`
        calls.
        It provides a way to track which files were changed and which were not
        by an update operation.
        """
        def __init__(self, *args, **kwargs):
            super(AptCache.AcquireProgress, self).__init__(*args, **kwargs)
            self.fetched = []
            self.hit = []

        def done(self, item):
            self.fetched.append(os.path.split(item.owner.destfile)[1])

        def ims_hit(self, item):
            self.hit.append(os.path.split(item.owner.destfile)[1])

        def pulse(self, owner):
            return True

    def __init__(self):
        # The root cache directory is a subdirectory in the PTS_CACHE_DIRECTORY
        self.cache_root_dir = os.path.join(
            settings.PTS_CACHE_DIRECTORY,
            'apt-cache'
        )
        # Create the cache directory if it didn't already exist
        self._create_cache_directory()

        self.sources_list_path = os.path.join(
            self.cache_root_dir,
            'sources.list')
        self.conf_file_path = os.path.join(self.cache_root_dir, 'apt.conf')

        self.sources = []
        self.packages = []
        self.cache_max_size = getattr(
            settings, 'PTS_APT_CACHE_MAX_SIZE', self.DEFAULT_MAX_SIZE)
        #: The directory where source package files are cached
        self.source_cache_directory = os.path.join(self.cache_root_dir, 'packages')
        self._cache_size = None  # Evaluate the cache size lazily

        self.configure_cache()

    @property
    def cache_size(self):
        if self._cache_size is None:
            self._cache_size = self.get_directory_size(self.source_cache_directory)
        return self._cache_size

    def get_directory_size(self, directory_path):
        """
        Returns the total space taken by the given directory in bytes.

        :param directory_path: The path to the directory
        :type directory_path: string

        :rtype: int
        """
        # Convert the directory path to bytes to make sure all os calls deal
        # with bytes, not unicode objects.
        # This way any file names with invalid utf-8 names, are correctly
        # handled, without causing an error.
        directory_path = force_bytes(directory_path)
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(directory_path):
            for file_name in filenames:
                file_path = os.path.join(dirpath, file_name)
                stat = os.lstat(file_path)
                total_size += stat.st_size

        return total_size

    def _create_cache_directory(self):
        if not os.path.exists(self.cache_root_dir):
            os.makedirs(self.cache_root_dir)

    def clear_cache(self):
        """
        Removes all cache information. This causes the next update to retrieve
        fresh repository files.
        """
        self._remove_dir(self.cache_root_dir)
        self._create_cache_directory()

    def update_sources_list(self):
        """
        Updates the ``sources.list`` file used to list repositories for which
        package information should be cached.
        """
        with open(self.sources_list_path, 'w') as sources_list:
            for repository in Repository.objects.all():
                sources_list.write(repository.sources_list_entry + '\n')

    def update_apt_conf(self):
        """
        Updates the ``apt.conf`` file which gives general settings for the
        :class:`apt.cache.Cache`.

        In particular, this updates the list of all architectures which should
        be considered in package updates based on architectures that the
        repositories support.
        """
        with open(self.conf_file_path, 'w') as conf_file:
            conf_file.write('APT::Architectures { ')
            for architecture in Architecture.objects.all():
                conf_file.write('"{arch}"; '.format(arch=architecture))
            conf_file.write('};\n')

    def _configure_apt(self):
        """
        Initializes the :mod:`apt_pkg` module global settings.
        """
        apt_pkg.init_config()
        apt_pkg.init_system()
        apt_pkg.read_config_file(apt_pkg.config, self.conf_file_path)
        apt_pkg.config.set('Dir::Etc', self.cache_root_dir)

    def configure_cache(self):
        """
        Configures the cache based on the most current repository information.
        """
        self.update_sources_list()
        self.update_apt_conf()
        self._configure_apt()

    def _index_file_full_path(self, file_name):
        """
        Returns the absolute path for the given cached index file.

        :param file_name: The name of the cached index file.
        :type file_name: string

        :rtype: string
        """
        return os.path.join(
            self.cache_root_dir,
            'var/lib/apt/lists',
            file_name
        )

    def _match_index_file_to_repository(self, sources_file):
        """
        Returns the :class:`Repository <pts.core.models.Repository>` instance
        which matches the given cached ``Sources`` file.

        :rtype: :class:`Repository <pts.core.models.Repository>`
        """
        sources_list = apt_pkg.SourceList()
        sources_list.read_main_list()
        component_url = None
        for entry in sources_list.list:
            for index_file in entry.index_files:
                if os.path.basename(sources_file) in index_file.describe:
                    split_description = index_file.describe.split()
                    component_url = split_description[0] + split_description[1]
                    break
        for repository in Repository.objects.all():
            if component_url in repository.component_urls:
                return repository

    def _get_all_cached_files(self):
        """
        Returns a list of all cached files.
        """
        lists_directory = os.path.join(self.cache_root_dir, 'var/lib/apt/lists')
        try:
            return [
                os.path.join(lists_directory, file_name)
                for file_name in os.listdir(lists_directory)
                if os.path.isfile(os.path.join(lists_directory, file_name))
            ]
        except OSError:
            # The directory structure does not exist => nothing is cached
            return []

    def get_cached_files(self, filter_function=None):
        """
        Returns cached files, optionally filtered by the given
        ``filter_function``

        :param filter_function: Takes a file name as the only parameter and
            returns a :class:`bool` indicating whether it should be included
            in the result.
        :type filter_function: callable

        :returns: A list of cached file names
        :rtype: list
        """
        if filter_function is None:
            # Include all files if the filter function is not provided
            filter_function = lambda x: True

        return [
            file_name
            for file_name in self._get_all_cached_files()
            if filter_function(file_name)
        ]

    def get_sources_files_for_repository(self, repository):
        """
        Returns all ``Sources`` files which are cached for the given
        repository.

        For instance, ``Sources`` files for different suites are cached
        separately.

        :param repository: The repository for which to return all cached
            ``Sources`` files
        :type repository: :class:`Repository <pts.core.models.Repository>`

        :rtype: ``iterable`` of strings
        """
        return self.get_cached_files(
            lambda file_name: (
                file_name.endswith('Sources') and
                self._match_index_file_to_repository(file_name) == repository))

    def get_packages_files_for_repository(self, repository):
        """
        Returns all ``Packages`` files which are cached for the given
        repository.

        For instance, ``Packages`` files for different suites are cached
        separately.

        :param repository: The repository for which to return all cached
            ``Packages`` files
        :type repository: :class:`Repository <pts.core.models.Repository>`

        :rtype: ``iterable`` of strings
        """
        return self.get_cached_files(
            lambda file_name: (
                file_name.endswith('Packages') and
                self._match_index_file_to_repository(file_name) == repository))

    def update_repositories(self, force_download=False):
        """
        Initiates a cache update.

        :param force_download: If set to ``True`` causes the cache to be
            cleared before starting the update, thus making sure all index
            files are downloaded again.

        :returns: A two-tuple ``(updated_sources, updated_packages)``. Each of
            the tuple's members is a list of
            (:class:`Repository <pts.core.models.Repository>`, ``file_name``)
            pairs representing the repository which was updated and the file
            which contains the fresh information. The file is either a
            ``Sources`` or a ``Packages`` file, respectively.
        """
        if force_download:
            self.clear_cache()

        self.configure_cache()

        cache = apt.cache.Cache(rootdir=self.cache_root_dir)
        progress = AptCache.AcquireProgress()
        cache.update(progress)

        updated_sources = []
        updated_packages = []
        for fetched_file in progress.fetched:
            if fetched_file.endswith('Sources'):
                dest = updated_sources
            elif fetched_file.endswith('Packages'):
                dest = updated_packages
            else:
                continue
            repository = self._match_index_file_to_repository(fetched_file)
            dest.append((
                repository, self._index_file_full_path(fetched_file)
            ))

        return updated_sources, updated_packages

    def _get_format(self, record):
        """
        Returns the Format field value of the given source package record.
        """
        record = deb822.Deb822(record)
        return record['format']

    def _extract_quilt_package_debian_tar(self, debian_tar_path, outdir):
        """
        Extracts the given tarball to the given output directory.
        """
        extract_tar_archive(debian_tar_path, outdir)

    def get_package_source_cache_directory(self, package_name):
        """
        Returns the path to the directory where a particular source package is
        cached.

        :param package_name: The name of the source package
        :type package_name: string

        :rtype: string
        """
        package_hash = (
            package_name[0]
            if not package_name.startswith('lib') else
            package_name[:4]
        )
        return os.path.join(
            self.source_cache_directory,
            package_hash,
            package_name)

    def get_source_version_cache_directory(self, package_name, version):
        """
        Returns the path to the directory where a particular source package
        version files are extracted.

        :param package_name: The name of the source package
        :type package_name: string

        :param version: The version of the source package
        :type version: string

        :rtype: string
        """
        package_dir = self.get_package_source_cache_directory(package_name)
        return os.path.join(package_dir, package_name + '-' + version)

    def _remove_dir(self, directory_path):
        """
        Removes the given directory, including any subdirectories and files.
        The method makes sure to correctly handle the situation where the
        directory contains files with names which are invalid utf-8.
        """
        # Convert the directory path to bytes to make sure all os calls deal
        # with bytes, not unicode objects.
        # This way any file names with invalid utf-8 names, are correctly
        # handled, without causing an error.
        directory_path = force_bytes(directory_path)
        if os.path.exists(directory_path):
            shutil.rmtree(directory_path)

    def clear_cached_sources(self):
        """
        Clears all cached package source files.
        """
        self._remove_dir(self.source_cache_directory)
        self._cache_size = self.get_directory_size(self.source_cache_directory)

    def _get_apt_source_records(self, source_name, version):
        """
        Returns a :class:`apt_pkg.SourceRecords` instance where the given
        source package is the current working record.
        """
        cache = apt.cache.Cache(rootdir=self.cache_root_dir)
        source_records = apt_pkg.SourceRecords()
        source_records.restart()
        # Find the cached record matching this source package and version
        found = False
        while source_records.lookup(source_name):
            if source_records.version == version:
                found = True
                break

        if not found:
            # Package version does not exist in the cache
            raise SourcePackageRetrieveError(
                "Could not retrieve package {pkg} version {ver}:"
                " No such version found in the cache".format(
                    pkg=source_name, ver=version))

        return source_records

    def _extract_dpkg_source(self, retrieved_files, outdir):
        """
        Uses dpkg-source to extract the source package.
        """
        dsc_file_path = next(
            file_path
            for file_path in retrieved_files
            if file_path.endswith('.dsc'))
        subprocess.check_call(["dpkg-source", "-x", dsc_file_path, outdir])

    def _apt_acquire_package(self,
                             source_records,
                             dest_dir_path,
                             debian_directory_only):
        """
        Using :class:`apt_pkg.Acquire`, retrieves the source files for the
        source package described by the current source_records record.

        :param source_records: The record describing the source package whose
            files should be retrieved.
        :type source_records: :class:`apt_pkg.Acquire`

        :param dest_dir_path: The path to the directory where the downloaded
            files should be saved.
        :type dest_dir_path: string

        :param debian_directory_only: A flag indicating whether only the debian
            directory should be downloaded.

        :returns: A list of absolute paths of all retrieved source files.
        :rtype: list of strings
        """
        package_format = self._get_format(source_records.record)
        # A reference to each AcquireFile instance must be kept
        files = []
        acquire = apt_pkg.Acquire(apt.progress.base.AcquireProgress())
        for md5, size, path, file_type in source_records.files:
            base = os.path.basename(path)
            dest_file_path = os.path.join(dest_dir_path, base)
            if debian_directory_only and package_format == self.QUILT_FORMAT:
                if file_type != 'diff':
                    # Only retrieve the .debian.tar.* file for quilt packages
                    # when only the debian directory is wanted
                    continue
            files.append(apt_pkg.AcquireFile(
                acquire,
                source_records.index.archive_uri(path),
                md5,
                size,
                base,
                destfile=dest_file_path
            ))

        acquire.run()

        # Check if all items are correctly retrieved and build the list of file
        # paths.
        retrieved_paths = []
        for item in acquire.items:
            if item.status != item.STAT_DONE:
                raise SourcePackageRetrieveError(
                    'Could not retrieve file {file}: {error}'.format(
                        file=item.destfile, error=item.error_text))
            retrieved_paths.append(item.destfile)

        return retrieved_paths

    def retrieve_source(self, source_name, version,
                        debian_directory_only=False):
        """
        Retrieve the source package files for the given source package version.

        :param source_name: The name of the source package
        :type source_name: string
        :param version: The version of the source package
        :type version: string
        :param debian_directory_only: Flag indicating if the method should try
            to retrieve only the debian directory of the source package. This
            is usually only possible when the package format is 3.0 (quilt).
        :type debian_directory_only: Boolean

        :returns: The path to the directory containing the extracted source
            package files.
        :rtype: string
        """
        if self.cache_size > self.cache_max_size:
            # If the maximum allowed cache size has been exceeded, clear the cache
            self.clear_cached_sources()

        source_records = self._get_apt_source_records(source_name, version)

        dest_dir_path = self.get_package_source_cache_directory(source_name)
        if not os.path.exists(dest_dir_path):
            os.makedirs(dest_dir_path)
        # Remember the size of the directory in the beginning
        old_size = self.get_directory_size(dest_dir_path)

        # Download the source files
        retrieved_files = self._apt_acquire_package(
            source_records, dest_dir_path, debian_directory_only)

        # Extract the retrieved source files
        outdir = self.get_source_version_cache_directory(source_name, version)
        # dpkg-source expects this directory not to exist
        self._remove_dir(outdir)

        package_format = self._get_format(source_records.record)
        if debian_directory_only and package_format == self.QUILT_FORMAT:
            # dpkg-source cannot extract an incomplete package
            self._extract_quilt_package_debian_tar(retrieved_files[0], outdir)
        else:
            # Let dpkg-source handle the extraction in all other cases
            self._extract_dpkg_source(retrieved_files, outdir)

        # Update the current cache size based on the changes made by getting
        # this source package.
        new_size = self.get_directory_size(dest_dir_path)
        size_delta = new_size - old_size
        self._cache_size += size_delta

        return outdir
