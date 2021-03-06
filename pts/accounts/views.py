# Copyright 2013 The Distro Tracker Developers
# See the COPYRIGHT file at the top-level directory of this distribution and
# at http://deb.li/DTAuthors
#
# This file is part of Distro Tracker. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution and at http://deb.li/DTLicense. No part of Distro Tracker,
# including this file, may be copied, modified, propagated, or distributed
# except according to the terms contained in the LICENSE file.
"""Views for the :mod:`pts.accounts` app."""
from __future__ import unicode_literals
from django.views.generic import TemplateView
from django.views.generic.edit import CreateView
from django.views.generic.edit import UpdateView
from django.views.generic.edit import FormView
from django.views.generic.base import View
from django.core.urlresolvers import reverse_lazy
from django.core.urlresolvers import reverse
from django.core.mail import send_mail
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login
from django.contrib.auth import logout
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.utils.http import urlencode
from django.http import HttpResponseForbidden
from django.http import Http404
from django.conf import settings
from pts.accounts.models import User
from pts.accounts.models import UserEmail
from pts.accounts.models import UserRegistrationConfirmation
from pts.accounts.models import AddEmailConfirmation
from pts.accounts.models import ResetPasswordConfirmation
from pts.core.utils import pts_render_to_string
from pts.core.utils import render_to_json_response
from pts.core.models import Subscription
from pts.core.models import EmailUser
from pts.core.models import Keyword

from django_email_accounts import views as email_accounts_views
from django_email_accounts.views import LoginRequiredMixin


class ConfirmationRenderMixin(object):
    def get_confirmation_email_content(self, confirmation):
        return pts_render_to_string(self.confirmation_email_template, {
            'confirmation': confirmation,
        })


class LoginView(email_accounts_views.LoginView):
    success_url = reverse_lazy('pts-accounts-profile')


class LogoutView(email_accounts_views.LogoutView):
    success_url = reverse_lazy('pts-index')


class RegisterUser(ConfirmationRenderMixin, email_accounts_views.RegisterUser):
    success_url = reverse_lazy('pts-accounts-register-success')

    confirmation_email_subject = 'PTS Registration Confirmation'
    confirmation_email_from_address = settings.PTS_CONTACT_EMAIL


class RegistrationConfirmation(email_accounts_views.RegistrationConfirmation):
    success_url = reverse_lazy('pts-accounts-profile')
    message = 'You have successfully registered to the PTS'


class ResetPasswordView(ConfirmationRenderMixin, email_accounts_views.ResetPasswordView):
    success_url = reverse_lazy('pts-accounts-profile')


class ForgotPasswordView(ConfirmationRenderMixin, email_accounts_views.ForgotPasswordView):
    success_url = reverse_lazy('pts-accounts-password-reset-success')
    email_subject = 'PTS Password Reset Confirmation'
    email_from_address = settings.PTS_CONTACT_EMAIL


class ChangePersonalInfoView(email_accounts_views.ChangePersonalInfoView):
    success_url = reverse_lazy('pts-accounts-profile-modify')


class PasswordChangeView(email_accounts_views.PasswordChangeView):
    success_url = reverse_lazy('pts-accounts-profile-password-change')


class AccountProfile(email_accounts_views.AccountProfile):
    pass


class ManageAccountEmailsView(ConfirmationRenderMixin, email_accounts_views.ManageAccountEmailsView):
    success_url = reverse_lazy('pts-accounts-manage-emails')
    merge_accounts_url = reverse_lazy('pts-accounts-merge-confirmation')

    confirmation_email_subject = 'PTS Add Email To Account'
    confirmation_email_from_address = settings.PTS_CONTACT_EMAIL


class AccountMergeConfirmView(ConfirmationRenderMixin, email_accounts_views.AccountMergeConfirmView):
    success_url = reverse_lazy('pts-accounts-merge-confirmed')
    confirmation_email_subject = 'Merge PTS Accounts'
    confirmation_email_from_address = settings.PTS_CONTACT_EMAIL


class AccountMergeFinalize(email_accounts_views.AccountMergeFinalize):
    success_url = reverse_lazy('pts-accounts-merge-finalized')


class AccountMergeConfirmedView(email_accounts_views.AccountMergeConfirmedView):
    template_name = 'accounts/pts-accounts-merge-confirmed.html'


class ConfirmAddAccountEmail(email_accounts_views.ConfirmAddAccountEmail):
    pass


class SubscriptionsView(LoginRequiredMixin, View):
    """
    Displays a user's subscriptions.

    This includes both direct package subscriptions and team memberships.
    """
    template_name = 'accounts/subscriptions.html'

    def get(self, request):
        user = request.user
        # Map users emails to the subscriptions of that email
        emails = [
            EmailUser.objects.get_or_create(user_email=email)[0]
            for email in user.emails.all()
        ]
        subscriptions = {
            email: {
                'subscriptions': sorted([
                    subscription for subscription in email.subscription_set.all()
                ], key=lambda sub: sub.package.name),
                'team_memberships': sorted([
                    membership for membership in email.membership_set.all()
                ], key=lambda m: m.team.name)
            }
            for email in emails
        }
        return render(request, self.template_name, {
            'subscriptions': subscriptions,
        })


class UserEmailsView(LoginRequiredMixin, View):
    """
    Returns a JSON encoded list of the currently logged in user's emails.
    """
    def get(self, request):
        user = request.user
        return render_to_json_response([
            email.email for email in user.emails.all()
        ])


class SubscribeUserToPackageView(LoginRequiredMixin, View):
    """
    Subscribes the user to a package.

    The user whose email address is provided must currently be logged in.
    """
    def post(self, request):
        package = request.POST.get('package', None)
        emails = request.POST.getlist('email', None)

        if not package or not emails:
            raise Http404

        # Check whether the logged in user is associated with the given emails
        users_emails = [e.email for e in request.user.emails.all()]
        for email in emails:
            if email not in users_emails:
                return HttpResponseForbidden()

        # Create the subscriptions
        for email in emails:
            Subscription.objects.create_for(
                package_name=package,
                email=email)

        if request.is_ajax():
            return render_to_json_response({
                'status': 'ok',
            })
        else:
            next = request.POST.get('next', None)
            if not next:
                return redirect('pts-package-page', package_name=package)
            return redirect(next)


class UnsubscribeUserView(LoginRequiredMixin, View):
    """
    Unsubscribes the currently logged in user from the given package.
    An email can be optionally provided in which case only the given email is
    unsubscribed from the package, if the logged in user owns it.
    """
    def post(self, request):
        if 'package' not in request.POST:
            raise Http404

        package = request.POST['package']
        user = request.user

        if 'email' not in request.POST:
            # Unsubscribe all the user's emails from the package
            qs = Subscription.objects.filter(
                email_user__in=user.emails.all(),
                package__name=package)
        else:
            # Unsubscribe only the given email from the package
            qs = Subscription.objects.filter(
                email_user__user_email__email=request.POST['email'],
                package__name=package)

        qs.delete()

        if request.is_ajax():
            return render_to_json_response({
                'status': 'ok',
            })
        else:
            if 'next' in request.POST:
                return redirect(request.POST['next'])
            else:
                return redirect('pts-package-page', package_name=package)


class UnsubscribeAllView(LoginRequiredMixin, View):
    """
    The view unsubscribes the currently logged in user from all packages.
    If an optional ``email`` POST parameter is provided, only removes all
    subscriptions for the given emails.
    """
    def post(self, request):
        user = request.user
        if 'email' not in request.POST:
            emails = user.emails.all()
        else:
            emails = user.emails.filter(email__in=request.POST.getlist('email'))

        # Remove all the subscriptions
        Subscription.objects.filter(email_user__in=emails).delete()

        if request.is_ajax():
            return render_to_json_response({
                'status': 'ok',
            })
        else:
            if 'next' in request.POST:
                return redirect(request.POST['next'])
            else:
                return redirect('pts-index')


class ChooseSubscriptionEmailView(LoginRequiredMixin, View):
    """
    Lets the user choose which email to subscribe to a package with.
    This is an alternative view when JS is disabled and the appropriate choice
    cannot be offered in a popup.
    """
    template_name = 'accounts/choose-email.html'
    def get(self, request):
        if 'package' not in request.GET:
            raise Http404

        return render(request, self.template_name, {
            'package': request.GET['package'],
            'emails': request.user.emails.all(),
        })


class ModifyKeywordsView(LoginRequiredMixin, View):
    """
    Lets the logged in user modify his default keywords or
    subscription-specific keywords.
    """
    def get_keywords(self, keywords):
        """
        :returns: :class:`Keyword <pts.core.models.Keyword>` instances for the
            given keyword names.
        """
        return Keyword.objects.filter(name__in=keywords)

    def modify_default_keywords(self, email, keywords):
        try:
            email_user = self.user.emails.get(email=email)
            email_user = email_user.emailuser
        except (EmailUser.DoesNotExist, UserEmail.DoesNotExist):
            return HttpResponseForbidden()

        email_user.default_keywords = self.get_keywords(keywords)

        return self.render_response()

    def modify_subscription_keywords(self, email, package, keywords):
        try:
            email_user = self.user.emails.get(email=email)
            email_user = email_user.emailuser
        except (EmailUser.DoesNotExist, UserEmail.DoesNotExist):
            return HttpResponseForbidden()

        subscription = get_object_or_404(
            Subscription, email_user=email_user, package__name=package)

        subscription.keywords.clear()
        for keyword in self.get_keywords(keywords):
            subscription.keywords.add(keyword)

        return self.render_response()

    def render_response(self):
        if self.request.is_ajax():
            return render_to_json_response({
                'status': 'ok',
            })
        else:
            if 'next' in request.POST:
                return redirect(request.POST['next'])
            else:
                return redirect('pts-index')

    def post(self, request):
        if 'email' not in request.POST or 'keyword[]' not in request.POST:
            raise Http404

        self.user = request.user
        self.request = request
        email = request.POST['email']
        keywords = request.POST.getlist('keyword[]')

        if 'package' in request.POST:
            return self.modify_subscription_keywords(
                email, request.POST['package'], keywords)
        else:
            return self.modify_default_keywords(email, keywords)

    def get(self, request):
        if 'email' not in request.GET:
            raise Http404
        email = request.GET['email']

        try:
            email_user = request.user.emails.get(email=email)
        except EmailUser.DoesNotExist:
            return HttpResponseForbidden()

        if 'package' in request.GET:
            package = request.GET['package']
            subscription = get_object_or_404(
                Subscription, email_user=email_user, package__name=package)
            context = {
                'post': {
                    'email': email,
                    'package': package,
                },
                'package': package,
                'user_keywords': subscription.keywords.all(),
            }
        else:
            context = {
                'post': {
                    'email': email,
                },
                'user_keywords': email_user.default_keywords.all(),
            }

        context.update({
            'keywords': Keyword.objects.order_by('name').all(),
            'email': email,
        })

        return render(request, 'accounts/modify-subscription.html', context)
