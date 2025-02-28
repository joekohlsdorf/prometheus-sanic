import os

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, \
    core, multiprocess, start_http_server
from prometheus_client.exposition import generate_latest
from sanic.request import Request
from sanic.response import raw

from . import endpoint, metrics, handlers
from .constants import BaseMetrics
from .exceptions import SanicPrometheusError


class MonitorSetup:
    def __init__(self, app, metrics_path, multiprocess_on=False):
        self._app = app
        self._multiprocess_on = multiprocess_on
        self._metrics_path = metrics_path

    def expose_endpoint(self):
        """
        Expose /metrics endpoint on the same Sanic server.

        This may be useful if Sanic is launched from a container
        and you do not want to expose more than one port for some
        reason.
        """

        @self._app.route(self._metrics_path, methods=['GET'])
        async def expose_metrics(request):
            return raw(self._get_metrics_data(),
                       content_type=CONTENT_TYPE_LATEST)

    def start_server(self, addr='', port=8000):
        """
        Expose /metrics endpoint on a new server that will
        be launched on `<addr>:<port>`.

        This may be useful if you want to restrict access to
        metrics data with firewall rules.
        NOTE: can not be used in multiprocessing mode
        """
        if self._multiprocess_on:
            raise SanicPrometheusError(
                "start_server can not be used when "
                "multiprocessing is turned on"
            )
        start_http_server(addr=addr, port=port)

    def _get_metrics_data(self):
        if not self._multiprocess_on:
            registry = core.REGISTRY
        else:
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
        data = generate_latest(registry)
        return data


def monitor(app, endpoint_type='url:1',
            get_endpoint_fn=None,
            latency_buckets=None,
            multiprocess_mode='all',
            metrics_path='/metrics',
            is_middleware=True,
            metrics_list=None,
            before_handler=None,
            after_handler=None,
            base_metrics=None,
            init_metrics_object: dict = None
            ):
    """
    Regiesters a bunch of metrics for Sanic server
    (request latency, count, etc) and exposes /metrics endpoint
    to allow Prometheus to scrape them out.

    :param init_metrics_object:
    :param base_metrics:
    :param before_handler:
    :param after_handler:
    :param metrics_list:
    :param is_middleware:
    :param metrics_path:
    :param multiprocess_mode:
    :param app: an instance of sanic.app
    :param endpoint_type: All request related metrics have a label called
                         'endpoint'. It can be fetched from Sanic `request`
                          object using different strategies specified by
                          `endpoint_type`:
                            url - full relative path of a request URL
                                (i.e. for http://something/a/b/c?f=x you end up
                                having `/a/b/c` as the endpoint)
                            url:n - like URL but with at most `n` path elements
                                in the endpoint (i.e. with `url:1`
                                http://something/a/b/c becomes `/a`).
                            custom - custom endpoint fetching funciton that
                                should be specified by `get_endpoint_fn`
    :param get_endpoint_fn: a custom endpoint fetching function that is ignored
                            until `endpoint_type='custom'`.
                              get_endpoint_fn = lambda r: ...
                            where `r` is Sanic request object
    :param latency_buckets: an optional list of bucket sizes for latency
                            histogram (see prometheus `Histogram` metric)

    :multiprocess_mode':
    :metrics_path:         path where metrics will be exposed

    NOTE: memory usage is not collected when when multiprocessing is enabled
    """
    multiprocess_on = 'prometheus_multiproc_dir' in os.environ
    get_endpoint = endpoint.fn_by_type(endpoint_type, get_endpoint_fn)

    if base_metrics is None:
        base_metrics = BaseMetrics

    if before_handler is None:
        before_handler = handlers.before_request_handler

    if after_handler is None:
        after_handler = handlers.after_request_endpoint_handler

    @app.listener('before_server_start')
    def before_start(init_app, _loop):
        init_app.ctx.metrics = {}
        if init_metrics_object is not None:
            init_app.ctx.metrics = init_metrics_object
        metrics.init(
            init_app, latency_buckets, multiprocess_mode,
            metrics_list, base_metrics
        )

    if is_middleware is True:
        @app.middleware('request')
        async def before_request(request: Request):
            if request.path != metrics_path and request.method != "OPTIONS":
                before_handler(request)

        @app.middleware('response')
        async def before_response(request: Request, response):
            if request.path != metrics_path and request.method != "OPTIONS":
                after_handler(request, response, get_endpoint)

    if multiprocess_on:
        @app.listener('after_server_stop')
        def after_stop(_app, _loop):
            multiprocess.mark_process_dead(os.getpid())

    return MonitorSetup(app, metrics_path, multiprocess_on)


__all__ = ['monitor']
