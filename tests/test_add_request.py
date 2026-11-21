import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT=Path(__file__).resolve().parents[1]/'scripts'/'add_request.py'
SPEC=importlib.util.spec_from_file_location('add_request_under_test',SCRIPT)
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

def event(url='https://github.com/example/app/releases',pattern='*.ipa',pre='false',actor='xx123456789xx',author='xx123456789xx',title=None):
    body='\n'.join(['IPA_SOURCE_REQUEST_V1','Source URL: '+url,'Asset pattern: '+pattern,'Include prereleases: '+pre])
    return {'sender':{'login':actor},'issue':{'number':31,'title':title or module.TITLE,'body':body,'user':{'login':author}}}

class RequestParsingTests(unittest.TestCase):
    def test_github_repository_url_normalized(self):
        self.assertEqual(module.parse_request(event('https://github.com/owner/repo/releases/tag/v2')),
                         {'repo':'owner/repo','asset_pattern':'*.ipa','include_prereleases':False,'enabled':True})
    def test_owner_repo_shorthand_in_url(self):
        self.assertEqual(module.parse_request(event('https://github.com/owner/repo'))['repo'],'owner/repo')
    def test_public_direct_ipa_url(self):
        p=module.parse_request(event('https://cdn.example/app.ipa'))
        self.assertEqual(p,{'url':'https://cdn.example/app.ipa','enabled':True})
    def test_prerelease_opt_in_and_pattern(self):
        p=module.parse_request(event(pattern='*arm64.ipa',pre='true'))
        self.assertEqual((p['asset_pattern'],p['include_prereleases']),('*arm64.ipa',True))
    def test_only_repository_owner_may_open_request(self):
        for actor,author in [('other','xx123456789xx'),('xx123456789xx','other'),('OTHER','xx123456789xx')]:
            with self.subTest(actor=actor,author=author),self.assertRaises(ValueError):
                module.parse_request(event(actor=actor,author=author))
    def test_title_must_match_exactly(self):
        with self.assertRaises(ValueError): module.parse_request(event(title='[IPA Source] Add project evil'))
    def test_request_prefix_required(self):
        e=event();e['issue']['body']=e['issue']['body'].replace('IPA_SOURCE_REQUEST_V1','OTHER',1)
        with self.assertRaises(ValueError): module.parse_request(e)
    def test_duplicate_and_unknown_fields_rejected(self):
        for body in [event()['issue']['body']+'\nSource URL: https://evil.example/x.ipa',
                     event()['issue']['body']+'\nPassword: abc']:
            e=event();e['issue']['body']=body
            with self.subTest(body=body),self.assertRaises(ValueError): module.parse_request(e)
    def test_missing_prerelease_or_pattern_rejected(self):
        for missing in ['Asset pattern: *.ipa','Include prereleases: false']:
            e=event();e['issue']['body']='\n'.join(line for line in e['issue']['body'].splitlines() if line!=missing)
            with self.subTest(missing=missing),self.assertRaises(ValueError):module.parse_request(e)
    def test_direct_url_must_end_in_ipa(self):
        for url in ['https://cdn.example/app.zip','https://cdn.example/app.ipa?sig=x','http://cdn.example/app.ipa']:
            with self.subTest(url=url),self.assertRaises(ValueError):module.parse_request(event(url))
    def test_private_address_and_credential_url_rejected(self):
        for url in ['https://127.0.0.1/app.ipa','https://u:p@cdn.example/app.ipa','https://10.0.0.1/app.ipa']:
            with self.subTest(url=url),self.assertRaises(ValueError):module.parse_request(event(url))
    def test_github_non_github_host_and_bad_path_rejected(self):
        for url in ['https://evil.example/owner/repo','https://github.com/owner','https://github.com/owner/repo?token=x']:
            with self.subTest(url=url),self.assertRaises(ValueError):module.parse_request(event(url))
    def test_bad_pattern_and_bool_rejected(self):
        for pattern,pre in [('*.zip','false'),('x'*121+'.ipa','false'),('*.ipa','yes')]:
            with self.subTest(pattern=pattern,pre=pre),self.assertRaises(ValueError):module.parse_request(event(pattern=pattern,pre=pre))

class ProcessTests(unittest.TestCase):
    def test_candidate_is_validated_before_config_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps({'source':{},'projects':[]}))
            with mock.patch.object(module,'validate_candidate',side_effect=ValueError('bad IPA')):
                with self.assertRaises(ValueError):module.process_event_from_data(event(),'not-used',cfg)

    def test_duplicate_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps({'source':{},'projects':[{'repo':'example/app'}]}))
            with mock.patch.object(module,'validate_candidate'):
                with self.assertRaisesRegex(ValueError,'already'):
                    module.process_event_from_data(event(),'not-used',cfg)

    def test_success_only_persists_after_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps({'source':{},'projects':[]}))
            with mock.patch.object(module,'validate_candidate') as valid:
                p=module.process_event_from_data(event(),'not-used',cfg)
            valid.assert_called_once()
            saved=json.loads(cfg.read_text())
            self.assertEqual(saved['projects'][0],p)

if __name__=='__main__':unittest.main()
