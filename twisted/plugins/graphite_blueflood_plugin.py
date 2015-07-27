import logging

from twisted.application.service import IServiceMaker, Service, MultiService
from twisted.internet.endpoints import serverFromString
from twisted.internet.protocol import Factory
from twisted.internet.task import LoopingCall
from twisted.application.internet import TCPServer
from twisted.plugin import IPlugin
from twisted.python import usage, log, logfile
from twisted.web.client import Agent
from zope.interface import implementer

from bluefloodserver.protocols import MetricLineReceiver, MetricPickleReceiver
from bluefloodserver.collect import MetricCollection, ConsumeFlush, BluefloodFlush
from bluefloodserver.blueflood import BluefloodEndpoint
from carbonforwarderlogging.forwarder_log_observer import LevelFileLogObserver

from txKeystone import KeystoneAgent

class Options(usage.Options):
    AUTH_URL = 'https://identity.api.rackspacecloud.com/v2.0/tokens'
    DEFAULT_TTL = 60 * 60 * 24
    optParameters = [
        ['endpoint', 'e', 'tcp:2004', 'Twisted formatted endpoint to listen to pickle protocol metrics'],
        ['interval', 'i', 30, 'Metric send interval, sec'],
        ['blueflood', 'b', 'http://localhost:19000', 'Blueflood server ingest URL (schema, host, port)'],
        ['tenant', 't', '', 'Blueflood tenant ID'],
        ['ttl', '', DEFAULT_TTL, 'TTL value for metrics, sec'],
        ['user', 'u', '', 'Rackspace authentication username. Leave empty if no auth is required'],
        ['key', 'k', '', 'Rackspace authentication password'],
        ['auth_url', '', AUTH_URL, 'Auth URL'],
        ['limit', '', 0, 'Blueflood json payload limit, bytes. 0 means no limit'],
        ['log_dir', '', '/', 'Directory to store Carbon Forwarder logs'],
        ['log_level', '', 'DEBUG', 'Logging level for the Carbon Forwarder log messages'],
        ['log_rotate_length', '', 10000, 'Maximum size of log file'],
        ['max_rotated_files', '', 1000, 'Maximum number of log files']

    ]


class GraphiteMetricFactory(Factory):

    def __init__(self):
        flusher = ConsumeFlush()
        self._metric_collection = MetricCollection(flusher)

    def processMetric(self, metric, datapoint):
        log.msg('Receive metric {} {}:{}'.format(metric, datapoint[0], datapoint[1]), level=logging.DEBUG)
        self._metric_collection.collect(metric, datapoint)

    def flushMetric(self):
        try:
            log.msg('Sending {} metrics'.format(self._metric_collection.count()), level=logging.DEBUG)
            self._metric_collection.flush()
        except Exception, e:
            log.err(e)


class MetricService(Service):

    def __init__(self, **kwargs):
        self.protocol_cls = kwargs.get('protocol_cls')
        self.endpoint = kwargs.get('endpoint')
        self.flush_interval = kwargs.get('interval')
        self.blueflood_url = kwargs.get('blueflood_url')
        self.tenant = kwargs.get('tenant')
        self.ttl = kwargs.get('ttl')
        self.user = kwargs.get('user')
        self.key = kwargs.get('key')
        self.auth_url = kwargs.get('auth_url')
        self.limit = kwargs.get('limit', 0)
        self.port = None
        self.logLevel = kwargs.get('logLevel')
        self.rotateLength = kwargs.get('rotateLength')
        self.maxRotatedFiles = kwargs.get('maxRotatedFiles')
        self.log_dir = kwargs.get('log_dir')

    def startService(self):
        from twisted.internet import reactor

        f = logfile.LogFile("carbon_forwarder.log", self.log_dir, rotateLength=self.rotateLength, maxRotatedFiles=self.maxRotatedFiles)
        log_level = logging.getLevelName(self.logLevel)
        logger = LevelFileLogObserver(f, log_level)
        log.addObserver(logger.emit)

        server = serverFromString(reactor, self.endpoint)
        log.msg('Start listening at {}'.format(self.endpoint))
        factory = GraphiteMetricFactory.forProtocol(self.protocol_cls)
        agent = Agent(reactor)
        if self.user:
            agent = KeystoneAgent(agent, self.auth_url, (self.user, self.key))
            log.msg('Auth URL: {}, user: {}'.format(self.auth_url, self.user))
        self._setup_blueflood(factory, agent)
        self.timer = LoopingCall(factory.flushMetric)
        self.timer.start(self.flush_interval)

        d = server.listen(factory)
        d.addCallback(self._store_listening_port)
        return d

    def _store_listening_port(self, port):
        self.port = port

    def stopService(self):
        if self.port:
            self.port.stopListening()
        self.timer.stop()

    def _setup_blueflood(self, factory, agent):
        log.msg('Send metrics to {} as {} with {} sec interval'
            .format(self.blueflood_url, self.tenant, self.flush_interval))
        log.msg('Limit is {} bytes'.format(self.limit))
        client = BluefloodEndpoint(
            ingest_url=self.blueflood_url,
            tenant=self.tenant,
            agent=agent,
            limit=int(self.limit))
        flusher = BluefloodFlush(client=client, ttl=self.ttl)
        factory._metric_collection.flusher = flusher

@implementer(IServiceMaker, IPlugin)
class MetricServiceMaker(object):
    tapname = 'blueflood-forward'
    description = 'Forwarding metrics from graphite sources to Blueflood'
    options = Options

    def makeService(self, options):
        return MetricService(
            protocol_cls=MetricPickleReceiver,
            endpoint=options['endpoint'],
            interval=float(options['interval']),
            blueflood_url=options['blueflood'],
            tenant=options['tenant'],
            ttl=int(options['ttl']),
            user=options['user'],
            key=options['key'],
            auth_url=options['auth_url'],
            limit=options['limit'],
            log_dir=options['log_dir'],
            logLevel=options['log_level'],
            rotateLength=options['log_rotate_length'],
            maxRotatedFiles=options['max_rotated_files']
        )


serviceMaker = MetricServiceMaker()