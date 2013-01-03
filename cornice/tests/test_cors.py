from pyramid import testing
from webtest import TestApp

from cornice.service import Service
from cornice.tests.support import TestCase, CatchErrors


squirel = Service(path='/squirel', name='squirel', cors_origins=('foobar',))


@squirel.get(cors_origins=('notmyidea.org',))
def get_squirel(request):
    return "got squirels"


@squirel.post(cors_support=False, cors_headers=('X-Another-Header'))
def post_squirel(request):
    return "posting squirels"


@squirel.put(cors_headers=('X-My-Header',))
def put_squirel(request):
    return "putting squirels"


class TestCORS(TestCase):

    def setUp(self):
        self.config = testing.setUp()
        self.config.include("cornice")
        self.config.scan("cornice.tests.test_cors")
        self.app = TestApp(CatchErrors(self.config.make_wsgi_app()))

        def tearDown(self):
            testing.tearDown()

    def test_missing_headers(self):
        # we should have an OPTION method defined.
        # If we just try to reach it, without using correct headers:
        # "Access-Control-Request-Method"or without the "Origin" header,
        # we should get a 400.
        resp = self.app.options('/squirel', status=400)
        self.assertEquals(len(resp.json['errors']), 2)

    def test_missing_origin(self):

        resp = self.app.options(
            '/squirel',
            headers={'Access-Control-Request-Method': 'GET'},
            status=400)
        self.assertEquals(len(resp.json['errors']), 1)

    def test_missing_request_method(self):

        resp = self.app.options(
            '/squirel',
            headers={'Origin': 'foobar.org'},
            status=400)

        self.assertEquals(len(resp.json['errors']), 1)

    def test_incorrect_origin(self):
        # we put "lolnet.org" where only "notmyidea.org" is authorized
        resp = self.app.options(
            '/squirel',
            headers={'Origin': 'lolnet.org',
                     'Access-Control-Request-Method': 'GET'},
            status=400)
        self.assertEquals(len(resp.json['errors']), 1)

    def test_correct_origin(self):
        resp = self.app.options(
            '/squirel',
            headers={'Origin': 'notmyidea.org',
                     'Access-Control-Request-Method': 'GET'})
        self.assertEquals(
            resp.headers['Access-Control-Allow-Origin'],
            'notmyidea.org')

        allowed_methods = (resp.headers['Access-Control-Allow-Methods']
                           .split(','))

        self.assertFalse('POST' in allowed_methods)
        self.assertIn('GET', allowed_methods)
        self.assertIn('PUT', allowed_methods)
        self.assertIn('HEAD', allowed_methods)

        allowed_headers = (resp.headers['Access-Control-Allow-Headers']
                           .split(','))

        self.assertIn('X-My-Header', allowed_headers)
        self.assertFalse('X-Another-Header' in allowed_headers)