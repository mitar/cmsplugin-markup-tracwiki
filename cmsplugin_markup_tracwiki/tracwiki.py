from __future__ import with_statement

import contextlib
import functools
import inspect
import os
import re
import string
import sys

from StringIO import StringIO

from genshi.builder import tag

from trac.core import *

from trac import cache
from trac import log
from trac import mimeview
from trac import test
from trac import resource
from trac import web
from trac import wiki
from trac.util import datefmt, translation as trac_translation
from trac.web import chrome as trac_chrome
from trac.web import main
from trac.wiki import interwiki

from django import http
from django import template
from django.conf import settings
from django.contrib.sites import models as sites_models
from django.core import urlresolvers
from django.core.servers import basehttp
from django.db.models import Q
from django.utils import translation as django_translation
from django.utils import safestring

from cms import models as cms_models
from cms import utils as cms_utils
from cms.utils import moderator

if 'filer' in settings.INSTALLED_APPS:
    from filer.models import filemodels as filer_models
    USING_FILER = True
else:
    USING_FILER = False

from cmsplugin_blog import models as blog_models

from cmsplugin_markup import plugins as markup_plugins

OBJ_ADMIN_RE_PATTERN = ur'\[\[CMSPlugin\(\s*(\d+)\s*\)\]\]'
OBJ_ADMIN_RE = re.compile(OBJ_ADMIN_RE_PATTERN)

PLUGIN_EDIT_RE_PATTERN = ur'edit-plugin/(\d+)'
PLUGIN_EDIT_RE = re.compile(PLUGIN_EDIT_RE_PATTERN)

COMPONENTS = [
    'cmsplugin_markup_tracwiki.tracwiki.DjangoComponent',
    'cmsplugin_markup_tracwiki.tracwiki.DjangoInterWikiMap',
    'trac.mimeview.pygments',
    'trac.mimeview.rst',
    'trac.mimeview.txtl',
    'trac.mimeview.patch',
    'trac.mimeview.*',
    'trac.wiki.macros.MacroListMacro',
    'trac.wiki.macros.KnownMimeTypesMacro',
    'trac.wiki.macros.PageOutlineMacro',
    'trac.wiki.intertrac',
    'trac.web.chrome.Chrome',
    'cmsplugin_markup_tracwiki.components.BaseHandler',
    'cmsplugin_markup_tracwiki.macros.CMSPluginMacro',
    'cmsplugin_markup_tracwiki.macros.URLMacro',
    'cmsplugin_markup_tracwiki.macros.NowMacro',
]

TRACWIKI_HEADER_OFFSET = 1

def tracwiki_base_path():
    return urlresolvers.reverse('cmsplugin_markup_tracwiki', kwargs={'path': ''})

def temporary_switch_to_trac_root(f):
    @functools.wraps(f)
    def wrapper(req, *args, **kwargs):
        orig_href = req.href
        try:
            req.href = web.href.Href(tracwiki_base_path())
            return f(req, *args, **kwargs)
        finally:
            req.href = orig_href
    return wrapper

@contextlib.contextmanager
def django_root(formatter):
    formatter_env_href = formatter.env.href
    formatter_href = formatter.href
    try:
        formatter.env.switch_to_django_root()
        formatter.href = formatter.env.href
        yield
    finally:
        formatter.env.href = formatter_env_href
        formatter.href = formatter_href

@contextlib.contextmanager
def trac_root(formatter):
    formatter_env_href = formatter.env.href
    formatter_href = formatter.href
    try:
        formatter.env.switch_to_trac_root()
        formatter.href = formatter.env.href
        yield
    finally:
        formatter.env.href = formatter_env_href
        formatter.href = formatter_href

# Those two methods should always use trac root
trac_chrome.add_stylesheet = temporary_switch_to_trac_root(trac_chrome.add_stylesheet)
trac_chrome.add_script = temporary_switch_to_trac_root(trac_chrome.add_script)

class DjangoEnvironment(test.EnvironmentStub):
    """A Django environment for Trac."""
    
    def __init__(self):
        components = list(COMPONENTS)
        components.extend(getattr(settings, 'CMS_MARKUP_TRAC_COMPONENTS', []))

        super(DjangoEnvironment, self).__init__(enable=components)
        
        for c in components:
            module_and_class = c.rsplit('.', 1)
            if len(module_and_class) == 1:
                __import__(name=module_and_class[0])
            else:
                __import__(name=module_and_class[0], fromlist=[module_and_class[1]])

        self.config.set('trac', 'default_charset', 'utf-8')
        self.config.set('trac', 'never_obfuscate_mailto', True)

        self.config.set('trac', 'default_handler', 'BaseHandler')

        # TODO: Use Django logging facilities?
        self.config.set('logging', 'log_level', 'WARN')
        self.config.set('logging', 'log_type', 'stderr')
        self.setup_log()

        for (ns, conf) in getattr(settings, 'CMS_MARKUP_TRAC_INTERTRAC', {}).iteritems():
            if 'URL' in conf:
                if 'TITLE' in conf:
                    self.config.set('intertrac', '%s.title' % (ns,), conf['TITLE'])
                self.config.set('intertrac', '%s.url' % (ns,), conf['URL'])
                self.config.set('intertrac', '%s.compat' % (ns,), conf.get('COMPAT', False))

        for (section, conf) in getattr(settings, 'CMS_MARKUP_TRAC_CONFIGURATION', {}).iteritems():
            for (key, value) in conf.iteritems():
                self.config.set(section, key, value)

        # TODO: Sync activated locales with Django?

    def _set_abs_href(self, request):
        site = sites_models.Site.objects.get_current() if sites_models.Site._meta.installed else sites_models.RequestSite(request)

        server_port = str(request.META.get('SERVER_PORT', '80'))
        if request.is_secure():
            self.abs_href = web.href.Href('https://' + site.domain + (':' + server_port if server_port != '443' else '') + self.href())
        else:
            self.abs_href = web.href.Href('http://' + site.domain + (':' + server_port if server_port != '80' else '') + self.href())

    def switch_to_django_root(self, request=None):
        self.href = web.href.Href(urlresolvers.reverse('pages-root'))
        if request:
            self._set_abs_href(request)

    def switch_to_trac_root(self, request=None):
        self.href = web.href.Href(tracwiki_base_path())
        if request:
            self._set_abs_href(request)

    def get_templates_dir(self):
        return getattr(settings, 'CMS_MARKUP_TRAC_TEMPLATES_DIR', super(DjangoEnvironment, self).get_templates_dir())

class DjangoChrome(trac_chrome.Chrome):
    @property
    def htdocs_location(self):
        return web.href.Href(tracwiki_base_path()).chrome('common')

class DjangoRequestDispatcher(main.RequestDispatcher):
    pass

class DjangoRequest(web.Request):
    def __init__(self, env, request, context=None, placeholder=None):
        super(DjangoRequest, self).__init__(request.META, self._start_response)

        # We override request's hrefs from environment (which are based on Django's URLs)
        self.href = env.href
        self.abs_href = env.abs_href

        self.django_request = request
        self.django_context = context
        if placeholder:
            self.django_placeholder = placeholder
        elif context:
            self.django_placeholder = context.get('placeholder')
        else:
            self.django_placeholder = None
        self.django_response = None
        
        self.perm = main.FakePerm()
        self.session = main.FakeSession()

        chrome = DjangoChrome(env)

        if request.user.is_authenticated():
            self.session['name'] = request.user.get_full_name()
            self.session['email'] = request.user.email

        self.callbacks.update({
            'authname': self._get_authname,
            'chrome': chrome.prepare_request,
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

    def _start_response(self, status, headers, exc_info=None):
        if exc_info:
            try:
                raise exc_info[0], exc_info[1], exc_info[2]
            finally:
                exc_info = None # Avoids dangling circular ref

        headers = dict(headers)

        if 'Content-Type' in headers:
            content_type = headers.pop('Content-Type')
        else:
            content_type = None

        self.django_response = http.HttpResponse(status=status, content_type=content_type)

        for (k, v) in headers.iteritems():
            self.django_response[k] = v

        return self.django_response.write

class DjangoInterWikiMap(interwiki.InterWikiMap):
    """
    InterWiki map manager.
    """

    @cache.cached
    def interwiki_map(self, db):
        """
        Map from upper-cased namespaces to (namespace, prefix, title) values.
        """
        map = {}
        for (ns, conf) in getattr(settings, 'CMS_MARKUP_TRAC_INTERWIKI', {}).iteritems():
            if 'URL' in conf:
                url = conf['URL'].strip()
                title = conf.get('TITLE')
                title = title and title.strip() or ns
                map[ns.upper()] = (ns, url, title)
        return map

class DjangoFormatter(wiki.formatter.Formatter):
    def _parse_heading(self, match, fullmatch, shorten):
        (depth, heading, anchor) = super(DjangoFormatter, self)._parse_heading(match, fullmatch, shorten)
        depth = min(depth + getattr(settings, 'CMS_MARKUP_TRAC_HEADING_OFFSET', 1), 6)
        return (depth, heading, anchor)
    
    def _make_lhref_link(self, match, fullmatch, rel, ns, target, label):
        # We override _make_lhref_link to make 'cms' namespace default and Django root
        with django_root(self):
            return super(DjangoFormatter, self)._make_lhref_link(match, fullmatch, rel, ns or 'cms', target, label)

    def _make_ext_link(self, url, text, title=''):
        # TODO: Make configurable which links are external, currently we do not render external links any differently than internal
        return tag.a(text, href=url, title=title or None)

    def _make_interwiki_link(self, ns, target, label):
        interwiki = DjangoInterWikiMap(self.env)
        if ns in interwiki:
            url, title = interwiki.url(ns, target)
            return self._make_ext_link(url, label, title)

class DjangoResource(resource.Resource):
    __slots__ = ('django_request', 'django_context')

class DjangoComponent(Component):
    implements(resource.IResourceManager, wiki.IWikiSyntaxProvider)
    
    def _format_link(self, formatter, ns, target, label, fullmatch=None):
        link, params, fragment = formatter.split_link(target)
        res = DjangoResource(ns, link)
        res.django_request = _get_django_request(req=formatter.req)
        res.django_context = _get_django_context(req=formatter.req)
        try:
            href = resource.get_resource_url(self.env, res, formatter.href)
            title = resource.get_resource_name(self.env, res)
            return tag.a(label, title=title, href=href + params + fragment)
        except resource.ResourceNotFound:
            return tag.a(label + '?', class_='missing', href=target, rel='nofollow')
    
    # IResourceManager methods
    
    def get_resource_realms(self):
        yield 'cms'
        if USING_FILER:
            yield 'filer'
        yield 'blog'
    
    def get_resource_url(self, res, href, **kwargs):
        if res.id is None or not res.realm:
            raise resource.ResourceNotFound()
        elif res.realm == 'filer':
            try:
                link = self._get_file(res).url
            except filer_models.File.DoesNotExist as e:
                raise resource.ResourceNotFound(e)
        elif res.realm == 'blog':
            try:
                link = self._get_blog(res).get_absolute_url()
            except blog_models.EntryTitle.DoesNotExist as e:
                raise resource.ResourceNotFound(e)
        elif res.realm == 'cms':
            try:
                request = _get_django_request(res=res)
                lang = cms_utils.get_language_from_request(request)
                link = self._get_page(res).get_absolute_url(language=lang)
            except cms_models.Page.DoesNotExist as e:
                # Test again as maybe we got [cms: this page] link but we could not get current page
                if not res.id:
                    raise resource.ResourceNotFound(e)

                try:
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
            raise resource.ResourceNotFound()
        elif res.realm == 'filer':
            try:
                return self._get_file(res, ctx).label
            except filer_models.File.DoesNotExist as e:
                raise resource.ResourceNotFound(e)
        elif res.realm == 'blog':
            try:
                return self._get_blog(res, ctx).title
            except blog_models.EntryTitle.DoesNotExist as e:
                raise resource.ResourceNotFound(e)
        elif res.realm == 'cms':
            try:
                request = _get_django_request(res=res, ctx=ctx)
                lang = cms_utils.get_language_from_request(request)
                return self._get_page(res, ctx).get_title(language=lang)
            except cms_models.Page.DoesNotExist as e:
                raise resource.ResourceNotFound(e)
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
        elif res.realm == 'blog':
            try:
                self._get_blog(res)
                return True
            except blog_models.EntryTitle.DoesNotExist:
                return False
        elif res.realm == 'cms':
            try:
                self._get_page(res)
                return True
            except cms_models.Page.DoesNotExist:
                pass

            # Test again as maybe we got [cms: this page] link but we could not get current page
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
        if not page_id: # links like [cms: current page]
            # cms.middleware.page.CurrentPageMiddleware is required for this
            if request.current_page:
                return request.current_page
            # It is not really necessary that the current page is known as plugins can be rendered also outside of pages (like in preview view in admin), we can try to use a hint
            elif request.POST.get('page_id'):
                return moderator.get_page_queryset(request).get(pk=request.POST['page_id'])
            else:
                context = _get_django_context(res=res, ctx=ctx)
                plugin = self._get_plugin(request, context)

                if not plugin:
                    raise cms_models.Page.DoesNotExist()

                try:
                    # TODO: If plugin is used in an app this does not find an anchor page for the app, but this happens only in a preview as otherwise request.current_page works
                    return plugin.placeholder.page_set.get()
                except cms_models.Page.MultipleObjectsReturned as e:
                    # Should not happen
                    raise cms_models.Page.DoesNotExist(e)
        else:
            return moderator.get_page_queryset(request).get(reverse_id=page_id)

    def _get_file(self, res, ctx=None):
        file_id = res.id
        if not file_id:
            raise filer_models.File.DoesNotExist()
        request = _get_django_request(res=res, ctx=ctx)
        try:
            f = filer_models.File.objects.get(Q(original_filename=file_id) | Q(name=file_id) | Q(sha1=file_id) | Q(file=file_id))
        except filer_models.File.MultipleObjectsReturned as e:
            raise filer_models.File.DoesNotExist(e)
        if f.is_public or f.has_read_permission(request):
            return f
        else:
            raise filer_models.File.DoesNotExist()

    def _get_blog(self, res, ctx=None):
        blog_id = res.id

        if not blog_id: # links like [blog: current blog entry]
            request = _get_django_request(res=res, ctx=ctx)
            context = _get_django_context(res=res, ctx=ctx)
            plugin = self._get_plugin(request, context)

            if not plugin:
                raise blog_models.EntryTitle.DoesNotExist()

            try:
                return plugin.placeholder.entry_set.get().entrytitle_set.get()
            except (blog_models.Entry.DoesNotExist, blog_models.Entry.MultipleObjectsReturned) as e:
                # MultipleObjectsReturned should not happen
                raise blog_models.EntryTitle.DoesNotExist(e)
            except blog_models.EntryTitle.MultipleObjectsReturned as e:
                # Should not happen
                raise blog_models.EntryTitle.DoesNotExist(e)

        else:
            ids = blog_id.split(":", 1)

            if len(ids) == 1:
                blog_id = ids[0]

                entries = blog_models.EntryTitle.objects.filter(slug=blog_id)
                if len(entries) == 0:
                    raise blog_models.EntryTitle.DoesNotExist
                elif len(entries) == 1:
                    return entries[0]
                else:
                    lang = django_translation.get_language()
                    return entries.get(language=lang)

            else:
                (lang, blog_id) = ids
                return blog_models.EntryTitle.objects.get(slug=blog_id, language=lang)

    def _get_plugin(self, request, context):
        if context and context.get('object'):
            return context['object']
        else:
            # We come to here probably only in admin
            match = PLUGIN_EDIT_RE.search(request.path)

            if request.POST.get('plugin_id') or match:
                try:
                    return cms_models.CMSPlugin.objects.get(pk=request.POST.get('plugin_id') or match.group(1))
                except cms_models.CMSPlugin.DoesNotExist:
                    return None
            else:
                return None
    
    # IWikiSyntaxProvider methods
    
    def get_wiki_syntax(self):
        return
    
    def get_link_resolvers(self):
        yield ('cms', self._format_link)
        if USING_FILER:
            yield ('filer', self._format_link)
        yield ('blog', self._format_link)

class Markup(markup_plugins.MarkupBase):
    name = 'Trac wiki'
    identifier = 'tracwiki'
    text_enabled_plugins = True
    is_dynamic = True

    _formatter = DjangoFormatter

    def __init__(self, *args, **kwargs):
        self.env = DjangoEnvironment()
        self.scripts = []
        self.links = {}

    def _prepare_environment(self, context=None, placeholder=None):
        request = _get_django_request(context=context)
        self.env.switch_to_trac_root(request)
        if not context:
            context = template.RequestContext(request, {})
        req = DjangoRequest(self.env, request, context, placeholder)
        res = DjangoResource('cms', 'pages-root') # TODO: Get ID from request (and version?)
        ctx = mimeview.Context.from_request(req, res)
        self._early_scripts_and_links(req)
        return ctx, req

    def parse(self, value, context=None, placeholder=None):
        ctx, req = self._prepare_environment(context, placeholder)
        out = StringIO()
        self._formatter(self.env, ctx).format(value, out)
        self._all_scripts_and_links(req)
        return out.getvalue()

    def plugin_id_list(self, text):
        return OBJ_ADMIN_RE.findall(text)

    def _early_scripts_and_links(self, req):
        self._early_scripts_hrefs = [s['href'] for s in req.chrome.get('scripts', [])]
        self._early_links_ids = ['%s:%s' % (r, l['href']) for (r, ls) in req.chrome.get('links', {}).iteritems() for l in ls]

    def _all_scripts_and_links(self, req):
        for script in req.chrome.get('scripts', []):
            if script['href'] not in self._early_scripts_hrefs:
                self.scripts.append({'href': script['href'], 'type': script.get('type', "text/javascript")})

        for (rel, links) in req.chrome.get('links', {}).iteritems():
            for l in links:
                if '%s:%s' % (rel, l['href']) not in self._early_links_ids:
                    self.links.setdefault(rel, []).append(l)

    def get_scripts(self):
        return self.scripts

    def get_stylesheets(self):
        return [{'href': s['href'], 'type': s.get('type', "text/css")} for s in self.links.get('stylesheet', [])]

    def get_plugin_urls(self):
        from django.conf.urls.defaults import patterns, url
        
        urls = super(Markup, self).get_plugin_urls()

        trac_urls = patterns('',
            url(r'^tracwiki/(?P<path>.*)$', self.serve_trac_path, name='cmsplugin_markup_tracwiki'),
        )

        return trac_urls + urls

    def serve_trac_path(self, request, path):
        self.env.switch_to_trac_root(request)
        context = template.RequestContext(request, {})
        req = DjangoRequest(self.env, request, context, None)

        base_path = req.href()
        req.environ['PATH_INFO'] = req.environ.get('PATH_INFO', '')[len(base_path):]

        # Have to encode Unicode back to UTF-8 for Trac
        if isinstance(req.environ.get('PATH_INFO', ''), unicode):
            req.environ['PATH_INFO'] = req.environ.get('PATH_INFO', '').encode('utf-8')

        try:
            dispatcher = DjangoRequestDispatcher(self.env)
            dispatcher.dispatch(req)
        except web.RequestDone:
            pass
        except web.HTTPNotFound, e:
            raise http.Http404(e)
        
        if req._response:
            req.django_response._container = req._response
            req.django_response._is_string = False

        if isinstance(req.django_response.status_code, basestring):
            # Django expects integer status codes
            req.django_response.status_code = int(req.django_response.status_code.split()[0])

        return req.django_response

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
    if req and getattr(req, 'django_request', None):
        return req.django_request
    if context and context.get('request'):
        return context['request']
    if getattr(res, 'django_request', None):
        return res.django_request
    if ctx and ctx.req and getattr(ctx.req, 'django_request', None):
        return ctx.req.django_request

    frame = inspect.currentframe()
    try:
        while frame.f_back:
            frame = frame.f_back
            request = frame.f_locals.get('request')
            if request and isinstance(request, http.HttpRequest):
                return request
    finally:
        del frame
    return None

def _get_django_context(req=None, res=None, ctx=None):
    if req and getattr(req, 'django_context', None):
        return req.django_context
    if getattr(res, 'django_context', None):
        return res.django_context
    if ctx and ctx.req and getattr(ctx.req, 'django_context', None):
        return ctx.req.django_context

    frame = inspect.currentframe()
    try:
        while frame.f_back:
            frame = frame.f_back
            context = frame.f_locals.get('context')
            if context and isinstance(context, template.Context):
                return context
    finally:
        del frame
    return None

