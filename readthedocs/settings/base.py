# pylint: disable=missing-docstring

import os
import re
import subprocess
import socket

import structlog

from celery.schedules import crontab

from readthedocs.core.logs import shared_processors
from corsheaders.defaults import default_headers
from readthedocs.core.settings import Settings


try:
    import readthedocsext  # noqa
    ext = True
except ImportError:
    ext = False

try:
    import readthedocsext.theme  # noqa
    ext_theme = True
except ImportError:
    ext_theme = False


_ = gettext = lambda s: s
log = structlog.get_logger(__name__)


class CommunityBaseSettings(Settings):

    """Community base settings, don't use this directly."""

    # Django settings
    SITE_ID = 1
    ROOT_URLCONF = 'readthedocs.urls'
    LOGIN_REDIRECT_URL = '/dashboard/'
    FORCE_WWW = False
    SECRET_KEY = 'replace-this-please'  # noqa
    ATOMIC_REQUESTS = True

    DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

    # Debug settings
    DEBUG = True
    RTD_FORCE_SHOW_DEBUG_TOOLBAR = False

    @property
    def DEBUG_TOOLBAR_CONFIG(self):
        def _show_debug_toolbar(request):
            return request.environ.get('SERVER_NAME', None) != 'testserver' and self.SHOW_DEBUG_TOOLBAR

        return {
            'SHOW_TOOLBAR_CALLBACK': _show_debug_toolbar,
        }

    @property
    def SHOW_DEBUG_TOOLBAR(self):
        """
        Show django-debug-toolbar on DEBUG or if it's forced by RTD_FORCE_SHOW_DEBUG_TOOLBAR.

        This will show the debug toolbar on:

          - Docker local instance
          - web-extra production instance
        """
        return self.DEBUG or self.RTD_FORCE_SHOW_DEBUG_TOOLBAR

    # Domains and URLs
    RTD_IS_PRODUCTION = False
    PRODUCTION_DOMAIN = 'readthedocs.org'
    PUBLIC_DOMAIN = None
    PUBLIC_DOMAIN_USES_HTTPS = False
    USE_SUBDOMAIN = False
    PUBLIC_API_URL = 'https://{}'.format(PRODUCTION_DOMAIN)
    RTD_INTERSPHINX_URL = 'https://{}'.format(PRODUCTION_DOMAIN)
    RTD_EXTERNAL_VERSION_DOMAIN = 'external-builds.readthedocs.io'

    # Doc Builder Backends
    MKDOCS_BACKEND = 'readthedocs.doc_builder.backends.mkdocs'
    SPHINX_BACKEND = 'readthedocs.doc_builder.backends.sphinx'

    # slumber settings
    SLUMBER_API_HOST = 'https://readthedocs.org'
    SLUMBER_USERNAME = None
    SLUMBER_PASSWORD = None

    # Email
    DEFAULT_FROM_EMAIL = 'no-reply@readthedocs.org'
    SERVER_EMAIL = DEFAULT_FROM_EMAIL
    SUPPORT_EMAIL = None
    SUPPORT_FORM_ENDPOINT = None

    # Sessions
    SESSION_COOKIE_DOMAIN = 'readthedocs.org'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_AGE = 30 * 24 * 60 * 60  # 30 days
    SESSION_SAVE_EVERY_REQUEST = False

    @property
    def SESSION_COOKIE_SAMESITE(self):
        """
        Cookie used in cross-origin API requests from *.rtd.io to rtd.org/api/v2/sustainability/.
        """
        if self.USE_PROMOS:
            return None
        # This is django's default.
        return 'Lax'

    # CSRF
    CSRF_COOKIE_HTTPONLY = True
    CSRF_COOKIE_AGE = 30 * 24 * 60 * 60  # 30 days

    # Security & X-Frame-Options Middleware
    # https://docs.djangoproject.com/en/1.11/ref/middleware/#django.middleware.security.SecurityMiddleware
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    X_FRAME_OPTIONS = 'DENY'

    # Content Security Policy
    # https://django-csp.readthedocs.io/
    CSP_BLOCK_ALL_MIXED_CONTENT = True
    CSP_DEFAULT_SRC = None  # This could be improved
    CSP_FRAME_ANCESTORS = ("'none'",)
    CSP_OBJECT_SRC = ("'none'",)
    CSP_REPORT_URI = None
    CSP_REPORT_ONLY = False
    CSP_EXCLUDE_URL_PREFIXES = (
        "/admin/",
    )

    # Read the Docs
    READ_THE_DOCS_EXTENSIONS = ext
    RTD_LATEST = 'latest'
    RTD_LATEST_VERBOSE_NAME = 'latest'
    RTD_STABLE = 'stable'
    RTD_STABLE_VERBOSE_NAME = 'stable'
    RTD_CLEAN_AFTER_BUILD = False
    RTD_MAX_CONCURRENT_BUILDS = 4
    RTD_BUILDS_MAX_RETRIES = 25
    RTD_BUILDS_RETRY_DELAY = 5 * 60  # seconds
    RTD_BUILD_STATUS_API_NAME = 'docs/readthedocs'
    RTD_ANALYTICS_DEFAULT_RETENTION_DAYS = 30 * 3
    RTD_AUDITLOGS_DEFAULT_RETENTION_DAYS = 30 * 3

    # Number of days the validation process for a domain will be retried.
    RTD_CUSTOM_DOMAINS_VALIDATION_PERIOD = 30

    # Keep BuildData models on database during this time
    RTD_TELEMETRY_DATA_RETENTION_DAYS = 30 * 6  # 180 days / 6 months

    # Number of days an invitation is valid.
    RTD_INVITATIONS_EXPIRATION_DAYS = 15

    @property
    def RTD_DEFAULT_FEATURES(self):
        # Features listed here will be available to users that don't have a
        # subscription or if their subscription doesn't include the feature.
        # Depending on the feature type, the numeric value represents a
        # number of days or limit of the feature.
        from readthedocs.subscriptions import constants
        return {
            constants.TYPE_CNAME: 1,
            constants.TYPE_EMBED_API: 1,
            # Retention days for search analytics.
            constants.TYPE_SEARCH_ANALYTICS: self.RTD_ANALYTICS_DEFAULT_RETENTION_DAYS,
            # Retention days for page view analytics.
            constants.TYPE_PAGEVIEW_ANALYTICS: self.RTD_ANALYTICS_DEFAULT_RETENTION_DAYS,
            # Retention days for audit logs.
            constants.TYPE_AUDIT_LOGS: self.RTD_AUDITLOGS_DEFAULT_RETENTION_DAYS,
            # Max number of concurrent builds.
            constants.TYPE_CONCURRENT_BUILDS: self.RTD_MAX_CONCURRENT_BUILDS,
        }

    # Database and API hitting settings
    DONT_HIT_DB = True
    RTD_SAVE_BUILD_COMMANDS_TO_STORAGE = False
    DATABASE_ROUTERS = ['readthedocs.core.db.MapAppsRouter']

    USER_MATURITY_DAYS = 7

    # override classes
    CLASS_OVERRIDES = {}

    DOC_PATH_PREFIX = '_/'

    @property
    def RTD_EXT_THEME_ENABLED(self):
        return ext_theme and 'RTD_EXT_THEME_ENABLED' in os.environ

    RTD_EXT_THEME_DEV_SERVER = None

    # Application classes
    @property
    def INSTALLED_APPS(self):  # noqa
        apps = [
            'django.contrib.auth',
            'django.contrib.admin',
            'django.contrib.contenttypes',
            'django.contrib.sessions',
            'django.contrib.sites',
            'django.contrib.messages',
            'django.contrib.humanize',

            # readthedocs.core app needs to be before
            # django.contrib.staticfiles to use our custom collectstatic
            # command
            'readthedocs.core',
            'django.contrib.staticfiles',

            # third party apps
            'dj_pagination',
            'taggit',
            'django_gravatar',
            'rest_framework',
            'rest_framework.authtoken',
            'corsheaders',
            'annoying',
            'django_extensions',
            'crispy_forms',
            'messages_extends',
            'django_elasticsearch_dsl',
            'django_filters',
            'polymorphic',
            'simple_history',
            'djstripe',

            # our apps
            'readthedocs.projects',
            'readthedocs.organizations',
            'readthedocs.builds',
            'readthedocs.doc_builder',
            'readthedocs.oauth',
            'readthedocs.redirects',
            'readthedocs.sso',
            'readthedocs.audit',
            'readthedocs.rtd_tests',
            'readthedocs.api.v2',
            'readthedocs.api.v3',

            'readthedocs.gold',
            'readthedocs.payments',
            'readthedocs.subscriptions',
            'readthedocs.notifications',
            'readthedocs.integrations',
            'readthedocs.analytics',
            'readthedocs.sphinx_domains',
            'readthedocs.search',
            'readthedocs.embed',
            'readthedocs.telemetry',
            'readthedocs.domains',
            'readthedocs.invitations',

            # allauth
            'allauth',
            'allauth.account',
            'allauth.socialaccount',
            'allauth.socialaccount.providers.github',
            'allauth.socialaccount.providers.gitlab',
            'allauth.socialaccount.providers.bitbucket',
            'allauth.socialaccount.providers.bitbucket_oauth2',
            'cacheops',
        ]
        if ext:
            apps.append('readthedocsext.cdn')
            apps.append('readthedocsext.donate')
            apps.append('readthedocsext.spamfighting')
        if self.RTD_EXT_THEME_ENABLED:
            apps.append('readthedocsext.theme')
        if self.SHOW_DEBUG_TOOLBAR:
            apps.append('debug_toolbar')

        return apps

    @property
    def CRISPY_TEMPLATE_PACK(self):
        if self.RTD_EXT_THEME_ENABLED:
            return 'semantic-ui'
        return 'bootstrap'

    @property
    def CRISPY_ALLOWED_TEMPLATE_PACKS(self):
        if self.RTD_EXT_THEME_ENABLED:
            return ('semantic-ui',)
        return ("bootstrap", "uni_form", "bootstrap3", "bootstrap4")

    @property
    def USE_PROMOS(self):  # noqa
        return 'readthedocsext.donate' in self.INSTALLED_APPS

    @property
    def MIDDLEWARE(self):
        middlewares = [
            'readthedocs.core.middleware.NullCharactersMiddleware',
            'readthedocs.core.middleware.ReadTheDocsSessionMiddleware',
            'django.middleware.locale.LocaleMiddleware',
            'corsheaders.middleware.CorsMiddleware',
            'django.middleware.common.CommonMiddleware',
            'django.middleware.security.SecurityMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware',
            'django.middleware.clickjacking.XFrameOptionsMiddleware',
            'django.contrib.auth.middleware.AuthenticationMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware',
            'dj_pagination.middleware.PaginationMiddleware',
            'csp.middleware.CSPMiddleware',
            'readthedocs.core.middleware.ReferrerPolicyMiddleware',
            'simple_history.middleware.HistoryRequestMiddleware',
            'readthedocs.core.logs.ReadTheDocsRequestMiddleware',
            'django_structlog.middlewares.CeleryMiddleware',
        ]
        if self.SHOW_DEBUG_TOOLBAR:
            middlewares.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
        return middlewares



    AUTHENTICATION_BACKENDS = (
        # Needed to login by username in Django admin, regardless of `allauth`
        'django.contrib.auth.backends.ModelBackend',
        # `allauth` specific authentication methods, such as login by e-mail
        'allauth.account.auth_backends.AuthenticationBackend',
    )

    AUTH_PASSWORD_VALIDATORS = [
        {
            'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
        },
        {
            'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
            'OPTIONS': {
                'min_length': 9,
            }
        },
        {
            'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
        },
        {
            'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
        },
    ]

    MESSAGE_STORAGE = 'readthedocs.notifications.storages.FallbackUniqueStorage'

    NOTIFICATION_BACKENDS = [
        'readthedocs.notifications.backends.EmailBackend',
        'readthedocs.notifications.backends.SiteBackend',
    ]

    # Paths
    SITE_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    TEMPLATE_ROOT = os.path.join(SITE_ROOT, 'readthedocs', 'templates')
    DOCROOT = os.path.join(SITE_ROOT, 'user_builds')
    LOGS_ROOT = os.path.join(SITE_ROOT, 'logs')
    PRODUCTION_ROOT = os.path.join(SITE_ROOT, 'prod_artifacts')
    PRODUCTION_MEDIA_ARTIFACTS = os.path.join(PRODUCTION_ROOT, 'media')

    # Assets and media
    STATIC_ROOT = os.path.join(SITE_ROOT, 'static')
    STATIC_URL = '/static/'
    MEDIA_ROOT = os.path.join(SITE_ROOT, 'media/')
    MEDIA_URL = '/media/'
    ADMIN_MEDIA_PREFIX = '/media/admin/'
    STATICFILES_DIRS = [
        os.path.join(SITE_ROOT, 'readthedocs', 'static'),
        os.path.join(SITE_ROOT, 'media'),
    ]
    STATICFILES_FINDERS = [
        'readthedocs.core.static.SelectiveFileSystemFinder',
        'django.contrib.staticfiles.finders.AppDirectoriesFinder',
        'readthedocs.core.finders.DebugToolbarFinder',
    ]
    PYTHON_MEDIA = False

    # Django Storage subclass used to write build artifacts to cloud or local storage
    # https://docs.readthedocs.io/page/development/settings.html#rtd-build-media-storage
    RTD_BUILD_MEDIA_STORAGE = 'readthedocs.builds.storage.BuildMediaFileSystemStorage'
    RTD_BUILD_ENVIRONMENT_STORAGE = 'readthedocs.builds.storage.BuildMediaFileSystemStorage'
    RTD_BUILD_TOOLS_STORAGE = 'readthedocs.builds.storage.BuildMediaFileSystemStorage'
    RTD_BUILD_COMMANDS_STORAGE = 'readthedocs.builds.storage.BuildMediaFileSystemStorage'
    RTD_STATICFILES_STORAGE = 'readthedocs.builds.storage.StaticFilesStorage'

    @property
    def TEMPLATES(self):
        dirs = [self.TEMPLATE_ROOT]
        if self.RTD_EXT_THEME_ENABLED:
            dirs.insert(0, os.path.join(
                os.path.dirname(readthedocsext.theme.__file__),
                'templates',
            ))
        return [
            {
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
                'DIRS': dirs,
                'APP_DIRS': True,
                'OPTIONS': {
                    'debug': self.DEBUG,
                    'context_processors': [
                        'django.contrib.auth.context_processors.auth',
                        'django.contrib.messages.context_processors.messages',
                        'django.template.context_processors.debug',
                        'django.template.context_processors.i18n',
                        'django.template.context_processors.media',
                        'django.template.context_processors.request',
                        # Read the Docs processor
                        'readthedocs.core.context_processors.readthedocs_processor',
                    ],
                },
            },
        ]

    # Cache
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'PREFIX': 'docs',
        }
    }
    CACHE_MIDDLEWARE_SECONDS = 60

    # I18n
    TIME_ZONE = 'UTC'
    USE_TZ = True
    LANGUAGE_CODE = 'en-us'
    LANGUAGES = (
        ('ca', gettext('Catalan')),
        ('en', gettext('English')),
        ('es', gettext('Spanish')),
        ('pt-br', gettext('Brazilian Portuguese')),
        ('nb', gettext('Norwegian Bokmål')),
        ('fr', gettext('French')),
        ('ru', gettext('Russian')),
        ('de', gettext('German')),
        ('gl', gettext('Galician')),
        ('vi', gettext('Vietnamese')),
        ('zh-cn', gettext('Simplified Chinese')),
        ('zh-tw', gettext('Traditional Chinese')),
        ('ja', gettext('Japanese')),
        ('uk', gettext('Ukrainian')),
        ('it', gettext('Italian')),
        ('ko', gettext('Korean')),
    )
    LOCALE_PATHS = [
        os.path.join(SITE_ROOT, 'readthedocs', 'locale'),
    ]
    USE_I18N = True
    USE_L10N = True

    # Celery
    CELERY_APP_NAME = 'readthedocs'
    CELERY_ALWAYS_EAGER = True
    CELERYD_TASK_TIME_LIMIT = 60 * 60  # 60 minutes
    CELERY_SEND_TASK_ERROR_EMAILS = False
    CELERY_IGNORE_RESULT = True
    CELERYD_HIJACK_ROOT_LOGGER = False
    # This stops us from pre-fetching a task that then sits around on the builder
    CELERY_ACKS_LATE = True
    # Don't queue a bunch of tasks in the workers
    CELERYD_PREFETCH_MULTIPLIER = 1
    CELERY_CREATE_MISSING_QUEUES = True

    CELERY_DEFAULT_QUEUE = 'celery'
    CELERYBEAT_SCHEDULE = {
        'quarter-finish-inactive-builds': {
            'task': 'readthedocs.projects.tasks.utils.finish_inactive_builds',
            'schedule': crontab(minute='*/15'),
            'options': {'queue': 'web'},
        },
        'every-three-hour-clear-persistent-messages': {
            'task': 'readthedocs.core.tasks.clear_persistent_messages',
            'schedule': crontab(minute=0, hour='*/3'),
            'options': {'queue': 'web'},
        },
        'every-day-delete-old-search-queries': {
            'task': 'readthedocs.search.tasks.delete_old_search_queries_from_db',
            'schedule': crontab(minute=0, hour=0),
            'options': {'queue': 'web'},
        },
        'every-day-delete-old-page-views': {
            'task': 'readthedocs.analytics.tasks.delete_old_page_counts',
            'schedule': crontab(minute=0, hour=1),
            'options': {'queue': 'web'},
        },
        'every-day-delete-old-buildata-models': {
            'task': 'readthedocs.telemetry.tasks.delete_old_build_data',
            'schedule': crontab(minute=0, hour=2),
            'options': {'queue': 'web'},
        },
        'weekly-delete-old-personal-audit-logs': {
            'task': 'readthedocs.audit.tasks.delete_old_personal_audit_logs',
            'schedule': crontab(day_of_week='wednesday', minute=0, hour=7),
            'options': {'queue': 'web'},
        },
        'every-day-resync-sso-organization-users': {
            'task': 'readthedocs.oauth.tasks.sync_remote_repositories_organizations',
            'schedule': crontab(minute=0, hour=4),
            'options': {'queue': 'web'},
        },
        'quarter-archive-builds': {
            'task': 'readthedocs.builds.tasks.archive_builds_task',
            'schedule': crontab(minute='*/15'),
            'options': {'queue': 'web'},
            'kwargs': {
                'days': 1,
                'limit': 500,
                'delete': True,
            },
        },
        'every-day-delete-inactive-external-versions': {
            'task': 'readthedocs.builds.tasks.delete_closed_external_versions',
            'schedule': crontab(minute=0, hour=1),
            'options': {'queue': 'web'},
        },
        'every-day-resync-remote-repositories': {
            'task': 'readthedocs.oauth.tasks.sync_active_users_remote_repositories',
            'schedule': crontab(minute=30, hour=2),
            'options': {'queue': 'web'},
        },
        'every-day-email-pending-custom-domains': {
            'task': 'readthedocs.domains.tasks.email_pending_custom_domains',
            'schedule': crontab(minute=0, hour=3),
            'options': {'queue': 'web'},
        },
        'every-15m-delete-pidbox-objects': {
            'task': 'readthedocs.core.tasks.cleanup_pidbox_keys',
            'schedule': crontab(minute='*/15'),
            'options': {'queue': 'web'},
        },
        'weekly-config-file-notification': {
            'task': 'readthedocs.projects.tasks.utils.deprecated_config_file_used_notification',
            'schedule': crontab(day_of_week='wednesday', hour=11, minute=15),
            'options': {'queue': 'web'},
        },
    }

    MULTIPLE_BUILD_SERVERS = [CELERY_DEFAULT_QUEUE]

    # Sentry
    SENTRY_CELERY_IGNORE_EXPECTED = True

    # Docker
    DOCKER_ENABLE = False
    DOCKER_SOCKET = 'unix:///var/run/docker.sock'
    # This settings has been deprecated in favor of DOCKER_IMAGE_SETTINGS
    DOCKER_BUILD_IMAGES = None

    # User used to create the container.
    # In production we use the same user than the one defined by the
    # ``USER docs`` instruction inside the Dockerfile.
    # In development, we can use the "UID:GID" of the current user running the
    # instance to avoid file permissions issues.
    # https://docs.docker.com/engine/reference/run/#user
    RTD_DOCKER_USER = 'docs:docs'
    RTD_DOCKER_SUPER_USER = 'root:root'
    RTD_DOCKER_WORKDIR = '/home/docs/'

    RTD_DOCKER_COMPOSE = False

    DOCKER_DEFAULT_IMAGE = 'readthedocs/build'
    DOCKER_VERSION = 'auto'
    DOCKER_DEFAULT_VERSION = 'latest'
    DOCKER_IMAGE = '{}:{}'.format(DOCKER_DEFAULT_IMAGE, DOCKER_DEFAULT_VERSION)
    DOCKER_IMAGE_SETTINGS = {
        # A large number of users still have this pinned in their config file.
        # We must have documented it at some point.
        'readthedocs/build:2.0': {
            'python': {
                'supported_versions': ['2', '2.7', '3', '3.5'],
                'default_version': {
                    '2': '2.7',
                    '3': '3.5',
                },
            },
        },
        'readthedocs/build:4.0': {
            'python': {
                'supported_versions': ['2', '2.7', '3', '3.5', '3.6', 3.7],
                'default_version': {
                    '2': '2.7',
                    '3': '3.7',
                },
            },
        },
        'readthedocs/build:5.0': {
            'python': {
                'supported_versions': ['2', '2.7', '3', '3.5', '3.6', '3.7', 'pypy3.5'],
                'default_version': {
                    '2': '2.7',
                    '3': '3.7',
                },
            },
        },
        'readthedocs/build:6.0': {
            'python': {
                'supported_versions': ['2', '2.7', '3', '3.5', '3.6', '3.7', '3.8', 'pypy3.5'],
                'default_version': {
                    '2': '2.7',
                    '3': '3.7',
                },
            },
        },
        'readthedocs/build:7.0': {
            'python': {
                'supported_versions': ['2', '2.7', '3', '3.5', '3.6', '3.7', '3.8', '3.9', '3.10', 'pypy3.5'],
                'default_version': {
                    '2': '2.7',
                    '3': '3.7',
                },
            },
        },
    }

    # Alias tagged via ``docker tag`` on the build servers
    DOCKER_IMAGE_SETTINGS.update({
        'readthedocs/build:stable': DOCKER_IMAGE_SETTINGS.get('readthedocs/build:5.0'),
        'readthedocs/build:latest': DOCKER_IMAGE_SETTINGS.get('readthedocs/build:6.0'),
        'readthedocs/build:testing': DOCKER_IMAGE_SETTINGS.get('readthedocs/build:7.0'),
    })
    # Additional binds for the build container
    RTD_DOCKER_ADDITIONAL_BINDS = {}

    # Adding a new tool/version to this setting requires:
    #
    # - a mapping between the expected version in the config file, to the full
    # version installed via asdf (found via ``asdf list all <tool>``)
    #
    # - running the script ``./scripts/compile_version_upload.sh`` in
    # development and production environments to compile and cache the new
    # tool/version
    #
    # Note that when updating this options, you should also update the file:
    # readthedocs/rtd_tests/fixtures/spec/v2/schema.json
    RTD_DOCKER_BUILD_SETTINGS = {
        # Mapping of build.os options to docker image.
        'os': {
            'ubuntu-20.04': f'{DOCKER_DEFAULT_IMAGE}:ubuntu-20.04',
            'ubuntu-22.04': f'{DOCKER_DEFAULT_IMAGE}:ubuntu-22.04',
        },
        # Mapping of build.tools options to specific versions.
        'tools': {
            'python': {
                '2.7': '2.7.18',
                '3.6': '3.6.15',
                '3.7': '3.7.15',
                '3.8': '3.8.15',
                '3.9': '3.9.15',
                '3.10': '3.10.8',
                '3.11': '3.11.0',
                'pypy3.7': 'pypy3.7-7.3.9',
                'pypy3.8': 'pypy3.8-7.3.9',
                'pypy3.9': 'pypy3.9-7.3.9',
                'miniconda3-4.7': 'miniconda3-4.7.12',
                'mambaforge-4.10': 'mambaforge-4.10.3-10',
            },
            'nodejs': {
                '14': '14.20.1',
                '16': '16.18.0',
                '18': '18.11.0',
                '19': '19.0.0',
            },
            'rust': {
                '1.55': '1.55.0',
                '1.61': '1.61.0',
                '1.64': '1.64.0',
            },
            'golang': {
                '1.17': '1.17.13',
                '1.18': '1.18.7',
                '1.19': '1.19.2',
            },
        },
    }
    # Always point to the latest stable release.
    RTD_DOCKER_BUILD_SETTINGS['tools']['python']['3'] = RTD_DOCKER_BUILD_SETTINGS['tools']['python']['3.11']

    def _get_docker_memory_limit(self):
        try:
            total_memory = int(subprocess.check_output(
                "free -m | awk '/^Mem:/{print $2}'",
                shell=True,
            ))
            return total_memory, round(total_memory - 1000, -2)
        except ValueError:
            # On systems without a `free` command it will return a string to
            # int and raise a ValueError
            log.exception('Failed to get memory size, using defaults Docker limits.')

    # Coefficient used to determine build time limit, as a percentage of total
    # memory. Historical values here were 0.225 to 0.3.
    DOCKER_TIME_LIMIT_COEFF = 0.25

    @property
    def DOCKER_LIMITS(self):
        """
        Set docker limits dynamically, if in production, based on system memory.

        We do this to avoid having separate build images. This assumes 1 build
        process per server, which will be allowed to consume all available
        memory.

        We subtract 750MiB for overhead of processes and base system, and set
        the build time as proportional to the memory limit.
        """
        # Our normal default
        limits = {
            'memory': '1g',
            'time': 600,
        }

        # Only run on our servers
        if self.RTD_IS_PRODUCTION:
            total_memory, memory_limit = self._get_docker_memory_limit()
            if memory_limit:
                limits = {
                    'memory': f'{memory_limit}m',
                    'time': max(
                        limits['time'],
                        round(total_memory * self.DOCKER_TIME_LIMIT_COEFF, -2),
                    )
                }
        log.info(
            'Using dynamic docker limits.',
            hostname=socket.gethostname(),
            memory=limits['memory'],
            time=limits['time'],
        )
        return limits

    # All auth
    ACCOUNT_ADAPTER = 'readthedocs.core.adapters.AccountAdapter'
    ACCOUNT_EMAIL_REQUIRED = True

    # Make email verification mandatory.
    # Users won't be able to login until they verify the email address.
    ACCOUNT_EMAIL_VERIFICATION = 'mandatory'

    ACCOUNT_AUTHENTICATION_METHOD = 'username_email'
    ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 7
    SOCIALACCOUNT_AUTO_SIGNUP = False
    SOCIALACCOUNT_STORE_TOKENS = True

    SOCIALACCOUNT_PROVIDERS = {
        'github': {
            "VERIFIED_EMAIL": True,
            'SCOPE': [
                'user:email',
                'read:org',
                'admin:repo_hook',
                'repo:status',
            ],
        },
        'gitlab': {
            "VERIFIED_EMAIL": True,
            'SCOPE': [
                'api',
                'read_user',
            ],
        },
        # Bitbucket scope/permissions are determined by the Oauth consumer setup on bitbucket.org
    }
    ACCOUNT_FORMS = {
        'signup': 'readthedocs.forms.SignupFormWithNewsletter',
    }

    # CORS
    # Don't allow sending cookies in cross-domain requests, this is so we can
    # relax our CORS headers for more views, but at the same time not opening
    # users to CSRF attacks. The sustainability API is the only view that requires
    # cookies to be send cross-site, we override that for that view only.
    CORS_ALLOW_CREDENTIALS = False

    # Allow cross-site requests from any origin,
    # all information from our allowed endpoits is public.
    #
    # NOTE: We don't use `CORS_ALLOW_ALL_ORIGINS=True`,
    # since that will set the `Access-Control-Allow-Origin` header to `*`,
    # we won't be able to pass credentials fo the sustainability API with that value.
    CORS_ALLOWED_ORIGIN_REGEXES = [re.compile(".+")]
    CORS_ALLOW_HEADERS = list(default_headers) + [
        'x-hoverxref-version',
    ]
    # Additional protection to allow only idempotent methods.
    CORS_ALLOW_METHODS = [
        'GET',
        'OPTIONS',
        'HEAD',
    ]

    # URLs to allow CORS to read from unauthed.
    CORS_URLS_REGEX = re.compile(
        r"""
        ^(
            /api/v2/footer_html
            |/api/v2/search
            |/api/v2/docsearch
            |/api/v2/embed
            |/api/v3/embed
            |/api/v2/sustainability
        )
        """,
        re.VERBOSE,
    )

    # RTD Settings
    ALLOW_PRIVATE_REPOS = False
    DEFAULT_PRIVACY_LEVEL = 'public'
    DEFAULT_VERSION_PRIVACY_LEVEL = 'public'
    ALLOW_ADMIN = True

    # Organization settings
    RTD_ALLOW_ORGANIZATIONS = False
    RTD_ORG_DEFAULT_STRIPE_SUBSCRIPTION_PRICE = 'trial-v2-monthly'
    RTD_ORG_TRIAL_PERIOD_DAYS = 30

    # Elasticsearch settings.
    ES_HOSTS = ['search:9200']
    ELASTICSEARCH_DSL = {
        'default': {
            'hosts': 'search:9200'
        },
    }
    # Chunk size for elasticsearch reindex celery tasks
    ES_TASK_CHUNK_SIZE = 500

    # Info from Honza about this:
    # The key to determine shard number is actually usually not the node count,
    # but the size of your data.
    # There are advantages to just having a single shard in an index since
    # you don't have to do the distribute/collect steps when executing a search.
    # If your data will allow it (not significantly larger than 40GB)
    # I would recommend going to a single shard and one replica meaning
    # any of the two nodes will be able to serve any search without talking to the other one.
    # Scaling to more searches will then just mean adding a third node
    # and a second replica resulting in immediate 50% bump in max search throughput.

    ES_INDEXES = {
        'project': {
            'name': 'project_index',
            'settings': {
                'number_of_shards': 1,
                'number_of_replicas': 1
            },
        },
        'page': {
            'name': 'page_index',
            'settings': {
                'number_of_shards': 1,
                'number_of_replicas': 1,
            },
        },
    }

    # ANALYZER = 'analysis': {
    #     'analyzer': {
    #         'default_icu': {
    #             'type': 'custom',
    #             'tokenizer': 'icu_tokenizer',
    #             'filter': ['word_delimiter', 'icu_folding', 'icu_normalizer'],
    #         }
    #     }
    # }

    # Disable auto refresh for increasing index performance
    ELASTICSEARCH_DSL_AUTO_REFRESH = False

    ALLOWED_HOSTS = ['*']

    ABSOLUTE_URL_OVERRIDES = {
        'auth.user': lambda o: '/profiles/{}/'.format(o.username)
    }

    INTERNAL_IPS = ('127.0.0.1',)

    # Taggit
    # https://django-taggit.readthedocs.io
    TAGGIT_TAGS_FROM_STRING = 'readthedocs.projects.tag_utils.rtd_parse_tags'

    # Stripe
    # Existing values we use
    STRIPE_SECRET = None
    STRIPE_PUBLISHABLE = None

    # DJStripe values -- **CHANGE THESE IN PRODUCTION**
    STRIPE_LIVE_SECRET_KEY = None
    STRIPE_TEST_SECRET_KEY = "sk_test_x" # A default so the `checks` don't fail
    DJSTRIPE_WEBHOOK_SECRET = None
    STRIPE_LIVE_MODE = False  # Change to True in production
    # This is less optimal than setting the webhook secret
    # However, the app won't start without the secret
    # with this setting set to the default
    DJSTRIPE_WEBHOOK_VALIDATION = "retrieve_event"

    # These values shouldn't need to change..
    DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
    DJSTRIPE_USE_NATIVE_JSONFIELD = True  # We recommend setting to True for new installations

    # Disable adding djstripe metadata to the Customer objects.
    # We are managing the subscriber relationship by ourselves,
    # since we have subscriptions attached to an organization or gold user
    # we can't make use of the DJSTRIPE_SUBSCRIBER_MODEL setting.
    DJSTRIPE_SUBSCRIBER_CUSTOMER_KEY = None

    # Do Not Track support
    DO_NOT_TRACK_ENABLED = False

    # Advertising configuration defaults
    ADSERVER_API_BASE = None
    ADSERVER_API_KEY = None
    ADSERVER_API_TIMEOUT = 0.35  # seconds

    # Misc application settings
    GLOBAL_ANALYTICS_CODE = None
    DASHBOARD_ANALYTICS_CODE = None  # For the dashboard, not docs
    GRAVATAR_DEFAULT_IMAGE = 'https://assets.readthedocs.org/static/images/silhouette.png'  # NOQA
    OAUTH_AVATAR_USER_DEFAULT_URL = GRAVATAR_DEFAULT_IMAGE
    OAUTH_AVATAR_ORG_DEFAULT_URL = GRAVATAR_DEFAULT_IMAGE
    RESTRUCTUREDTEXT_FILTER_SETTINGS = {
        'cloak_email_addresses': True,
        'file_insertion_enabled': False,
        'raw_enabled': False,
        'strip_comments': True,
        'doctitle_xform': True,
        'sectsubtitle_xform': True,
        'initial_header_level': 2,
        'report_level': 5,
        'syntax_highlight': 'none',
        'math_output': 'latex',
        'field_name_limit': 50,
    }
    REST_FRAMEWORK = {
        'DEFAULT_FILTER_BACKENDS': ('django_filters.rest_framework.DjangoFilterBackend',),
        'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',  # NOQA
        'DEFAULT_THROTTLE_RATES': {
            'anon': '5/minute',
            'user': '60/minute',
        },
        'PAGE_SIZE': 10,
        'TEST_REQUEST_DEFAULT_FORMAT': 'json',
    }

    SILENCED_SYSTEM_CHECKS = ['fields.W342']

    # Logging
    LOG_FORMAT = '%(name)s:%(lineno)s[%(process)d]: %(levelname)s %(message)s'
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': True,
        'formatters': {
            'default': {
                'format': LOG_FORMAT,
                'datefmt': '%d/%b/%Y %H:%M:%S',
            },
            # structlog
            "plain_console": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.dev.ConsoleRenderer(colors=False),
                ],
                # Allows to add extra data to log entries generated via ``logging`` module
                # See https://www.structlog.org/en/stable/standard-library.html#rendering-using-structlog-based-formatters-within-logging
                "foreign_pre_chain": shared_processors,
            },
            "colored_console": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.dev.ConsoleRenderer(colors=True),
                ],
                # Allows to add extra data to log entries generated via ``logging`` module
                # See https://www.structlog.org/en/stable/standard-library.html#rendering-using-structlog-based-formatters-within-logging
                "foreign_pre_chain": shared_processors,
            },
            "key_value": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.processors.TimeStamper(fmt='iso'),
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.KeyValueRenderer(key_order=['timestamp', 'level', 'event', 'logger']),
                ],
                # Allows to add extra data to log entries generated via ``logging`` module
                # See https://www.structlog.org/en/stable/standard-library.html#rendering-using-structlog-based-formatters-within-logging
                "foreign_pre_chain": shared_processors,
            },
        },
        'handlers': {
            'console': {
                'level': 'INFO',
                'class': 'logging.StreamHandler',
                'formatter': 'plain_console',
            },
            'debug': {
                'level': 'DEBUG',
                'class': 'logging.handlers.RotatingFileHandler',
                'filename': os.path.join(LOGS_ROOT, 'debug.log'),
                'formatter': 'key_value',
            },
            'null': {
                'class': 'logging.NullHandler',
            },
        },
        'loggers': {
            '': {  # root logger
                'handlers': ['debug', 'console'],
                # Always send from the root, handlers can filter levels
                'level': 'INFO',
            },
            'docker.utils.config': {
                'handlers': ['null'],
                # Don't double log at the root logger for these.
                'propagate': False,
            },
            'django_structlog.middlewares.request': {
                'handlers': ['null'],
                # Don't double log at the root logger for these.
                'propagate': False,
            },
            'readthedocs': {
                'handlers': ['debug', 'console'],
                'level': 'DEBUG',
                # Don't double log at the root logger for these.
                'propagate': False,
            },
            'django.security.DisallowedHost': {
                'handlers': ['null'],
                'propagate': False,
            },
        },
    }

    # MailerLite API for newsletter signups
    MAILERLITE_API_SUBSCRIBERS_URL = 'https://api.mailerlite.com/api/v2/subscribers'
    MAILERLITE_API_ONBOARDING_GROUP_ID = None
    MAILERLITE_API_ONBOARDING_GROUP_URL = None
    MAILERLITE_API_KEY = None

    RTD_EMBED_API_EXTERNAL_DOMAINS = [
        r'docs\.python\.org',
        r'docs\.scipy\.org',
        r'docs\.sympy\.org',
        r'numpy\.org',
    ]
    RTD_EMBED_API_PAGE_CACHE_TIMEOUT = 5 * 10
    RTD_EMBED_API_DEFAULT_REQUEST_TIMEOUT = 1
    RTD_EMBED_API_DOMAIN_RATE_LIMIT = 50
    RTD_EMBED_API_DOMAIN_RATE_LIMIT_TIMEOUT = 60

    RTD_SPAM_THRESHOLD_DONT_SHOW_ADS = 100
    RTD_SPAM_THRESHOLD_DENY_ON_ROBOTS = 200
    RTD_SPAM_THRESHOLD_DONT_SHOW_DASHBOARD = 300
    RTD_SPAM_THRESHOLD_DONT_SERVE_DOCS = 500
    RTD_SPAM_THRESHOLD_DELETE_PROJECT = 1000
    RTD_SPAM_MAX_SCORE = 9999

    CACHEOPS_ENABLED = False
    CACHEOPS_TIMEOUT = 60 * 60  # seconds
    CACHEOPS_OPS = {'get', 'fetch'}
    CACHEOPS_DEGRADE_ON_FAILURE = True
    CACHEOPS = {
        # readthedocs.projects.*
        'projects.project': {
            'ops': CACHEOPS_OPS,
            'timeout': CACHEOPS_TIMEOUT,
        },
        'projects.feature': {
            'ops': CACHEOPS_OPS,
            'timeout': CACHEOPS_TIMEOUT,
        },
        'projects.projectrelationship': {
            'ops': CACHEOPS_OPS,
            'timeout': CACHEOPS_TIMEOUT,
        },
        'projects.domain': {
            'ops': CACHEOPS_OPS,
            'timeout': CACHEOPS_TIMEOUT,
        },

        # readthedocs.builds.*
        'builds.version': {
            'ops': CACHEOPS_OPS,
            'timeout': CACHEOPS_TIMEOUT,
        },

        # readthedocs.organizations.*
        'organizations.organization': {
            'ops': CACHEOPS_OPS,
            'timeout': CACHEOPS_TIMEOUT,
        },

        # readthedocs.subscriptions.*
        'subscriptions.planfeature': {
            'ops': CACHEOPS_OPS,
            'timeout': CACHEOPS_TIMEOUT,
        },
    }
