
#!/usr/bin/env python3
"""Generate a public AltStore-style source; Python 3.11+, standard library only.
Never installs, executes, patches or signs IPA files. Config must be trusted.
"""
import argparse
import fnmatch
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = 'Personal-IPA-Source/1.0'


def https_url(value):
    if not isinstance(value, str):
        raise ValueError('URL 必须是字符串')
    p = urllib.parse.urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('仅支持不含账号密码的 HTTPS URL')
    host = p.hostname.lower().rstrip('.')
    if not host or host == 'localhost' or host.endswith(('.local', '.localhost')):
        raise ValueError('不支持本地主机')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject legacy inet_aton spellings: 127.1, 2130706433, 0x7f000001.
        if re.fullmatch(r'(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+))*', host):
            raise ValueError('不支持非标准数字 IP 写法')
    else:
        if not address.is_global:
            raise ValueError('不支持非公网 IP')
    return value


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None:
            # Never forward GitHub API credentials to download/CDN hosts.
            if urllib.parse.urlsplit(newurl).hostname != 'api.github.com':
                result.remove_header('Authorization')
        return result


OPENER = urllib.request.build_opener(SafeRedirect())


def request(url, timeout, api=False):
    https_url(url)
    headers = {'User-Agent': USER_AGENT}
    if api:
        if urllib.parse.urlsplit(url).hostname != 'api.github.com':
            raise ValueError('API 凭据仅允许发送至 api.github.com')
        headers['Accept'] = 'application/vnd.github+json'
        headers['X-GitHub-Api-Version'] = '2022-11-28'
        token = os.environ.get('GITHUB_TOKEN', '')
        if token:
            headers['Authorization'] = 'Bearer ' + token
    return OPENER.open(urllib.request.Request(url, headers=headers), timeout=timeout)


def github_json(url, timeout):
    try:
        with request(url, timeout, api=True) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('GitHub 元数据超过大小限制')
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Never print the request/headers/token or an arbitrary response body.
        raise ValueError(f'GitHub API HTTP {exc.code}；检查仓库公开性和请求额度') from None


def repo_name(value):
    if not isinstance(value, str):
        raise ValueError('repo 必须是 owner/repo 或 GitHub HTTPS 地址')
    if value.startswith('https://github.com/'):
        p = urllib.parse.urlsplit(value)
        parts = p.path.strip('/').split('/')
        if len(parts) < 2 or p.query or p.fragment:
            raise ValueError('GitHub 仓库地址格式错误')
        value = '/'.join(parts[:2])
    value = value.removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
        raise ValueError('repo 必须是 owner/repo 或 GitHub HTTPS 地址')
    if any(x in ('.', '..') for x in value.split('/')):
        raise ValueError('仓库名称不合法')
    return value


def pick_release(releases, project):
    """Newest published eligible release; fail on ambiguous asset selection."""
    pattern = project.get('asset_pattern', '*.ipa')
    if not isinstance(pattern, str) or not pattern:
        raise ValueError('asset_pattern 必须是非空 glob 字符串')
    eligible = [r for r in releases if not r.get('draft') and
                (project.get('include_prereleases', False) or not r.get('prerelease'))]
    eligible.sort(key=lambda r: r.get('published_at') or r.get('created_at') or '', reverse=True)
    for release in eligible:
        assets = [a for a in release.get('assets', [])
                  if a.get('name', '').lower().endswith('.ipa')
                  and fnmatch.fnmatchcase(a.get('name', ''), pattern)
                  and a.get('state', 'uploaded') == 'uploaded']
        if len(assets) > 1:
            raise ValueError('同一发行版匹配到多个 IPA，请用 asset_pattern 精确指定；不自动猜包')
        if len(assets) == 1:
            asset = assets[0]
            return {
                'url': https_url(asset['browser_download_url']),
                'release_date': release.get('published_at') or release.get('created_at'),
                'description': str(release.get('body') or '')[:12000],
                'release_tag': release.get('tag_name', ''),
                'fingerprint': [asset.get('id'), asset.get('updated_at'), asset.get('size'),
                                asset.get('browser_download_url')],
                'asset_size': asset.get('size'),
            }
    raise ValueError('最近发行版中没有匹配的公开 IPA；不会尝试从源码编译或回退非 IPA 文件')


def resolve_github(project, timeout):
    repo = repo_name(project['repo'])
    # Bound API work: up to the newest 100 releases; no hidden/unbounded scan.
    data = github_json(f'https://api.github.com/repos/{repo}/releases?per_page=100', timeout)
    if not isinstance(data, list):
        raise ValueError('GitHub 返回的发行列表格式不正确')
    resolved = pick_release(data, project)
    resolved['project_url'] = 'https://github.com/' + repo
    resolved['key'] = 'github:' + repo
    return resolved


def inspect_ipa(path):
    """Read only the root application's plist, never zip-extract the archive."""
    with zipfile.ZipFile(path) as archive:
        roots = [i for i in archive.infolist()
                 if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', i.filename)]
        if len(roots) != 1:
            raise ValueError('IPA 必须且只能含一个 Payload/*.app/Info.plist')
        entry = roots[0]
        if entry.file_size > 2 * 1024 * 1024:
            raise ValueError('Info.plist 过大')
        with archive.open(entry) as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        info = plistlib.loads(data)
    if not isinstance(info, dict):
        raise ValueError('Info.plist 不是字典')
    bundle = info.get('CFBundleIdentifier')
    if not isinstance(bundle, str) or not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', bundle):
        raise ValueError('缺少有效 CFBundleIdentifier')
    version = str(info.get('CFBundleShortVersionString') or info.get('CFBundleVersion') or '').strip()
    if not version:
        raise ValueError('缺少 CFBundleShortVersionString / CFBundleVersion')
    metadata = {
        'bundleIdentifier': bundle,
        'version': version,
        'name': str(info.get('CFBundleDisplayName') or info.get('CFBundleName') or bundle),
    }
    build = info.get('CFBundleVersion')
    if build is not None and str(build).strip():
        metadata['buildVersion'] = str(build).strip()
    minimum = info.get('MinimumOSVersion')
    if isinstance(minimum, str) and re.fullmatch(r'\d+(?:\.\d+){0,2}', minimum):
        metadata['minOSVersion'] = minimum
    return metadata


def download_metadata(url, maximum, timeout):
    digest = hashlib.sha256()
    count = 0
    try:
        with request(url, timeout) as response, tempfile.TemporaryFile() as file:
            declared = response.headers.get('Content-Length')
            if declared and int(declared) > maximum:
                raise ValueError('IPA 超过 max_ipa_mb 限制')
            modified = response.headers.get('Last-Modified')
            while True:
                chunk = response.read(min(1024 * 1024, maximum + 1 - count))
                if not chunk:
                    break
                count += len(chunk)
                if count > maximum:
                    raise ValueError('IPA 超过 max_ipa_mb 限制')
                digest.update(chunk)
                file.write(chunk)
            file.seek(0)
            metadata = inspect_ipa(file)
        metadata.update(size=count, sha256=digest.hexdigest())
        # Direct URLs can change without any release API; always re-download.
        if modified:
            from email.utils import parsedate_to_datetime
            try:
                metadata['lastModified'] = parsedate_to_datetime(modified).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        return metadata
    except urllib.error.HTTPError as exc:
        raise ValueError(f'IPA 下载 HTTP {exc.code}；仅支持无需登录的公开下载') from None


def build_app(project, resolved, metadata, base_url, previous=None):
    expected = project.get('bundle_identifier')
    if expected and expected != metadata['bundleIdentifier']:
        raise ValueError('IPA Bundle ID 与锁定的 bundle_identifier 不一致，拒绝发布')
    icon = project.get('icon_url') or (base_url + '/icon.png' if base_url else '')
    if not icon:
        raise ValueError('收录应用前请设置 source.base_url 或 SOURCE_BASE_URL')
    https_url(icon)
    date = resolved.get('release_date') or metadata.get('lastModified')
    if not date and previous and previous.get('sha256') == metadata['sha256']:
        date = previous.get('date')
    date = date or datetime.now(timezone.utc).isoformat(timespec='seconds')
    version = {key: metadata[key] for key in ('version', 'size')}
    if metadata.get('buildVersion'):
        # LiveContainer reads buildNumber; AltStore Classic reads buildVersion.
        version['buildVersion'] = metadata['buildVersion']
        version['buildNumber'] = metadata['buildVersion']
    version.update(date=date, downloadURL=resolved['url'])
    if 'minOSVersion' in metadata:
        version['minOSVersion'] = metadata['minOSVersion']
    app = {
        'name': project.get('name') or metadata['name'],
        'bundleIdentifier': metadata['bundleIdentifier'],
        'developerName': project.get('developer_name') or
                         resolved.get('project_url', '').removeprefix('https://github.com/').split('/')[0]
                         or '上游发行者',
        'localizedDescription': project.get('description') or resolved.get('description') or
                                '公开上游 IPA。收录不代表兼容性或安全认证。',
        'iconURL': icon,
        'tintColor': '4263EB',
        'versions': [version],
        # Legacy single-version fields for older source readers.
        'version': version['version'],
        'versionDate': date,
        'versionDescription': resolved.get('description', ''),
        'downloadURL': resolved['url'],
        'size': metadata['size'],
    }
    if 'app_permissions' in project:
        permissions = project['app_permissions']
        if not isinstance(permissions, dict) or not all(
                isinstance(permissions.get(key), list) for key in ('entitlements', 'privacy')):
            raise ValueError('app_permissions 必须是含 entitlements 和 privacy 数组的对象')
        # Explicit maintainer declaration, not inferred/verified by this tool.
        app['appPermissions'] = permissions
    return app, date


def read_json(path, default=None):
    if not path.exists() and default is not None:
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    # Avoid replacing tracked files when output is identical.
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    temporary = path.with_suffix(path.suffix + '.tmp')
    try:
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_bundle(entries):
    """Best-effort rollback for handled I/O errors; not a crash-safe database.

    Pages publishes only after this function succeeds, in a separate deploy job.
    """
    # Validate serializability and retain prior bytes before changing any file.
    snapshots = {}
    for path, data in entries:
        json.dumps(data, ensure_ascii=False)
        snapshots[path] = path.read_bytes() if path.exists() else None
    try:
        for path, data in entries:
            write_json(path, data)
    except Exception:
        for path, original in reversed(list(snapshots.items())):
            if original is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != original:
                backup = path.with_suffix(path.suffix + '.rollback')
                try:
                    backup.write_bytes(original)
                    backup.replace(path)
                finally:
                    backup.unlink(missing_ok=True)
        raise


def generate(config_path, output, cache_path):
    config = read_json(config_path)
    source = config['source']
    projects = config.get('projects', [])
    if not isinstance(projects, list):
        raise ValueError('projects 必须是数组')
    base_url = (os.environ.get('SOURCE_BASE_URL') or source.get('base_url') or '').rstrip('/')
    if base_url:
        https_url(base_url)
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.query or parsed.fragment:
            raise ValueError('base_url 不能带查询参数或锚点')
    limits = config.get('limits', {})
    maximum_mb = int(limits.get('max_ipa_mb', 1024))
    timeout = int(limits.get('request_timeout_seconds', 90))
    if not 1 <= maximum_mb <= 4096 or not 5 <= timeout <= 300:
        raise ValueError('max_ipa_mb 应在 1–4096，超时应在 5–300 秒之间')
    maximum = maximum_mb * 1024 * 1024
    try:
        old_cache = read_json(cache_path, {'schema': 1, 'items': {}})
        if old_cache.get('schema') != 1:
            old_cache = {'schema': 1, 'items': {}}
    except (ValueError, OSError):
        old_cache = {'schema': 1, 'items': {}}
    new_cache = {'schema': 1, 'items': {}}
    apps, status, bundle_ids = [], [], set()
    errors = []
    for number, project in enumerate(projects, 1):
        try:
            if not isinstance(project, dict):
                raise ValueError('项目条目必须是对象')
            if project.get('enabled', True) is False:
                continue
            if bool(project.get('repo')) == bool(project.get('url')):
                raise ValueError('每个项目必须且只能设置 repo 或 url 中的一项')
            if not base_url and not project.get('icon_url'):
                raise ValueError('收录应用前需设置 source.base_url 或 SOURCE_BASE_URL')
            if project.get('repo'):
                resolved = resolve_github(project, timeout)
            else:
                url = https_url(project['url'])
                resolved = {'url': url, 'key': 'direct:' + hashlib.sha256(url.encode()).hexdigest()}
            previous = old_cache.get('items', {}).get(resolved['key'], {})
            advertised = resolved.get('asset_size')
            if advertised and int(advertised) > maximum:
                raise ValueError('发行文件超过 max_ipa_mb 限制')
            fingerprint = resolved.get('fingerprint')
            cached = previous.get('metadata', {})
            if fingerprint and previous.get('fingerprint') == fingerprint and cached:
                metadata = cached
            else:
                metadata = download_metadata(resolved['url'], maximum, timeout)
            if metadata['size'] > maximum:
                raise ValueError('IPA 超过当前 max_ipa_mb 限制')
            if advertised is not None and int(advertised) != metadata['size']:
                raise ValueError('IPA 实际大小与 GitHub 发行资产记录不符')
            app, date = build_app(project, resolved, metadata, base_url, previous)
            bundle = app['bundleIdentifier']
            if bundle in bundle_ids:
                raise ValueError('源中出现重复 Bundle ID，请只保留一个发行渠道')
            bundle_ids.add(bundle)
            apps.append(app)
            new_cache['items'][resolved['key']] = {
                'fingerprint': fingerprint, 'metadata': metadata,
                'date': date, 'sha256': metadata['sha256'],
            }
            status.append({
                'name': app['name'], 'bundleIdentifier': bundle,
                'version': metadata['version'], 'buildVersion': metadata.get('buildVersion'),
                'sha256': metadata['sha256'], 'size': metadata['size'],
                'projectURL': resolved.get('project_url'),
                'releaseTag': resolved.get('release_tag'),
                'downloadURL': resolved['url'],
            })
            print(f'OK #{number}: {bundle} {metadata["version"]}')
        except Exception as exc:
            # Don't print arbitrary network exception strings: URLs may contain query secrets.
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            errors.append(f'项目 #{number}: {detail}')
    if errors:
        raise ValueError('\n'.join(errors) + '\n本次不写入或发布任何源更新；请修复配置后重试。')
    result = {
        'name': source['name'], 'identifier': source['identifier'],
        'subtitle': source.get('subtitle', ''),
        'description': source.get('description', ''), 'apps': apps, 'news': [],
    }
    if base_url:
        result.update(website=base_url + '/', iconURL=base_url + '/apple-touch-icon-v2.png',
                      sourceURL=base_url + '/source.json')
    # Only public metadata goes in public/. IPA bytes and credentials are never published.
    write_bundle([(output / 'source.json', result),
                  (output / 'status.json', {'apps': status}),
                  (cache_path, new_cache)])
    print(f'生成完成：{len(apps)} 个应用 -> {output / "source.json"}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'public')
    parser.add_argument('--cache', type=Path, default=ROOT / '.cache' / 'metadata.json')
    args = parser.parse_args()
    try:
        generate(args.config, args.output, args.cache)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
        print('生成失败：' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
