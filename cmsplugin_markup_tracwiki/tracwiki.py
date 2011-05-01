import os
import inspect

from StringIO import StringIO

from genshi.builder import tag

from trac.core import *

from trac import mimeview
from trac import test
from trac import resource
from trac import web
from trac import wiki
from trac.util import datefmt, translation as trac_translation
from trac.web import main

from django.contrib.sites import models as sites_models
from django.core import urlresolvers
from django.utils import translation as django_translation

components = [
    'trac.wiki.macros.MacroListMacro',
    'trac.wiki.macros.KnownMimeTypesMacro',
    'trac.wiki.macros.ImageMacro',
    'trac.wiki.macros.PageOutlineMacro',
    'cmsplugin_markup_tracwiki.tracwiki.DjangoResource',
]

TRACWIKI_HEADER_OFFSET = 1

class DjangoEnvironment(test.EnvironmentStub):
    """A Django environment for Trac."""
    
    def __init__(self):
        super(DjangoEnvironment, self).__init__(enable=components)
        
        for c in components:
            module_and_class = c.rsplit('.', 1)
            if len(module_and_class) == 1:
                __import__(name=module_and_class[0])
            else:
                __import__(name=module_and_class[0], fromlist=[module_and_class[1]])

        self.href = web.href.Href(urlresolvers.reverse('pages-root'))
       
        # TODO: Use Django logging facilities?
        # TODO: Sync activated locales with Django?

    def set_abs_href(self, request):
        site = sites_models.Site.objects.get_current() if sites_models.Site._meta.installed else sites_models.RequestSite(request)

        server_port = request.META.get('SERVER_PORT', '80')
        if request.is_secure():
            self.abs_href = web.href.Href('https://' + site.domain + (':' + server_port if server_port != '443' else '') + self.href())
        else:
            self.abs_href = web.href.Href('http://' + site.domain + (':' + server_port if server_port != '80' else '') + self.href())

class DjangoRequest(web.Request):
    def __init__(self, request):
        super(DjangoRequest, self).__init__(request.META, self._start_response)

        self.django_request = request
        
        self.perm = main.FakePerm()
        self.session = main.FakeSession()

        if request.user.is_authenticated():
            self.session['name'] = request.user.get_full_name()
            self.session['email'] = request.user.email

        self.callbacks.update({
            'authname': self._get_authname,
            'tz': self._get_timezone,
            'locale': self._get_locale,
        })
   
    def _get_authname(self, req):
        if self.django_request.user.is_authenticated():
            return self.django_request.user.username
        else:
            return 'anonymous'

    def _get_locale(self, req):
        if trac_translation.has_babel:
            return trac_translation.get_negotiated_locale([django_translation.get_language()])

    def _get_timezone(self, req):
        # Django sets TZ environment variable
        return datefmt.localtz

    def _write(self, data):
        if not data:
            return

        # TODO: Use Django logging facilities?
        sys.stderr.write(data)
        sys.stderr.write("\n")
    
    def _start_response(self, status, headers, exc_info):
        if exc_info:
            raise exc_info[0], exc_info[1], exc_info[2]

        # TODO: Use Django logging facilities?
        sys.stderr.write("Trac rasponse data, %s:\n", status)

        return self._write

class DjangoFormatter(wiki.formatter.Formatter):
    def _parse_heading(self, match, fullmatch, shorten):
        (depth, heading, anchor) = super(DjangoFormatter, self)._parse_heading(match, fullmatch, shorten)
        depth = min(depth + TRACWIKI_HEADER_OFFSET, 6)
        return (depth, heading, anchor)
    
    def _make_lhref_link(self, match, fullmatch, rel, ns, target, label):
        """We override _make_lhref_link to make 'cms' namespace default."""
        return super(DjangoFormatter, self)._make_lhref_link(match, fullmatch, rel, ns or 'cms', target, label)

class DjangoResource(Component):
    implements(resource.IResourceManager, wiki.IWikiSyntaxProvider)
    
    def _format_link(self, formatter, ns, target, label, fullmatch=None):
        link, params, fragment = formatter.split_link(target)
        page = resource.Resource('cms', link)
        href = resource.get_resource_url(self.env, page, formatter.href)
        title = resource.get_resource_name(self.env, page)
        return tag.a(label, title=title, href=href + params + fragment)
    
    # IResourceManager methods
    
    def get_resource_realms(self):
        yield 'cms'
    
    # TODO: Define get_resource_url which can work also on Django CMS page names (so that links can survive moving pages around)
    # TODO: Check if it has to end with /
    # TODO: Revert Django URL
    def get_resource_url(self, resource, href, **kwargs):
        if resource.id:
            path = resource.id.split('/')
        else:
            path = []
        return href(*path, **kwargs)
    
    def get_resource_description(self, resource, format='default', context=None, **kwargs):
        # TODO: Return page name
        return 'Name'
    
    # TODO: Check if it has to end with /
    def resource_exists(self, resource):
        return True
    
    # IWikiSyntaxProvider methods
    
    def get_wiki_syntax(self):
        return
    
    def get_link_resolvers(self):
        yield ('cms', self._format_link)

# TODO: Use content from filer for [[Image]] and attachments (attachments could be file and image plugins in the same placeholder)
# TODO: Relative links [..] should traverse Django CMS hierarchy
# TODO: Make Trac and Django CMS caching interoperate (how does dynamic macros currently behave?)
# TODO: Does request really have URL we want (for example in admin URL is not the URL of a resulting page)

class Markup(object):
    name = 'Trac wiki'
    identifier = 'tracwiki'

    def __init__(self, *args, **kwargs):
        self.env = DjangoEnvironment()

    def _get_request(self):
        frame = inspect.currentframe()
        try:
            while frame.f_back:
                frame = frame.f_back
                request = frame.f_locals.get('request')
                if request:
                    return request
        finally:
            del frame
        return None

    def parse(self, value):
        request = self._get_request()
        self.env.set_abs_href(request)
        req = DjangoRequest(request)
        res = resource.Resource('cms', 'test') # TODO: Get ID from request (and version?)
        ctx = mimeview.Context.from_request(req, res)
        out = StringIO()
        DjangoFormatter(self.env, ctx).format(value, out)
        return out.getvalue()
