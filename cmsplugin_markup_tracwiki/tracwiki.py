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

from cms import models as cms_models
from cms import utils as cms_utils
from cms.utils import moderator

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
            # TODO: Use django-cms function (get_language_from_request) instead? Why req is not used?
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
        page = resource.Resource(ns, link)
        try:
            href = resource.get_resource_url(self.env, page, formatter.href)
            title = resource.get_resource_name(self.env, page)
            return tag.a(label, title=title, href=href + params + fragment)
        except resource.ResourceNotFound:
            return tag.a(label + '?', class_='missing', href=target, rel='nofollow')
    
    # IResourceManager methods
    
    def get_resource_realms(self):
        yield 'cms'
    
    def get_resource_url(self, res, href, **kwargs):
        if res.id is None:
            return href(**kwargs)
        else:
            try:
                request = _get_django_request()
                lang = cms_utils.get_language_from_request(request)
                link = self._get_page(res.id).get_absolute_url(language=lang)
            except cms_models.Page.DoesNotExist:
                try:
                    # Test again as request.current_page could be None
                    if not res.id:
                        return href(**kwargs)
                    link = urlresolvers.reverse(res.id)
                except urlresolvers.NoReverseMatch as e:
                    raise resource.ResourceNotFound(e)

            args = [link]
            if link.endswith('/') and not link.strip('/') == '':
                # We add an empty link component at the end to force trailing slash
                args.append('')
            return href(*args, **kwargs)
    
    def get_resource_description(self, res, format='default', context=None, **kwargs):
        if res.id is None:
            return ''
        else:
            try:
                request = _get_django_request()
                lang = cms_utils.get_language_from_request(request)
                return self._get_page(res.id, context).get_title(language=lang)
            except cms_models.Page.DoesNotExist:
                return ''
    
    def resource_exists(self, res):
        if res.id is None:
            return False
        else:
            try:
                self._get_page(res.id)
                return True
            except cms_models.Page.DoesNotExist:
                pass

            # Test again as request.current_page could be None
            if not res.id:
                return False
            
            try:
                urlresolvers.reverse(res.id)
                return True
            except urlresolvers.NoReverseMatch:
                return False

    def _get_page(self, page_id, context=None):
        if context is not None:
            request = context.req.django_request
        else:
            request = _get_django_request()
        if not page_id:
            # cms.middleware.page.CurrentPageMiddleware is required for this
            if request.current_page:
                return request.current_page
            else:
                # It is not really necessary that current page is known
                # TODO: Check what happens on blog
                # TODO: Check what happens with preview
                raise cms_models.Page.DoesNotExist()
        else:
            return moderator.get_page_queryset(request).get(reverse_id=page_id)
    
    # IWikiSyntaxProvider methods
    
    def get_wiki_syntax(self):
        return
    
    def get_link_resolvers(self):
        yield ('cms', self._format_link)

# TODO: Use content from filer for [[Image]] and attachments (attachments could be file and image plugins in the same placeholder)
# TODO: Relative links [..] should traverse Django CMS hierarchy
# TODO: Make Trac and Django CMS caching interoperate (how does dynamic macros currently behave?)
# TODO: Does request really have URL we want (for example in admin URL is not the URL of a resulting page)
# TODO: Wrap some Django template tags into macros (url for example)

class Markup(object):
    name = 'Trac wiki'
    identifier = 'tracwiki'

    def __init__(self, *args, **kwargs):
        self.env = DjangoEnvironment()

    def parse(self, value):
        request = _get_django_request()
        self.env.set_abs_href(request)
        req = DjangoRequest(request)
        res = resource.Resource('cms', 'test') # TODO: Get ID from request (and version?)
        ctx = mimeview.Context.from_request(req, res)
        out = StringIO()
        DjangoFormatter(self.env, ctx).format(value, out)
        return out.getvalue()

def _get_django_request():
    frame = inspect.currentframe()
    try:
        while frame.f_back:
            frame = frame.f_back
            request = frame.f_locals.get('request')
            if request:
                # TODO: Check if it is really a Django request
                return request
    finally:
        del frame
    return None

