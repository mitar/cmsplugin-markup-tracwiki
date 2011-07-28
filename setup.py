import os

from setuptools import setup, find_packages

VERSION = '0.1.1'

setup(
    name = 'cmsplugin-markup-tracwiki',
    version = VERSION,
    description = 'Trac wiki engine integration with Django CMS as a plugin for cmsplugin-markup.',
    long_description = open(os.path.join(os.path.dirname(__file__), 'README.txt')).read(),
    author = 'Mitar',
    author_email = 'mitar.django@tnode.com',
    url = 'https://bitbucket.org/mitar/cmsplugin-markup-tracwiki',
    license = 'GPLv3',
    packages = find_packages(),
    classifiers = [
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Framework :: Django',
    ],
    include_package_data = True,
    zip_safe = False,
    install_requires = [
        'Django>=1.2',
        'trac>=0.12',
        'cmsplugin-markup>=0.1',
    ],
)
