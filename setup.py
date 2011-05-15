from setuptools import setup

VERSION = '0.1'
PACKAGE = 'cmsplugin_markup_tracwiki'

setup(
    name = 'cmsplugin-markup-tracwiki',
    version = VERSION,
    description = 'Trac wiki engine integration with Django CMS as a plugin for cmsplugin-markup.',
    author = 'Mitar',
    author_email = 'mitar.markup@tnode.com',
    url = 'http://mitar.tnode.com/',
    license = 'GPLv3',
    packages = [PACKAGE],
    include_package_data = True,
    install_requires = [
        'Django>=1.2',
        'trac>=0.12',
        'cmsplugin-markup>=0.1',
    ],
    zip_safe = False
)
