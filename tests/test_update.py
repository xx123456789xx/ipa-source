"""Offline contract tests for scripts/update.py; stdlib only.

All network paths are intercepted. Fixtures are synthetic ZIP/plist bytes, never
real IPA files. The environment is cleared before every test: no user token is
read. Failing tests document requirements, rather than hiding known bugs.
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request
import warnings
import zipfile


MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'update.py'
SPEC = importlib.util.spec_from_file_location('ipa_update_under_test', MODULE_PATH)
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)
FAKE_TOKEN = 'synthetic-offline-token-not-a-real-credential'
BASE = 'https://source.example'
DOWNLOAD = 'https://downloads.example/app.ipa'
PLIST = {
    'CFBundleIdentifier': 'org.example.app',
    'CFBundleShortVersionString': '2.3.4',
    'CFBundleVersion': '204',
    'CFBundleDisplayName': 'Synthetic App',
    'MinimumOSVersion': '15.0',
}


def make_ipa(info=None, entries=None, binary=False):
    """Return a tiny in-memory synthetic IPA; never extract anything."""
    target = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            if entries is None:
                value = PLIST if info is None else info
                # XML property lists cannot encode null; None means a missing key.
                if isinstance(value, dict):
                    value = {key: item for key, item in value.items() if item is not None}
                payload = plistlib.dumps(value,
                                         fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML)
                entries = [('Payload/Synthetic.app/Info.plist', payload)]
            for name, data in entries:
                archive.writestr(name, data)
    return target.getvalue()


class Response(io.BytesIO):
    def __init__(self, data=b'', headers=None):
        super().__init__(data)
        self.headers = headers or {}


def asset(name='App.ipa', ident=1, url=DOWNLOAD, size=123, **extra):
    result = dict(name=name, id=ident, browser_download_url=url,
                  size=size, updated_at='2026-01-01T00:00:00Z', state='uploaded')
    result.update(extra)
    return result


def release(tag='v1', published='2026-01-01T00:00:00Z', assets=None, **extra):
    result = dict(tag_name=tag, published_at=published, draft=False,
                  prerelease=False, body='Synthetic release',
                  assets=[asset()] if assets is None else assets)
    result.update(extra)
    return result


class OfflineCase(unittest.TestCase):
    def setUp(self):
        # Do not inspect, copy, or print real environment values.
        self.enterContext(mock.patch.dict(os.environ, {}, clear=True))
        self.net = self.enterContext(mock.patch.object(
            update.OPENER, 'open', side_effect=AssertionError('Network forbidden in offline tests')))
        self.enterContext(mock.patch('urllib.request.urlopen',
                                     side_effect=AssertionError('Network forbidden in offline tests')))
        for target in ['socket.create_connection', 'socket.getaddrinfo',
                       'socket.socket.connect', 'socket.socket.connect_ex']:
            self.enterContext(mock.patch(target,
                                         side_effect=AssertionError('Network forbidden in offline tests')))


class URLAndCredentialTests(OfflineCase):
    def test_https_public_urls_accepted(self):
        for url in [DOWNLOAD, 'https://github.com/a/b', 'https://8.8.8.8/a.ipa']:
            with self.subTest(url=url):
                self.assertEqual(update.https_url(url), url)

    def test_invalid_urls_rejected_before_network(self):
        for url in [None, 1, '', 'http://example.com/a', 'file:///tmp/x',
                    'https:///missing', 'https://u:p@example.com/a',
                    'https://localhost/a', 'https://thing.local/a',
                    'https://a.localhost/a', 'https://127.0.0.1/a',
                    'https://10.0.0.1/a', 'https://169.254.169.254/a',
                    'https://[::1]/a', 'https://[fc00::1]/a']:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    update.request(url, 7)
        self.net.assert_not_called()

    def test_trailing_dot_localhost_rejected(self):
        with self.assertRaises(ValueError):
            update.https_url('https://localhost./app.ipa')

    def test_noncanonical_loopback_rejected(self):
        for host in ['2130706433', '0x7f000001', '127.1']:
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    update.https_url('https://' + host + '/app.ipa')

    def test_repo_normalization(self):
        for value in ['owner/project', 'owner/project.git',
                      'https://github.com/owner/project',
                      'https://github.com/owner/project.git/']:
            with self.subTest(value=value):
                self.assertEqual(update.repo_name(value), 'owner/project')

    def test_invalid_repo_never_requests(self):
        values = [None, '', 'one', '../repo', 'owner/..', 'a/b/c',
                  'https://evil.example/a/b', 'https://github.com/a',
                  'https://github.com/a/b?token=synthetic', 'a/b\nInjected']
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    update.resolve_github({'repo': value}, 7)
        self.net.assert_not_called()

    def test_api_request_has_only_synthetic_token_and_expected_headers(self):
        with mock.patch.dict(os.environ, {'GITHUB_TOKEN': FAKE_TOKEN}):
            self.net.side_effect = None
            self.net.return_value = Response(b'[]')
            update.request('https://api.github.com/repos/a/b/releases', 12, api=True)
        req = self.net.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + FAKE_TOKEN)
        self.assertEqual(req.get_header('Accept'), 'application/vnd.github+json')
        self.assertEqual(self.net.call_args.kwargs['timeout'], 12)

    def test_download_never_gets_environment_token(self):
        with mock.patch.dict(os.environ, {'GITHUB_TOKEN': FAKE_TOKEN}):
            self.net.side_effect = None
            self.net.return_value = Response()
            update.request(DOWNLOAD, 7)
        self.assertIsNone(self.net.call_args.args[0].get_header('Authorization'))

    def test_api_token_refused_for_non_api_host(self):
        with mock.patch.dict(os.environ, {'GITHUB_TOKEN': FAKE_TOKEN}):
            for host in ['downloads.example', 'api.github.com.evil.example', 'github.com']:
                with self.subTest(host=host), self.assertRaises(ValueError):
                    update.request('https://' + host + '/x', 7, api=True)
        self.net.assert_not_called()

    def test_no_token_header_when_environment_empty(self):
        self.net.side_effect = None
        self.net.return_value = Response()
        update.request('https://api.github.com/x', 7, api=True)
        self.assertIsNone(self.net.call_args.args[0].get_header('Authorization'))

    def test_cross_host_redirect_strips_authorization(self):
        req = urllib.request.Request('https://api.github.com/a',
                                     headers={'Authorization': 'Bearer ' + FAKE_TOKEN})
        redirected = update.SafeRedirect().redirect_request(req, None, 302, 'Found', {}, DOWNLOAD)
        self.assertIsNone(redirected.get_header('Authorization'))
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + FAKE_TOKEN)

    def test_same_api_host_redirect_can_keep_authorization(self):
        req = urllib.request.Request('https://api.github.com/a',
                                     headers={'Authorization': 'Bearer ' + FAKE_TOKEN})
        redirected = update.SafeRedirect().redirect_request(
            req, None, 302, 'Found', {}, 'https://api.github.com/b')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer ' + FAKE_TOKEN)

    def test_unsafe_redirect_rejected(self):
        req = urllib.request.Request('https://api.github.com/a')
        for url in ['http://downloads.example/x', 'https://127.0.0.1/x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                update.SafeRedirect().redirect_request(req, None, 302, 'Found', {}, url)

    def test_github_http_error_sanitized(self):
        error = urllib.error.HTTPError('https://api.github.com/x?secret=synthetic',
                                       403, FAKE_TOKEN, {}, io.BytesIO(FAKE_TOKEN.encode()))
        self.net.side_effect = error
        with self.assertRaises(ValueError) as caught:
            update.github_json('https://api.github.com/x', 7)
        self.assertIn('403', str(caught.exception))
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))
        self.assertNotIn('secret=', str(caught.exception))

    def test_github_metadata_size_limit(self):
        with mock.patch.object(update, 'request', return_value=Response(b'x' * (8 * 1024 * 1024 + 1))):
            with self.assertRaises(ValueError):
                update.github_json('https://api.github.com/x', 7)


class ReleaseTests(OfflineCase):
    def test_newest_stable_selected_by_publication_not_input_order(self):
        releases = [release('old'), release('pre', '2026-03-01', prerelease=True),
                    release('new', '2026-02-01'), release('draft', '2026-04-01', draft=True)]
        self.assertEqual(update.pick_release(releases, {})['release_tag'], 'new')

    def test_prerelease_opt_in_selects_newest(self):
        releases = [release('stable'), release('pre', '2026-03-01', prerelease=True),
                    release('draft', '2026-04-01', draft=True)]
        self.assertEqual(update.pick_release(releases, {'include_prereleases': True})['release_tag'], 'pre')

    def test_release_created_date_fallback(self):
        picked = update.pick_release([release(published=None, created_at='2026-02-01')], {})
        self.assertEqual(picked['release_date'], '2026-02-01')

    def test_newest_without_matching_asset_falls_back_to_eligible_release(self):
        releases = [release('latest', '2026-02-01', assets=[asset('source.zip')]), release('older')]
        self.assertEqual(update.pick_release(releases, {})['release_tag'], 'older')

    def test_ambiguous_glob_fails_instead_of_guessing_or_falling_back(self):
        releases = [release('ambiguous', '2026-02-01', assets=[asset('A.ipa'), asset('B.ipa')]), release()]
        with self.assertRaisesRegex(ValueError, 'asset_pattern'):
            update.pick_release(releases, {})

    def test_precise_glob_selects_asset(self):
        picked = update.pick_release([release(assets=[asset('A.ipa'), asset('B.ipa', ident=2)])],
                                     {'asset_pattern': 'B.ipa'})
        self.assertEqual(picked['fingerprint'][0], 2)

    def test_glob_is_case_sensitive_and_non_ipa_assets_ignored(self):
        with self.assertRaises(ValueError):
            update.pick_release([release(assets=[asset('App.IPA'), asset('App.zip')])], {})
        self.assertEqual(update.pick_release([release(assets=[asset('App.IPA')])],
                                            {'asset_pattern': '*.IPA'})['url'], DOWNLOAD)

    def test_unuploaded_asset_ignored(self):
        with self.assertRaises(ValueError):
            update.pick_release([release(assets=[asset(state='new')])], {})

    def test_invalid_glob_rejected(self):
        for pattern in ['', None, 8]:
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                update.pick_release([], {'asset_pattern': pattern})

    def test_empty_release_list_reports_no_match(self):
        with self.assertRaisesRegex(ValueError, 'IPA'):
            update.pick_release([], {})

    def test_release_description_bounded(self):
        self.assertEqual(len(update.pick_release([release(body='a' * 13000)], {})['description']), 12000)

    def test_resolve_uses_bounded_public_api_and_stable_key(self):
        with mock.patch.object(update, 'github_json',
                               side_effect=[[release()], {'description': 'Demo app'}]) as get:
            resolved = update.resolve_github({'repo': 'a/b'}, 14)
        self.assertEqual(get.call_count, 2)
        get.assert_any_call('https://api.github.com/repos/a/b/releases?per_page=100', 14)
        get.assert_any_call('https://api.github.com/repos/a/b', 14)
        self.assertEqual(resolved['key'], 'github:a/b')
        self.assertEqual(resolved['project_url'], 'https://github.com/a/b')
        self.assertEqual(resolved['repo_description'], 'Demo app')

    def test_resolve_tolerates_repo_info_failure(self):
        with mock.patch.object(update, 'github_json',
                               side_effect=[[release()], ValueError('boom')]):
            resolved = update.resolve_github({'repo': 'a/b'}, 14)
        self.assertNotIn('repo_description', resolved)
        with mock.patch.object(update, 'github_json',
                               side_effect=[[release()], ['not-a-dict']]):
            resolved = update.resolve_github({'repo': 'a/b'}, 14)
        self.assertNotIn('repo_description', resolved)

    def test_bad_api_shape_rejected(self):
        with mock.patch.object(update, 'github_json', return_value={'message': 'not a list'}):
            with self.assertRaises(ValueError):
                update.resolve_github({'repo': 'a/b'}, 7)


class IPATests(OfflineCase):
    def inspect(self, info=None, entries=None, binary=False):
        return update.inspect_ipa(io.BytesIO(make_ipa(info, entries, binary)))

    def test_xml_and_binary_plists(self):
        for binary in [False, True]:
            with self.subTest(binary=binary):
                metadata = self.inspect(binary=binary)
                self.assertEqual(metadata, dict(bundleIdentifier='org.example.app', version='2.3.4',
                                               buildVersion='204', name='Synthetic App', minOSVersion='15.0'))

    def test_build_number_used_when_short_version_missing(self):
        info = dict(PLIST)
        del info['CFBundleShortVersionString']
        self.assertEqual(self.inspect(info)['version'], '204')

    def test_short_version_remains_valid_without_build_number(self):
        info = dict(PLIST)
        del info['CFBundleVersion']
        metadata = self.inspect(info)
        self.assertEqual(metadata['version'], '2.3.4')
        # Never invent a build value absent from CFBundleVersion.
        self.assertNotIn('buildVersion', metadata)

    def test_name_falls_back_to_bundle_name_then_identifier(self):
        info = dict(PLIST, CFBundleName='Fallback Name')
        del info['CFBundleDisplayName']
        self.assertEqual(self.inspect(info)['name'], 'Fallback Name')
        del info['CFBundleName']
        self.assertEqual(self.inspect(info)['name'], PLIST['CFBundleIdentifier'])

    def test_missing_version_rejected(self):
        info = {'CFBundleIdentifier': 'org.example.app'}
        with self.assertRaisesRegex(ValueError, 'Version'):
            self.inspect(info)

    def test_bad_bundle_ids_rejected(self):
        for bundle in [None, '', 'single', 'a..b', '.a.b', 'a.b_', '../a.b']:
            with self.subTest(bundle=bundle), self.assertRaises(ValueError):
                self.inspect(dict(PLIST, CFBundleIdentifier=bundle))

    def test_non_dictionary_plist_rejected(self):
        with self.assertRaisesRegex(ValueError, '字典'):
            self.inspect(['not', 'a dictionary'])

    def test_invalid_minimum_os_not_published(self):
        for minimum in ['15.x', '15.1.2.3', 15, None]:
            with self.subTest(minimum=minimum):
                self.assertNotIn('minOSVersion', self.inspect(dict(PLIST, MinimumOSVersion=minimum)))

    def test_valid_minimum_os_preserved(self):
        for minimum in ['15', '15.1', '15.1.2']:
            with self.subTest(minimum=minimum):
                self.assertEqual(self.inspect(dict(PLIST, MinimumOSVersion=minimum))['minOSVersion'], minimum)

    def test_only_exact_root_app_info_is_used(self):
        root = plistlib.dumps(PLIST)
        entries = [('Payload/Main.app/Info.plist', root),
                   ('Payload/Main.app/PlugIns/Extra.appex/Info.plist', b'not a plist'),
                   ('Payload/Main.app/Frameworks/Framework.framework/Info.plist', b'bad')]
        self.assertEqual(self.inspect(entries=entries)['bundleIdentifier'], 'org.example.app')

    def test_missing_nested_wrong_case_and_multiple_roots_rejected(self):
        root = plistlib.dumps(PLIST)
        cases = [[], [('Info.plist', root)], [('Payload/A.app/Sub/Info.plist', root)],
                 [('payload/A.app/Info.plist', root)],
                 [('Payload/A.app/Info.plist', root), ('Payload/B.app/Info.plist', root)],
                 [('Payload/A.app/Info.plist', root), ('Payload/A.app/Info.plist', root)]]
        for entries in cases:
            with self.subTest(names=[name for name, _ in entries]), self.assertRaises(ValueError):
                self.inspect(entries=entries)

    def test_zip_traversal_never_extracts(self):
        entries = [('Payload/A.app/Info.plist', plistlib.dumps(PLIST)),
                   ('../../outside.txt', b'never extract'), ('/absolute-outside.txt', b'never extract'),
                   ('Payload/../Escape.app/Info.plist', b'not root')]
        with mock.patch.object(zipfile.ZipFile, 'extract', side_effect=AssertionError('extract forbidden')), \
                mock.patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('extractall forbidden')):
            with tempfile.TemporaryDirectory() as directory:
                before = list(Path(directory).iterdir())
                self.assertEqual(self.inspect(entries=entries)['version'], '2.3.4')
                self.assertEqual(list(Path(directory).iterdir()), before)

    def test_traversal_plist_alone_is_not_a_root(self):
        with self.assertRaises(ValueError):
            self.inspect(entries=[('Payload/../Escape.app/Info.plist', plistlib.dumps(PLIST))])

    def test_oversized_root_plist_rejected(self):
        with self.assertRaisesRegex(ValueError, '过大'):
            self.inspect(entries=[('Payload/A.app/Info.plist', b' ' * (2 * 1024 * 1024 + 1))])

    def test_malformed_plist_and_non_zip_rejected(self):
        with self.assertRaises(plistlib.InvalidFileException):
            self.inspect(entries=[('Payload/A.app/Info.plist', b'invalid plist')])
        with self.assertRaises(zipfile.BadZipFile):
            update.inspect_ipa(io.BytesIO(b'not a zip'))

    def test_download_records_exact_bytes_version_build_size_and_sha256(self):
        blob = make_ipa(binary=True)
        headers = {'Content-Length': str(len(blob)), 'Last-Modified': 'Wed, 21 Oct 2015 07:28:00 GMT'}
        with mock.patch.object(update, 'request', return_value=Response(blob, headers)) as get:
            metadata = update.download_metadata(DOWNLOAD, len(blob), 13)
        get.assert_called_once_with(DOWNLOAD, 13)
        self.assertEqual(metadata['size'], len(blob))
        self.assertEqual(metadata['sha256'], hashlib.sha256(blob).hexdigest())
        self.assertEqual(metadata['version'], '2.3.4')
        self.assertEqual(metadata['buildVersion'], '204')
        self.assertEqual(metadata['lastModified'], '2015-10-21T07:28:00+00:00')

    def test_download_declared_limit_checked_before_read(self):
        response = Response(b'x', {'Content-Length': '1001'})
        with mock.patch.object(response, 'read', side_effect=AssertionError('body must not be read')), \
                mock.patch.object(update, 'request', return_value=response):
            with self.assertRaisesRegex(ValueError, 'max_ipa_mb'):
                update.download_metadata(DOWNLOAD, 1000, 7)

    def test_stream_limit_without_or_with_false_content_length(self):
        for headers in [{}, {'Content-Length': '1'}]:
            with self.subTest(headers=headers), mock.patch.object(
                    update, 'request', return_value=Response(b'x' * 1001, headers)):
                with self.assertRaisesRegex(ValueError, 'max_ipa_mb'):
                    update.download_metadata(DOWNLOAD, 1000, 7)

    def test_bad_last_modified_ignored(self):
        with mock.patch.object(update, 'request', return_value=Response(make_ipa(), {'Last-Modified': 'nonsense'})):
            self.assertNotIn('lastModified', update.download_metadata(DOWNLOAD, 100000, 7))

    def test_download_http_error_sanitized(self):
        error = urllib.error.HTTPError(DOWNLOAD + '?secret=synthetic', 401, FAKE_TOKEN, {}, None)
        with mock.patch.object(update, 'request', side_effect=error):
            with self.assertRaises(ValueError) as caught:
                update.download_metadata(DOWNLOAD, 100000, 7)
        self.assertIn('401', str(caught.exception))
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))
        self.assertNotIn('secret=', str(caught.exception))


class GenerationTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config_path = self.directory / 'config.json'
        self.output = self.directory / 'public'
        self.cache = self.directory / 'cache.json'
        self.blob = make_ipa()
        self.stdout = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.config = {'source': {'name': 'Offline Source', 'identifier': 'org.example.source',
                                  'base_url': BASE}, 'projects': []}
        self.github = self.enterContext(mock.patch.object(
            update, 'github_json', return_value=[release(assets=[asset(size=len(self.blob))])]))
        self.download = self.enterContext(mock.patch.object(
            update, 'request', side_effect=lambda *args, **kwargs: Response(self.blob)))

    def generate(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        update.generate(self.config_path, self.output, self.cache)
        return json.loads((self.output / 'source.json').read_text())

    def contents(self):
        return {str(path.relative_to(self.directory)): path.read_bytes()
                for path in [self.output / 'source.json', self.output / 'status.json', self.cache]
                if path.exists()}

    def cache_json(self):
        return json.loads(self.cache.read_text())

    def test_empty_manifest_generates_valid_empty_source_status_and_cache(self):
        result = self.generate()
        self.assertEqual(result['apps'], [])
        self.assertEqual(result['news'], [])
        self.assertEqual(result['sourceURL'], BASE + '/source.json')
        self.assertEqual(json.loads((self.output / 'status.json').read_text()), {'apps': []})
        self.assertEqual(self.cache_json(), {'schema': 2, 'items': {}})
        self.download.assert_not_called()
        self.github.assert_not_called()

    def test_empty_manifest_without_base_url_works(self):
        del self.config['source']['base_url']
        self.assertNotIn('sourceURL', self.generate())

    def test_disabled_project_skipped_without_resolution(self):
        self.config['projects'] = [{'enabled': False, 'repo': 'invalid'}]
        self.assertEqual(self.generate()['apps'], [])
        self.github.assert_not_called()
        self.download.assert_not_called()

    def test_direct_publishes_exact_version_build_size_hash_and_no_ipa_bytes(self):
        self.config['projects'] = [{'url': DOWNLOAD}]
        app = self.generate()['apps'][0]
        version = app['versions'][0]
        self.assertEqual((version['version'], version['buildVersion'], version['size']),
                         ('2.3.4', '204', len(self.blob)))
        self.assertEqual(version['downloadURL'], DOWNLOAD)
        self.assertEqual(version['buildNumber'], '204')
        self.assertNotIn('appPermissions', app)
        status = json.loads((self.output / 'status.json').read_text())['apps'][0]
        self.assertEqual(status['sha256'], hashlib.sha256(self.blob).hexdigest())
        self.assertEqual(status['size'], len(self.blob))
        self.assertEqual(set(path.name for path in self.output.iterdir()), {'source.json', 'status.json'})
        self.assertNotIn(FAKE_TOKEN, json.dumps(self.cache_json()))

    def test_missing_build_omits_both_spellings_and_status_uses_null(self):
        info = dict(PLIST)
        del info['CFBundleVersion']
        self.blob = make_ipa(info)
        self.config['projects'] = [{'url': DOWNLOAD}]
        app = self.generate()['apps'][0]
        self.assertEqual(app['version'], '2.3.4')
        self.assertNotIn('buildVersion', app['versions'][0])
        self.assertNotIn('buildNumber', app['versions'][0])
        status = json.loads((self.output / 'status.json').read_text())['apps'][0]
        self.assertIsNone(status['buildVersion'])
        self.assertNotIn('buildVersion', next(iter(self.cache_json()['items'].values()))['metadata'])

    def test_app_permissions_explicit_declarations_pass_through_unchanged(self):
        declarations = {'entitlements': ['com.example.synthetic-entitlement'],
                        'privacy': [{'name': 'Camera', 'usageDescription': 'Synthetic declaration'}]}
        saved = copy.deepcopy(declarations)
        self.config['projects'] = [{'url': DOWNLOAD, 'app_permissions': declarations}]
        self.assertEqual(self.generate()['apps'][0]['appPermissions'], saved)
        self.assertEqual(declarations, saved)

    def test_empty_app_permissions_lists_are_valid_explicit_declarations(self):
        declarations = {'entitlements': [], 'privacy': []}
        self.config['projects'] = [{'url': DOWNLOAD, 'app_permissions': declarations}]
        self.assertEqual(self.generate()['apps'][0]['appPermissions'], declarations)

    def test_invalid_app_permissions_rejected_without_replacing_outputs_or_cache(self):
        self.generate()
        before = self.contents()
        invalid = [None, [], 'text', {}, {'entitlements': []}, {'privacy': []},
                   {'entitlements': {}, 'privacy': []},
                   {'entitlements': [], 'privacy': 'not a list'},
                   {'entitlements': None, 'privacy': []}]
        for declarations in invalid:
            with self.subTest(declarations=declarations):
                self.config['projects'] = [{'url': DOWNLOAD, 'app_permissions': declarations}]
                with self.assertRaisesRegex(ValueError, 'app_permissions'):
                    self.generate()
                self.assertEqual(self.contents(), before)

    def test_bundle_lock_match_succeeds(self):
        self.config['projects'] = [{'url': DOWNLOAD, 'bundle_identifier': 'org.example.app'}]
        self.assertEqual(len(self.generate()['apps']), 1)

    def test_bundle_lock_mismatch_preserves_everything(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'url': DOWNLOAD, 'bundle_identifier': 'org.example.other'}]
        with self.assertRaisesRegex(ValueError, 'Bundle ID'):
            self.generate()
        self.assertEqual(self.contents(), before)

    def test_bundle_lock_is_rechecked_when_metadata_is_cached(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.generate()
        before = self.contents()
        self.config['projects'][0]['bundle_identifier'] = 'org.example.other'
        with self.assertRaisesRegex(ValueError, 'Bundle ID'):
            self.generate()
        self.assertEqual(self.contents(), before)
        self.assertEqual(self.download.call_count, 1)

    def test_duplicate_bundle_rejected_and_preserves_everything(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'url': DOWNLOAD}, {'url': 'https://downloads.example/other.ipa'}]
        with self.assertRaisesRegex(ValueError, '重复 Bundle ID'):
            self.generate()
        self.assertEqual(self.contents(), before)

    def test_same_github_fingerprint_reuses_metadata_but_refreshes_api(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        first = self.generate()
        self.generate()
        # Two API calls per run: releases plus the repo-info lookup used
        # for the derived subtitle. The IPA itself is downloaded once.
        self.assertEqual(self.github.call_count, 4)
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(first, json.loads((self.output / 'source.json').read_text()))
        item = self.cache_json()['items']['github:owner/repo']
        self.assertEqual(item['metadata']['sha256'], hashlib.sha256(self.blob).hexdigest())

    def test_changed_github_fingerprint_downloads_again(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.generate()
        self.github.return_value = [release(assets=[asset(ident=2, size=len(self.blob))])]
        self.generate()
        self.assertEqual(self.download.call_count, 2)

    def test_release_notes_refresh_even_when_metadata_cached(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.generate()
        self.github.return_value[0]['body'] = 'Updated public notes'
        result = self.generate()
        self.assertEqual(result['apps'][0]['versionDescription'], 'Updated public notes')
        self.assertNotIn('Updated public notes', result['apps'][0]['localizedDescription'])
        self.assertEqual(self.download.call_count, 1)

    def test_direct_urls_always_fetch_and_unchanged_content_retains_date(self):
        self.config['projects'] = [{'url': DOWNLOAD}]
        first = self.generate()['apps'][0]['versionDate']
        self.generate()
        self.assertEqual(self.download.call_count, 2)
        self.assertEqual(json.loads((self.output / 'source.json').read_text())['apps'][0]['versionDate'], first)

    def test_direct_changed_content_updates_hash_and_version(self):
        self.config['projects'] = [{'url': DOWNLOAD}]
        self.generate()
        old_hash = next(iter(self.cache_json()['items'].values()))['sha256']
        self.blob = make_ipa(dict(PLIST, CFBundleShortVersionString='3.0'))
        self.assertEqual(self.generate()['apps'][0]['version'], '3.0')
        self.assertNotEqual(next(iter(self.cache_json()['items'].values()))['sha256'], old_hash)
        self.assertEqual(self.download.call_count, 2)

    def test_corrupt_or_wrong_schema_cache_triggers_download(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        for cache in ['{broken', '{"schema":1,"items":{}}', '{"schema":99,"items":{}}']:
            with self.subTest(cache=cache):
                self.cache.write_text(cache)
                previous_calls = self.download.call_count
                self.generate()
                self.assertEqual(self.download.call_count, previous_calls + 1)
                self.assertEqual(self.cache_json()['schema'], 2)

    def test_missing_cached_metadata_triggers_download(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.generate()
        cache = self.cache_json()
        cache['items']['github:owner/repo']['metadata'] = {}
        self.cache.write_text(json.dumps(cache))
        self.generate()
        self.assertEqual(self.download.call_count, 2)

    def test_reduced_limit_rejects_cached_oversized_metadata(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.generate()
        cache = self.cache_json()
        cache['items']['github:owner/repo']['metadata']['size'] = 2 * 1024 * 1024
        self.cache.write_text(json.dumps(cache))
        self.config['limits'] = {'max_ipa_mb': 1}
        before = self.contents()
        with self.assertRaisesRegex(ValueError, 'max_ipa_mb'):
            self.generate()
        self.assertEqual(self.contents(), before)
        self.assertEqual(self.download.call_count, 1)

    def test_removed_projects_pruned_from_cache(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.generate()
        self.config['projects'] = []
        self.generate()
        self.assertEqual(self.cache_json()['items'], {})

    def test_advertised_oversize_rejected_before_download(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.config['limits'] = {'max_ipa_mb': 1}
        self.github.return_value = [release(assets=[asset(size=1024 * 1024 + 1)])]
        with self.assertRaisesRegex(ValueError, 'max_ipa_mb'):
            self.generate()
        self.download.assert_not_called()
        self.assertEqual(self.contents(), {})

    def test_advertised_size_mismatch_preserves_everything(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.github.return_value = [release(assets=[asset(size=len(self.blob) + 1)])]
        with self.assertRaisesRegex(ValueError, '实际大小'):
            self.generate()
        self.assertEqual(self.contents(), before)

    def test_download_failure_after_successful_project_preserves_everything(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'url': DOWNLOAD}, {'url': 'https://downloads.example/fail.ipa'}]
        self.download.side_effect = [Response(self.blob), OSError('offline simulated failure')]
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.contents(), before)

    def test_glob_ambiguity_preserves_everything(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.github.return_value = [release(assets=[asset('One.ipa'), asset('Two.ipa')])]
        with self.assertRaisesRegex(ValueError, 'asset_pattern'):
            self.generate()
        self.assertEqual(self.contents(), before)
        self.download.assert_not_called()

    def test_no_matching_release_is_fatal_and_preserves_previous_source(self):
        # Fail closed: silently omitting a previously listed app can publish a
        # misleading empty/partial source when upstream assets disappear.
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'repo': 'owner/no-ipa'}, {'url': DOWNLOAD}]
        self.github.return_value = [release(assets=[asset('source.zip')])]
        with self.assertRaisesRegex(ValueError, 'IPA'):
            self.generate()
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(self.contents(), before)

    def test_empty_release_list_blocks_publication_and_preserves_previous_source(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'repo': 'owner/empty'}]
        self.github.return_value = []
        with self.assertRaisesRegex(ValueError, 'IPA'):
            self.generate()
        self.assertEqual(self.contents(), before)
        self.download.assert_not_called()

    def test_invalid_projects_fail_without_network_or_output(self):
        for project in [None, {}, {'repo': 'a/b', 'url': DOWNLOAD},
                        {'repo': '../x'}, {'url': 'http://downloads.example/x'}]:
            with self.subTest(project=project):
                self.config['projects'] = [project]
                with self.assertRaises(ValueError):
                    self.generate()
                self.assertEqual(self.contents(), {})
        self.download.assert_not_called()
        self.github.assert_not_called()

    def test_invalid_manifest_limits_and_base_url_fail_without_download(self):
        cases = [({'projects': {}}, None),
                 ({'limits': {'max_ipa_mb': 0}}, None),
                 ({'limits': {'max_ipa_mb': 4097}}, None),
                 ({'limits': {'request_timeout_seconds': 4}}, None),
                 ({'limits': {'request_timeout_seconds': 301}}, None),
                 ({}, BASE + '/?query=x'), ({}, BASE + '/#fragment')]
        for changed, base in cases:
            with self.subTest(changed=changed, base=base):
                saved = copy.deepcopy(self.config)
                self.config.update(changed)
                if base:
                    self.config['source']['base_url'] = base
                with self.assertRaises(ValueError):
                    self.generate()
                self.config = saved
        self.download.assert_not_called()
        self.assertEqual(self.contents(), {})

    def test_icon_required_before_download_and_explicit_icon_supported(self):
        del self.config['source']['base_url']
        self.config['projects'] = [{'url': DOWNLOAD}]
        with self.assertRaises(ValueError):
            self.generate()
        self.download.assert_not_called()
        self.config['projects'][0]['icon_url'] = 'https://images.example/icon.png'
        self.assertEqual(self.generate()['apps'][0]['iconURL'], 'https://images.example/icon.png')

    def test_public_base_url_override(self):
        with mock.patch.dict(os.environ, {'SOURCE_BASE_URL': 'https://override.example/'}):
            self.assertEqual(self.generate()['sourceURL'], 'https://override.example/source.json')

    def test_network_exception_details_not_exposed(self):
        self.config['projects'] = [{'url': DOWNLOAD}]
        self.download.side_effect = OSError('synthetic URL secret ' + FAKE_TOKEN)
        with self.assertRaises(ValueError) as caught:
            self.generate()
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))
        self.assertIn('OSError', str(caught.exception))
        self.assertEqual(self.contents(), {})

    def test_unchanged_generation_does_not_replace_files(self):
        self.generate()
        with mock.patch.object(Path, 'replace', side_effect=AssertionError('unexpected replace')):
            self.generate()

    def test_status_write_failure_rolls_back_all_outputs_and_cache(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'url': DOWNLOAD}]
        original = update.write_json

        def fail_status(path, data):
            if path == self.output / 'status.json':
                raise OSError('simulated full disk writing status')
            return original(path, data)

        with mock.patch.object(update, 'write_json', side_effect=fail_status):
            with self.assertRaises(OSError):
                self.generate()
        self.assertEqual(self.contents(), before)

    def test_cache_write_failure_rolls_back_public_outputs(self):
        self.generate()
        before = self.contents()
        self.config['projects'] = [{'url': DOWNLOAD}]
        original = update.write_json

        def fail_cache(path, data):
            if path == self.cache:
                raise OSError('simulated permission failure writing cache')
            return original(path, data)

        with mock.patch.object(update, 'write_json', side_effect=fail_cache):
            with self.assertRaises(OSError):
                self.generate()
        self.assertEqual(self.contents(), before)



def solid_png(width, height, rgba):
    return update.encode_png_rgba(width, height, bytearray(bytes(rgba) * (width * height)))


def cgbi_png(width, height, rgba):
    """Build an Apple-crushed PNG: CgBI chunk, raw deflate, BGRA premultiplied, filter 0."""
    import struct as _struct
    import zlib as _zlib
    r, g, b, a = rgba
    if a != 255:
        r, g, b = (c * a // 255 for c in (r, g, b))
    row = b'\x00' + bytes((b, g, r, a)) * width
    compressor = _zlib.compressobj(9, _zlib.DEFLATED, -15)
    idat = compressor.compress(row * height) + compressor.flush()
    def chunk(kind, payload):
        return (_struct.pack('>I', len(payload)) + kind + payload +
                _struct.pack('>I', _zlib.crc32(kind + payload) & 0xFFFFFFFF))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'CgBI', b'\x00\x00\x00\x00') +
            chunk(b'IHDR', _struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', idat) + chunk(b'IEND', b''))


def ipa_with_icon(icon_bytes, icon_name='AppIcon60x60@2x.png'):
    info = dict(PLIST, CFBundleIcons={'CFBundlePrimaryIcon': {
        'CFBundleIconFiles': ['AppIcon60x60'], 'CFBundleIconName': 'AppIcon'}})
    entries = [('Payload/Synthetic.app/Info.plist', plistlib.dumps(info)),
               ('Payload/Synthetic.app/' + icon_name, icon_bytes)]
    return make_ipa(entries=entries)


class IconTests(GenerationTests):
    def test_icon_extracted_published_and_tinted(self):
        self.blob = ipa_with_icon(solid_png(4, 4, (255, 0, 0, 255)))
        self.config['projects'] = [{'url': DOWNLOAD}]
        app = self.generate()['apps'][0]
        self.assertEqual(app['iconURL'], BASE + '/icons/org.example.app.png')
        self.assertEqual(app['tintColor'], 'FF0000')
        written = (self.output / 'icons' / 'org.example.app.png').read_bytes()
        width, height, pixels = update.decode_png_rgba(written)
        self.assertEqual((width, height), (4, 4))
        self.assertEqual(bytes(pixels[:4]), bytes((255, 0, 0, 255)))

    def test_largest_icon_candidate_wins(self):
        info = dict(PLIST, CFBundleIcons={'CFBundlePrimaryIcon': {
            'CFBundleIconFiles': ['AppIcon60x60'], 'CFBundleIconName': 'AppIcon'}})
        entries = [('Payload/Synthetic.app/Info.plist', plistlib.dumps(info)),
                   ('Payload/Synthetic.app/AppIcon60x60.png', solid_png(2, 2, (0, 0, 255, 255))),
                   ('Payload/Synthetic.app/AppIcon60x60@2x.png', solid_png(4, 4, (255, 0, 0, 255)))]
        self.blob = make_ipa(entries=entries)
        self.config['projects'] = [{'url': DOWNLOAD}]
        self.assertEqual(self.generate()['apps'][0]['tintColor'], 'FF0000')

    def test_cgbi_icon_normalised_to_standard_png(self):
        self.blob = ipa_with_icon(cgbi_png(4, 4, (0, 255, 0, 255)))
        self.config['projects'] = [{'url': DOWNLOAD}]
        app = self.generate()['apps'][0]
        self.assertEqual(app['tintColor'], '00FF00')
        written = (self.output / 'icons' / 'org.example.app.png').read_bytes()
        self.assertNotIn(b'CgBI', written)
        _w, _h, pixels = update.decode_png_rgba(written)
        self.assertEqual(bytes(pixels[:4]), bytes((0, 255, 0, 255)))

    def test_no_icon_falls_back_to_source_icon(self):
        self.config['projects'] = [{'url': DOWNLOAD}]
        app = self.generate()['apps'][0]
        self.assertEqual(app['iconURL'], BASE + '/AltStore-Gallery.png')
        self.assertFalse((self.output / 'icons').exists())

    def test_explicit_icon_url_wins_and_no_file_written(self):
        self.blob = ipa_with_icon(solid_png(4, 4, (255, 0, 0, 255)))
        self.config['projects'] = [{'url': DOWNLOAD, 'icon_url': 'https://images.example/icon.png'}]
        app = self.generate()['apps'][0]
        self.assertEqual(app['iconURL'], 'https://images.example/icon.png')
        self.assertFalse((self.output / 'icons' / 'org.example.app.png').exists())


class SubtitleTests(GenerationTests):
    def generate_with_description(self, description, project_extra=None):
        project = {'repo': 'owner/repo'}
        if project_extra:
            project.update(project_extra)
        self.config['projects'] = [project]
        releases = [release(assets=[asset(size=len(self.blob))])]

        def fake(url, timeout):
            if 'releases' in url:
                return releases
            return {'description': description}

        with mock.patch.object(update, 'github_json', side_effect=fake):
            return self.generate()

    def test_auto_subtitle_prefers_cjk_segment(self):
        result = self.generate_with_description(
            'A cross-platform Bilibili client | 跨平台B站第三方客户端 - 专注观看体验')
        self.assertEqual(result['apps'][0]['subtitle'],
                         '跨平台B站第三方客户端 - 专注观看体验')

    def test_auto_subtitle_english_passthrough(self):
        result = self.generate_with_description(
            'File browser and cache cleaner for iOS')
        self.assertEqual(result['apps'][0]['subtitle'],
                         'File browser and cache cleaner for iOS')

    def test_no_description_no_subtitle_key(self):
        result = self.generate_with_description('')
        self.assertNotIn('subtitle', result['apps'][0])
        # Default mock returns a list for the repo-info call too.
        self.config['projects'] = [{'repo': 'owner/repo'}]
        result = self.generate()
        self.assertNotIn('subtitle', result['apps'][0])

    def test_explicit_subtitle_wins_over_derived(self):
        result = self.generate_with_description(
            'Upstream words', {'subtitle': '手写简介'})
        self.assertEqual(result['apps'][0]['subtitle'], '手写简介')

    def test_explicit_empty_subtitle_suppresses(self):
        result = self.generate_with_description(
            'Upstream words', {'subtitle': ''})
        self.assertNotIn('subtitle', result['apps'][0])

    def test_invalid_subtitle_rejected(self):
        self.config['projects'] = [{'repo': 'owner/repo', 'subtitle': 5}]
        with self.assertRaises(ValueError):
            self.generate()

    def test_clean_subtitle_units(self):
        self.assertEqual(update.clean_subtitle(None), '')
        self.assertEqual(update.clean_subtitle('  '), '')
        self.assertEqual(update.clean_subtitle('Cool app https://example.com/x now'),
                         'Cool app now')
        long_text = '这是一个非常长的仓库简介，用来验证副标题的截断逻辑是否正确工作，超过四十八个字以后应该被截断并加上省略号表示还有后续内容'
        cleaned = update.clean_subtitle(long_text)
        self.assertTrue(cleaned.endswith('…'))
        self.assertLessEqual(len(cleaned), 49)


class StaleFallbackTests(GenerationTests):
    def test_failed_upstream_keeps_last_good_entry(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        first = self.generate()
        self.github.return_value = []
        result = self.generate()
        self.assertEqual(result['apps'], first['apps'])
        status = json.loads((self.output / 'status.json').read_text())['apps'][0]
        self.assertTrue(status['stale'])
        self.assertEqual(self.download.call_count, 1)

    def test_failed_upstream_without_history_stays_fatal(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.github.return_value = []
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.contents(), {})

    def test_config_mistake_is_not_masked_by_fallback(self):
        self.config['projects'] = [{'repo': 'owner/repo'}]
        self.generate()
        self.config['projects'][0]['bundle_identifier'] = 'org.example.other'
        with self.assertRaisesRegex(ValueError, 'Bundle ID'):
            self.generate()


if __name__ == '__main__':
    unittest.main()
