import os
import textwrap
from os.path import exists
from tempfile import mkdtemp
from unittest import mock
from unittest.mock import Mock, patch

import django_dynamic_fixture as fixture
from django.contrib.auth.models import User
from django.test import TestCase

from readthedocs.builds.constants import EXTERNAL
from readthedocs.builds.models import Version
from readthedocs.config import ALL
from readthedocs.doc_builder.environments import LocalBuildEnvironment
from readthedocs.projects.exceptions import RepositoryError
from readthedocs.projects.models import Feature, Project
from readthedocs.rtd_tests.utils import (
    create_git_branch,
    create_git_tag,
    delete_git_branch,
    delete_git_tag,
    get_current_commit,
    make_test_git,
    make_test_hg,
)


# Avoid trying to save the commands via the API
@mock.patch('readthedocs.doc_builder.environments.BuildCommand.save', mock.MagicMock())
class TestGitBackend(TestCase):
    def setUp(self):
        git_repo = make_test_git()
        super().setUp()
        self.eric = User(username='eric')
        self.eric.set_password('test')
        self.eric.save()
        self.project = Project.objects.create(
            name='Test Project',
            repo_type='git',
            #Our top-level checkout
            repo=git_repo,
        )
        self.project.users.add(self.eric)
        self.dummy_conf = Mock()
        # These are the default values from v1
        self.dummy_conf.submodules.include = ALL
        self.dummy_conf.submodules.exclude = []
        self.build_environment = LocalBuildEnvironment(api_client=mock.MagicMock())

    def test_git_lsremote(self):
        repo_path = self.project.repo
        default_branches = [
            # comes from ``make_test_git`` function
            'submodule',
            'invalidsubmodule',
        ]
        branches = [
            'develop',
            'master',
            '2.0.X',
            'release/2.0.0',
            'release/foo/bar',
            "with\xa0space",
        ]
        for branch in branches:
            create_git_branch(repo_path, branch)

        create_git_tag(repo_path, 'v01')
        create_git_tag(repo_path, 'v02', annotated=True)
        create_git_tag(repo_path, 'release-ünîø∂é')

        repo = self.project.vcs_repo(environment=self.build_environment)
        # create the working dir if it not exists. It's required to ``cwd`` to
        # execute the command
        repo.check_working_dir()
        commit = get_current_commit(repo_path)
        repo_branches, repo_tags = repo.lsremote()

        self.assertEqual(
            {branch: branch for branch in default_branches + branches},
            {branch.verbose_name: branch.identifier for branch in repo_branches},
        )

        self.assertEqual(
            {"v01": commit, "v02": commit, "release-ünîø∂é": commit},
            {tag.verbose_name: tag.identifier for tag in repo_tags},
        )

    def test_git_lsremote_tags_only(self):
        repo_path = self.project.repo
        create_git_tag(repo_path, "v01")
        create_git_tag(repo_path, "v02", annotated=True)
        create_git_tag(repo_path, "release-ünîø∂é")

        repo = self.project.vcs_repo(environment=self.build_environment)
        # create the working dir if it not exists. It's required to ``cwd`` to
        # execute the command
        repo.check_working_dir()
        commit = get_current_commit(repo_path)
        repo_branches, repo_tags = repo.lsremote(
            include_tags=True, include_branches=False
        )

        self.assertEqual(repo_branches, [])
        self.assertEqual(
            {"v01": commit, "v02": commit, "release-ünîø∂é": commit},
            {tag.verbose_name: tag.identifier for tag in repo_tags},
        )

    def test_git_lsremote_branches_only(self):
        repo_path = self.project.repo
        default_branches = [
            # comes from ``make_test_git`` function
            "submodule",
            "invalidsubmodule",
        ]
        branches = [
            "develop",
            "master",
            "2.0.X",
            "release/2.0.0",
            "release/foo/bar",
        ]
        for branch in branches:
            create_git_branch(repo_path, branch)

        repo = self.project.vcs_repo(environment=self.build_environment)
        # create the working dir if it not exists. It's required to ``cwd`` to
        # execute the command
        repo.check_working_dir()
        repo_branches, repo_tags = repo.lsremote(
            include_tags=False, include_branches=True
        )

        self.assertEqual(repo_tags, [])
        self.assertEqual(
            {branch: branch for branch in default_branches + branches},
            {branch.verbose_name: branch.identifier for branch in repo_branches},
        )

    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_git_branches(self, checkout_path):
        repo_path = self.project.repo
        default_branches = [
            # comes from ``make_test_git`` function
            'submodule',
            'invalidsubmodule',
        ]
        branches = [
            'develop',
            'master',
            '2.0.X',
            'release/2.0.0',
            'release/foo/bar',
        ]
        for branch in branches:
            create_git_branch(repo_path, branch)

        # Create dir where to clone the repo
        local_repo = os.path.join(mkdtemp(), 'local')
        os.mkdir(local_repo)
        checkout_path.return_value = local_repo

        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.clone()

        self.assertEqual(
            {branch: branch for branch in default_branches + branches},
            {branch.verbose_name: branch.identifier for branch in repo.branches},
        )

    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_git_branches_unicode(self, checkout_path):
        repo_path = self.project.repo
        default_branches = [
            # comes from ``make_test_git`` function
            'submodule',
            'invalidsubmodule',
        ]
        branches = [
            'master',
            'release-ünîø∂é',
        ]
        for branch in branches:
            create_git_branch(repo_path, branch)

        # Create dir where to clone the repo
        local_repo = os.path.join(mkdtemp(), 'local')
        os.mkdir(local_repo)
        checkout_path.return_value = local_repo

        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.clone()

        self.assertEqual(
            set(branches + default_branches),
            {branch.verbose_name for branch in repo.branches},
        )

    def test_git_update_and_checkout(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        code, _, _ = repo.update()
        self.assertEqual(code, 0)

        # Returns `None` because there is no `identifier`,
        # so it uses the default branch
        self.assertIsNone(repo.checkout())

        self.assertTrue(exists(repo.working_dir))

    @patch('readthedocs.vcs_support.backends.git.Backend.fetch')
    def test_git_update_with_external_version(self, fetch):
        version = fixture.get(
            Version,
            project=self.project,
            type=EXTERNAL,
            active=True
        )
        repo = self.project.vcs_repo(
            verbose_name=version.verbose_name,
            version_type=version.type,
            environment=self.build_environment,
        )
        repo.update()
        fetch.assert_called_once()

    def test_git_fetch_with_external_version(self):
        version = fixture.get(
            Version,
            project=self.project,
            type=EXTERNAL,
            active=True
        )
        repo = self.project.vcs_repo(
            verbose_name=version.verbose_name,
            version_type=version.type,
            environment=self.build_environment,
        )
        repo.update()
        code, _, _ = repo.fetch()
        self.assertEqual(code, 0)

    def test_git_checkout_invalid_revision(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.update()
        version = 'invalid-revision'
        with self.assertRaises(RepositoryError) as e:
            repo.checkout(version)
        self.assertEqual(
            str(e.exception),
            RepositoryError.FAILED_TO_CHECKOUT.format(version),
        )

    def test_git_tags(self):
        repo_path = self.project.repo
        create_git_tag(repo_path, 'v01')
        create_git_tag(repo_path, 'v02', annotated=True)
        create_git_tag(repo_path, 'release-ünîø∂é')
        repo = self.project.vcs_repo(environment=self.build_environment)
        # We aren't cloning the repo,
        # so we need to hack the repo path
        repo.working_dir = repo_path
        commit = get_current_commit(repo_path)
        self.assertEqual(
            {"v01": commit, "v02": commit, "release-ünîø∂é": commit},
            {tag.verbose_name: tag.identifier for tag in repo.tags},
        )

    def test_check_for_submodules(self):
        repo = self.project.vcs_repo(environment=self.build_environment)

        repo.update()
        self.assertFalse(repo.are_submodules_available(self.dummy_conf))

        # The submodule branch contains one submodule
        repo.checkout('submodule')
        self.assertTrue(repo.are_submodules_available(self.dummy_conf))

    def test_skip_submodule_checkout(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.update()
        repo.checkout('submodule')
        self.assertTrue(repo.are_submodules_available(self.dummy_conf))

    def test_use_shallow_clone(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.update()
        repo.checkout('submodule')
        self.assertTrue(repo.use_shallow_clone())
        fixture.get(
            Feature,
            projects=[self.project],
            feature_id=Feature.DONT_SHALLOW_CLONE,
        )
        self.assertTrue(self.project.has_feature(Feature.DONT_SHALLOW_CLONE))
        self.assertFalse(repo.use_shallow_clone())

    def test_check_submodule_urls(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.update()
        repo.checkout('submodule')
        valid, _ = repo.validate_submodules(self.dummy_conf)
        self.assertTrue(valid)

    def test_check_invalid_submodule_urls(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.update()
        repo.checkout('invalidsubmodule')
        with self.assertRaises(RepositoryError) as e:
            repo.update_submodules(self.dummy_conf)
        # `invalid` is created in `make_test_git`
        # it's a url in ssh form.
        self.assertEqual(
            str(e.exception),
            RepositoryError.INVALID_SUBMODULES.format(['invalid']),
        )

    def test_invalid_submodule_is_ignored(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.update()
        repo.checkout('submodule')
        gitmodules_path = os.path.join(repo.working_dir, '.gitmodules')

        with open(gitmodules_path, 'a') as f:
            content = textwrap.dedent("""
                [submodule "not-valid-path"]
                    path = not-valid-path
                    url = https://github.com/readthedocs/readthedocs.org
            """)
            f.write(content)

        valid, submodules = repo.validate_submodules(self.dummy_conf)
        self.assertTrue(valid)
        self.assertEqual(list(submodules), ['foobar'])

    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_fetch_clean_tags_and_branches(self, checkout_path):
        upstream_repo = self.project.repo
        create_git_tag(upstream_repo, 'v01')
        create_git_tag(upstream_repo, 'v02')
        create_git_branch(upstream_repo, 'newbranch')

        local_repo = os.path.join(mkdtemp(), 'local')
        os.mkdir(local_repo)
        checkout_path.return_value = local_repo

        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.clone()

        delete_git_tag(upstream_repo, 'v02')
        delete_git_branch(upstream_repo, 'newbranch')

        # We still have all branches and tags in the local repo
        self.assertEqual(
            {'v01', 'v02'},
            {vcs.verbose_name for vcs in repo.tags},
        )
        self.assertEqual(
            {
                'invalidsubmodule', 'master', 'submodule', 'newbranch',
            },
            {vcs.verbose_name for vcs in repo.branches},
        )

        repo.update()

        # We don't have the eliminated branches and tags in the local repo
        self.assertEqual(
            {'v01'},
            {vcs.verbose_name for vcs in repo.tags},
        )
        self.assertEqual(
            {
                'invalidsubmodule', 'master', 'submodule',
            },
            {vcs.verbose_name for vcs in repo.branches},
        )


# Avoid trying to save the commands via the API
@mock.patch('readthedocs.doc_builder.environments.BuildCommand.save', mock.MagicMock())
class TestHgBackend(TestCase):

    def setUp(self):
        hg_repo = make_test_hg()
        super().setUp()
        self.eric = User(username='eric')
        self.eric.set_password('test')
        self.eric.save()
        self.project = Project.objects.create(
            name='Test Project',
            repo_type='hg',
            # Our top-level checkout
            repo=hg_repo,
        )
        self.project.users.add(self.eric)
        self.build_environment = LocalBuildEnvironment(api_client=mock.MagicMock())

    def test_parse_branches(self):
        data = """\
        stable
        default
        """

        expected_ids = ["stable", "default"]
        given_ids = [
            x.identifier
            for x in self.project.vcs_repo(
                environment=self.build_environment
            ).parse_branches(data)
        ]
        self.assertEqual(expected_ids, given_ids)

    def test_update_and_checkout(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.make_clean_working_dir()
        code, _, _ = repo.update()
        self.assertEqual(code, 0)
        code, _, _ = repo.checkout()
        self.assertEqual(code, 0)
        self.assertTrue(exists(repo.working_dir))

    def test_checkout_invalid_revision(self):
        repo = self.project.vcs_repo(environment=self.build_environment)
        repo.update()
        version = 'invalid-revision'
        with self.assertRaises(RepositoryError) as e:
            repo.checkout(version)
        self.assertEqual(
            str(e.exception),
            RepositoryError.FAILED_TO_CHECKOUT.format(version),
        )

    def test_parse_tags(self):
        data = """\
        tip                            13575:8e94a1b4e9a4
        1.8.1                          13573:aa1f3be38ab1
        1.8                            13515:2616325766e3
        1.7.5                          13334:2b2155623ee2
         """
        expected_tags = [
            ('aa1f3be38ab1', '1.8.1'),
            ('2616325766e3', '1.8'),
            ('2b2155623ee2', '1.7.5'),
        ]

        given_ids = [
            (x.identifier, x.verbose_name)
            for x in self.project.vcs_repo(
                environment=self.build_environment
            ).parse_tags(data)
        ]
        self.assertEqual(expected_tags, given_ids)
