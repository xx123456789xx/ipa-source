#!/usr/bin/env python3
"""Owner-only, issue-driven source additions. Issue content is untrusted data."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.parse

sys.path.insert(0,str(Path(__file__).resolve().parent))
import update

ROOT=Path(__file__).resolve().parent.parent
OWNER='xx123456789xx'
TITLE='[IPA Source] Add project'
PREFIX='IPA_SOURCE_REQUEST_V1'


def parse_request(event):
    issue=event.get('issue') or {}
    actor=(event.get('sender') or {}).get('login','')
    author=(issue.get('user') or {}).get('login','')
    if actor.lower()!=OWNER.lower() or author.lower()!=OWNER.lower():
        raise ValueError('Only the repository owner may add source entries')
    if issue.get('title')!=TITLE:
        raise ValueError('Unexpected request title')
    body=issue.get('body')
    if not isinstance(body,str) or len(body)>4000:
        raise ValueError('Request body is missing or too long')
    lines=body.splitlines()
    if not lines or lines[0].strip()!=PREFIX:
        raise ValueError('Unknown request format')
    values={}
    for line in lines[1:]:
        if not line.strip(): continue
        if ':' not in line: raise ValueError('Malformed request field')
        key,value=line.split(':',1);key=key.strip();value=value.strip()
        if key not in {'Source URL','Asset pattern','Include prereleases'} or key in values:
            raise ValueError('Unknown or duplicated request field')
        values[key]=value
    if set(values)!={'Source URL','Asset pattern','Include prereleases'}:
        raise ValueError('A required field is missing')
    raw_url=values['Source URL']
    if len(raw_url)>2000 or any(ord(c)<32 for c in raw_url):
        raise ValueError('Source URL is invalid or too long')
    parsed=urllib.parse.urlsplit(raw_url)
    update.https_url(raw_url)
    if parsed.query or parsed.fragment:
        raise ValueError('Query strings/fragments are not accepted; do not submit signed/private links')
    pattern=values['Asset pattern']
    if not 1<=len(pattern)<=120 or not pattern.lower().endswith('.ipa'):
        raise ValueError('Asset pattern must be at most 120 characters and end with .ipa')
    if values['Include prereleases'] not in {'true','false'}:
        raise ValueError('Prerelease option must be true or false')
    if parsed.hostname.lower()=='github.com':
        repo=update.repo_name(raw_url)
        project={'repo':repo,'asset_pattern':pattern,
                 'include_prereleases':values['Include prereleases']=='true','enabled':True}
    else:
        # Public direct IPA URL only. Never store a credential-bearing query URL.
        if not parsed.path.lower().endswith('.ipa'):
            raise ValueError('Non-GitHub URL must point directly to a .ipa file')
        project={'url':raw_url,'enabled':True}
    return project


def project_identity(project):
    return 'github:'+project['repo'].lower() if 'repo' in project else 'direct:'+project['url']


def validate_candidate(config):
    """Run the production updater against a temporary candidate before committing."""
    with tempfile.TemporaryDirectory(prefix='ipa-source-request-') as temp:
        root=Path(temp)
        cfg=root/'config.json'
        cfg.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        update.generate(cfg,root/'public',root/'.cache'/'metadata.json')


def process_event_from_data(event,issue_number,config_path=ROOT/'config.json',validate=True,commit=False):
    project=parse_request(event)
    config=json.loads(Path(config_path).read_text(encoding='utf-8'))
    projects=config.setdefault('projects',[])
    identity=project_identity(project)
    if any(project_identity(p)==identity for p in projects):
        raise ValueError('This project is already in the source')
    candidate=json.loads(json.dumps(config))
    candidate['projects'].append(project)
    if validate:
        validate_candidate(candidate)
    # Persist only after the exact production updater accepts the new manifest.
    text=json.dumps(candidate,ensure_ascii=False,indent=2)+'\n'
    temporary=config_path.with_suffix('.json.tmp')
    temporary.write_text(text,encoding='utf-8')
    temporary.replace(config_path)
    if commit:
        number=int(issue_number)
        subprocess.run(['git','config','user.name','github-actions[bot]'],check=True)
        subprocess.run(['git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com'],check=True)
        subprocess.run(['git','add',str(config_path.relative_to(ROOT))],cwd=ROOT,check=True)
        subprocess.run(['git','commit','-m',f'Add IPA source request #{number}'],cwd=ROOT,check=True)
        subprocess.run(['git','push','origin','HEAD:main'],cwd=ROOT,check=True)
    return project


def process_event(event_path,config_path=ROOT/'config.json',validate=True,commit=False):
    event=json.loads(Path(event_path).read_text(encoding='utf-8'))
    return process_event_from_data(event,event.get('issue',{}).get('number',0),config_path,validate,commit)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--event',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'config.json')
    p.add_argument('--skip-validation',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--commit',action='store_true')
    args=p.parse_args()
    project=process_event(args.event,args.config,not args.skip_validation,args.commit)
    label=project.get('repo') or project.get('url')
    print('Validated and added source entry:',label)

if __name__=='__main__':
    try: main()
    except Exception as exc:
        detail=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        print('Source request rejected:',detail,file=sys.stderr)
        sys.exit(1)
