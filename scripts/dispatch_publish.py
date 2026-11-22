#!/usr/bin/env python3
"""Trigger the checked-in Pages publishing workflow after a validated add."""
import json
import os
import urllib.error
import urllib.request

TOKEN=os.environ.get('GH_TOKEN')
if not TOKEN:
    raise SystemExit('Workflow token is missing')
REPO=os.environ.get('GITHUB_REPOSITORY') or 'xx123456789xx/ipa-source'
REF=os.environ.get('GITHUB_REF_NAME') or 'main'
url=f'https://api.github.com/repos/{REPO}/actions/workflows/publish.yml/dispatches'
body=json.dumps({'ref':REF}).encode()
request=urllib.request.Request(url,body,{
    'Authorization':'Bearer '+TOKEN,
    'Accept':'application/vnd.github+json',
    'X-GitHub-Api-Version':'2022-11-28',
    'Content-Type':'application/json',
    'User-Agent':'IPA-Source-Owner-Request'
})
try:
    with urllib.request.urlopen(request,timeout=30) as response:
        if response.status!=204:
            raise SystemExit('workflow_dispatch returned unexpected status')
except urllib.error.HTTPError as error:
    # Never print response bodies or authorization headers.
    raise SystemExit(f'workflow_dispatch rejected: HTTP {error.code}') from None
print('Source publication requested.')
