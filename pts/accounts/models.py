# Copyright 2013 The Distro Tracker Developers
# See the COPYRIGHT file at the top-level directory of this distribution and
# at http://deb.li/DTAuthors
#
# This file is part of Distro Tracker. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution and at http://deb.li/DTLicense. No part of Distro Tracker,
# including this file, may be copied, modified, propagated, or distributed
# except according to the terms contained in the LICENSE file.
"""Models for the :mod:`pts.accounts` app."""
from __future__ import unicode_literals
from django.db import models
from django_email_accounts.models import User as EmailAccountsUser
from django_email_accounts.models import UserEmail
from django_email_accounts.models import (
    UserRegistrationConfirmation,
    ResetPasswordConfirmation,
    AddEmailConfirmation,
    MergeAccountConfirmation,
)


class User(EmailAccountsUser):
    """
    Proxy model for :class:`django_email_accounts.models.User` extending it
    with some PTS specific methods.
    """
    class Meta:
        proxy = True

    def is_subscribed_to(self, package):
        """
        Checks if the user is subscribed to the given package. The user is
        considered subscribed if at least one of its associated emails is
        subscribed.

        :param package: The name of the package or a package instance
        :type package: string or :class:`pts.core.models.PackageName`
        """
        from pts.core.models import PackageName
        if not isinstance(package, PackageName):
            package = PackageName.objects.get(name=package)
        qs = package.subscriptions.filter(pk__in=self.emails.all())
        return qs.exists()


