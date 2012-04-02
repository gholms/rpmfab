#!/usr/bin/python -tt

# grab *one* srpm from the previous task
# copy it into the workspace
# run mock on it, putting its logs and outputs into workspace dirs
# copy logs and outputs to 'pending' dir?

import glob
import logging
import optparse
import os.path
import subprocess
import sys

__version__ = '0.1'

def build_arch(srpm, chroot, resultdir, mock_opts=None):
    logging.info('Building RPMs in %s using chroot %s', resultdir, chroot)
    args = ['mock', '-r', chroot, '--resultdir', resultdir]
    args.extend(mock_opts or [])
    args.extend(['--rebuild', srpm])
    logging.debug("Executing ``%s''", ' '.join(args))
    subprocess.check_call(args)
    rpms = glob.glob(os.path.join(resultdir, '*.rpm'))
    assert len(rpms) > 0

def parse_cli_args():
    usage = '%prog [-d] [--mock-opts OPTS] -r CHROOT -o RESULTDIR SRPM'
    parser = optparse.OptionParser(usage=usage,
                                   version='%prog %s'.format(__version__))
    parser.add_option('-d', '--debug', dest='loglevel', action='store_const',
                      const=logging.DEBUG, default=logging.INFO)
    parser.add_option('-r', '--chroot', default=None,
                      help='mock chroot to use')
    parser.add_option('-o', '--resultdir', default=None,
                      help='directory to place results into')
    parser.add_option('--mock-options', metavar='OPTS', default='',
                      help='options to pass to mock')
    (options, args) = parser.parse_args()
    if len(args) != 1:
        parser.error('exactly 1 positional argument is required')
    if not options.chroot:
        parser.error('chroot name must be specified with -r')
    if not options.resultdir:
        parser.error('result directory must be specified with -o')
    return (options, args)

def main():
    (options, args) = parse_cli_args()
    srpm      = os.path.abspath(args[0])
    resultdir = os.path.abspath(options.resultdir)
    logging.basicConfig(stream=sys.stdout, level=options.loglevel,
                        format='%(asctime)-15s [%(levelname)s] %(message)s')

    build_arch(srpm, options.chroot, resultdir,
               mock_opts=options.mock_options.split())
    logging.info('Build complete; results in %s', resultdir)

if __name__ == '__main__':
    main()
