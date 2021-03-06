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
Settings for the admin panel for the models defined in the :mod:`pts.core`
app.
"""
from __future__ import unicode_literals
from django.contrib import admin
from django import forms
from .models import Repository
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from pts.core.models import Architecture
from pts.core.retrieve_data import retrieve_repository_info
from pts.core.retrieve_data import InvalidRepositoryException
import requests


def validate_sources_list_entry(value):
    """
    A custom validator for the sources.list entry form field.

    Makes sure that it follows the correct syntax and that the specified Web
    resource is available.

    :param value: The value of the sources.list entry which needs to be
        validated

    :raises ValidationError: Giving the validation failure message.
    """
    split = value.split(None, 3)
    if len(split) < 3:
        raise ValidationError("Invalid syntax: all parts not found.")

    repository_type, url, name = split[:3]
    if repository_type not in ('deb', 'deb-src'):
        raise ValidationError(
            "Invalid syntax: the line must start with deb or deb-src")

    url_validator = URLValidator()
    try:
        url_validator(url)
    except ValidationError:
        raise ValidationError("Invalid repository URL")

    # Check whether a Release file even exists.
    if url.endswith('/'):
        url = url.rstrip('/')
    try:
        response = requests.head(Repository.release_file_url(url, name))
    except requests.exceptions.Timeout as e:
        raise ValidationError(
            "Invalid repository: Could not connect to {url}."
            " Request timed out.".format(url=url))
    except requests.exceptions.ConnectionError as e:
        raise ValidationError(
            "Invalid repository: Could not connect to {url} due to a network"
            " problem. The URL may not exist or is refusing to receive"
            " connections.".format(url=url))
    except requests.exceptions.HTTPError as e:
        raise ValidationError(
            "Invalid repository:"
            " Received an invalid HTTP response from {url}.".format(url=url))
    except:
        raise ValidationError(
            "Invalid repository: Could not connect to {url}".format(url=url))

    if response.status_code != 200:
        raise ValidationError(
            "Invalid repository: No Release file found. "
            "received a {status_code} HTTP response code.".format(
                status_code=response.status_code))


class RepositoryAdminForm(forms.ModelForm):
    """
    A custom :class:`ModelForm <django.forms.ModelForm>` used for creating and
    modifying :class:`Repository <pts.core.models.Repository>` model instances.

    The class adds the ability to enter only a sources.list entry describing
    the repository and other properties of the repository are automatically
    filled in by using the ``Release`` file of the repository.
    """
    #: The additional form field which allows entring the sources.list entry
    sources_list_entry = forms.CharField(
        required=False,
        help_text="You can enter a sources.list formatted entry and have the"
                  " rest of the fields automatically filled by using the "
                  "Release file of the repository.",
        max_length=200,
        widget=forms.TextInput(attrs={
            'size': 100,
        }),
        validators=[
            validate_sources_list_entry,
        ]
    )

    class Meta:
        model = Repository
        exclude = (
            'position',
            'source_packages',
        )

    def __init__(self, *args, **kwargs):
        super(RepositoryAdminForm, self).__init__(*args, **kwargs)
        # Fields can't be required if we want to support different methods of
        # setting their value through the same form (sources.list and directly)
        # The clean method makes sure that they are set in the end.
        # So, save originally required fields in order to check them later.
        self.original_required_fields = []
        for name, field in self.fields.items():
            if field.required:
                field.required = False
                self.original_required_fields.append(name)
        # These fields are always required
        self.fields['name'].required = True
        self.fields['shorthand'].required = True

    def clean(self, *args, **kwargs):
        """
        Overrides the :meth:`clean <django.forms.ModelForm.clean>` method of the
        parent class to allow validating the form based on the sources.list
        entry, not only the model fields.
        """
        self.cleaned_data = super(RepositoryAdminForm, self).clean(*args, **kwargs)
        if 'sources_list_entry' not in self.cleaned_data:
            # Sources list entry was given to the form but it failed
            # validation.
            return self.cleaned_data
        # Check if the entry was not even given
        if not self.cleaned_data['sources_list_entry']:
            # If not, all the fields required by the model must be found
            # instead
            for name in self.original_required_fields:
                self.fields[name].required = True
            self._clean_fields()
        else:
            # If it was given, need to make sure now that the Relase file
            # contains usable data.
            try:
                repository_info = retrieve_repository_info(
                    self.cleaned_data['sources_list_entry'])
            except InvalidRepositoryException as e:
                raise ValidationError("The Release file was invalid.")
            # Use the data to construct a Repository object.
            self.cleaned_data.update(repository_info)
            # Architectures have to be matched with their primary keys
            self.cleaned_data['architectures'] = [
                Architecture.objects.get(name=architecture_name).pk
                for architecture_name in self.cleaned_data['architectures']
                if Architecture.objects.filter(name=architecture_name).exists()
            ]

        return self.cleaned_data


class RepositoryAdmin(admin.ModelAdmin):
    """
    Actual configuration for the :class:`Repository <pts.core.models.Repository>`
    admin panel.
    """
    class Media:
        """
        Add extra Javascript resources to the page in order to support
        drag-and-drop repository position modification.
        """
        js = (
            'js/jquery-2.0.3.min.js',
            'js/jquery-ui.min.js',
            'js/admin-list-reorder.js',
        )

    form = RepositoryAdminForm

    #: Sections the form in three main parts
    fieldsets = [
        (None, {
            'fields': [
                'name',
                'shorthand',
            ]
        }),
        ('sources.list entry', {
            'fields': [
                'sources_list_entry',
            ]
        }),
        ('Repository information', {
            'fields': [
                field
                for field in RepositoryAdminForm().fields.keyOrder
                if field not in ('sources_list_entry', 'name', 'shorthand')
            ]
        })
    ]

    #: Gives a list of fields which should be displayed as columns in the
    #: list of existing :class:`Repository <pts.core.models.Repository>`
    #: instances.
    list_display = (
        'name',
        'shorthand',
        'codename',
        'uri',
        'components_string',
        'architectures_string',
        'default',
        'optional',
        'binary',
        'source',
        'position',
    )

    ordering = (
        'position',
    )

    list_editable = (
        'position',
    )

    def components_string(self, obj):
        """
        Helper method for displaying Repository objects.
        Turns the components list into a display-friendly string.

        :param obj: The repository whose components are to be formatted
        :type obj: :class:`Repository <pts.core.models.Repository>`
        """
        return ' '.join(obj.components)
    components_string.short_description = 'components'

    def architectures_string(self, obj):
        """
        Helper method for displaying Repository objects.
        Turns the architectures list into a display-friendly string.

        :param obj: The repository whose architectures are to be formatted
        :type obj: :class:`Repository <pts.core.models.Repository>`
        """
        return ' '.join(map(str, obj.architectures.all()))
    architectures_string.short_description = 'architectures'


admin.site.register(Repository, RepositoryAdmin)
