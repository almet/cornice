import fnmatch


CORS_PARAMETERS = ('cors_headers', 'cors_enabled', 'cors_origins',
                   'cors_credentials', 'cors_max_age')


def get_cors_preflight_view(service):
    """Return a view for the OPTION method.

    Checks that the User-Agent is authorized to do a request to the server, and
    to this particular service, and add the various checks that are specified
    in http://www.w3.org/TR/cors/#resource-processing-model.
    """

    def _preflight_view(request):
        response = request.response
        origin = request.headers.get('Origin')
        supported_headers = service.cors_supported_headers

        if not origin:
            request.errors.add('header', 'Origin',
                               'this header is mandatory')

        requested_method = request.headers.get('Access-Control-Request-Method')
        if not requested_method:
            request.errors.add('header', 'Access-Control-Request-Method',
                               'this header is mandatory')

        if not (requested_method and origin):
            return

        requested_headers = (
            request.headers.get('Access-Control-Request-Headers', ()))

        if requested_headers:
            requested_headers = requested_headers.split()

        if requested_method not in service.cors_supported_methods:
            request.errors.add('header', 'Access-Control-Request-Method',
                               'Method not allowed')

        for h in requested_headers:
            if not h in supported_headers:
                request.errors.add('header', 'Access-Control-Request-Headers',
                                   'Header "%s" not allowed' % h)

        response.headers['Access-Control-Allow-Headers'] = (
                ', '.join(supported_headers))

        response.headers['Access-Control-Allow-Methods'] = (
            ','.join(service.cors_supported_methods))

        max_age = service.cors_max_age_for(requested_method)
        if max_age is not None:
            response.headers['Access-Control-Max-Age'] = str(max_age)

        return 'ok'
    return _preflight_view


def _get_method(request):
    """Return what's supposed to be the method for CORS operations.
    (e.g if the verb is options, look at the A-C-Request-Method header,
    otherwise return the HTTP verb).
    """
    if request.method == 'OPTIONS':
        method = request.headers.get('Access-Control-Request-Method',
                                     request.method)
    else:
        method = request.method
    return method


def get_cors_validator(service):
    """Create a cornice validator to handle CORS-related verifications.

    Checks, if an "Origin" header is present, that the origin is authorized
    (and issue an error if not)
    """

    def _cors_validator(request):
        response = request.response
        method = _get_method(request)

        # If we have an "Origin" header, check it's authorized and add the
        # response headers accordingly.
        origin = request.headers.get('Origin')
        if origin:
            if not any([fnmatch.fnmatchcase(origin, o)
                        for o in service.cors_origins_for(method)]):
                request.errors.add('header', 'Origin',
                                   '%s not allowed' % origin)
            else:
                response.headers['Access-Control-Allow-Origin'] = origin
    return _cors_validator


def get_cors_filter(service):
    """Create a cornice filter to handle CORS-related post-request
    things.

    Add some response headers, such as the Expose-Headers and the
    Allow-Credentials ones.
    """

    def _cors_filter(response, request):
        method = _get_method(request)

        if (service.cors_support_credentials(method) and
                not 'Access-Control-Allow-Credentials' in response.headers):
            response.headers['Access-Control-Allow-Credentials'] = 'true'

        if request.method is not 'OPTIONS':
            # Which headers are exposed?
            supported_headers = service.cors_supported_headers
            if supported_headers:
                response.headers['Access-Control-Expose-Headers'] = (
                        ', '.join(supported_headers))

        return response
    return _cors_filter