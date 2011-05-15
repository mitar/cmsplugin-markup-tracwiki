from trac.wiki import macros

from django import template
from django.template import defaulttags

from cms.models import pluginmodel as plugin_models

class DjangoTagMacroBase(macros.WikiMacroBase):
    def expand_macro(self, formatter, name, content):
        tag = getattr(defaulttags, self.django_tag_name)
        node = tag(template.Parser(''), template.Token(template.TOKEN_BLOCK, "%s %s" % (self.django_tag_name, content)))
        return node.render(formatter.req.django_context)

    def get_macros(self):
        yield self.django_tag_name

class URLMacro(DjangoTagMacroBase):
    """Wrapper around Django's `url` template tag.

    For more information on how to use it and of its syntax please refer to
    [http://docs.djangoproject.com/en/dev/ref/templates/builtins/#url Django documentation].

    Using it with `as` probably does not do anything really useful.

    Examples:

    {{{
        [[url(path.to.some_view v1 v2)]]
        [[url(path.to.some_view arg1=v1 arg2=v2)]]
    }}}
    """

    django_tag_name = 'url'

class NowMacro(DjangoTagMacroBase):
    """Wrapper around Django's `now` template tag.

    For more information on how to use it and of its syntax please refer to
    [http://docs.djangoproject.com/en/dev/ref/templates/builtins/#now Django documentation].

    Format string should be quoted.

    Examples:

    {{{
        [[now("jS F Y H:i")]]
        [[now("jS o\\f F")]]
    }}}
    """
 
    django_tag_name = 'now'

class CMSPluginMacro(macros.WikiMacroBase):
    """Macro which renders Django CMS plugin.
    
    It takes only one argument, an object ID of a Django CMS plugin which is attached to the markup plugin using this macro. Attaching
    the plugin and inserting the proper ID is done by the Django CMS itself so using this macro by its own has little practical use.

    Examples:

    {{{
        [[CMSPlugin(42)]]
    }}}
    """

    def expand_macro(self, formatter, name, content):
        request = formatter.req.django_request
        context = formatter.req.django_context
        placeholder = formatter.req.django_placeholder
        try:
            plugin = plugin_models.CMSPlugin.objects.get(pk=content.strip())
            plugin._render_meta.text_enabled = True
            return plugin.render_plugin(context, placeholder)
        except Exception as e:
            # TODO: Log
            if (request.user.is_authenticated() and request.user.is_staff) or 'preview' in request.GET:
                raise e
            else:
                return u''
