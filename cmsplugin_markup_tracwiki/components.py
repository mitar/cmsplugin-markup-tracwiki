from trac.core import *
from trac.web import IRequestHandler, HTTPNotFound

class BaseHandler(Component):
    """
    A simple Trac request handler for use as a base handler. It simply always returns 404.
    """

    implements(IRequestHandler)

    # IRequestHandler methods

    def match_request(self, req):
        return False

    def process_request(self, req):
        raise HTTPNotFound('No handler matched request to %s', req.path_info)
