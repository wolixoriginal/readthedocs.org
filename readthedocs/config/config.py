# pylint: disable=too-many-lines

"""Build configuration for rtd."""

import copy
import os
import re
from contextlib import contextmanager
from functools import lru_cache

from django.conf import settings

from readthedocs.config.utils import list_to_dict, to_dict
from readthedocs.core.utils.filesystem import safe_open
from readthedocs.projects.constants import GENERIC

from .find import find_one
from .models import (
    Build,
    BuildJobs,
    BuildTool,
    BuildWithOs,
    Conda,
    Mkdocs,
    Python,
    PythonInstall,
    PythonInstallRequirements,
    Search,
    Sphinx,
    Submodules,
)
from .parser import ParseError, parse
from .validation import (
    VALUE_NOT_FOUND,
    ValidationError,
    validate_bool,
    validate_choice,
    validate_dict,
    validate_list,
    validate_path,
    validate_path_pattern,
    validate_string,
)

__all__ = (
    "ALL",
    "load",
    "BuildConfigV1",
    "BuildConfigV2",
    "ConfigError",
    "ConfigOptionNotSupportedError",
    "ConfigFileNotFound",
    "DefaultConfigFileNotFound",
    "InvalidConfig",
    "PIP",
    "SETUPTOOLS",
    "LATEST_CONFIGURATION_VERSION",
)

ALL = 'all'
PIP = 'pip'
SETUPTOOLS = 'setuptools'
CONFIG_FILENAME_REGEX = r'^\.?readthedocs.ya?ml$'

CONFIG_NOT_SUPPORTED = 'config-not-supported'
VERSION_INVALID = 'version-invalid'
CONFIG_SYNTAX_INVALID = 'config-syntax-invalid'
CONFIG_REQUIRED = 'config-required'
CONFIG_FILE_REQUIRED = 'config-file-required'
PYTHON_INVALID = 'python-invalid'
SUBMODULES_INVALID = 'submodules-invalid'
INVALID_KEYS_COMBINATION = 'invalid-keys-combination'
INVALID_KEY = 'invalid-key'
INVALID_NAME = 'invalid-name'

LATEST_CONFIGURATION_VERSION = 2


# TODO: make these exception to inherit from `BuildUserError`
class ConfigError(Exception):

    """Base error for the rtd configuration file."""

    def __init__(self, message, code):
        self.code = code
        super().__init__(message)


class ConfigFileNotFound(ConfigError):

    """Error when we can't find a configuration file."""

    def __init__(self, directory):
        super().__init__(
            f'Configuration file not found in: {directory}',
            CONFIG_FILE_REQUIRED,
        )


class DefaultConfigFileNotFound(ConfigError):

    """Error when we can't find a configuration file."""

    def __init__(self, directory):
        super().__init__(
            f"No default configuration file in: {directory}",
            CONFIG_FILE_REQUIRED,
        )


class ConfigOptionNotSupportedError(ConfigError):

    """Error for unsupported configuration options in a version."""

    def __init__(self, configuration):
        self.configuration = configuration
        template = (
            'The "{}" configuration option is not supported in this version'
        )
        super().__init__(
            template.format(self.configuration),
            CONFIG_NOT_SUPPORTED,
        )


class InvalidConfig(ConfigError):

    """Error for a specific key validation."""

    message_template = 'Invalid "{key}": {error}'

    def __init__(self, key, code, error_message, source_file=None):
        self.key = key
        self.code = code
        self.source_file = source_file
        message = self.message_template.format(
            key=self._get_display_key(),
            code=code,
            error=error_message,
        )
        super().__init__(message, code=code)

    def _get_display_key(self):
        """
        Display keys in a more friendly format.

        Indexes are displayed like ``n``,
        but users may be more familiar with the ``[n]`` syntax.
        For example ``python.install.0.requirements``
        is changed to `python.install[0].requirements`.
        """
        return re.sub(
            r'^([a-zA-Z_.-]+)\.(\d+)([a-zA-Z_.-]*)$',
            r'\1[\2]\3',
            self.key
        )


class BuildConfigBase:

    """
    Config that handles the build of one particular documentation.

    .. note::

       You need to call ``validate`` before the config is ready to use.

    :param env_config: A dict that contains additional information
                       about the environment.
    :param raw_config: A dict with all configuration without validation.
    :param source_file: The file that contains the configuration.
                        All paths are relative to this file.
                        If a dir is given, the configuration was loaded
                        from another source (like the web admin).
    """

    PUBLIC_ATTRIBUTES = [
        'version',
        'formats',
        'python',
        'conda',
        'build',
        'doctype',
        'sphinx',
        'mkdocs',
        'submodules',
        'search',
    ]

    default_build_image = settings.DOCKER_DEFAULT_VERSION

    version = None

    def __init__(self, env_config, raw_config, source_file):
        self.env_config = env_config
        self._raw_config = copy.deepcopy(raw_config)
        self.source_config = copy.deepcopy(raw_config)
        self.source_file = source_file
        if os.path.isdir(self.source_file):
            self.base_path = self.source_file
        else:
            self.base_path = os.path.dirname(self.source_file)
        self.defaults = self.env_config.get('defaults', {})

        self._config = {}

    def error(self, key, message, code):
        """Raise an error related to ``key``."""
        if not os.path.isdir(self.source_file):
            source = os.path.relpath(self.source_file, self.base_path)
            error_message = '{source}: {message}'.format(
                source=source,
                message=message,
            )
        else:
            error_message = message
        raise InvalidConfig(
            key=key,
            code=code,
            error_message=error_message,
            source_file=self.source_file,
        )

    @contextmanager
    def catch_validation_error(self, key):
        """Catch a ``ValidationError`` and raises an ``InvalidConfig`` error."""
        try:
            yield
        except ValidationError as error:
            raise InvalidConfig(
                key=key,
                code=error.code,
                error_message=str(error),
                source_file=self.source_file,
            )

    def pop(self, name, container, default, raise_ex):
        """
        Search and pop a key inside a dict.

        This will pop the keys recursively if the container is empty.

        :param name: the key name in a list form (``['key', 'inner']``)
        :param container: a dictionary that contains the key
        :param default: default value to return if the key doesn't exists
        :param raise_ex: if True, raises an exception when a key is not found
        """
        key = name[0]
        validate_dict(container)
        if key in container:
            if len(name) > 1:
                value = self.pop(name[1:], container[key], default, raise_ex)
                if not container[key]:
                    container.pop(key)
            else:
                value = container.pop(key)
            return value
        if raise_ex:
            raise ValidationError(key, VALUE_NOT_FOUND)
        return default

    def pop_config(self, key, default=None, raise_ex=False):
        """
        Search and pop a key (recursively) from `self._raw_config`.

        :param key: the key name in a dotted form (``key.innerkey``)
        :param default: Optionally, it can receive a default value
        :param raise_ex: If True, raises an exception when the key is not found
        """
        return self.pop(key.split('.'), self._raw_config, default, raise_ex)

    def validate(self):
        raise NotImplementedError()

    @property
    def using_build_tools(self):
        return isinstance(self.build, BuildWithOs)

    @property
    def is_using_conda(self):
        if self.using_build_tools:
            return self.python_interpreter in ("conda", "mamba")
        return self.conda is not None

    @property
    def is_using_setup_py_install(self):
        """Check if this project is using `setup.py install` as installation method."""
        for install in self.python.install:
            if isinstance(install, PythonInstall) and install.method == SETUPTOOLS:
                return True
        return False

    @property
    def python_interpreter(self):
        if self.using_build_tools:
            tool = self.build.tools.get('python')
            if tool and tool.version.startswith('mamba'):
                return 'mamba'
            if tool and tool.version.startswith('miniconda'):
                return 'conda'
            if tool:
                return 'python'
            return None
        version = self.python_full_version
        if version.startswith('pypy'):
            # Allow to specify ``pypy3.5`` as Python interpreter
            return version
        return f'python{version}'

    @property
    def docker_image(self):
        if self.using_build_tools:
            return self.settings['os'][self.build.os]
        return self.build.image

    @property
    def python_full_version(self):
        version = self.python.version
        if version in ['2', '3']:
            # use default Python version if user only set '2', or '3'
            return self.get_default_python_version_for_image(
                self.build.image,
                version,
            )
        return version

    @property
    def valid_build_images(self):
        """
        Return all the valid Docker image choices for ``build.image`` option.

        The user can use any of this values in the YAML file. These values are
        the keys of ``DOCKER_IMAGE_SETTINGS`` Django setting (without the
        ``readthedocs/build`` part) plus ``stable``, ``latest`` and ``testing``.
        """
        images = {'stable', 'latest', 'testing'}
        for k in settings.DOCKER_IMAGE_SETTINGS:
            _, version = k.split(':')
            if re.fullmatch(r'^[\d\.]+$', version):
                images.add(version)
        return images

    def get_valid_python_versions_for_image(self, build_image):
        """
        Return all the valid Python versions for a Docker image.

        The Docker image (``build_image``) has to be its complete name, already
        validated: ``readthedocs/build:4.0``, not just ``4.0``.

        Returns supported versions for the ``DOCKER_DEFAULT_VERSION`` if not
        ``build_image`` found.
        """
        if build_image not in settings.DOCKER_IMAGE_SETTINGS:
            build_image = '{}:{}'.format(
                settings.DOCKER_DEFAULT_IMAGE,
                self.default_build_image,
            )
        return settings.DOCKER_IMAGE_SETTINGS[build_image]['python']['supported_versions']

    def get_default_python_version_for_image(self, build_image, python_version):
        """
        Return the default Python version for Docker image and Py2 or Py3.

        :param build_image: the Docker image complete name, already validated
            (``readthedocs/build:4.0``, not just ``4.0``)
        :type build_image: str

        :param python_version: major Python version (``2`` or ``3``) to get its
            default full version
        :type python_version: int

        :returns: default version for the ``DOCKER_DEFAULT_VERSION`` if not
                  ``build_image`` found.
        """
        if build_image not in settings.DOCKER_IMAGE_SETTINGS:
            build_image = '{}:{}'.format(
                settings.DOCKER_DEFAULT_IMAGE,
                self.default_build_image,
            )
        return (
            # For linting
            settings.DOCKER_IMAGE_SETTINGS[build_image]['python']
            ['default_version'][python_version]
        )

    def as_dict(self):
        config = {}
        for name in self.PUBLIC_ATTRIBUTES:
            attr = getattr(self, name)
            config[name] = to_dict(attr)
        return config

    def __getattr__(self, name):
        """Raise an error for unknown attributes."""
        raise ConfigOptionNotSupportedError(name)


class BuildConfigV1(BuildConfigBase):

    """Version 1 of the configuration file."""

    PYTHON_INVALID_MESSAGE = '"python" section must be a mapping.'
    PYTHON_EXTRA_REQUIREMENTS_INVALID_MESSAGE = (
        '"python.extra_requirements" section must be a list.'
    )

    version = '1'

    def get_valid_python_versions(self):
        """
        Return all valid Python versions.

        .. note::

            It does not take current build image used into account.
        """
        try:
            return self.env_config['python']['supported_versions']
        except (KeyError, TypeError):
            versions = set()
            for _, options in settings.DOCKER_IMAGE_SETTINGS.items():
                versions = versions.union(
                    options['python']['supported_versions']
                )
            return versions

    def get_valid_formats(self):  # noqa
        """Get all valid documentation formats."""
        return (
            'htmlzip',
            'pdf',
            'epub',
        )

    def validate(self):
        """
        Validate and process ``raw_config`` and ``env_config`` attributes.

        It makes sure that:

        - ``base`` is a valid directory and defaults to the directory of the
          ``readthedocs.yml`` config file if not set
        """
        # Validate env_config.
        # Validate the build environment first
        # Must happen before `validate_python`!
        self._config['build'] = self.validate_build()

        # Validate raw_config. Order matters.
        self._config['python'] = self.validate_python()
        self._config['formats'] = self.validate_formats()

        self._config['conda'] = self.validate_conda()
        self._config['requirements_file'] = self.validate_requirements_file()

    def validate_build(self):
        """
        Validate the build config settings.

        This is a bit complex,
        so here is the logic:

        * We take the default image & version if it's specific in the environment
        * Then update the _version_ from the users config
        * Then append the default _image_, since users can't change this
        * Then update the env_config with the settings for that specific image
           - This is currently used for a build image -> python version mapping

        This means we can use custom docker _images_,
        but can't change the supported _versions_ that users have defined.
        """
        # Defaults
        if 'build' in self.env_config:
            build = self.env_config['build'].copy()
        else:
            build = {'image': settings.DOCKER_IMAGE}

        # User specified
        if 'build' in self._raw_config:
            _build = self._raw_config['build']
            if 'image' in _build:
                with self.catch_validation_error('build'):
                    build['image'] = validate_choice(
                        str(_build['image']),
                        self.valid_build_images,
                    )
            if ':' not in build['image']:
                # Prepend proper image name to user's image name
                build['image'] = '{}:{}'.format(
                    settings.DOCKER_DEFAULT_IMAGE,
                    build['image'],
                )
        # Update docker default settings from image name
        if build['image'] in settings.DOCKER_IMAGE_SETTINGS:
            self.env_config.update(
                settings.DOCKER_IMAGE_SETTINGS[build['image']]
            )

        # Allow to override specific project
        config_image = self.defaults.get('build_image')
        if config_image:
            build['image'] = config_image
        return build

    def validate_python(self):
        """Validates the ``python`` key, set default values it's necessary."""
        install_project = self.defaults.get('install_project', False)
        use_system_packages = self.defaults.get('use_system_packages', False)
        version = self.defaults.get('python_version', '2')
        python = {
            'use_system_site_packages': use_system_packages,
            'install_with_pip': False,
            'extra_requirements': [],
            'install_with_setup': install_project,
            'version': version,
        }

        if 'python' in self._raw_config:
            raw_python = self._raw_config['python']
            if not isinstance(raw_python, dict):
                self.error(
                    'python',
                    self.PYTHON_INVALID_MESSAGE,
                    code=PYTHON_INVALID,
                )

            # Validate use_system_site_packages.
            if 'use_system_site_packages' in raw_python:
                with self.catch_validation_error('python.use_system_site_packages'):
                    python['use_system_site_packages'] = validate_bool(
                        raw_python['use_system_site_packages'],
                    )

            # Validate pip_install.
            if 'pip_install' in raw_python:
                with self.catch_validation_error('python.pip_install'):
                    python['install_with_pip'] = validate_bool(
                        raw_python['pip_install'],
                    )

            # Validate extra_requirements.
            if 'extra_requirements' in raw_python:
                raw_extra_requirements = raw_python['extra_requirements']
                if not isinstance(raw_extra_requirements, list):
                    self.error(
                        'python.extra_requirements',
                        self.PYTHON_EXTRA_REQUIREMENTS_INVALID_MESSAGE,
                        code=PYTHON_INVALID,
                    )
                if not python['install_with_pip']:
                    python['extra_requirements'] = []
                else:
                    for extra_name in raw_extra_requirements:
                        with self.catch_validation_error('python.extra_requirements'):
                            python['extra_requirements'].append(
                                validate_string(extra_name),
                            )

            # Validate setup_py_install.
            if 'setup_py_install' in raw_python:
                with self.catch_validation_error('python.setup_py_install'):
                    python['install_with_setup'] = validate_bool(
                        raw_python['setup_py_install'],
                    )

            if 'version' in raw_python:
                with self.catch_validation_error('python.version'):
                    version = str(raw_python['version'])
                    python['version'] = validate_choice(
                        version,
                        self.get_valid_python_versions(),
                    )

        return python

    def validate_conda(self):
        """Validates the ``conda`` key."""
        conda = {}

        if 'conda' in self._raw_config:
            raw_conda = self._raw_config['conda']
            with self.catch_validation_error('conda'):
                validate_dict(raw_conda)
            with self.catch_validation_error('conda.file'):
                if 'file' not in raw_conda:
                    raise ValidationError('file', VALUE_NOT_FOUND)
                conda_environment = validate_path(
                    raw_conda['file'],
                    self.base_path,
                )
                conda['environment'] = conda_environment
            return conda
        return None

    def validate_requirements_file(self):
        """Validates that the requirements file exists."""
        if 'requirements_file' not in self._raw_config:
            requirements_file = self.defaults.get('requirements_file')
        else:
            requirements_file = self._raw_config['requirements_file']
        if not requirements_file:
            return None
        with self.catch_validation_error('requirements_file'):
            requirements_file = validate_path(
                requirements_file,
                self.base_path,
            )
        return requirements_file

    def validate_formats(self):
        """Validates that formats contains only valid formats."""
        formats = self._raw_config.get('formats')
        if formats is None:
            return self.defaults.get('formats', [])
        if formats == ['none']:
            return []

        with self.catch_validation_error('format'):
            validate_list(formats)
            for format_ in formats:
                validate_choice(format_, self.get_valid_formats())

        return formats

    @property
    def formats(self):
        """The documentation formats to be built."""
        return self._config['formats']

    @property
    def python(self):
        """Python related configuration."""
        python = self._config['python']
        requirements = self._config['requirements_file']
        python_install = []

        # Always append a `PythonInstallRequirements` option.
        # If requirements is None, rtd will try to find a requirements file.
        python_install.append(
            PythonInstallRequirements(
                requirements=requirements,
            ),
        )
        if python['install_with_pip']:
            python_install.append(
                PythonInstall(
                    path=self.base_path,
                    method=PIP,
                    extra_requirements=python['extra_requirements'],
                ),
            )
        elif python['install_with_setup']:
            python_install.append(
                PythonInstall(
                    path=self.base_path,
                    method=SETUPTOOLS,
                    extra_requirements=[],
                ),
            )

        return Python(
            version=python['version'],
            install=python_install,
            use_system_site_packages=python['use_system_site_packages'],
        )

    @property
    def conda(self):
        if self._config['conda'] is not None:
            return Conda(**self._config['conda'])
        return None

    @property
    @lru_cache(maxsize=1)
    def build(self):
        """The docker image used by the builders."""
        return Build(**self._config['build'])

    @property
    def doctype(self):
        return self.defaults['doctype']

    @property
    def sphinx(self):
        config_file = self.defaults['sphinx_configuration']
        if config_file is not None:
            config_file = os.path.join(self.base_path, config_file)
        return Sphinx(
            builder=self.doctype,
            configuration=config_file,
            fail_on_warning=False,
        )

    @property
    def mkdocs(self):
        return Mkdocs(
            configuration=None,
            fail_on_warning=False,
        )

    @property
    def submodules(self):
        return Submodules(
            include=ALL,
            exclude=[],
            recursive=True,
        )

    @property
    def search(self):
        return Search(ranking={}, ignore=[])


class BuildConfigV2(BuildConfigBase):

    """Version 2 of the configuration file."""

    version = '2'
    valid_formats = ['htmlzip', 'pdf', 'epub']
    valid_install_method = [PIP, SETUPTOOLS]
    valid_sphinx_builders = {
        'html': 'sphinx',
        'htmldir': 'sphinx_htmldir',
        'dirhtml': 'sphinx_htmldir',
        'singlehtml': 'sphinx_singlehtml',
    }

    @property
    def settings(self):
        return settings.RTD_DOCKER_BUILD_SETTINGS

    def validate(self):
        """
        Validates and process ``raw_config`` and ``env_config``.

        Sphinx is the default doc type to be built. We don't merge some values
        from the database (like formats or python.version) to allow us set
        default values.
        """
        self._config['formats'] = self.validate_formats()
        self._config['conda'] = self.validate_conda()
        # This should be called before validate_python
        self._config['build'] = self.validate_build()
        self._config['python'] = self.validate_python()
        # Call this before validate sphinx and mkdocs
        self.validate_doc_types()
        self._config['mkdocs'] = self.validate_mkdocs()
        self._config['sphinx'] = self.validate_sphinx()
        self._config['submodules'] = self.validate_submodules()
        self._config['search'] = self.validate_search()
        self.validate_keys()

    def validate_formats(self):
        """
        Validates that formats contains only valid formats.

        The ``ALL`` keyword can be used to indicate that all formats are used.
        We ignore the default values here.
        """
        formats = self.pop_config('formats', [])
        if formats == ALL:
            return self.valid_formats
        with self.catch_validation_error('formats'):
            validate_list(formats)
            for format_ in formats:
                validate_choice(format_, self.valid_formats)
        return formats

    def validate_conda(self):
        """Validates the conda key."""
        raw_conda = self._raw_config.get('conda')
        if raw_conda is None:
            return None

        with self.catch_validation_error('conda'):
            validate_dict(raw_conda)

        conda = {}
        with self.catch_validation_error('conda.environment'):
            environment = self.pop_config('conda.environment', raise_ex=True)
            conda['environment'] = validate_path(environment, self.base_path)
        return conda

    def validate_build_config_with_os(self):
        """
        Validates the build object (new format).

        At least one element must be provided in ``build.tools``.
        """
        build = {}
        with self.catch_validation_error('build.os'):
            build_os = self.pop_config('build.os', raise_ex=True)
            build['os'] = validate_choice(build_os, self.settings['os'].keys())

        tools = {}
        with self.catch_validation_error('build.tools'):
            tools = self.pop_config('build.tools')
            if tools:
                validate_dict(tools)
                for tool in tools.keys():
                    validate_choice(tool, self.settings["tools"].keys())

        jobs = {}
        with self.catch_validation_error("build.jobs"):
            # FIXME: should we use `default={}` or kept the `None` here and
            # shortcircuit the rest of the logic?
            jobs = self.pop_config("build.jobs", default={})
            validate_dict(jobs)
            # NOTE: besides validating that each key is one of the expected
            # ones, we could validate the value of each of them is a list of
            # commands. However, I don't think we should validate the "command"
            # looks like a real command.
            for job in jobs.keys():
                validate_choice(
                    job,
                    BuildJobs.__slots__,
                )

        commands = []
        with self.catch_validation_error("build.commands"):
            commands = self.pop_config("build.commands", default=[])
            validate_list(commands)

        if not (tools or commands):
            self.error(
                key="build.tools",
                message=(
                    "At least one item should be provided in 'tools' or 'commands'"
                ),
                code=CONFIG_REQUIRED,
            )

        if commands and jobs:
            self.error(
                key="build.commands",
                message="The keys build.jobs and build.commands can't be used together.",
                code=INVALID_KEYS_COMBINATION,
            )

        build["jobs"] = {}
        for job, job_commands in jobs.items():
            with self.catch_validation_error(f"build.jobs.{job}"):
                build["jobs"][job] = [
                    validate_string(job_command)
                    for job_command in validate_list(job_commands)
                ]

        build["commands"] = []
        for command in commands:
            with self.catch_validation_error("build.commands"):
                build["commands"].append(validate_string(command))

        build['tools'] = {}
        if tools:
            for tool, version in tools.items():
                with self.catch_validation_error(f"build.tools.{tool}"):
                    build["tools"][tool] = validate_choice(
                        version,
                        self.settings["tools"][tool].keys(),
                    )

        build['apt_packages'] = self.validate_apt_packages()
        return build

    def validate_old_build_config(self):
        """
        Validates the build object (old format).

        It prioritizes the value from the default image if exists.
        """
        build = {}
        with self.catch_validation_error('build.image'):
            image = self.pop_config('build.image', self.default_build_image)
            build['image'] = '{}:{}'.format(
                settings.DOCKER_DEFAULT_IMAGE,
                validate_choice(
                    image,
                    self.valid_build_images,
                ),
            )

            # Allow to override specific project
            config_image = self.defaults.get('build_image')
            if config_image:
                build['image'] = config_image

        build['apt_packages'] = self.validate_apt_packages()
        return build

    def validate_apt_packages(self):
        apt_packages = []
        with self.catch_validation_error('build.apt_packages'):
            raw_packages = self._raw_config.get('build', {}).get('apt_packages', [])
            validate_list(raw_packages)
            # Transform to a dict, so is easy to validate individual entries.
            self._raw_config.setdefault('build', {})['apt_packages'] = (
                list_to_dict(raw_packages)
            )

            apt_packages = [
                self.validate_apt_package(index)
                for index in range(len(raw_packages))
            ]
            if not raw_packages:
                self.pop_config('build.apt_packages')

        return apt_packages

    def validate_build(self):
        raw_build = self._raw_config.get('build', {})
        with self.catch_validation_error('build'):
            validate_dict(raw_build)
        if "os" in raw_build or "commands" in raw_build or "tools" in raw_build:
            return self.validate_build_config_with_os()
        return self.validate_old_build_config()

    def validate_apt_package(self, index):
        """
        Validate the package name to avoid injections of extra options.

        We validate that they aren't interpreted as an option or file.
        See https://manpages.ubuntu.com/manpages/xenial/man8/apt-get.8.html
        and https://www.debian.org/doc/manuals/debian-reference/ch02.en.html#_debian_package_file_names  # noqa
        for allowed chars in packages names.
        """
        key = f'build.apt_packages.{index}'
        package = self.pop_config(key)
        with self.catch_validation_error(key):
            validate_string(package)
            package = package.strip()
            invalid_starts = [
                # Don't allow extra options.
                '-',
                # Don't allow to install from a path.
                '/',
                '.',
            ]
            for start in invalid_starts:
                if package.startswith(start):
                    self.error(
                        key=key,
                        message=(
                            'Invalid package name. '
                            f'Package can\'t start with {start}.',
                        ),
                        code=INVALID_NAME,
                    )
            # List of valid chars in packages names.
            pattern = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9.+-]*$')
            if not pattern.match(package):
                self.error(
                    key=key,
                    message='Invalid package name.',
                    code=INVALID_NAME,
                )
        return package

    def validate_python(self):
        """
        Validates the python key.

        validate_build should be called before this, since it initialize the
        build.image attribute.

        Fall back to the defaults of:
        - ``requirements``
        - ``install`` (only for setup.py method)
        - ``system_packages``

        .. note::
           - ``version`` can be a string or number type.
           - ``extra_requirements`` needs to be used with ``install: 'pip'``.
           - If the new build config is used (``build.os``),
             ``python.version`` shouldn't exist.
        """
        raw_python = self._raw_config.get('python', {})
        with self.catch_validation_error('python'):
            validate_dict(raw_python)

        python = {}
        if not self.using_build_tools:
            with self.catch_validation_error('python.version'):
                version = self.pop_config('python.version', '3')
                if version == 3.1:
                    # Special case for ``python.version: 3.10``,
                    # yaml will transform this to the numeric value of `3.1`.
                    # Save some frustration to users.
                    version = '3.10'
                version = str(version)
                python['version'] = validate_choice(
                    version,
                    self.get_valid_python_versions(),
                )

        with self.catch_validation_error('python.install'):
            raw_install = self._raw_config.get('python', {}).get('install', [])
            validate_list(raw_install)
            if raw_install:
                # Transform to a dict, so it's easy to validate extra keys.
                self._raw_config.setdefault('python', {})['install'] = (
                    list_to_dict(raw_install)
                )
            else:
                self.pop_config('python.install')

        raw_install = self._raw_config.get('python', {}).get('install', [])
        python['install'] = [
            self.validate_python_install(index)
            for index in range(len(raw_install))
        ]

        with self.catch_validation_error('python.system_packages'):
            system_packages = self.pop_config(
                'python.system_packages',
                False,
            )
            python['use_system_site_packages'] = validate_bool(system_packages)

        return python

    def validate_python_install(self, index):
        """Validates the python.install.{index} key."""
        python_install = {}
        key = 'python.install.{}'.format(index)
        raw_install = self._raw_config['python']['install'][str(index)]
        with self.catch_validation_error(key):
            validate_dict(raw_install)

        if 'requirements' in raw_install:
            requirements_key = key + '.requirements'
            with self.catch_validation_error(requirements_key):
                requirements = validate_path(
                    self.pop_config(requirements_key),
                    self.base_path,
                )
                python_install['requirements'] = requirements
        elif 'path' in raw_install:
            path_key = key + '.path'
            with self.catch_validation_error(path_key):
                path = validate_path(
                    self.pop_config(path_key),
                    self.base_path,
                )
                python_install['path'] = path

            method_key = key + '.method'
            with self.catch_validation_error(method_key):
                method = validate_choice(
                    self.pop_config(method_key, PIP),
                    self.valid_install_method,
                )
                python_install['method'] = method

            extra_req_key = key + '.extra_requirements'
            with self.catch_validation_error(extra_req_key):
                extra_requirements = validate_list(
                    self.pop_config(extra_req_key, []),
                )
                if extra_requirements and python_install['method'] != PIP:
                    self.error(
                        extra_req_key,
                        'You need to install your project with pip '
                        'to use extra_requirements',
                        code=PYTHON_INVALID,
                    )
                python_install['extra_requirements'] = extra_requirements
        else:
            self.error(
                key,
                '"path" or "requirements" key is required',
                code=CONFIG_REQUIRED,
            )
        return python_install

    def get_valid_python_versions(self):
        """
        Get the valid python versions for the current docker image.

        This should be called after ``validate_build()``.
        """
        build_image = self.build.image
        return self.get_valid_python_versions_for_image(build_image)

    def validate_doc_types(self):
        """
        Validates that the user only have one type of documentation.

        This should be called before validating ``sphinx`` or ``mkdocs`` to
        avoid innecessary validations.
        """
        with self.catch_validation_error('.'):
            if 'sphinx' in self._raw_config and 'mkdocs' in self._raw_config:
                self.error(
                    '.',
                    'You can not have the ``sphinx`` and ``mkdocs`` '
                    'keys at the same time',
                    code=INVALID_KEYS_COMBINATION,
                )

    def validate_mkdocs(self):
        """
        Validates the mkdocs key.

        It makes sure we are using an existing configuration file.
        """
        raw_mkdocs = self._raw_config.get('mkdocs')
        if raw_mkdocs is None:
            return None

        with self.catch_validation_error('mkdocs'):
            validate_dict(raw_mkdocs)

        mkdocs = {}
        with self.catch_validation_error('mkdocs.configuration'):
            configuration = self.pop_config('mkdocs.configuration', None)
            if configuration is not None:
                configuration = validate_path(configuration, self.base_path)
            mkdocs['configuration'] = configuration

        with self.catch_validation_error('mkdocs.fail_on_warning'):
            fail_on_warning = self.pop_config('mkdocs.fail_on_warning', False)
            mkdocs['fail_on_warning'] = validate_bool(fail_on_warning)

        return mkdocs

    def validate_sphinx(self):
        """
        Validates the sphinx key.

        It makes sure we are using an existing configuration file.

        .. note::
           It should be called after ``validate_mkdocs``. That way
           we can default to sphinx if ``mkdocs`` is not given.
        """
        raw_sphinx = self._raw_config.get('sphinx')
        if raw_sphinx is None:
            if self.mkdocs is None:
                raw_sphinx = {}
            else:
                return None

        with self.catch_validation_error('sphinx'):
            validate_dict(raw_sphinx)

        sphinx = {}
        with self.catch_validation_error('sphinx.builder'):
            builder = validate_choice(
                self.pop_config('sphinx.builder', 'html'),
                self.valid_sphinx_builders.keys(),
            )
            sphinx['builder'] = self.valid_sphinx_builders[builder]

        with self.catch_validation_error('sphinx.configuration'):
            configuration = self.defaults.get('sphinx_configuration')
            # The default value can be empty
            if not configuration:
                configuration = None
            configuration = self.pop_config(
                'sphinx.configuration',
                configuration,
            )
            if configuration is not None:
                configuration = validate_path(configuration, self.base_path)
            sphinx['configuration'] = configuration

        with self.catch_validation_error('sphinx.fail_on_warning'):
            fail_on_warning = self.pop_config('sphinx.fail_on_warning', False)
            sphinx['fail_on_warning'] = validate_bool(fail_on_warning)

        return sphinx

    def validate_submodules(self):
        """
        Validates the submodules key.

        - We can use the ``ALL`` keyword in include or exclude.
        - We can't exclude and include submodules at the same time.
        """
        raw_submodules = self._raw_config.get('submodules', {})
        with self.catch_validation_error('submodules'):
            validate_dict(raw_submodules)

        submodules = {}
        with self.catch_validation_error('submodules.include'):
            include = self.pop_config('submodules.include', [])
            if include != ALL:
                include = [
                    validate_string(submodule)
                    for submodule in validate_list(include)
                ]
            submodules['include'] = include

        with self.catch_validation_error('submodules.exclude'):
            default = [] if submodules['include'] else ALL
            exclude = self.pop_config('submodules.exclude', default)
            if exclude != ALL:
                exclude = [
                    validate_string(submodule)
                    for submodule in validate_list(exclude)
                ]
            submodules['exclude'] = exclude

        with self.catch_validation_error('submodules'):
            is_including = bool(submodules['include'])
            is_excluding = (
                submodules['exclude'] == ALL or bool(submodules['exclude'])
            )
            if is_including and is_excluding:
                self.error(
                    'submodules',
                    'You can not exclude and include submodules '
                    'at the same time',
                    code=SUBMODULES_INVALID,
                )

        with self.catch_validation_error('submodules.recursive'):
            recursive = self.pop_config('submodules.recursive', False)
            submodules['recursive'] = validate_bool(recursive)

        return submodules

    def validate_search(self):
        """
        Validates the search key.

        - ``ranking`` is a map of path patterns to a rank.
        - ``ignore`` is a list of patterns.
        - The path pattern supports basic globs (*, ?, [seq]).
        - The rank can be a integer number between -10 and 10.
        """
        raw_search = self._raw_config.get('search', {})
        with self.catch_validation_error('search'):
            validate_dict(raw_search)

        search = {}
        with self.catch_validation_error('search.ranking'):
            ranking = self.pop_config('search.ranking', {})
            validate_dict(ranking)

            valid_rank_range = list(range(-10, 10 + 1))

            final_ranking = {}
            for pattern, rank in ranking.items():
                pattern = validate_path_pattern(pattern)
                validate_choice(rank, valid_rank_range)
                final_ranking[pattern] = rank

            search['ranking'] = final_ranking

        with self.catch_validation_error('search.ignore'):
            ignore_default = [
                'search.html',
                'search/index.html',
                '404.html',
                '404/index.html',
            ]
            search_ignore = self.pop_config('search.ignore', ignore_default)
            validate_list(search_ignore)

            final_ignore = [
                validate_path_pattern(pattern)
                for pattern in search_ignore
            ]
            search['ignore'] = final_ignore

        return search

    def validate_keys(self):
        """
        Checks that we don't have extra keys (invalid ones).

        This should be called after all the validations are done and all keys
        are popped from `self._raw_config`.
        """
        msg = (
            'Invalid configuration option: {}. '
            'Make sure the key name is correct.'
        )
        # The version key isn't popped, but it's
        # validated in `load`.
        self.pop_config('version', None)
        wrong_key = '.'.join(self._get_extra_key(self._raw_config))
        if wrong_key:
            self.error(
                wrong_key,
                msg.format(wrong_key),
                code=INVALID_KEY,
            )

    def _get_extra_key(self, value):
        """
        Get the extra keyname (list form) of a dict object.

        If there is more than one extra key, the first one is returned.

        Example::

        {
            'key': {
                'name':  'inner',
            }
        }

        Will return `['key', 'name']`.
        """
        if isinstance(value, dict) and value:
            key_name = next(iter(value))
            return [key_name] + self._get_extra_key(value[key_name])
        return []

    @property
    def formats(self):
        return self._config['formats']

    @property
    def conda(self):
        if self._config['conda']:
            return Conda(**self._config['conda'])
        return None

    @property
    @lru_cache(maxsize=1)
    def build(self):
        build = self._config['build']
        if 'os' in build:
            tools = {
                tool: BuildTool(
                    version=version,
                    full_version=self.settings['tools'][tool][version],
                )
                for tool, version in build['tools'].items()
            }
            return BuildWithOs(
                os=build['os'],
                tools=tools,
                jobs=BuildJobs(**build["jobs"]),
                commands=build["commands"],
                apt_packages=build["apt_packages"],
            )
        return Build(**build)

    @property
    def python(self):
        python_install = []
        python = self._config['python']
        for install in python['install']:
            if 'requirements' in install:
                python_install.append(PythonInstallRequirements(**install),)
            elif 'path' in install:
                python_install.append(PythonInstall(**install),)
        return Python(
            version=python.get('version'),
            install=python_install,
            use_system_site_packages=python['use_system_site_packages'],
        )

    @property
    def sphinx(self):
        if self._config['sphinx']:
            return Sphinx(**self._config['sphinx'])
        return None

    @property
    def mkdocs(self):
        if self._config['mkdocs']:
            return Mkdocs(**self._config['mkdocs'])
        return None

    @property
    def doctype(self):
        if "commands" in self._config["build"] and self._config["build"]["commands"]:
            return GENERIC

        if self.mkdocs:
            return 'mkdocs'
        return self.sphinx.builder

    @property
    def submodules(self):
        return Submodules(**self._config['submodules'])

    @property
    def search(self):
        return Search(**self._config['search'])


def load(path, env_config, readthedocs_yaml_path=None):
    """
    Load a project configuration and the top-most build config for a given path.

    That is usually the root of the project, but will look deeper. According to
    the version of the configuration a build object would be load and validated,
    ``BuildConfigV1`` is the default.
    """
    # Custom non-default config file location
    if readthedocs_yaml_path:
        filename = os.path.join(path, readthedocs_yaml_path)
        # When a config file is specified and not found, we raise ConfigError
        # because ConfigFileNotFound
        if not os.path.exists(filename):
            raise ConfigFileNotFound(os.path.relpath(filename, path))
    # Default behavior
    else:
        filename = find_one(path, CONFIG_FILENAME_REGEX)
        if not filename:
            # This exception is current caught higher up and will result in an attempt
            # to load the v1 config schema.
            raise DefaultConfigFileNotFound(path)

    # Allow symlinks, but only the ones that resolve inside the base directory.
    with safe_open(
        filename, "r", allow_symlinks=True, base_path=path
    ) as configuration_file:
        try:
            config = parse(configuration_file.read())
        except ParseError as error:
            raise ConfigError(
                'Parse error in {filename}: {message}'.format(
                    filename=os.path.relpath(filename, path),
                    message=str(error),
                ),
                code=CONFIG_SYNTAX_INVALID,
            )
        version = config.get('version', 1)
        build_config = get_configuration_class(version)(
            env_config,
            config,
            source_file=filename,
        )

    build_config.validate()
    return build_config


def get_configuration_class(version):
    """
    Get the appropriate config class for ``version``.

    :type version: str or int
    """
    configurations_class = {
        1: BuildConfigV1,
        2: BuildConfigV2,
    }
    try:
        version = int(version)
        return configurations_class[version]
    except (KeyError, ValueError):
        raise InvalidConfig(
            'version',
            code=VERSION_INVALID,
            error_message='Invalid version of the configuration file',
        )
