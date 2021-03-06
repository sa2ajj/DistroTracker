# -*- coding: utf-8 -*-

# Copyright 2013 The Distro Tracker Developers
# See the COPYRIGHT file at the top-level directory of this distribution and
# at http://deb.li/DTAuthors
#
# This file is part of Distro Tracker. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution and at http://deb.li/DTLicense. No part of Distro Tracker,
# including this file, may be copied, modified, propagated, or distributed
# except according to the terms contained in the LICENSE file.

"""
Tests for the PTS core module's models.
"""
from __future__ import unicode_literals
from django.test import TestCase
from django.test.utils import override_settings
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from pts.core.tests.common import temporary_media_dir
from django.core.urlresolvers import reverse
from pts.core.models import Subscription, EmailUser, PackageName, BinaryPackageName
from pts.core.models import BinaryPackage
from pts.core.models import Architecture
from pts.core.models import BinaryPackageRepositoryEntry
from pts.core.models import SourcePackageName, SourcePackageRepositoryEntry
from pts.core.models import Keyword
from pts.core.models import ActionItem, ActionItemType
from pts.core.models import PseudoPackageName
from pts.core.models import Repository
from pts.core.models import News
from pts.core.models import EmailNews
from pts.core.models import SourcePackage
from pts.core.models import ExtractedSourceFile
from pts.core.models import MailingList
from pts.core.models import Team
from pts.core.models import TeamMembership
from pts.core.models import MembershipPackageSpecifics
from pts.core.utils import message_from_bytes
from pts.core.utils.email_messages import get_decoded_message_payload
from pts.accounts.models import User
from .common import make_temp_directory
from .common import create_source_package

from email import message_from_string

import os
import email
import gpgme


class SubscriptionManagerTest(TestCase):
    def setUp(self):
        self.package = PackageName.objects.create(name='dummy-package')
        self.email_user = EmailUser.objects.create(email='email@domain.com')

    def create_subscription(self, package, email, active=True):
        """
        Helper method which creates a subscription for the given user to the
        given package.
        """
        return Subscription.objects.create_for(
            package_name=package,
            email=email,
            active=active)

    def test_create_for_existing_email(self):
        subscription = self.create_subscription(
            self.package.name, self.email_user.email)

        self.assertEqual(subscription.email_user, self.email_user)
        self.assertEqual(subscription.package, self.package)
        self.assertIn(self.email_user, self.package.subscriptions.all())
        self.assertTrue(subscription.active)

    def test_create_for_existing_email_inactive(self):
        """
        Tests the create_for method when creating an inactive subscription.
        """
        subscription = self.create_subscription(
            self.package.name, self.email_user.email, active=False)

        self.assertEqual(subscription.email_user, self.email_user)
        self.assertEqual(subscription.package, self.package)
        self.assertIn(self.email_user, self.package.subscriptions.all())
        self.assertFalse(subscription.active)

    def test_create_for_unexisting_email(self):
        previous_count = EmailUser.objects.count()
        subscription = Subscription.objects.create_for(
            package_name=self.package.name,
            email='non-existing@email.com')

        self.assertEqual(EmailUser.objects.count(), previous_count + 1)
        self.assertEqual(subscription.package, self.package)
        self.assertTrue(subscription.active)

    def test_create_for_twice(self):
        """
        Tests that the create_for method creates only one Subscription for a
        user, package pair.
        """
        prev_cnt_subs = Subscription.objects.count()
        self.create_subscription(self.package.name, self.email_user.email)
        self.create_subscription(self.package.name, self.email_user.email)

        self.assertEqual(Subscription.objects.count(), prev_cnt_subs + 1)

    def test_get_for_email(self):
        """
        Tests the get_for_email method when the user is subscribed to multiple
        packages.
        """
        self.create_subscription(self.package.name, self.email_user.email)
        p = PackageName.objects.create(name='temp')
        self.create_subscription(p.name, self.email_user.email)
        package_not_subscribed_to = PackageName.objects.create(name='qwer')
        self.create_subscription(package_not_subscribed_to.name,
                                 self.email_user.email,
                                 active=False)

        l = Subscription.objects.get_for_email(self.email_user.email)
        l = [sub.package for sub in l]

        self.assertIn(self.package, l)
        self.assertIn(p, l)
        self.assertNotIn(package_not_subscribed_to, l)

    def test_get_for_email_no_subsriptions(self):
        """
        Tests the get_for_email method when the user is not subscribed to any
        packages.
        """
        l = Subscription.objects.get_for_email(self.email_user.email)

        self.assertEqual(len(l), 0)

    def test_all_active(self):
        active_subs = [
            self.create_subscription(self.package.name, self.email_user.email),
            self.create_subscription(self.package.name, 'email@d.com')
        ]
        inactive_subs = [
            self.create_subscription(self.package.name, 'email2@d.com', False),
            self.create_subscription(self.package.name, 'email3@d.com', False),
        ]

        for active in active_subs:
            self.assertIn(active, Subscription.objects.all_active())
        for inactive in inactive_subs:
            self.assertNotIn(inactive, Subscription.objects.all_active())

    def test_all_active_filter_keyword(self):
        """
        Tests the all_active method when it should filter based on a keyword
        """
        active_subs = [
            self.create_subscription(self.package.name, self.email_user.email),
            self.create_subscription(self.package.name, 'email1@a.com')
        ]
        sub_no_kw = self.create_subscription(self.package.name, 'email2@a.com')
        for active in active_subs:
            active.keywords.add(Keyword.objects.get_or_create(name='cvs')[0])
        sub_no_kw.keywords.remove(Keyword.objects.get(name='cvs'))
        inactive_subs = [
            self.create_subscription(self.package.name, 'email2@d.com', False),
            self.create_subscription(self.package.name, 'email3@d.com', False),
        ]

        for active in active_subs:
            self.assertIn(active, Subscription.objects.all_active('cvs'))
        self.assertNotIn(sub_no_kw, Subscription.objects.all_active('cvs'))
        for inactive in inactive_subs:
            self.assertNotIn(inactive, Subscription.objects.all_active('cvs'))


class KeywordsTest(TestCase):
    def setUp(self):
        self.package = PackageName.objects.create(name='dummy-package')
        self.email_user = EmailUser.objects.create(email='email@domain.com')
        Keyword.objects.all().delete()
        self.email_user.default_keywords.add(
            Keyword.objects.get_or_create(name='cvs')[0])
        self.email_user.default_keywords.add(
            Keyword.objects.get_or_create(name='bts')[0])
        self.subscription = Subscription.objects.create(
            package=self.package,
            email_user=self.email_user)
        self.new_keyword = Keyword.objects.create(name='new')

    def test_keywords_add_to_subscription(self):
        """
        Test adding a new keyword to the subscription.
        """
        self.subscription.keywords.add(self.new_keyword)

        self.assertIn(self.new_keyword, self.subscription.keywords.all())
        self.assertNotIn(
            self.new_keyword, self.email_user.default_keywords.all())
        for keyword in self.email_user.default_keywords.all():
            self.assertIn(keyword, self.subscription.keywords.all())

    def test_keywords_remove_from_subscription(self):
        """
        Tests removing a keyword from the subscription.
        """
        keyword = self.email_user.default_keywords.all()[0]
        self.subscription.keywords.remove(keyword)

        self.assertNotIn(keyword, self.subscription.keywords.all())
        self.assertIn(keyword, self.email_user.default_keywords.all())

    def test_get_keywords_when_default(self):
        """
        Tests that the subscription uses the user's default keywords if none
        have explicitly been set for the subscription.
        """
        self.assertEqual(len(self.email_user.default_keywords.all()),
                         len(self.subscription.keywords.all()))
        self.assertEqual(self.email_user.default_keywords.count(),
                         self.subscription.keywords.count())
        for kw1, kw2 in zip(self.email_user.default_keywords.all(),
                            self.subscription.keywords.all()):
            self.assertEqual(kw1, kw2)


class EmailUserTest(TestCase):
    def setUp(self):
        self.package = PackageName.objects.create(name='dummy-package')
        self.email_user = EmailUser.objects.create(email='email@domain.com')

    def test_is_subscribed_to(self):
        """
        Tests that the is_subscribed_to method returns True when the user is
        subscribed to a package.
        """
        Subscription.objects.create_for(
            package_name=self.package.name,
            email=self.email_user.email)
        self.assertTrue(self.email_user.is_subscribed_to(self.package))
        self.assertTrue(self.email_user.is_subscribed_to(self.package.name))

    def test_is_subscribed_to_false(self):
        """
        Tests that the ``is_subscribed_to`` method returns False when the user
        is not subscribed to the package.
        """
        self.assertFalse(self.email_user.is_subscribed_to(self.package))
        self.assertFalse(self.email_user.is_subscribed_to(self.package.name))

    def test_is_subscribed_to_false_inactive(self):
        """
        Tests that the ``is_subscribed_to`` method returns False when the user
        has not confirmed the subscription (the subscription is inactive)
        """
        Subscription.objects.create_for(
            package_name=self.package.name,
            email=self.email_user.email,
            active=False)
        self.assertFalse(self.email_user.is_subscribed_to(self.package))

    def test_new_user_has_default_keywords(self):
        """
        Tests that newly created users always have all the default keywords.
        """
        all_default_keywords = Keyword.objects.filter(default=True)
        self.assertEqual(self.email_user.default_keywords.count(),
                         all_default_keywords.count())
        for keyword in self.email_user.default_keywords.all():
            self.assertIn(keyword, all_default_keywords)

    def test_unsubscribe_all(self):
        """
        Tests the unsubscribe all method.
        """
        Subscription.objects.create(email_user=self.email_user,
                                    package=self.package)

        self.email_user.unsubscribe_all()

        self.assertEqual(self.email_user.subscription_set.count(), 0)


class EmailUserManagerTest(TestCase):
    def setUp(self):
        self.package = PackageName.objects.create(name='dummy-package')
        self.email_user = EmailUser.objects.create(email='email@domain.com')

    def test_is_subscribed_to(self):
        """
        Tests that the is_user_subscribed_to method returns True when the
        user is subscribed to the given package.
        """
        Subscription.objects.create_for(
            package_name=self.package.name,
            email=self.email_user.email)
        self.assertTrue(
            EmailUser.objects.is_user_subscribed_to(self.email_user.email,
                                                    self.package.name))

    def test_is_subscribed_to_false(self):
        """
        Tests that the is_user_subscribed_to method returns False when the
        user is not subscribed to the given package.
        """
        self.assertFalse(
            EmailUser.objects.is_user_subscribed_to(self.email_user.email,
                                                    self.package.name))

    def test_is_subscribed_to_user_doesnt_exist(self):
        """
        Tests that the is_user_subscribed_to method returns False when the
        given user does not exist.
        """
        self.assertFalse(
            EmailUser.objects.is_user_subscribed_to('unknown-user@foo.com',
                                                    self.package.name))

    def test_is_subscribed_to_package_doesnt_exist(self):
        """
        Tests that the is_user_subscribed_to method returns False when the
        given package does not exist.
        """
        self.assertFalse(
            EmailUser.objects.is_user_subscribed_to(self.email_user.email,
                                                    'unknown-package'))


class PackageManagerTest(TestCase):
    def setUp(self):
        self.package = PackageName.objects.create(
            source=True,
            name='dummy-package')

    def test_package_exists(self):
        self.assertTrue(PackageName.objects.exists_with_name(self.package.name))

    def test_package_exists_false(self):
        self.assertFalse(PackageName.objects.exists_with_name('unexisting'))

    def test_source_package_create(self):
        """
        Tests that the sources manager creates source packages.
        """
        p = PackageName.source_packages.create(name='source-package')

        self.assertTrue(p.source)
        self.assertFalse(p.binary)
        self.assertFalse(p.pseudo)

    def test_pseudo_package_create(self):
        """
        Tests that the pseudo packages manager creates pseudo pacakges.
        """
        p = PackageName.pseudo_packages.create(name='pseudo-package')

        self.assertFalse(p.source)
        self.assertFalse(p.binary)
        self.assertTrue(p.pseudo)

    def test_subscription_only_package_create(self):
        """
        Tests that the subscription only packages manager creates
        subscription only packages.
        """
        p = PackageName.objects.create(name='package')

        self.assertFalse(p.source)
        self.assertFalse(p.binary)
        self.assertFalse(p.pseudo)

    def test_binary_package_create(self):
        p = PackageName.binary_packages.create(name='pkg')

        self.assertFalse(p.source)
        self.assertTrue(p.binary)
        self.assertFalse(p.pseudo)

    def test_manager_types_correct_objects(self):
        """
        Tests that the different manager types always return only their
        associated package type.
        """
        # Make sure there are no packages in the beginning
        PackageName.objects.all().delete()
        self.assertEqual(PackageName.objects.count(), 0)

        src_pkg = PackageName.source_packages.create(name='source-package')
        pseudo_pkg = PackageName.pseudo_packages.create(name='pseudo-package')
        sub_only_pkg = PackageName.objects.create(name='package')

        # objects manager returns all packages
        self.assertEqual(PackageName.objects.count(), 3)
        # specific pacakge type managers:
        self.assertEqual(PackageName.source_packages.count(), 1)
        self.assertIn(src_pkg, PackageName.source_packages.all())
        self.assertEqual(PackageName.pseudo_packages.count(), 1)
        self.assertIn(pseudo_pkg, PackageName.pseudo_packages.all())

    def test_all_with_subscriptions(self):
        """
        Tests the manager method which should return a QuerySet with all
        packages that have at least one subscriber.
        """
        pseudo_package = PseudoPackageName.objects.create(name='pseudo-package')
        sub_only_pkg = PackageName.objects.create(
            name='sub-only-pkg')
        PackageName.objects.create(name='sub-only-pkg-1')

        # When there are no subscriptions, it shouldn't return any results
        self.assertEqual(PackageName.objects.all_with_subscribers().count(), 0)
        self.assertEqual(
            PackageName.pseudo_packages.all_with_subscribers().count(),
            0)
        self.assertEqual(
            PackageName.source_packages.all_with_subscribers().count(),
            0)

        # When subscriptions are added, only the packages with subscriptions
        # are returned
        Subscription.objects.create_for(package_name=self.package.name,
                                        email='user@domain.com')
        Subscription.objects.create_for(package_name=sub_only_pkg.name,
                                        email='other-user@domain.com')
        Subscription.objects.create_for(package_name=pseudo_package.name,
                                        email='some-user@domain.com')

        self.assertEqual(PackageName.objects.all_with_subscribers().count(), 3)
        all_with_subscribers = [
            pkg.name
            for pkg in PackageName.objects.all_with_subscribers()
        ]
        self.assertIn(self.package.name, all_with_subscribers)
        self.assertIn(pseudo_package.name, all_with_subscribers)
        self.assertIn(sub_only_pkg.name, all_with_subscribers)
        # Specific managers...
        self.assertEqual(
            PackageName.pseudo_packages.all_with_subscribers().count(),
            1)
        self.assertEqual(
            PackageName.source_packages.all_with_subscribers().count(),
            1)


class BinaryPackageManagerTest(TestCase):
    def setUp(self):
        self.package = SourcePackageName.objects.create(name='dummy-package')
        self.binary_package = BinaryPackageName.objects.create(
            name='binary-package')

    def test_package_exists(self):
        self.assertTrue(
            BinaryPackageName.objects.exists_with_name(self.binary_package.name))

    def test_package_exists_false(self):
        self.assertFalse(
            BinaryPackageName.objects.exists_with_name('unexisting'))


class RepositoryTests(TestCase):
    fixtures = ['repository-test-fixture.json']

    def setUp(self):
        self.repository = Repository.objects.all()[0]
        self.src_pkg_name = SourcePackageName.objects.create(name='dummy-package')
        self.source_package = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='1.0.0')
        self.bin_pkg_name = PackageName.objects.get(name='dummy-package')
        self.bin_pkg_name.binary = True
        self.bin_pkg_name.save()
        self.bin_pkg_name = BinaryPackageName.objects.get(name='dummy-package')
        self.binary_package = BinaryPackage.objects.create(
            binary_package_name=self.bin_pkg_name,
            version='1.0.0',
            source_package=self.source_package)

    def test_add_source_entry_to_repository(self):
        """
        Tests adding a source package entry (name, version) to a repository
        instance.
        """
        self.repository.add_source_package(self.source_package)

        # An entry is created.
        self.assertEqual(SourcePackageRepositoryEntry.objects.count(), 1)
        e = SourcePackageRepositoryEntry.objects.all()[0]
        # Correct source package
        self.assertEqual(e.source_package, self.source_package)
        # Correct repository
        self.assertEqual(e.repository, self.repository)

    def test_add_binary_entry_to_repository(self):
        """
        Tests adding a new binary package entry (name, version) to a repository
        instance.
        """
        architecture = Architecture.objects.all()[0]
        self.repository.add_binary_package(
            self.binary_package,
            architecture=architecture)

        # An entry is created
        self.assertEqual(1, BinaryPackageRepositoryEntry.objects.count())
        e = BinaryPackageRepositoryEntry.objects.all()[0]
        # Correct binary package
        self.assertEqual(self.binary_package, e.binary_package)
        # Correct repository
        self.assertEqual(self.repository, e.repository)

    def test_add_source_entry_to_repository_extra_info(self):
        """
        Tests adding a source package entry (name, version + repository
        specific information) to a repository instance.
        """
        self.repository.add_source_package(self.source_package, **{
            'priority': 'source',
            'section': 'admin',
        })

        # An entry is created.
        self.assertEqual(SourcePackageRepositoryEntry.objects.count(), 1)
        e = SourcePackageRepositoryEntry.objects.all()[0]
        # Correct source package
        self.assertEqual(e.source_package, self.source_package)
        # Correct repository
        self.assertEqual(e.repository, self.repository)
        # Extra (repository-specific data is saved)
        self.assertEqual(e.priority, 'source')
        self.assertEqual(e.section, 'admin')

    def test_has_source_package_name_1(self):
        """
        Tests the has_source_package_name when the given source package is
        found in the repository.
        """
        self.repository.add_source_package(self.source_package)

        self.assertTrue(
            self.repository.has_source_package_name(self.source_package.name))

    def test_has_source_package_name_2(self):
        """
        Tests the has_source_package_name when the given source package is
        found in the repository.
        """
        self.repository.add_source_package(self.source_package)
        source_package = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='1.2.0')
        # Add another version of the same package
        self.repository.add_source_package(source_package)

        self.assertTrue(
            self.repository.has_source_package_name(self.source_package.name))

    def test_has_source_package_name_3(self):
        """
        Tests the has_source_package_name when the given source package is not
        found in the repository.
        """
        self.assertFalse(
            self.repository.has_source_package_name(self.source_package.name))

    def test_has_source_package_name_does_not_exist(self):
        """
        Tests the has_source_package_name when the given source package name
        does not exist.
        """
        # Sanity check - the package really does not exist
        self.assertFalse(
            SourcePackageName.objects.filter(name='no-exist').exists())

        self.assertFalse(
            self.repository.has_source_package_name('no-exist'))

    def test_has_source_package_1(self):
        """
        Tests the has_source_package when the given source package is found in
        the repository.
        """
        self.repository.add_source_package(self.source_package)

        self.assertTrue(
            self.repository.has_source_package(self.source_package))

    def test_has_source_package_2(self):
        """
        Tests the has_source_package when the given source package is not found
        in the repository.
        """
        self.assertFalse(
            self.repository.has_source_package(self.source_package))

    def test_get_source_package_repository_entry_single(self):
        """
        Tests the
        :meth:`get_source_package_entry <pts.core.models.Repository.get_source_package_entry>`
        method when there is only one version of the given package in the
        repository.
        """
        entry = self.repository.add_source_package(self.source_package)

        # When passing a SourcePackageName
        self.assertEqual(
            self.repository.get_source_package_entry(
                self.source_package.source_package_name),
            entry)

        # When passing a string
        self.assertEqual(
            self.repository.get_source_package_entry(
                self.source_package.source_package_name.name),
            entry)

    def test_get_source_package_repository_entry_multiple(self):
        """
        Tests the
        :meth:`get_source_package_entry <pts.core.models.Repository.get_source_package_entry>`
        method when there are multiple versions of the given package in the
        repository.
        """
        higher_version_package = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='2.0.0')
        self.repository.add_source_package(self.source_package)
        expected_entry = self.repository.add_source_package(
            higher_version_package)

        # When passing a SourcePackageName
        self.assertEqual(
            self.repository.get_source_package_entry(
                self.source_package.source_package_name),
            expected_entry)

        # When passing a string
        self.assertEqual(
            self.repository.get_source_package_entry(
                self.source_package.source_package_name.name),
            expected_entry)


class SourcePackageTests(TestCase):
    fixtures = ['repository-test-fixture.json']

    def setUp(self):
        self.repository = Repository.objects.all()[0]
        self.src_pkg_name = SourcePackageName.objects.create(name='dummy-package')
        self.source_package = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='1.0.0')

    def test_main_version_1(self):
        """
        Tests that the main version is correctly returned when the package is
        found in only one repository.
        """
        self.repository.add_source_package(self.source_package)

        self.assertEqual(self.source_package, self.src_pkg_name.main_version)

    def test_main_version_2(self):
        """
        Tests that the main version is correctly returned when the package is
        found multiple times (with different versions) in the default
        repository.
        """
        self.repository.add_source_package(self.source_package)
        higher_version_pkg = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='10.0.0')
        self.repository.add_source_package(higher_version_pkg)

        self.assertEqual(higher_version_pkg, self.src_pkg_name.main_version)

    def test_main_version_3(self):
        """
        Test that the main version is correctly returned when the package is
        found in multiple repositories.
        """
        self.repository.add_source_package(self.source_package)
        higher_version_pkg = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='10.0.0')
        non_default_repository = Repository.objects.create(name='repo')
        non_default_repository.add_source_package(higher_version_pkg)

        # The main version is the one from the default repository, regardless
        # of the fact that it has a lower version number.
        self.assertEqual(self.source_package, self.src_pkg_name.main_version)

    def test_main_entry_1(self):
        """
        Tests that the main entry is correctly returned when the package is
        found in only one repository.
        """
        self.repository.add_source_package(self.source_package)

        expected = SourcePackageRepositoryEntry.objects.get(
            source_package=self.source_package, repository=self.repository)
        self.assertEqual(expected, self.src_pkg_name.main_entry)

    def test_main_entry_2(self):
        """
        Tests that the main entry is correctly returned when the package is
        found multiple times (with different versions) in the default
        repository.
        """
        self.repository.add_source_package(self.source_package)
        higher_version_pkg = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='10.0.0')
        self.repository.add_source_package(higher_version_pkg)

        expected = SourcePackageRepositoryEntry.objects.get(
            source_package=higher_version_pkg, repository=self.repository)
        self.assertEqual(expected, self.src_pkg_name.main_entry)

    def test_main_entry_3(self):
        """
        Tests that the main entry is correctly returned when the package is
        found in multiple repositories.
        """
        self.repository.add_source_package(self.source_package)
        higher_version_pkg = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='10.0.0')
        non_default_repository = Repository.objects.create(name='repo')
        non_default_repository.add_source_package(higher_version_pkg)

        expected = SourcePackageRepositoryEntry.objects.get(
            source_package=self.source_package, repository=self.repository)
        self.assertEqual(expected, self.src_pkg_name.main_entry)

    def test_get_directory_url(self):
        """
        Tests retrieving the URL of the package's directory from the entry.
        """
        architectures = ['amd64', 'all']
        src_pkg = create_source_package({
            'name': 'package-with-directory',
            'binary_packages': ['binary-package'],
            'version': '0.1',
            'maintainer': {
                'name': 'Maintainer',
                'email': 'maintainer@domain.com'
            },
            'architectures': architectures,
            'directory': 'pool/path/to/dir',
        })
        entry = self.repository.add_source_package(src_pkg)

        self.assertEqual(
            self.repository.uri + 'pool/path/to/dir',
            entry.directory_url
        )

    def test_get_directory_url_no_directory_set(self):
        """
        Tests retrieving the URL of the package's directory from the repository
        entry when no directory is set for the source package.
        """
        entry = self.repository.add_source_package(self.source_package)

        self.assertIsNone(entry.directory_url)

    def test_get_dsc_file_url(self):
        """
        Tests retrieving the URL of the package's .dsc file given in the entry.
        """
        architectures = ['amd64', 'all']
        src_pkg = create_source_package({
            'name': 'package-with-dsc-file',
            'binary_packages': ['binary-package'],
            'version': '0.1',
            'maintainer': {
                'name': 'Maintainer',
                'email': 'maintainer@domain.com'
            },
            'architectures': architectures,
            'directory': 'pool/path/to/dir',
            'dsc_file_name': 'file.dsc',
        })
        entry = self.repository.add_source_package(src_pkg)

        self.assertEqual(
            self.repository.uri + 'pool/path/to/dir/file.dsc',
            entry.dsc_file_url
        )

    def test_get_dsc_file_url_no_file_set(self):
        """
        Tests retrieving the URL of the package's .dsc file given when there is
        no dsc file found in the source package information.
        """
        entry = self.repository.add_source_package(self.source_package)

        self.assertIsNone(entry.dsc_file_url)

    def test_get_version_entry_default_repo(self):
        """
        Tests that the
        :class:`SourcePackageRepositoryEntry <pts.core.models.SourcePackageRepositoryEntry>`
        matching the default repository is always returned from the
        :meth:`SourcePackage.main_entry <pts.core.models.SourcePackage.main_entry>`
        property.
        """
        # Make sure the repository is default
        self.repository.default = True
        self.repository.save()
        non_default_repository = Repository.objects.create(name='non-default')
        default_entry = self.repository.add_source_package(self.source_package)
        non_default_repository.add_source_package(self.source_package)

        self.assertEqual(self.source_package.main_entry, default_entry)

    def test_get_version_entry_non_default_repo(self):
        """
        Tests that the
        :class:`SourcePackageRepositoryEntry <pts.core.models.SourcePackageRepositoryEntry>`
        matching the repository with the highest
        :attr:`position <pts.core.models.Repository.position>` field is returned
        from
        :meth:`SourcePackage.main_entry <pts.core.models.SourcePackage.main_entry>`
        when the package is not found in the default repository.
        """
        self.repository.default = False
        self.repository.save()
        higher_position_repository = Repository.objects.create(
            name='higher-position', position=self.repository.position + 1)
        # Add the package to both repositories
        self.repository.add_source_package(self.source_package)
        expected_entry = higher_position_repository.add_source_package(
            self.source_package)

        self.assertEqual(self.source_package.main_entry, expected_entry)

    def test_get_version_entry_no_repo(self):
        """
        Tests that the
        :meth:`SourcePackage.main_entry <pts.core.models.SourcePackage.main_entry>`
        property returns ``None`` when the version is not found in any repository.
        """
        self.assertIsNone(self.source_package.main_entry)

    @temporary_media_dir
    def test_changelog_entry_only(self):
        """
        Tests that the
        :meth:`get_changelog_entry <pts.core.models.SourcePackage.get_changelog_entry>`
        returns the changelog part correctly when it is the only entry in the
        changelog file.
        """
        changelog_entry = (
            "{pkg} ({ver}) suite; urgency=high\n\n"
            "  * New stable release:\n"
            "    - Feature 1\n"
            "    - Feature 2\n\n"
            " -- Maintainer <email@domain.com>  Mon, 1 July 2013 09:00:00 +0000"
        ).format(pkg=self.source_package.name, ver=self.source_package.version)
        changelog_content = changelog_entry

        ExtractedSourceFile.objects.create(
            source_package=self.source_package,
            extracted_file=ContentFile(changelog_content, name='changelog'),
            name='changelog')

        self.assertEqual(
            self.source_package.get_changelog_entry(),
            changelog_entry)

    @temporary_media_dir
    def test_changelog_entry_beginning(self):
        """
        Tests that the
        :meth:`get_changelog_entry <pts.core.models.SourcePackage.get_changelog_entry>`
        returns the changelog part correctly when it is the latest entry in the
        changelog file.
        """
        changelog_entry = (
            "{pkg} ({ver}) suite; urgency=high\n\n"
            "  * New stable release:\n"
            "    - Feature 1\n"
            "    - Feature 2\n\n"
            " -- Maintainer <email@domain.com>  Mon, 1 July 2013 09:00:00 +0000"
        ).format(pkg=self.source_package.name, ver=self.source_package.version)
        other_content = (
            "{pkg} ({ver}) suite; urgency=high\n\n"
            "  * New stable release:\n"
            "    - Feature\n\n"
            " -- Maintainer <email@domain.com>  Mon, 1 July 2013 09:00:00 +0000"
        ).format(pkg=self.source_package.name, ver='9.9.9')
        changelog_content = changelog_entry + '\n' + other_content

        ExtractedSourceFile.objects.create(
            source_package=self.source_package,
            extracted_file=ContentFile(changelog_content, name='changelog'),
            name='changelog')

        self.assertEqual(
            self.source_package.get_changelog_entry(),
            changelog_entry)

    @temporary_media_dir
    def test_changelog_entry_not_first(self):
        """
        Tests that the
        :meth:`get_changelog_entry <pts.core.models.SourcePackage.get_changelog_entry>`
        returns the changelog part correctly when it is not the latest entry in the
        changelog file.
        """
        changelog_entry = (
            "{pkg} ({ver}) suite; urgency=high\n\n"
            "  * New stable release:\n"
            "    - Feature 1\n"
            "    - Feature 2\n\n"
            " -- Maintainer <email@domain.com>  Mon, 1 July 2013 09:00:00 +0000"
        ).format(pkg=self.source_package.name, ver=self.source_package.version)
        other_content = (
            "{pkg} ({ver}) suite; urgency=high\n\n"
            "  * New stable release:\n"
            "    - Feature\n\n"
            " -- Maintainer <email@domain.com>  Mon, 1 July 2013 09:00:00 +0000"
        ).format(pkg=self.source_package.name, ver='9.9.9')
        changelog_content = other_content + '\n' + changelog_entry

        ExtractedSourceFile.objects.create(
            source_package=self.source_package,
            extracted_file=ContentFile(changelog_content, name='changelog'),
            name='changelog')
        self.assertEqual(
            self.source_package.get_changelog_entry(),
            changelog_entry)

    @temporary_media_dir
    def test_changelog_entry_regex_meta_chars(self):
        """
        Tests that the
        :meth:`get_changelog_entry <pts.core.models.SourcePackage.get_changelog_entry>`
        returns the changelog part correctly when the version contains a
        regex meta character.
        """
        self.source_package.version = self.source_package.version + '+deb7u1'
        self.source_package.save()
        changelog_entry = (
            "{pkg} ({ver}) suite; urgency=high\n\n"
            "  * New stable release:\n"
            "    - Feature 1\n"
            "    - Feature 2\n\n"
            " -- Maintainer <email@domain.com>  Mon, 1 July 2013 09:00:00 +0000"
        ).format(pkg=self.source_package.name, ver=self.source_package.version)
        changelog_content = changelog_entry

        ExtractedSourceFile.objects.create(
            source_package=self.source_package,
            extracted_file=ContentFile(changelog_content, name='changelog'),
            name='changelog')

        self.assertEqual(
            self.source_package.get_changelog_entry(),
            changelog_entry)


class BinaryPackageTests(TestCase):
    fixtures = ['repository-test-fixture.json']

    def setUp(self):
        self.repository = Repository.objects.all()[0]
        self.src_pkg_name = SourcePackageName.objects.create(name='dummy-package')
        self.source_package = SourcePackage.objects.create(
            source_package_name=self.src_pkg_name, version='1.0.0')
        self.binary_package = BinaryPackageName.objects.create(
            name='binary-package')

    def test_binary_package_name_to_source_name_1(self):
        """
        Tests retrieving a source package name from a binary package name when
        the binary package name is registered for only one source package.
        """
        self.source_package.binary_packages.add(self.binary_package)

        self.assertEqual(
            self.src_pkg_name,
            self.binary_package.main_source_package_name
        )

    def test_binary_package_name_to_source_name_2(self):
        """
        Tests retrieving a source package name from a binary package name when
        the binary package is registered for two different source packages
        """
        self.source_package.binary_packages.add(self.binary_package)
        higher_version_name = SourcePackageName.objects.create(
            name='higher-version-name')
        higher_version_pkg = SourcePackage.objects.create(
            source_package_name=higher_version_name, version='10.0.0')
        higher_version_pkg.binary_packages.add(self.binary_package)

        self.assertEqual(
            higher_version_name,
            self.binary_package.main_source_package_name
        )

    def test_binary_package_name_to_source_name_default_repository(self):
        """
        Tests retrieving a source package name from a bianry package name when
        the resulting source package name should be the one from the default
        repository.
        """
        self.repository.add_source_package(self.source_package)
        self.source_package.binary_packages.add(self.binary_package)
        higher_version_name = SourcePackageName.objects.create(
            name='higher-version-name')
        higher_version_pkg = SourcePackage.objects.create(
            source_package_name=higher_version_name, version='10.0.0')
        # Add the higher version package to a non-default repository
        non_default_repository = Repository.objects.create(name='repo')
        non_default_repository.add_source_package(higher_version_pkg)
        higher_version_pkg.binary_packages.add(self.binary_package)

        # The resulting name is the name of the source package found in the
        # default repository.
        self.assertEqual(
            self.src_pkg_name,
            self.binary_package.main_source_package_name
        )


class MailingListTest(TestCase):
    def test_validate_url_template(self):
        """
        Tests validation of the URL template field.
        """
        mailing_list = MailingList(name='list', domain='some.domain.com')
        mailing_list.archive_url_template = (
            'http://this.does/not/have/user/parameter')

        with self.assertRaises(ValidationError):
            mailing_list.full_clean()

        mailing_list.archive_url_template = (
            'http://this.does/have/{user}')
        mailing_list.full_clean()

    def test_get_archive_url(self):
        """
        Tests retrieving the archive URL from a MailingList instance.
        """
        mailing_list = MailingList(name='list', domain='some.domain.com')
        mailing_list.archive_url_template = (
            'http://some.domain.com/archive/{user}/')

        self.assertEqual(
            mailing_list.archive_url('this-is-a-user'),
            'http://some.domain.com/archive/this-is-a-user/'
        )

    def test_get_archive_url_for_email(self):
        """
        Test retrieving the archive URL from a MailingList instance when an
        email is given, instead of a user.
        """
        mailing_list = MailingList(name='list', domain='some.domain.com')
        mailing_list.archive_url_template = (
            'http://some.domain.com/archive/{user}/')

        self.assertEqual(
            mailing_list.archive_url_for_email(
                'this-is-a-user@some.domain.com'),
            'http://some.domain.com/archive/this-is-a-user/'
        )

        # Not given a valid email
        self.assertIsNone(
            mailing_list.archive_url_for_email('this-is-not-an-email'))

        # Not given an email in the correct domain
        self.assertIsNone(
            mailing_list.archive_url_for_email('email@other.domain.com'))

    def test_find_matching_mailing_list(self):
        """
        Tests finding a matching mailing list object when given an email.
        """
        expect = MailingList.objects.create(
            name='list', domain='some.domain.com')
        MailingList.objects.create(name='other', domain='other.com')
        MailingList.objects.create(name='domain', domain='domain.com')

        email = 'username@some.domain.com'
        self.assertEqual(MailingList.objects.get_by_email(email), expect)

        email = 'not-an-email'
        self.assertIsNone(MailingList.objects.get_by_email(email))

        email = 'user@no.registered.domain'
        self.assertIsNone(MailingList.objects.get_by_email(email))


class NewsTests(TestCase):
    """
    Tests for the :class:`pts.core.models.News` model.
    """
    def setUp(self):
        self.package = SourcePackageName.objects.create(name='dummy-package')

    def import_key_from_test_file(self, file_name):
        """
        Helper function which imports the given test key file into the test
        keyring.
        """
        old = os.environ.get('GNUPGHOME', None)
        os.environ['GNUPGHOME'] = self.TEST_KEYRING_DIRECTORY
        ctx = gpgme.Context()
        file_path = os.path.join(
            os.path.dirname(__file__),
            'tests-data/keys',
            file_name
        )
        with open(file_path, 'rb') as key_file:
            ctx.import_(key_file)

        if old:
            os.environ['GNUPGHOME'] = old

    def get_test_file_path(self, file_name):
        """
        Helper method returning the full path to the test file with the given
        name.
        """
        return os.path.join(
            os.path.dirname(__file__),
            'tests-data',
            file_name)

    def test_content_from_db(self):
        """
        Tests that the :meth:`pts.core.models.News.content` property returns
        the correct contents when they are found in the database.
        """
        expected_content = 'This is some news content'
        news = News.objects.create(
            title='some title',
            _db_content=expected_content,
            package=self.package
        )

        self.assertEqual(news.content, expected_content)

    @temporary_media_dir
    def test_content_from_file(self):
        """
        Tests that the :meth:`pts.core.models.News.content` property returns
        the correct contents when they are found in a file.
        """
        expected_content = 'This is some news content'
        # Create a temporary file for the content
        content_file = ContentFile(expected_content, name='tmp-content')
        # Create the news item with the given content file
        news = News.objects.create(
            title='some title',
            package=self.package,
            news_file=content_file
        )

        self.assertEqual(news.content, expected_content)

    def test_no_content(self):
        """
        Tests that the :meth:`pts.core.models.News.content` property returns
        no content when neither the database content nor file content is set.
        """
        news = News.objects.create(title='some title', package=self.package)

        self.assertIsNone(news.content)

    def test_create_db_content(self):
        """
        Tests the :meth:`pts.core.models.NewsManager.create` method when it
        should create an instance whose content is stored in the database.
        """
        expected_content = 'Some content'
        news = News.objects.create(
            title='some title',
            content=expected_content,
            package=self.package)

        self.assertEqual(news._db_content, expected_content)
        self.assertFalse(news.news_file)

    @temporary_media_dir
    def test_create_file_content(self):
        """
        Tests the :meth:`pts.core.models.NewsManager.create` method when it
        should create an instance whose content is stored in a file.
        """
        expected_content = 'Some content'
        news = News.objects.create(
            title='some title',
            file_content=expected_content,
            package=self.package)

        self.assertTrue(news.news_file)
        self.assertIsNone(news._db_content)
        self.assertEqual(news.content, expected_content)

    @temporary_media_dir
    def test_create_email_news_signature(self):
        """
        Tests that the signature information is correctly extracted when
        creating a news item from an email message which was transfer encoded
        as quoted-printable.
        """
        with make_temp_directory('-pts-keyring') as TEST_KEYRING_DIRECTORY:
            self.TEST_KEYRING_DIRECTORY = TEST_KEYRING_DIRECTORY
            with self.settings(
                    PTS_KEYRING_DIRECTORY=self.TEST_KEYRING_DIRECTORY):
                self.import_key_from_test_file('key1.pub')
                # The content of the test news item is found in a file
                file_path = self.get_test_file_path(
                    'signed-message-quoted-printable')
                with open(file_path, 'r') as f:
                    content = f.read()
                expected_name = 'PTS Tests'
                expected_email = 'fake-address@domain.com'
                sender_name = 'Some User'

                news = EmailNews.objects.create_email_news(
                    message=message_from_string(content),
                    package=self.package)

                # The news contains a signature
                self.assertEqual(1, news.signed_by.count())
                # The signature information is correct?
                signer = news.signed_by.all()[0]
                self.assertEqual(expected_name, signer.name)
                self.assertEqual(expected_email, signer.email)
                # The created by field is also set, but to the sender of the
                # email
                self.assertEqual(sender_name, news.created_by)

    @temporary_media_dir
    def test_create_email_news_unknown_encoding_utf8(self):
        """
        Tests that creating an email news item from a message which does not
        specify an encoding works correctly when the actual encoding is utf-8.
        """
        message = email.message.Message()
        message['Subject'] = 'Some subject'
        content = 'è'
        raw_content = content.encode('utf-8')
        message.set_payload(raw_content)

        news = EmailNews.objects.create_email_news(
            message=message,
            package=self.package)

        # The news is successfully created
        self.assertEqual(1, EmailNews.objects.count())
        news = EmailNews.objects.all()[0]
        # The news can be converted back to a Message instance
        msg_from_news = message_from_bytes(news.content)
        # The payload is correct?
        self.assertEqual(raw_content, msg_from_news.get_payload())
        # It can be converted correctly to an actual unicode object
        self.assertEqual(
            content,
            get_decoded_message_payload(msg_from_news))

    @temporary_media_dir
    def test_create_email_news_unknown_encoding_latin1(self):
        """
        Tests that creating an email news item from a message which does not
        specify an encoding works correctly when the actual encoding is
        latin1.
        """
        message = email.message.Message()
        message['Subject'] = 'Some subject'
        content = 'è'
        raw_content = content.encode('latin-1')
        message.set_payload(raw_content)

        news = EmailNews.objects.create_email_news(
            message=message,
            package=self.package)

        # The news is successfully created
        self.assertEqual(1, EmailNews.objects.count())
        news = EmailNews.objects.all()[0]
        # The news can be converted back to a Message instance
        msg_from_news = message_from_bytes(news.content)
        # The payload is correct?
        self.assertEqual(raw_content, msg_from_news.get_payload())
        # It can be converted correctly to an actual unicode object
        self.assertEqual(
            content,
            get_decoded_message_payload(msg_from_news, 'latin-1'))

    @temporary_media_dir
    def test_email_news_render(self):
        """
        Tests that an email news is correctly rendered when the encoding of the
        message is unknown.
        """
        message = email.message.Message()
        message['Subject'] = 'Some subject'
        content = 'è'
        # Create two news items: one latin-1 the other utf-8 encoded.
        message.set_payload(content.encode('latin-1'))
        news_latin = EmailNews.objects.create_email_news(
            message=message,
            package=self.package)
        message.set_payload(content.encode('utf-8'))
        news_utf = EmailNews.objects.create_email_news(
            message=message,
            package=self.package)

        # Check that the latin-1 encoded news is correctly displayed
        response = self.client.get(reverse('pts-news-page', kwargs={
            'news_id': news_latin.id,
        }))
        # The response contains the correctly decoded content
        self.assertIn(content, response.content.decode('utf-8'))

        # Check that the utf-8 encoded news is correctly displayed
        response = self.client.get(reverse('pts-news-page', kwargs={
            'news_id': news_utf.id,
        }))
        # The response contains the correctly decoded content
        self.assertIn(content, response.content.decode('utf-8'))


@override_settings(TEMPLATE_DIRS=(os.path.join(
    os.path.dirname(__file__),
    'tests-data/tests-templates'),))
class ActionItemTests(TestCase):
    """
    Tests for the :class:`pts.core.models.ActionItem` model.
    """
    def setUp(self):
        self.package = PackageName.objects.create(name='dummy-package')
        self.action_type = ActionItemType.objects.create(type_name='test-type')

    def set_action_type_template(self, template_name):
        """
        Sets the template name for the test action item type.
        """
        self.action_type.full_description_template = template_name
        self.action_type.save()

    def test_full_description_from_template(self):
        """
        Tests that the :attr:`pts.core.models.ActionItem.full_description`
        property returns content by rendering the correct template.
        """
        self.set_action_type_template('action-item-test.html')
        action_item = ActionItem.objects.create(
            package=self.package,
            item_type=self.action_type,
            short_description='Short description of item')

        self.assertIn(
            "Item's PK is {pk}".format(pk=action_item.pk),
            action_item.full_description)
        self.assertIn(
            "Short description: Short description of item",
            action_item.full_description)

    def test_full_description_unexisting_template(self):
        """
        Tests that the :attr:`pts.core.models.ActionItem.full_description`
        returns an empty full description if the given template does not
        exist.
        """
        self.set_action_type_template('this-template-does-not-exist.html')
        action_item = ActionItem.objects.create(
            package=self.package,
            item_type=self.action_type,
            short_description='Short description of item')

        self.assertEqual('', action_item.full_description)

    def test_full_description_no_template_given(self):
        """
        Tests that the :attr:`pts.core.models.ActionItem.full_description`
        returns an empty full description if no template is set for the item.
        """
        action_item = ActionItem.objects.create(
            package=self.package,
            item_type=self.action_type,
            short_description='Short description of item')

        self.assertEqual('', action_item.full_description)

    def test_full_description_extra_data(self):
        """
        Tests that the :attr:`pts.core.models.ActionItem.full_description`
        returns a description which can use the extra_data of a
        :class:`pts.core.models.ActionItem`.
        """
        self.set_action_type_template('action-item-test.html')
        action_item = ActionItem.objects.create(
            package=self.package,
            item_type=self.action_type,
            short_description='Short description of item')
        action_item.extra_data = ['data1', 'data2']
        action_item.save()

        self.assertIn("data1, data2", action_item.full_description)


class TeamTests(TestCase):
    """
    Tests for the :class:`Team <pts.core.models.Team>` model.
    """
    def setUp(self):
        self.password = 'asdf'
        self.user = User.objects.create_user(
            main_email='user@domain.com', password=self.password,
            first_name='', last_name='')
        self.team = Team.objects.create_with_slug(
            owner=self.user, name="Team name")
        self.package_name = PackageName.objects.create(name='dummy')
        self.team.packages.add(self.package_name)
        self.email_user = EmailUser.objects.create(
            email='other@domain.com')

    def assert_keyword_sets_equal(self, set1, set2):
        self.assertEqual(
            [k.name for k in set1],
            [k.name for k in set2])

    def assert_keyword_sets_not_equal(self, set1, set2):
        self.assertNotEqual(
            [k.name for k in set1],
            [k.name for k in set2])

    def test_no_membership_keywords(self):
        """
        Tests that when there are no membership keywords, the user's default
        keywords are returned.
        """
        membership = self.team.add_members([self.email_user])[0]
        MembershipPackageSpecifics.objects.create(
            membership=membership,
            package_name=self.package_name)

        self.assert_keyword_sets_equal(
            self.email_user.default_keywords.all(),
            membership.get_keywords(self.package_name))

    def test_set_membership_keywords(self):
        membership = self.team.add_members([self.email_user])[0]
        keywords = Keyword.objects.all()[:3]

        membership.set_membership_keywords([k.name for k in keywords])

        # The set of membership keywords is correctly set
        self.assert_keyword_sets_equal(
            keywords, membership.default_keywords.all())
        # A flag is set indicating that the set exists
        self.assertTrue(membership.has_membership_keywords)
        # The keywords returned for the package are equal to the membership
        # keywords.
        self.assert_keyword_sets_equal(
            keywords,
            membership.get_keywords(self.package_name))

    def test_set_membership_package_specifics(self):
        # Add another package to the team
        package = self.team.packages.create(name='other-pkg')
        self.team.packages.add(package)
        membership = self.team.add_members([self.email_user])[0]
        keywords = Keyword.objects.all()[:3]

        membership.set_keywords(self.package_name, [k.name for k in keywords])

        # A MembershipPackageSpecifics instance is created
        self.assertEqual(1, MembershipPackageSpecifics.objects.count())
        # The keywords returned for the package are correct
        self.assert_keyword_sets_equal(
            keywords,
            membership.get_keywords(self.package_name))
        # But the other package still returns the user's default keywords
        self.assert_keyword_sets_equal(
            self.email_user.default_keywords.all(),
            membership.get_keywords(package))

    def test_mute_package(self):
        """
        Tests that it is possible to mute only one package in the team.
        """
        package = self.team.packages.create(name='other-pkg')
        self.team.packages.add(package)
        membership = self.team.add_members([self.email_user])[0]

        membership.mute_package(self.package_name)

        # Refresh the instance
        membership = TeamMembership.objects.get(pk=membership.pk)
        # The whole membership is not muted
        self.assertFalse(membership.muted)
        # The package is though
        self.assertTrue(membership.is_muted(self.package_name))
        # The other package isn't
        self.assertFalse(membership.is_muted(package))
