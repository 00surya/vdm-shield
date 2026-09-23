"""Local online licence client. Secrets are never returned to the browser."""
import hashlib
import json
import os
import platform
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class LicenceError(RuntimeError):
    pass


class Denied(LicenceError):
    pass


class SecureStore:
    def __init__(self, server, instance):
        self.service = 'VDM device ' + hashlib.sha256((server + '|' + str(instance)).encode()).hexdigest()[:24]

    def backend(self):
        # Explicit native backends: never accept an installed plaintext fallback.
        try:
            if platform.system() == 'Darwin':
                from keyring.backends.macOS import Keyring
                return Keyring()
            if platform.system() == 'Windows':
                from keyring.backends.Windows import WinVaultKeyring
                return WinVaultKeyring()
        except ImportError:
            pass
        raise LicenceError('Native secure storage unavailable. Install the desktop dependencies on macOS or Windows.')

    def read(self):
        try:
            raw = self.backend().get_password(self.service, 'device')
            return json.loads(raw) if raw else {}
        except LicenceError:
            raise
        except Exception:
            raise LicenceError('Cannot read secure storage. Unlock your operating system credential store.') from None

    def write(self, value):
        try:
            self.backend().set_password(self.service, 'device', json.dumps(value))
        except LicenceError:
            raise
        except Exception:
            raise LicenceError('Cannot save credentials securely. Check operating system permissions.') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class CloudTransport:
    def __init__(self, server, allow_local=False):
        url = urlsplit(server)
        if url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}:
            raise ValueError('Use a cloud server origin without credentials, path or query.')
        if not url.hostname or (url.scheme != 'https' and not (allow_local and url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost', '::1'})):
            raise ValueError('Cloud licensing requires HTTPS. Local HTTP requires VDM_CLOUD_ALLOW_LOCAL=1.')
        self.server = server.rstrip('/')
        self.opener = build_opener(NoRedirect())

    def __call__(self, path, data, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = Request(self.server + path, json.dumps(data).encode(), headers, method='POST')
        try:
            with self.opener.open(req, timeout=8) as response:
                result = json.loads(response.read(16384))
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise Denied('Licence rejected. It may have expired or been revoked; contact your administrator.') from None
            raise LicenceError('Licence server request failed. Try again shortly.') from None
        except (OSError, URLError, ValueError):
            raise LicenceError('Cannot reach the licence server. Check your internet connection.') from None


class LicenceClient:
    def __init__(self, server='', instance='', store=None, transport=None, clock=time.monotonic):
        self.managed = bool(server)
        self.server = server.rstrip('/')
        self.context = {}
        self.scope_changed = None
        self.store = store or SecureStore(server, instance)
        self.transport = transport or (CloudTransport(server, os.getenv('VDM_CLOUD_ALLOW_LOCAL') == '1') if server else None)
        self.clock = clock
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.deadline = 0
        self.next_check = 0
        self.camera_limit = 0
        self.device_id = None
        self.expires = None
        self.message = 'Activation required.' if self.managed else 'Development mode: cloud licensing is not configured.'
        self.metadata_provider = None
        self.next_sync = 0
        self.sync_message = 'Waiting for activation.'
        self.last_sync = None
        self.shutdown = threading.Event()

    def status(self):
        with self.lock:
            valid = not self.managed or self.clock() < self.deadline
            message = self.message
            if self.managed and not valid and message.startswith('Licence active'):
                message = 'Licence check overdue. Processing is stopped; reconnect to renew.'
            return {'managed': self.managed, 'valid': valid, 'message': message,
                    'cloud_url': self.server, **self.context,
                    'camera_limit': self.camera_limit if self.managed else 4,
                    'sync_message': self.sync_message, 'last_sync': self.last_sync,
                    'device_id': self.device_id, 'licence_expires': self.expires,
                    'remaining_seconds': max(0, int(self.deadline - self.clock())) if self.managed else None}

    def allowance(self):
        state = self.status()
        return min(4, state['camera_limit']) if state['valid'] else 0

    def apply(self, result, started):
        # Bound permission by a monotonic deadline; system clock rollback cannot extend it.
        duration = min(300, result['valid_until'] - result['server_time'])
        if duration <= 0 or not isinstance(result['camera_limit'], int) or result['camera_limit'] < 1:
            raise LicenceError('Licence response is invalid.')
        context = {key: result.get(key) for key in ('organisation_id', 'organisation_name', 'group_id', 'group_name', 'owner_email')}
        for field in ('organisation_id', 'group_id'):
            if context[field] is not None and (type(context[field]) is not int or context[field] < 1):
                raise LicenceError('Licence workspace is invalid.')
        if context['group_id'] is not None and context['organisation_id'] is None:
            raise LicenceError('Licence workspace is invalid.')
        old_scope = (self.context.get('organisation_id'), self.context.get('group_id'))
        new_scope = (context['organisation_id'], context['group_id'])
        if new_scope != old_scope and self.scope_changed:
            with self.lock:
                self.deadline = 0
            self.scope_changed(context)
        with self.lock:
            self.context = context
            self.deadline = started + duration
            self.next_check = started + min(120, duration)
            self.camera_limit = result['camera_limit']
            self.device_id = result['device_id']
            self.expires = result['licence_expires']
            self.message = 'Licence active. Online checks run automatically.'

    def activate(self, code):
        if not self.managed:
            raise LicenceError('Set VDM_CLOUD_URL before activating this device.')
        if not isinstance(code, str) or not 20 <= len(code.strip()) <= 128:
            raise LicenceError('Enter a valid activation code.')
        with self.operation:
            current = self.store.read()
            if current.get('refresh_token'):
                raise LicenceError('This installation is already activated. Revoke it in the cloud before replacing it.')
            # Probe storage before consuming a one-time code.
            self.store.write(current)
            started = self.clock()
            result = self.transport('/api/device/activate', {'activation_code': code.strip()})
            self.store.write({'refresh_token': result['refresh_token']})
            self.apply(result, started)
        return self.status()

    def renew(self):
        if not self.managed:
            return
        with self.operation:
            try:
                saved = self.store.read()
                if not saved.get('refresh_token'):
                    raise Denied('Activation required. Ask your administrator for an activation code.')
                # Persist before sending. An interrupted renewal retries the same request.
                saved.setdefault('request_id', secrets.token_urlsafe(24))
                self.store.write(saved)
                started = self.clock()
                result = self.transport('/api/device/renew', {'request_id': saved['request_id']}, saved['refresh_token'])
                self.store.write({'refresh_token': result['refresh_token']})
                self.apply(result, started)
            except (LicenceError, KeyError, TypeError, ValueError) as exc:
                with self.lock:
                    if isinstance(exc, Denied):
                        self.deadline = 0
                    self.message = str(exc) if isinstance(exc, LicenceError) else 'Invalid response from licence server.'
                    self.next_check = self.clock() + 15

    def clear(self):
        with self.operation:
            self.store.write({})
            with self.lock:
                self.deadline = 0
                self.message = 'Local activation removed. Revoke the old device on the website to free its slot.'
            if self.scope_changed:
                self.scope_changed({})
            with self.lock:
                self.context = {}

    def sync_metadata(self):
        if not self.managed or not self.metadata_provider or not self.status()['valid']:
            return
        with self.operation:
            try:
                from .metadata import validate_snapshot
                payload = validate_snapshot(self.metadata_provider())
                saved = self.store.read()
                # Finish interrupted credential rotation before sending metadata.
                if saved.get('request_id') or not saved.get('refresh_token'):
                    return
                self.transport('/api/device/metadata', payload, saved['refresh_token'])
                with self.lock:
                    self.last_sync = time.time()
                    self.sync_message = 'Device health and aggregate counts synced. No media sent.'
            except Exception:
                # Metadata errors never extend or cancel a licence; renewal checks authority.
                with self.lock:
                    self.sync_message = 'Metadata sync unavailable. It will retry automatically.'

    def start(self):
        if self.managed:
            self.thread = threading.Thread(target=self.run, name='licence-renewal', daemon=True)
            self.thread.start()

    def run(self):
        while not self.shutdown.is_set():
            if self.clock() >= self.next_check:
                self.renew()
            if self.clock() >= self.next_sync:
                self.sync_metadata()
                self.next_sync = self.clock() + 60
            self.shutdown.wait(1)

    def close(self):
        self.shutdown.set()
