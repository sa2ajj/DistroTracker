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
Module implementing the processing of received emails which could be turned
into news items.
"""
from __future__ import unicode_literals
from django.utils.html import escape
from pts.core.utils import message_from_bytes
from pts.core.utils import get_or_none
from pts.core.models import News
from pts.core.models import EmailNews
from pts.core.models import PackageName
from pts import vendor


def create_news(message, package):
    """
    Create a news item from the given message.

    The created news parameters are:

    - title - the Subject of the message
    - content - the message content itself
    - content_type - message/rfc822

    :param message: A message which should be turned into a news item.
    :type message: :class:`email.message.Message`
    :param package: The package for which this news item should be created.
    :type package: :class:`pts.core.models.PackageName`

    :returns: The created news item
    :rtype: :class:`pts.core.models.News`
    """
    return EmailNews.objects.create_email_news(message, package)


def process(message):
    """
    Process an incoming message which is potentially a news item.

    The function first tries to call the vendor-provided function
    :func:`create_news_from_email_message <pts.vendor.skeleton.rules.create_news_from_email_message>`.

    If this function does not exist a news item is created only if there is a
    ``X-PTS-Package`` header set giving the name of an existing source or
    pseudo package.

    If the ``X-PTS-Url`` is also set then the content of the message will not
    be the email content, rather the URL given in this header.

    :param message: The received message
    :type message: :class:`bytes`
    """
    assert isinstance(message, bytes), 'Message must be given as bytes'

    msg = message_from_bytes(message)

    # Try asking the vendor function first.
    created, implemented = vendor.call('create_news_from_email_message', msg)
    if implemented and created:
        return

    # If the message has an X-PTS-Package header, it is automatically made into
    # a news item.
    if 'X-PTS-Package' in msg:
        package_name = msg['X-PTS-Package']
        package = get_or_none(PackageName, name=package_name)
        if not package:
            return
        if 'X-PTS-Url' not in msg:
            create_news(msg, package)
        else:
            pts_url = msg['X-PTS-Url']
            News.objects.create(
                title=pts_url,
                content="<a href={url}>{url}</a>".format(url=escape(pts_url)),
                package=package,
                content_type='text/html')

