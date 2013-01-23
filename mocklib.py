import os
import sys
from os.path import basename, splitext, isdir, isfile
import urllib
import tempfile
import shutil

class MockTemp(object):
    DEFAULT_SITE_CONFIG = '/etc/mock/site-defaults.cfg'
    DEFAULT_LOGGING_CONFIG = '/etc/mock/logging.ini'

    def __init__(self, logging, mock_opts=[]):
        # Create mock tempfiles
        self.logging = logging
        self.mock_opts = mock_opts
        self.config_tempdir = None
        self.config_tempfile = None

    def apply_config(self, config):
        self.cleanup()

        self.config = config
        self.config_tempdir = tempfile.mkdtemp(prefix='rpmfab-', dir='/tmp')
        self.config_tempfile = tempfile.NamedTemporaryFile(prefix='mock-',
            suffix='.cfg', dir=self.config_tempdir)

        # Write config file data to temp file
        self.logging.info("reading config file '%s'" % (self.config))
        config_file = urllib.urlopen(self.config)
        self.config_tempfile.write(config_file.read())
        self.config_tempfile.flush()
        config_file.close()
        self.logging.info("created temporary mock config '%s'" %
            (self.config_tempfile.name))

        # Add default config files
        if isfile(MockTemp.DEFAULT_SITE_CONFIG):
            shutil.copy2(MockTemp.DEFAULT_SITE_CONFIG, self.config_tempdir)
        else:
            MockTemp._generate_default_config(self.config_tempdir)
        if isfile(MockTemp.DEFAULT_LOGGING_CONFIG):
            shutil.copy2(MockTemp.DEFAULT_LOGGING_CONFIG, self.config_tempdir)
        else:
            shutil.copy2('logging.ini', self.config_tempdir)

        # Must set configdir to find temporary mock config file
        self.mock_opts.append('--configdir=%s' % (self.config_tempdir))
        self.chroot = splitext(basename(self.config_tempfile.name))[0]

    def cleanup(self):
        if self.config_tempfile:
            self.config_tempfile.close()
        if self.config_tempdir:
            shutil.rmtree(self.config_tempdir)

    @staticmethod
    def _generate_default_config(dir):
        config_filename = os.path.join(dir, 'site-defaults.cfg')
        f = open(config_filename, 'wb')
        f.write("""# Generated config file
# DO NOT EDIT
config_opts['plugin_conf']['yum_repo_enable'] = True
    """)
        f.close()
        return config_filename
