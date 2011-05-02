from trac.wiki import macros

from django import template
from django.template import defaulttags

class DjangoTagMacroBase(macros.WikiMacroBase):
    def expand_macro(self, formatter, name, content):
        tag = getattr(defaulttags, self.django_tag_name)
        node = tag(template.Parser(''), template.Token(template.TOKEN_BLOCK, "%s %s" % (self.django_tag_name, content)))
        return node.render(template.Context({}))

    def get_macros(self):
        yield self.django_tag_name

class URLMacro(DjangoTagMacroBase):
    """Wrapper around Django's `url` template tag.

    For more information on how to use it and of its syntax please refer to
    [http://docs.djangoproject.com/en/dev/ref/templates/builtins/#url Django documentation].

    Using it with `as` does not really do anything useful.

    Examples:

    {{{
        [[url(path.to.some_view v1 v2)]]
        [[url(path.to.some_view arg1=v1 arg2=v2)]]
    }}}
    """

    # TODO: Retain the same Django context through whole rendering of wiki content? So that "as" could work? But what then, it should be possible to output it somehow, too.

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
