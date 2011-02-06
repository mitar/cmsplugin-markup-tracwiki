import os
from StringIO import StringIO

from trac import env
from trac import core
from trac import config
from trac import mimeview
from trac import util
from trac.wiki import formatter

try:
    from babel import Locale
except ImportError:
    Locale = None

from trac.ticket.default_workflow import load_workflow_config_snippet

def get_dburi():
    if os.environ.has_key('TRAC_TEST_DB_URI'):
        dburi = os.environ['TRAC_TEST_DB_URI']
        if dburi:
            scheme, db_prop = _parse_db_str(dburi)
            # Assume the schema 'tractest' for Postgres
            if scheme == 'postgres' and \
                    not db_prop.get('params', {}).get('schema'):
                if '?' in dburi:
                    dburi += "&schema=tractest"
                else:
                    dburi += "?schema=tractest"
            return dburi
    return 'sqlite:db/trac.db'

from trac.db.sqlite_backend import SQLiteConnection

class InMemoryDatabase(SQLiteConnection):
    """
    DB-API connection object for an SQLite in-memory database, containing all
    the default Trac tables but no data.
    """
    def __init__(self):
        SQLiteConnection.__init__(self, ':memory:')
        cursor = self.cnx.cursor()

        from trac.db_default import schema
        from trac.db.sqlite_backend import _to_sql
        for table in schema:
            for stmt in _to_sql(table):
                cursor.execute(stmt)

        self.cnx.commit()

class DjangoEnvironment(env.Environment):
    """A Django environment for Trac."""
    
    href = abs_href = None
    dbenv = db = None
    
    def __init__(self, default_data=False, enable=None):
        core.ComponentManager.__init__(self)
        core.Component.__init__(self)
        self.systeminfo = []
        
        import trac
        self.path = os.path.dirname(trac.__file__)
        if not os.path.isabs(self.path):
            self.path = os.path.join(os.getcwd(), self.path)

        self.config = config.Configuration(None)
        # We have to have a ticket-workflow config for ''lots'' of things to
        # work.  So insert the basic-workflow config here.  There may be a
        # better solution than this.
        load_workflow_config_snippet(self.config, 'basic-workflow.ini')
        self.config.set('logging', 'log_level', 'DEBUG')
        self.config.set('logging', 'log_type', 'stderr')
        if enable is not None:
            self.config.set('components', 'trac.*', 'disabled')
        for name_or_class in enable or ():
            config_key = self._component_name(name_or_class)
            self.config.set('components', config_key, 'enabled')

        # -- logging
        from trac.log import logger_handler_factory
        self.log, self._log_handler = logger_handler_factory('test')

        # -- database
        self.dburi = get_dburi()
        if self.dburi.startswith('sqlite'):
            self.config.set('trac', 'database', 'sqlite::memory:')
            self.db = InMemoryDatabase()

        if default_data:
            self.reset_db(default_data)

        from trac.web.href import Href
        self.href = Href('/trac.cgi')
        self.abs_href = Href('http://example.org/trac.cgi')

        self.known_users = []
        util.translation.activate(Locale and Locale('en', 'US'))

    def get_read_db(self):
        return self.get_db_cnx()
    
    def get_db_cnx(self, destroying=False):
        if self.db:
            return self.db # in-memory SQLite

        # As most of the EnvironmentStubs are built at startup during
        # the test suite formation and the creation of test cases, we can't
        # afford to create a real db connection for each instance.
        # So we create a special EnvironmentStub instance in charge of
        # getting the db connections for all the other instances.
        dbenv = DjangoEnvironment.dbenv
        if not dbenv:
            dbenv = EnvironmentStub.dbenv = DjangoEnvironment()
            dbenv.config.set('trac', 'database', self.dburi)
            if not destroying:
                self.reset_db() # make sure we get rid of previous garbage
        return DatabaseManager(dbenv).get_connection()

    def reset_db(self, default_data=None):
        """Remove all data from Trac tables, keeping the tables themselves.
        :param default_data: after clean-up, initialize with default data
        :return: True upon success
        """
        from trac import db_default
        if DjangoEnvironment.dbenv:
            db = self.get_db_cnx()
            scheme, db_prop = _parse_db_str(self.dburi)

            tables = []
            db.rollback() # make sure there's no transaction in progress
            try:
                # check the database version
                cursor = db.cursor()
                cursor.execute("SELECT value FROM system "
                               "WHERE name='database_version'")
                database_version = cursor.fetchone()
                if database_version:
                    database_version = int(database_version[0])
                if database_version == db_default.db_version:
                    # same version, simply clear the tables (faster)
                    m = sys.modules[__name__]
                    reset_fn = 'reset_%s_db' % scheme
                    if hasattr(m, reset_fn):
                        tables = getattr(m, reset_fn)(db, db_prop)
                else:
                    # different version or version unknown, drop the tables
                    self.destroy_db(scheme, db_prop)
            except:
                db.rollback()
                # tables are likely missing

            if not tables:
                del db
                dm = DatabaseManager(DjangoEnvironment.dbenv)
                dm.init_db()
                # we need to make sure the next get_db_cnx() will re-create 
                # a new connection aware of the new data model - see #8518.
                dm.shutdown() 

        db = self.get_db_cnx()
        cursor = db.cursor()
        if default_data:
            for table, cols, vals in db_default.get_data(db):
                cursor.executemany("INSERT INTO %s (%s) VALUES (%s)"
                                   % (table, ','.join(cols),
                                      ','.join(['%s' for c in cols])),
                                   vals)
        elif DjangoEnvironment.dbenv:
            cursor.execute("INSERT INTO system (name, value) "
                           "VALUES (%s, %s)",
                           ('database_version', str(db_default.db_version)))
        db.commit()

    def destroy_db(self, scheme=None, db_prop=None):
        if not (scheme and db_prop):
            scheme, db_prop = _parse_db_str(self.dburi)

        db = self.get_db_cnx(destroying=True)
        cursor = db.cursor()
        try:
            if scheme == 'postgres' and db.schema:
                cursor.execute('DROP SCHEMA "%s" CASCADE' % db.schema)
            elif scheme == 'mysql':
                dbname = os.path.basename(db_prop['path'])
                cursor = db.cursor()
                cursor.execute('SELECT table_name FROM '
                               '  information_schema.tables '
                               'WHERE table_schema=%s', (dbname,))
                tables = cursor.fetchall()
                for t in tables:
                    cursor.execute('DROP TABLE IF EXISTS `%s`' % t)
            db.commit()
        except Exception:
            db.rollback()

    def get_known_users(self, cnx=None):
        return self.known_users

def write(self, data):
    if not data:
        return
    sys.stderr.write(data)
    sys.stderr.write("\n")

def start_response(status, headers, exc_info):
    if exc_info:
        raise exc_info[0], exc_info[1], exc_info[2]
    sys.stderr.write("Trac rasponse data, %s:\n", status)
    return write

from trac import resource
from trac import web
from trac.web import main

class FullPerm(dict):
    def require(self, *args):
        return True
    
    def __call__(self, *args):
        return self

env = DjangoEnvironment()
environ = {
    'SERVER_PORT': 80,
    'wsgi.url_scheme': 'http',
    'SERVER_NAME': 'localhost',
}
request = web.Request(environ, start_response)
request.perm = FullPerm()
request.sesion = main.FakeSession()

#.callbacks.update({
    #'authname': self.authenticate,
    #'chrome': chrome.prepare_request,
    #'hdf': self._get_hdf,
    #'locale': self._get_locale,
    #'tz': self._get_timezone,
    #'form_token': self._get_form_token,
#})

class DjangoFormatter(formatter.Formatter):
    def _parse_heading(self, match, fullmatch, shorten):
        (depth, heading, anchor) = super(DjangoFormatter, self)._parse_heading(match, fullmatch, shorten)
        depth = min(depth + 1, 6)
        return (depth, heading, anchor)

resource = resource.Resource('django')
context = mimeview.Context.from_request(request, resource)
out = StringIO()
DjangoFormatter(env, context).format("= Foo =\n[wiki:foo]\n[django:foo]\n[[MacroList]]\n`bar`", out)
print out.getvalue()

#abs_ref, href = (req or env).abs_href, (req or env).href
