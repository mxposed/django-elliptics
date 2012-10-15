import requests
import urllib
from cStringIO import StringIO

from django import conf
from django.core.files import base, storage

class BaseError (Exception):
    """Generic error for EllipticsStorage backend."""

class ModeError (BaseError):
    """File operation incompatible with file access mode."""
    def __init__(self, message):
        super(ModeError, self).__init__(message)

class HTTPError (BaseError):
    """Elliptics request failed."""
    def __init__(self, response):
        self.response = response

class SaveError (HTTPError):
    """Failed to store file to the backend."""
    def __str__(self):
        return 'got status code %s while sending to %s' % (
            self.resposne.status_code, self.response.url)

class ReadError (HTTPError):
    """Failed to read from the backend."""

    def __str__(self):
        return 'got status code %s while reading %s' % (
            self.response.status_code, self.response.url)

class EllipticsStorage (storage.Storage):
    """Django file storage backend for Elliptics via HTTP API.

    Configuration parameters:

    ELLIPTICS_PUBLIC_URL - URL pointing to public interface of the Elliptics cluster to serve files from.
    ELLIPTICS_PRIVATE_URL - URL to send modification requests to.
    """

    default_settings = {
        'public_url': 'http://localhost:8080/',
        'private_url': 'http://localhost:9000/',
    }

    def __init__(self, **kwargs):
        self.settings = self._build_settings(kwargs)
        self.session = requests.session()
        self.session.config['keep_alive'] = False
	
    def _build_settings(self, settings):
        return type('settings', (), dict(
            (name, settings.get(name, self._get_default(name)))
            for name in self.default_settings))

    def _get_default(self, name):
        setting_name = 'ELLIPTICS_%s' % (name.upper(),)
        return getattr(conf.settings, setting_name, self.default_settings[name])

    def delete(self, name):
        url = self._make_private_url('delete', name)
        self.session.get(url)

    def exists(self, name):
        url = self._make_private_url('get', name)
        r = self.session.head(url)
        return r.status_code == 200

    def url(self, name):
        return self._make_public_url('get', name)

    def _open(self, name, mode):
        return EllipticsFile(name, self, mode)

    def _save(self, name, content, append=False):
        args = {}

        if append:
            args['ioflags'] = 2 # DNET_IO_FLAGS_APPEND = (1<<1)

        url = self._make_private_url('upload', name, **args)
        r = self.session.post(url, data=content)

        if r.status_code != 200:
            raise SaveError(r)

        return name

    def _fetch(self, name):
        url = self._make_private_url('get', name)
        r = self.session.get(url)
        if r.status_code != 200:
            raise ReadError(r)

        return r.content

    def _make_private_url(self, *parts, **args):
        return self._make_url(self.settings.private_url, *parts, **args)

    def _make_public_url(self, *parts, **args):
        return self._make_url(self.settings.public_url, *parts, **args)

    def _make_url(self, *parts, **args):
        need_slash = False
        url = '/'.join(part.strip('/') for part in parts)

        if args:
            url += '?' + urllib.urlencode(args)

        return url

class EllipticsFile (base.File):
    def __init__(self, name, storage, mode):
        self.name = name
        self._storage = storage
        self._stream = None

        if 'r' in mode:
            self._mode = 'r'
        elif 'w' in mode:
            self._mode = 'w'
        elif 'a' in mode:
            self._mode = 'a'
        else:
            raise ValueError, 'mode must contain at least one of "r", "w" or "a"'

        if '+' in mode:
            raise ValueError, 'mixed mode access not supported yet.'

    def read(self, num_bytes=None):
        if self._mode != 'r':
            raise ModeError('reading from a file opened for writing.')

        if self._stream is None:
            content = self._storage._fetch(self.name)
            self._stream = StringIO(content)

        if num_bytes is None:
            return self._stream.read()

        return self._stream.read(num_bytes)

    def write(self, content):
        if self._mode not in ('w', 'a'):
            raise ModeError('writing to a file opened for reading.')

        if self._stream is None:
            self._stream = StringIO()

        return self._stream.write(content)

    def close(self):
        if self._stream is None:
            return

        if self._mode in ('w', 'a'):
            self._storage._save(self.name, self._stream.getvalue(), append=(self._mode == 'a'))

    @property
    def size(self):
        raise NotImplementedError

    @property
    def closed(self):
        return bool(self._stream is None)

    def seek(self, offset, mode=0):
        self._stream.seek(offset, mode)