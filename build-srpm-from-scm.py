#!/usr/bin/python -tt

import datetime
import glob
import logging
import optparse
import os
import os.path
import re
import rpm
import shutil
import subprocess
import sys
import tempfile
import urlparse
import urllib

__version__ = '0.1'
_DIR_STACK = []
_ORIG_EXECUTABLE = os.path.abspath(sys.argv[0])


def pushd(destdir):
    _DIR_STACK.append(os.getcwd())
    os.chdir(destdir)


def popd():
    os.chdir(_DIR_STACK.pop())


def _split_repo_url(url):
    if '?' in url:
        (basic_url, __, extras) = url.partition('?')
        (__,        __, commit) = extras.partition('#')
    else:
        (basic_url, __, commit) = url.partition('#')
    return (basic_url, commit or None)


class Repo(object):
    def __init__(self, url, ref):
        self.url = url
        self._ref = ref
        self.rev = None
        if os.path.exists(url):
            self.tree = url  # local filesystem
        else:
            self.tree = None

    def checkout(self, destdir):
        raise NotImplementedError()

    def record_rev(self):
        raise NotImplementedError()

    def create_tarball(self, tarball_name, destdir):
        raise NotImplementedError()

    def friendly_rev(self):
        return self.rev


class GitRepo(Repo):
    def checkout(self, destdir):
        if self.tree:
            return
        # destdir is a pre-existing directory in which a source checkout goes
        # e.g. mydir -> repo goes in mydir/myrepo
        self.tree = os.path.join(destdir, os.path.basename(self.url))
        if self.tree.endswith('.git'):
            self.tree = self.tree[:-4]
        if os.path.exists(self.tree):
            logging.info('Cleaning dir %s', self.tree)
            shutil.rmtree(self.tree)
        logging.info('Cloning git repo %s to %s', self.url, self.tree)
        args = ['git', 'clone', '-q', '--recursive', self.url, self.tree]
        logging.debug("Executing ``%s''", ' '.join(args))
        subprocess.check_call(args)

        if self._ref:
            logging.info('Checking out ref %s', self._ref)
            pushd(self.tree)
            args = ['git', 'checkout', '-q', self._ref]
            logging.debug("Executing ``%s''", ' '.join(args))
            subprocess.check_call(args)
            popd()

    def record_rev(self):
        if not self.tree:
            raise RuntimeError('checkout must precede record_rev')
        pushd(self.tree)
        args = ['git', 'rev-parse', 'HEAD']
        logging.debug("Executing ``%s''", ' '.join(args))
        git_revparse = subprocess.Popen(args, stdout=subprocess.PIPE)
        assert git_revparse.wait() == 0
        popd()
        self.rev = git_revparse.stdout.read().strip()
        logging.debug('git ref %s is %s', self._ref or 'HEAD', self.rev)

    def create_tarball(self, tarball_name, destdir):
        if not self.tree:
            raise RuntimeError('checkout call must precede create_tarball')
        # git archive requires a trailing / to make it a dir
        topdir  = tarball_name.rsplit('.tar', 1)[0] + '/'
        tarball = os.path.abspath(os.path.join(destdir, tarball_name))
        logging.debug('Creating tarball %s', tarball)

        pushd(self.tree)
        args = [os.path.join(os.path.dirname(_ORIG_EXECUTABLE),
                             'git-archive-recursive.sh'),
                'HEAD', '--prefix', topdir, '-o', tarball]
        logging.debug("Executing ``%s''", ' '.join(args))
        subprocess.check_call(args)
        popd()

    def friendly_rev(self):
        return self.rev[:8]


class BzrRepo(Repo):
    def checkout(self, destdir):
        if self.tree:
            return
        # destdir is a pre-existing directory in which a source checkout goes
        # e.g. mydir -> repo goes in mydir/myrepo
        self.tree = os.path.join(destdir, os.path.basename(self.url))
        args = ['bzr', 'co', '-q', '--lightweight']
        if self._ref:
            logging.info('Checking out bzr repo %s rev %s to %s',
                         self.url, self._ref, self.tree)
            args.extend(['-r', self._ref])
        else:
            logging.info('Checking out bzr repo %s to %s', self.url, self.tree)
        args.extend([self.url, self.tree])
        logging.debug("Executing ``%s''", ' '.join(args))
        subprocess.check_call(args)

    def record_rev(self):
        if not self.tree:
            raise RuntimeError('checkout must precede record_rev')
        args = ['bzr', 'revno', '-q', self.tree]
        logging.debug("Executing ``%s''", ' '.join(args))
        bzr_revno = subprocess.Popen(args, stdout=subprocess.PIPE)
        assert bzr_revno.wait() == 0
        self.rev = bzr_revno.stdout.read().strip()
        if self._ref:
            logging.debug('bzr rev %s is %s', self._ref, self.rev)
        else:
            logging.debug('bzr tip is %s', self.rev)

    def create_tarball(self, tarball_name, destdir):
        if not self.tree:
            raise RuntimeError('checkout call must precede create_tarball')
        tarball = os.path.abspath(os.path.join(destdir, tarball_name))
        logging.debug('Creating tarball %s', tarball)

        args = ['bzr', 'export', '-q', tarball, self.tree]
        logging.debug("Executing ``%s''", ' '.join(args))
        subprocess.check_call(args)


def build_repo(url):
    if not url:
        return None
    (basic_url, rev) = _split_repo_url(url)
    if '://' in basic_url:
        scheme = basic_url.split('://', 1)[0]
        if scheme in ['bzr', 'bzr+ssh']:
            return BzrRepo(basic_url, rev)
        elif scheme in ['git', 'git+ssh']:
            return GitRepo(basic_url, rev)
        else:
            raise ValueError('Unsupported repo scheme: ' + repr(scheme))
    elif basic_url.startswith('lp:'):
        return BzrRepo(basic_url, rev)
    else:
        # assume a local repo exists
        path = os.path.abspath(basic_url)
        if os.path.exists(os.path.join(path, '.bzr')):
            return BzrRepo(path, rev)
        elif os.path.exists(os.path.join(path, '.git')):
            return GitRepo(path, rev)
        else:
            raise ValueError('Unrecognized local repo: ' + repr(path))


class SRPMBuilder(object):
    def __init__(self, chroot, pkg_repo, sources=None, mock_opts=None):
        self.chroot    = chroot
        self.pkg_repo  = build_repo(pkg_repo)
        self.mock_opts = mock_opts
        self.specfile  = None
        self.sources   = {}
        for (i, url) in sources or []:
            self.sources[int(i)] = build_repo(url)

    def checkout_packaging_repo(self, destdir):
        """
        Create a checkout of self.pkg_repo inside of destdir, then chdir into
        it.
        """
        self.pkg_repo.checkout(destdir)
        logging.info('Packaging repo checked out to %s', self.pkg_repo.tree)
        os.chdir(self.pkg_repo.tree)

        specs = glob.glob('*.spec')
        assert len(specs) == 1
        self.specfile = os.path.abspath(specs[0])
        logging.info('Using spec file %s', self.specfile)

    def checkout_sources(self, destdir):
        for source in self.sources.itervalues():
            if not source.tree:
                source.checkout(destdir)
            source.record_rev()

    def add_macros_to_specfile(self, macros):
        """
        Read a spec file, looking for macro expansions that correspond to
        keys in a dict.  For each match, add a %global macro definition to
        the top of the spec file.
        """
        with open(self.specfile) as original_file:
            original = original_file.read()
        applicable_macros = {}
        for (key, val) in macros.iteritems():
            for fmt in ['%{{{0}}}', '%{{?{0}}}', '%{{!?{0}}}', '%{0}']:
                if fmt.format(key) in original:
                    applicable_macros[key] = val
                    break
        applicable_macros = self.substitute_magic_values(applicable_macros)
        if applicable_macros:
            logging.info('Adding %i macro(s) to spec file: %s',
                         len(applicable_macros),
                         ', '.join(applicable_macros.keys()))
            logging.debug('Macro values: %s', str(applicable_macros))
            modified = tempfile.NamedTemporaryFile(delete=False)
            try:
                for (key, val) in applicable_macros.iteritems():
                    modified.write('%global {0} {1}\n'.format(key, val))
                modified.write('\n')
                modified.write(original)
            except:
                os.remove(modified.name)
                raise
            finally:
                modified.close()
            shutil.move(modified.name, self.specfile)

    def substitute_magic_values(self, macros):
        utcnow = datetime.datetime.utcnow()
        newmacros = {}
        for (key, val) in macros.iteritems():
            if '@DATE@' in val:
                val = val.replace('@DATE@', utcnow.strftime('%Y%m%d'))
            if '@DATETIME@' in val:
                val = val.replace('@DATETIME@', utcnow.strftime('%Y%m%d%H%M'))
            for srev in set(re.findall(r'@REV[0-9]+@', val)):
                n = int(srev[4:-1])  # the numeric part
                if n in self.sources:
                    val = val.replace(srev, self.sources[n].friendly_rev())
                else:
                    logging.warn('-s%i not supplied at command line; not '
                                 'substituting %s in macro %s', n, repr(srev),
                                 repr(key))
            newmacros[key] = val
        return newmacros

    def build_tarballs(self):
        tset = rpm.ts()
        spec = tset.parseSpec(self.specfile)
        spec_sources = dict([(src[1], src[0]) for src in spec.sources
                             if src[2] == 1])

        for (i, source) in self.sources.iteritems():
            if i in spec_sources:
                tarball = os.path.basename(spec_sources[i])
                logging.info('Building Source%i: %s from %s', i, tarball,
                             source.tree)
                source.create_tarball(tarball, '.')
            else:
                logging.warn('Spec file does not contain Source%i; skipping '
                             'tarball build for url %s', i, source.url)

    def fetch_spec_sources(self):
        tset = rpm.ts()
        spec = tset.parseSpec(self.specfile)
        for source in spec.sources:
            srcuri  = source[0]
            srcno   = source[1]
            srcname = os.path.basename(srcuri)
            if not os.path.exists(srcname):
                if urlparse.urlparse(srcuri)[0]:
                    logging.info('Downloading Source%i: %s from %s', srcno,
                                 srcname, srcuri)
                    fetch_file(srcuri)
                else:
                    logging.warn('Unable to obtain Source%i: %s', srcno,
                                 srcname)

    def build_srpm(self, resultdir):
        logging.info('Building source RPM in %s using chroot %s', resultdir,
                     self.chroot)
        args = ['/usr/bin/mock', '-r', self.chroot, '--resultdir', resultdir]
        args.extend(self.mock_opts or [])
        args.extend(['--buildsrpm', '--spec', self.specfile, '--sources',
                     os.getcwd()])
        logging.debug("Executing ``%s''", ' '.join(args))
        subprocess.check_call(args)
        assert len(glob.glob(os.path.join(resultdir, '*.src.rpm'))) == 1

    def _get_nvr(self):
        tset = rpm.ts()
        tset.parseSpec(self.specfile)
        name    = rpm.expandMacro('%{name}')
        version = rpm.expandMacro('%{version}')
        release = rpm.expandMacro('%{release}')
        return (name, version, release)


def fetch_file(url, destdir='.'):
    """
    Fetch a file and deposit it in destdir.

    If url is a path to a local file it is copied.  Otherwise it is fetched
    using the necessary transport (e.g. HTTP) if possible.
    """
    filename = os.path.basename(urlparse.urlparse(url)[2])
    destfile = os.path.join(destdir, filename)
    if urlparse.urlparse(url)[0]:
        logging.debug('Downloading %s', url)
        urllib.urlretrieve(url, destfile)
    else:
        logging.debug('Copying local file %s', url)
        shutil.copy2(url, destfile)


def _parse_macro_def(option, opt, value, parser):
    if not '=' in value:
        parser.error('{0} value "{1}" must have form KEY=VALUE'.format(opt,
                                                                       value))
    (key, val) = value.split('=', 1)
    parsed_macros = getattr(parser.values, option.dest, {}) or {}
    parsed_macros[key] = val
    setattr(parser.values, option.dest, parsed_macros)


def parse_cli_args():
    usage = ('%prog [-d] [-m KEY=VALUE ...] [-sN SRC_REPO ...] '
             '[--mock-options OPTS] -r CHROOT -w WORKSPACE -o RESULTDIR '
             'PKG_REPO')
    parser = optparse.OptionParser(usage=usage,
                                   version='%prog {0}'.format(__version__))
    parser.add_option('-d', '--debug', dest='loglevel', action='store_const',
                      const=logging.DEBUG, default=logging.INFO)
    parser.add_option('-m', '--macro', metavar='KEY=VALUE', dest='macros',
                      action='callback', callback=_parse_macro_def,
                      type='string', default={},
                      help='define a macro in the spec file')
    parser.add_option('-s', metavar='N URL', dest='sources', action='append',
                      nargs=2, default=[],
                      help=('build a tarball for spec file source N from '
                            'revision control'))
    parser.add_option('-r', '--chroot', default=None,
                      help='mock chroot to use')
    parser.add_option('-w', '--workspace', default=None,
                      help='directory to use as a workspace')
    parser.add_option('-o', '--resultdir', default=None,
                      help='directory to place results into')
    parser.add_option('--mock-options', metavar='OPTS', default='',
                      help='options to pass to mock')
    (options, args) = parser.parse_args()
    if len(args) != 1:
        parser.error('exactly 1 positional argument is required')
    if not options.chroot:
        parser.error('chroot name must be specified with -r')
    if not options.workspace:
        parser.error('working directory must be specified with -w')
    if not options.resultdir:
        parser.error('result directory must be specified with -o')
    return (options, args)


def main():
    (options, args) = parse_cli_args()
    pkg_repo  = args[0]
    resultdir = os.path.abspath(options.resultdir)
    workspace = os.path.abspath(options.workspace)
    builddir  = os.path.join(workspace, 'builddir')
    logging.basicConfig(stream=sys.stdout, level=options.loglevel,
                        format='%(asctime)-15s [%(levelname)s] %(message)s')

    builder = SRPMBuilder(options.chroot, pkg_repo,
                          sources=options.sources,
                          mock_opts=options.mock_options.split())
    if not os.path.exists(workspace):
        os.makedirs(workspace)
    if not os.path.exists(builddir):
        os.makedirs(builddir)
    builder.checkout_packaging_repo(builddir)
    # We have now chdir'd into the packaging repo dir
    builder.checkout_sources(workspace)
    builder.add_macros_to_specfile(options.macros)
    builder.build_tarballs()
    builder.fetch_spec_sources()
    builder.build_srpm(resultdir)
    logging.info('Build complete; results in %s', resultdir)


if __name__ == '__main__':
    main()
