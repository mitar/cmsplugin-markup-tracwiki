from django import template
from django.template import defaultfilters

from trac.util import html
from trac.wiki import formatter

from cmsplugin_markup_tracwiki import tracwiki

register = template.Library()

class LinkFormatter(formatter.LinkFormatter, tracwiki.DjangoFormatter):
    pass

class ExtractLink(tracwiki.Markup):
    _formatter = LinkFormatter

    def extract_link(self, value, context):
        if not value:
            return u''
        ctx, req = self._prepare_environment(context)
        return self._formatter(self.env, ctx).match(u'[%s]' % value)

link_parser = ExtractLink()
parser = tracwiki.Markup()

@register.simple_tag(takes_context=True)
def tracwiki_link(context, value):
     elt = link_parser.extract_link(value.strip(), context)
     elt = html.find_element(elt, 'href')
     if elt is not None:
         return elt.attrib.get('href')
     else:
         return value

@register.simple_tag(takes_context=True)
def tracwiki(context, value):
     return parser.parse(value, context)
