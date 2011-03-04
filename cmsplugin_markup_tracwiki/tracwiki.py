import os
from StringIO import StringIO

from genshi.builder import tag

from trac.core import *

from trac import mimeview
from trac import test
from trac import resource
from trac import web
from trac import wiki
from trac.web import main

components = [
    'trac.wiki.macros.MacroListMacro',
    'cmsplugin_markup_tracwiki.tracwiki.DjangoResource',
]

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
        
        # TODO: Configure with Django sites
        self.href = web.href.Href('/')
        self.abs_href = web.href.Href('http://localhost/')

        # TODO: Use Django logging facilities?
        # TODO: Sync activated locales with Django?

class DjangoRequest(web.Request):
    def __init__(self):
        # TODO: Get from real the request
        environ = {
            'SERVER_PORT': 80,
            'wsgi.url_scheme': 'http',
            'SERVER_NAME': 'localhost',
        }
        
        super(DjangoRequest, self).__init__(environ, self._start_response)
        
        self.perm = main.FakePerm()
        self.sesion = main.FakeSession()
            
        #'authname': self.authenticate,
        #'chrome': chrome.prepare_request,
        #'hdf': self._get_hdf,
        #'locale': self._get_locale,
        #'tz': self._get_timezone,
        #'form_token': self._get_form_token,
    
    def _write(self, data):
        if not data:
            return
        sys.stderr.write(data)
        sys.stderr.write("\n")
    
    def _start_response(self, status, headers, exc_info):
        if exc_info:
            raise exc_info[0], exc_info[1], exc_info[2]
        sys.stderr.write("Trac rasponse data, %s:\n", status)
        return self._write

class DjangoFormatter(wiki.formatter.Formatter):
    def _parse_heading(self, match, fullmatch, shorten):
        (depth, heading, anchor) = super(DjangoFormatter, self)._parse_heading(match, fullmatch, shorten)
        depth = min(depth + 1, 6)
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
        def cmspagename_with_label_link(formatter, match, fullmatch):
            target = formatter._unquote(fullmatch.group('cms_target'))
            label = fullmatch.group('cms_label')
            link, params, fragment = formatter.split_link(target)
            exist = resource.resource_exists(self.env, resource.Resource('cms', link))
            if exist is None:
                return match
            elif exist:
                return self._format_link(formatter, 'cms', target, label.strip(), fullmatch)
            else:
                tag.a(label + '?', class_='missing', href=target, rel='nofollow')

        yield (r"!?\[(?P<cms_target>%s|[^/\s]\S*)\s+(?P<cms_label>%s|[^\]]+)\]" % (wiki.parser.WikiParser.QUOTED_STRING, wiki.parser.WikiParser.QUOTED_STRING), cmspagename_with_label_link)

    def get_link_resolvers(self):
        yield ('cms', self._format_link)

class Markup(object):
    name = 'Trac wiki'
    identifier = 'tracwiki'

    def __init__(self, *args, **kwargs):
        self.env = DjangoEnvironment()

    def parse(self, value):
        req = DjangoRequest()
        res = resource.Resource('cms', 'test') # TODO: Get ID from request (and version?)
        ctx = mimeview.Context.from_request(req, res)
        out = StringIO()
        DjangoFormatter(env, ctx).format(value, out)
        return out.getvalue()
