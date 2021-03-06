.. _webserver:

Integration with a Web Server
=============================

.. _apache_webserver:

Apache2
-------

PTS can be deployed as any other Django project on Apache. For more information
you can see the following
`link <https://docs.djangoproject.com/en/dev/howto/deployment/wsgi/modwsgi/>`_.

After installing mod_wsgi, a minimal configuration would be to include a new
file in sites-available with the following settings::

    <VirtualHost *:80>
            ServerAdmin owner@pts.some.domain
            ServerName pts.some.domain

            DocumentRoot /path/to/assets/
            # To make sure all static file assets with no extension
            # (such as extracted source files) have the correct Content-Type
            DefaultType text/plain
            AddDefaultCharset utf-8

            ErrorLog ${APACHE_LOG_DIR}/pts/error.log
            LogLevel warn

            CustomLog ${APACHE_LOG_DIR}/pts/access.log combined

            WSGIScriptAlias / /path/to/pts/project/wsgi.py

            Alias /static/ /path/to/assets/static/

            <Directory /path/to/pts/project>
                    <Files wsgi.py>
                            Order allow,deny
                            Allow from all
                    </Files>
            </Directory>

            <Directory /path/to/assets/static>
                    Order deny,allow
                    Allow from all
            </Directory>
    </VirtualHost>

.. note::
   Notice the placeholder paths which need to be set according to the local
   file system.

.. note::
   In this case, the same Web server serves both the static files and runs the
   Django app.

Other mod_wsgi apache configurations are, of course, possible, for using
`mod_wsgi daemon mode <https://docs.djangoproject.com/en/dev/howto/deployment/wsgi/modwsgi/#daemon-mode>`_.

nginx and Gunicorn
------------------

The PTS does not include gunicorn in its
:data:`INSTALLED_APPS <pts.project.settings.INSTALLED_APPS>`, but there is
nothing to prevent users to include it and deploy the PTS with gunicorn
running as the WSGI server and a reverse proxy in front of it.
