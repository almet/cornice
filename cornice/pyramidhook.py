# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
import json
import fnmatch
import functools

from pyramid.httpexceptions import HTTPMethodNotAllowed, HTTPNotAcceptable
from pyramid.exceptions import PredicateMismatch

from cornice.service import decorate_view
from cornice.errors import Errors
from cornice.util import to_list


def match_accept_header(func, context, request):
    acceptable = func(request)
    # attach the accepted content types to the request
    request.info['acceptable'] = acceptable
    return request.accept.best_match(acceptable) is not None


def make_route_factory(acl_factory):
    class ACLResource(object):
        def __init__(self, request):
            self.request = request
            self.__acl__ = acl_factory(request)

    return ACLResource


def get_fallback_view(service):
    """Fallback view for a given service, called when nothing else matches.

    This method provides the view logic to be executed when the request
    does not match any explicitly-defined view.  Its main responsibility
    is to produce an accurate error response, such as HTTPMethodNotAllowed
    or HTTPNotAcceptable.
    """

    def _fallback_view(request):
        # Maybe we failed to match any definitions for the request method?
        if request.method not in service.defined_methods:
            response = HTTPMethodNotAllowed()
            response.allow = service.defined_methods
            raise response
        # Maybe we failed to match an acceptable content-type?
        # First search all the definitions to find the acceptable types.
        # XXX: precalculate this like the defined_methods list?
        acceptable = []
        for method, _, args in service.definitions:
            if method != request.method:
                continue
            if 'accept' in args:
                acceptable.extend(
                    service.get_acceptable(method, filter_callables=True))
                if 'acceptable' in request.info:
                    for content_type in request.info['acceptable']:
                        if content_type not in acceptable:
                            acceptable.append(content_type)

                # Now check if that was actually the source of the problem.
                if not request.accept.best_match(acceptable):
                    response = HTTPNotAcceptable()
                    response.content_type = "application/json"
                    response.body = json.dumps(acceptable)
                    raise response

        # In the absence of further information about what went wrong,
        # let upstream deal with the mismatch.
        raise PredicateMismatch(service.name)
    return _fallback_view


def get_options_view(service):
    """Return a view for the OPTION method.

    Checks that the User-Agent is authorized to do a request to the server, and
    to this particular service.
    """

    def _options_view(request):
        origin = request.headers.get('Origin')
        if not origin:
            request.errors.add('headers', 'Origin',
                               'this header is mandatory')

        requested_method = request.headers.get('Access-Control-Request-Method')
        if not requested_method:
            request.errors.add('headers', 'Access-Control-Request-Method',
                               'this header is mandatory')

        if not (requested_method and origin):
            return

        requested_headers = (
            request.headers.get('Access-Control-Request-Headers', ()))

        if requested_headers:
            requested_headers = requested_headers.split()

        if not any([fnmatch.fnmatch(origin, o)
                    for o in service.cors_supported_origins]):
            request.errors.add('headers', 'Origin', '%s not allowed' % origin)
        else:
            request.response.headers['Access-Control-Allow-Origin'] = origin

        if requested_method not in service.cors_supported_methods:
            request.errors.add('headers', 'Access-Control-Request-Method',
                               'Method not allowed')

        supported_headers = service.cors_supported_headers
        for h in requested_headers:
            if not h in supported_headers:
                request.errors.add('headers', 'Access-Control-Request-Headers',
                                   'Header "%s" not allowed' % h)

        request.response.headers['Access-Control-Allow-Methods'] = (
            ",".join(service.cors_supported_methods))
        request.response.headers['Access-Control-Allow-Headers'] = (
            ",".join(service.cors_supported_headers))
        return "ok"
    return _options_view


def tween_factory(handler, registry):
    """Wraps the default WSGI workflow to provide cornice utilities"""
    def cornice_tween(request):
        response = handler(request)
        if request.matched_route is not None:
            # do some sanity checking on the response using filters
            services = request.registry.get('cornice_services', {})
            pattern = request.matched_route.pattern
            service = services.get(pattern, None)
            if service is not None:
                kwargs, ob = getattr(request, "cornice_args", ({}, None))
                for _filter in kwargs.get('filters', []):
                    if isinstance(_filter, basestring) and ob is not None:
                        _filter = getattr(ob, _filter)
                    response = _filter(response)
        return response
    return cornice_tween


def wrap_request(event):
    """Adds a "validated" dict, a custom "errors" object and an "info" dict to
    the request object if they don't already exists
    """
    request = event.request
    if not hasattr(request, 'validated'):
        setattr(request, 'validated', {})

    if not hasattr(request, 'errors'):
        setattr(request, 'errors', Errors(request))

    if not hasattr(request, 'info'):
        setattr(request, 'info', {})


def register_service_views(config, service):
    """Register the routes of the given service into the pyramid router.

    :param config: the pyramid configuration object that will be populated.
    :param service: the service object containing the definitions
    """
    services = config.registry.setdefault('cornice_services', {})
    services[service.path] = service

    # keep track of the registered routes
    registered_routes = []

    # before doing anything else, register a view for the OPTIONS method
    # if we need to
    if service.cors_support and 'OPTIONS' not in service.defined_methods:
        service.add_view('options', view=get_options_view(service))

    # register the fallback view, which takes care of returning good error
    # messages to the user-agent
    for method, view, args in service.definitions:

        args = dict(args)  # make a copy of the dict to not modify it
        args['request_method'] = method

        decorated_view = decorate_view(view, dict(args), method)
        for item in ('filters', 'validators', 'schema', 'klass',
                     'error_handler', 'cors_headers', 'cors_support',
                     'cors_origins', 'cors_allow_credentials'):
            if item in args:
                del args[item]

        # if acl is present, then convert it to a "factory"
        if 'acl' in args:
            args["factory"] = make_route_factory(args.pop('acl'))

        route_args = {}
        if 'factory' in args:
            route_args['factory'] = args.pop('factory')

        # register the route name with the path if it's not already done
        if service.path not in registered_routes:
            config.add_route(service.path, service.path, **route_args)
            config.add_view(view=get_fallback_view(service),
                            route_name=service.path)
            registered_routes.append(service.path)

        # loop on the accept fields: we need to build custom predicate if
        # callables were passed
        if 'accept' in args:
            for accept in to_list(args.pop('accept', ())):
                predicates = args.get('custom_predicates', [])
                if callable(accept):
                    predicate_checker = functools.partial(match_accept_header,
                                                          accept)
                    predicates.append(predicate_checker)
                    args['custom_predicates'] = predicates
                else:
                    # otherwise it means that it is a "standard" accept,
                    # so add it as such.
                    args['accept'] = accept

                # We register multiple times the same view with different
                # accept / custom_predicates arguments
                config.add_view(view=decorated_view, route_name=service.path,
                                **args)
        else:
            # it is a simple view, we don't need to loop on the definitions
            # and just add it one time.
            config.add_view(view=decorated_view, route_name=service.path,
                            **args)
