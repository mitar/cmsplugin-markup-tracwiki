import inspect
import os
import re

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

from django import template
from django.contrib.sites import models as sites_models
from django.core import urlresolvers
from django.db.models import Q
from django.utils import translation as django_translation
from django.utils import safestring

from cms import models as cms_models
from cms import utils as cms_utils
from cms.utils import moderator

from filer.models import filemodels as filer_models

from cmsplugin_markup import plugins as markup_plugins

OBJ_ADMIN_RE_PATTERN = ur'\[\[CMSPlugin\(\s*(\d+)\s*\)\]\]'
OBJ_ADMIN_RE = re.compile(OBJ_ADMIN_RE_PATTERN)

components = [
    'cmsplugin_markup_tracwiki.tracwiki.DjangoComponent',
    'trac.wiki.macros.MacroListMacro',
    'trac.wiki.macros.KnownMimeTypesMacro',
    'trac.wiki.macros.PageOutlineMacro',
    'cmsplugin_markup_tracwiki.macros.CMSPluginMacro',
    'cmsplugin_markup_tracwiki.macros.URLMacro',
    'cmsplugin_markup_tracwiki.macros.NowMacro',
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
    def __init__(self, request, context, placeholder):
        super(DjangoRequest, self).__init__(request.META, self._start_response)

        self.django_request = request
        self.django_context = context
        self.django_placeholder = placeholder
        
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

class DjangoResource(resource.Resource):
    __slots__ = ('django_request')

class DjangoComponent(Component):
    implements(resource.IResourceManager, wiki.IWikiSyntaxProvider)
    
    def _format_link(self, formatter, ns, target, label, fullmatch=None):
        link, params, fragment = formatter.split_link(target)
        page = DjangoResource(ns, link)
        page.django_request = _get_django_request(req=formatter.req)
        try:
            href = resource.get_resource_url(self.env, page, formatter.href)
            title = resource.get_resource_name(self.env, page)
            return tag.a(label, title=title, href=href + params + fragment)
        except resource.ResourceNotFound:
            return tag.a(label + '?', class_='missing', href=target, rel='nofollow')
    
    # IResourceManager methods
    
    def get_resource_realms(self):
        yield 'cms'
        yield 'filer'
    
    def get_resource_url(self, res, href, **kwargs):
        if res.id is None or not res.realm:
            return href(**kwargs)
        elif res.realm == 'filer':
            try:
                link = self._get_file(res).url
            except filer_models.File.DoesNotExist as e:
                raise resource.ResourceNotFound(e)
        elif res.realm == 'cms':
            try:
                request = _get_django_request(res=res)
                lang = cms_utils.get_language_from_request(request)
                link = self._get_page(res).get_absolute_url(language=lang)
            except cms_models.Page.DoesNotExist:
                try:
                    # Test again as request.current_page could be None
                    if not res.id:
                        return href(**kwargs)
                    link = urlresolvers.reverse(res.id)
                except urlresolvers.NoReverseMatch as e:
                    raise resource.ResourceNotFound(e)
        else:
            raise RuntimeError("This should be impossible")
        
        args = [link]
        if link.endswith('/') and not link.strip('/') == '':
            # We add an empty link component at the end to force trailing slash
            args.append('')
        return href(*args, **kwargs)
    
    def get_resource_description(self, res, format='default', ctx=None, **kwargs):
        if res.id is None or not res.realm:
            return ''
        elif res.realm == 'filer':
            return self._get_file(res, ctx).label
        elif res.realm == 'cms':
            try:
                request = _get_django_request(res=res, ctx=ctx)
                lang = cms_utils.get_language_from_request(request)
                return self._get_page(res, ctx).get_title(language=lang)
            except cms_models.Page.DoesNotExist:
                return ''
        else:
            raise RuntimeError("This should be impossible")
    
    def resource_exists(self, res):
        if res.id is None or not res.realm:
            return False
        elif res.realm == 'filer':
            try:
                self._get_file(res)
                return True
            except filer_models.File.DoesNotExist:
                return False
        elif res.realm == 'cms':
            try:
                self._get_page(res)
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
        else:
            raise RuntimeError("This should be impossible")

    def _get_page(self, res, ctx=None):
        page_id = res.id
        request = _get_django_request(res=res, ctx=ctx)
        if not page_id:
            # cms.middleware.page.CurrentPageMiddleware is required for this
            if request.current_page:
                return request.current_page
            else:
                # It is not really necessary that the current page is known as plugins can be rendered also outside of pages (like in preview view in admin)
                # TODO: Check what happens on blog (also in preview there as we use request.current_page in preview template)
                if request.POST['page_id']:
                    return moderator.get_page_queryset(request).get(pk=request.POST['page_id'])
                else:
                    raise cms_models.Page.DoesNotExist()
        else:
            return moderator.get_page_queryset(request).get(reverse_id=page_id)

    def _get_file(self, res, ctx=None):
        file_id = res.id
        if not file_id:
            raise filer_models.File.DoesNotExist()
        request = _get_django_request(res=res, ctx=ctx)
        f = filer_models.File.objects.get(Q(original_filename=file_id) | Q(name=file_id) | Q(sha1=file_id) | Q(file=file_id))
        if f.is_public or f.has_read_permission(request):
            return f
        else:
            raise filer_models.File.DoesNotExist()
    
    # IWikiSyntaxProvider methods
    
    def get_wiki_syntax(self):
        return
    
    def get_link_resolvers(self):
        yield ('cms', self._format_link)
        yield ('filer', self._format_link)

# TODO: Relative links [..] should traverse Django CMS hierarchy
# TODO: Make Trac and Django CMS caching interoperate (how does dynamic macros currently behave?)
# TODO: Does request really have URL we want (for example in admin URL is not the URL of a resulting page)
# TODO: Do we really need to use href() or should we just use Django URLs directly (as we configure href() with Django base URL anyway)
# TODO: When using django-reversion, add an option to compare versions of plugin content and display it in the same way as Trac does
# TODO: Is markup object really reused or is it created (and DjangoEnvironment with it) again and again for each page display?

class Markup(markup_plugins.MarkupBase):
    name = 'Trac wiki'
    identifier = 'tracwiki'
    text_enabled_plugins = True
    is_dynamic = True

    def __init__(self, *args, **kwargs):
        self.env = DjangoEnvironment()

    def parse(self, value, context=None, placeholder=None):
        request = _get_django_request(context=context)
        self.env.set_abs_href(request)
        if not context:
            context = template.RequestContext(request, {})
        req = DjangoRequest(request, context, placeholder)
        res = DjangoResource('cms', 'pages-root') # TODO: Get ID from request (and version?)
        ctx = mimeview.Context.from_request(req, res)
        out = StringIO()
        DjangoFormatter(self.env, ctx).format(value, out)
        return out.getvalue()

    def plugin_id_list(self, text):
        return OBJ_ADMIN_RE.findall(text)

    def replace_plugins(self, text, id_dict):
        def _replace_tag(m):
            plugin_id = int(m.groups()[0])
            new_id = id_dict.get(plugin_id)
            try:
                obj = cms_models.CMSPlugin.objects.get(pk=new_id)
            except cms_models.CMSPlugin.DoesNotExist:
                # Object must have been deleted.  It cannot be rendered to
                # end user, or edited, so just remove it from the HTML
                # altogether
                return u''
            return u'[[CMSPlugin(%s)]]' % (new_id,)
        return OBJ_ADMIN_RE.sub(_replace_tag, text)

    def plugin_markup(self):
        return safestring.mark_safe(r"""function(plugin_id, icon_src, icon_alt) { return '[[CMSPlugin(' + plugin_id + ')]]'; }""")

    def plugin_regexp(self):
        return safestring.mark_safe(r"""function(plugin_id) { return new RegExp('\\[\\[CMSPlugin\\(\\s*' + plugin_id + '\\s*\\)\\]\\]', 'g'); }""")

def _get_django_request(req=None, context=None, res=None, ctx=None):
    if req and hasattr(req, 'django_request'):
        return req.django_request
    if context and 'request' in context:
        return context['request']
    if hasattr(res, 'django_request'):
        return res.django_request
    if ctx and ctx.req and hasattr(ctx.req, 'django_request'):
        return ctx.req.django_request

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

