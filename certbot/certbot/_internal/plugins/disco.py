"""Utilities for plugins discovery and selection."""
from collections.abc import Mapping
import itertools
import logging
import sys
from typing import Dict
from typing import Optional
from typing import Union

import pkg_resources
import zope.interface
import zope.interface.verify

from certbot import errors
from certbot import interfaces
from certbot._internal import constants
from certbot.compat import os
from certbot.errors import Error

logger = logging.getLogger(__name__)

PREFIX_FREE_DISTRIBUTIONS = [
    "certbot",
    "certbot-apache",
    "certbot-dns-cloudflare",
    "certbot-dns-cloudxns",
    "certbot-dns-digitalocean",
    "certbot-dns-dnsimple",
    "certbot-dns-dnsmadeeasy",
    "certbot-dns-gehirn",
    "certbot-dns-google",
    "certbot-dns-linode",
    "certbot-dns-luadns",
    "certbot-dns-nsone",
    "certbot-dns-ovh",
    "certbot-dns-rfc2136",
    "certbot-dns-route53",
    "certbot-dns-sakuracloud",
    "certbot-nginx",
]
"""Distributions for which prefix will be omitted."""


class PluginEntryPoint:
    """Plugin entry point."""

    # this object is mutable, don't allow it to be hashed!
    __hash__ = None  # type: ignore

    def __init__(self, entry_point: pkg_resources.EntryPoint, with_prefix=False):
        self.name = self.entry_point_to_plugin_name(entry_point, with_prefix)
        self.plugin_cls: interfaces.IPluginFactory = entry_point.load()
        self.entry_point = entry_point
        self.warning_message: Optional[str] = None
        self._initialized: Optional[interfaces.IPlugin] = None
        self._prepared: Optional[Union[bool, Error]] = None
        self._hidden = False
        self._long_description: Optional[str] = None

    def check_name(self, name):
        """Check if the name refers to this plugin."""
        if name == self.name:
            if self.warning_message:
                logger.warning(self.warning_message)
            return True
        return False

    @classmethod
    def entry_point_to_plugin_name(cls, entry_point, with_prefix):
        """Unique plugin name for an ``entry_point``"""
        if with_prefix:
            return entry_point.dist.key + ":" + entry_point.name
        return entry_point.name

    @property
    def description(self):
        """Description of the plugin."""
        return self.plugin_cls.description

    @property
    def description_with_name(self):
        """Description with name. Handy for UI."""
        return "{0} ({1})".format(self.description, self.name)

    @property
    def long_description(self):
        """Long description of the plugin."""
        if self._long_description:
            return self._long_description
        try:
            return self.plugin_cls.long_description
        except AttributeError:
            return self.description

    @long_description.setter
    def long_description(self, description):
        self._long_description = description

    @property
    def hidden(self):
        """Should this plugin be hidden from UI?"""
        return self._hidden or getattr(self.plugin_cls, "hidden", False)

    @hidden.setter
    def hidden(self, hide):
        self._hidden = hide

    def ifaces(self, *ifaces_groups):
        """Does plugin implements specified interface groups?"""
        return not ifaces_groups or any(
            all(iface.implementedBy(self.plugin_cls)
                for iface in ifaces)
            for ifaces in ifaces_groups)

    @property
    def initialized(self):
        """Has the plugin been initialized already?"""
        return self._initialized is not None

    def init(self, config=None):
        """Memoized plugin initialization."""
        if not self.initialized:
            self.entry_point.require()  # fetch extras!
            # TODO: remove type ignore once the interface becomes a proper
            #  abstract class (using abc) that mypy understands.
            self._initialized = self.plugin_cls(config, self.name)  # type: ignore
        return self._initialized

    def verify(self, ifaces):
        """Verify that the plugin conforms to the specified interfaces."""
        if not self.initialized:
            raise ValueError("Plugin is not initialized.")
        for iface in ifaces:  # zope.interface.providedBy(plugin)
            try:
                zope.interface.verify.verifyObject(iface, self.init())
            except zope.interface.exceptions.BrokenImplementation as error:
                if iface.implementedBy(self.plugin_cls):
                    logger.debug(
                        "%s implements %s but object does not verify: %s",
                        self.plugin_cls, iface.__name__, error, exc_info=True)
                return False
        return True

    @property
    def prepared(self):
        """Has the plugin been prepared already?"""
        if not self.initialized:
            logger.debug(".prepared called on uninitialized %r", self)
        return self._prepared is not None

    def prepare(self):
        """Memoized plugin preparation."""
        if self._initialized is None:
            raise ValueError("Plugin is not initialized.")
        if self._prepared is None:
            try:
                # TODO: remove type ignore once the interface becomes a proper
                #  abstract class (using abc) that mypy understands.
                self._initialized.prepare()  # type: ignore
            except errors.MisconfigurationError as error:
                logger.debug("Misconfigured %r: %s", self, error, exc_info=True)
                self._prepared = error
            except errors.NoInstallationError as error:
                logger.debug(
                    "No installation (%r): %s", self, error, exc_info=True)
                self._prepared = error
            except errors.PluginError as error:
                logger.debug("Other error:(%r): %s", self, error, exc_info=True)
                self._prepared = error
            else:
                self._prepared = True
        return self._prepared

    @property
    def misconfigured(self):
        """Is plugin misconfigured?"""
        return isinstance(self._prepared, errors.MisconfigurationError)

    @property
    def problem(self):
        """Return the Exception raised during plugin setup, or None if all is well"""
        if isinstance(self._prepared, Exception):
            return self._prepared
        return None

    @property
    def available(self):
        """Is plugin available, i.e. prepared or misconfigured?"""
        return self._prepared is True or self.misconfigured

    def __repr__(self):
        return "PluginEntryPoint#{0}".format(self.name)

    def __str__(self):
        lines = [
            "* {0}".format(self.name),
            "Description: {0}".format(self.plugin_cls.description),
            "Interfaces: {0}".format(", ".join(
                iface.__name__ for iface in zope.interface.implementedBy(
                    self.plugin_cls))),
            "Entry point: {0}".format(self.entry_point),
        ]

        if self.initialized:
            lines.append("Initialized: {0}".format(self.init()))
            if self.prepared:
                lines.append("Prep: {0}".format(self.prepare()))

        return "\n".join(lines)


class PluginsRegistry(Mapping):
    """Plugins registry."""

    def __init__(self, plugins):
        # plugins are sorted so the same order is used between runs.
        # This prevents deadlock caused by plugins acquiring a lock
        # and ensures at least one concurrent Certbot instance will run
        # successfully.
        self._plugins = dict(sorted(plugins.items()))

    @classmethod
    def find_all(cls):
        """Find plugins using setuptools entry points."""
        plugins: Dict[str, PluginEntryPoint] = {}
        plugin_paths_string = os.getenv('CERTBOT_PLUGIN_PATH')
        plugin_paths = plugin_paths_string.split(':') if plugin_paths_string else []
        # XXX should ensure this only happens once
        sys.path.extend(plugin_paths)
        for plugin_path in plugin_paths:
            pkg_resources.working_set.add_entry(plugin_path)
        entry_points = itertools.chain(
            pkg_resources.iter_entry_points(
                constants.SETUPTOOLS_PLUGINS_ENTRY_POINT),
            pkg_resources.iter_entry_points(
                constants.OLD_SETUPTOOLS_PLUGINS_ENTRY_POINT),)
        for entry_point in entry_points:
            plugin_ep = cls._load_entry_point(entry_point, plugins, with_prefix=False)
            # entry_point.dist cannot be None here, we would have blown up
            # earlier, however, this assertion is needed for mypy.
            assert entry_point.dist is not None
            if entry_point.dist.key not in PREFIX_FREE_DISTRIBUTIONS:
                prefixed_plugin_ep = cls._load_entry_point(entry_point, plugins, with_prefix=True)
                prefixed_plugin_ep.hidden = True
                message = (
                    "Plugin legacy name {0} may be removed in a future version. "
                    "Please use {1} instead.").format(prefixed_plugin_ep.name, plugin_ep.name)
                prefixed_plugin_ep.warning_message = message
                prefixed_plugin_ep.long_description = "(WARNING: {0}) {1}".format(
                    message, prefixed_plugin_ep.long_description)

        return cls(plugins)

    @classmethod
    def _load_entry_point(cls, entry_point, plugins, with_prefix):
        plugin_ep = PluginEntryPoint(entry_point, with_prefix)
        if plugin_ep.name in plugins:
            other_ep = plugins[plugin_ep.name]
            plugin1 = plugin_ep.entry_point.dist.key if plugin_ep.entry_point.dist else "unknown"
            plugin2 = other_ep.entry_point.dist.key if other_ep.entry_point.dist else "unknown"
            raise Exception("Duplicate plugin name {0} from {1} and {2}.".format(
                plugin_ep.name, plugin1, plugin2))
        if interfaces.IPluginFactory.providedBy(plugin_ep.plugin_cls):
            plugins[plugin_ep.name] = plugin_ep
        else:  # pragma: no cover
            logger.warning(
                "%r does not provide IPluginFactory, skipping", plugin_ep)

        return plugin_ep

    def __getitem__(self, name):
        return self._plugins[name]

    def __iter__(self):
        return iter(self._plugins)

    def __len__(self):
        return len(self._plugins)

    def init(self, config):
        """Initialize all plugins in the registry."""
        return [plugin_ep.init(config) for plugin_ep
                in self._plugins.values()]

    def filter(self, pred):
        """Filter plugins based on predicate."""
        return type(self)({name: plugin_ep for name, plugin_ep
                               in self._plugins.items() if pred(plugin_ep)})

    def visible(self):
        """Filter plugins based on visibility."""
        return self.filter(lambda plugin_ep: not plugin_ep.hidden)

    def ifaces(self, *ifaces_groups):
        """Filter plugins based on interfaces."""
        return self.filter(lambda p_ep: p_ep.ifaces(*ifaces_groups))

    def verify(self, ifaces):
        """Filter plugins based on verification."""
        return self.filter(lambda p_ep: p_ep.verify(ifaces))

    def prepare(self):
        """Prepare all plugins in the registry."""
        return [plugin_ep.prepare() for plugin_ep in self._plugins.values()]

    def available(self):
        """Filter plugins based on availability."""
        return self.filter(lambda p_ep: p_ep.available)
        # successfully prepared + misconfigured

    def find_init(self, plugin):
        """Find an initialized plugin.

        This is particularly useful for finding a name for the plugin
        (although `.IPluginFactory.__call__` takes ``name`` as one of
        the arguments, ``IPlugin.name`` is not part of the interface)::

          # plugin is an instance providing IPlugin, initialized
          # somewhere else in the code
          plugin_registry.find_init(plugin).name

        Returns ``None`` if ``plugin`` is not found in the registry.

        """
        # use list instead of set because PluginEntryPoint is not hashable
        candidates = [plugin_ep for plugin_ep in self._plugins.values()
                      if plugin_ep.initialized and plugin_ep.init() is plugin]
        assert len(candidates) <= 1
        if candidates:
            return candidates[0]
        return None

    def __repr__(self):
        return "{0}({1})".format(
            self.__class__.__name__, ','.join(
                repr(p_ep) for p_ep in self._plugins.values()))

    def __str__(self):
        if not self._plugins:
            return "No plugins"
        return "\n\n".join(str(p_ep) for p_ep in self._plugins.values())
