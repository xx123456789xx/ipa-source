#!/usr/bin/env python3
"""Generate a public AltStore-style source; Python 3.11+, standard library only.
Never installs, executes, patches or signs IPA files. Config must be trusted.

v1.3: app icons are extracted from the IPA itself (including Apple CgBI
crushed PNGs, normalised back to standard PNG), a single failing project
with a previously published entry keeps that entry instead of blocking the
whole source, and release notes no longer double as the app description.
"""
import argparse
import base64
import copy
import fnmatch
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = 'Personal-IPA-Source/1.3'
CACHE_SCHEMA = 2
DEFAULT_TINT = '4263EB'
DEFAULT_DESCRIPTION = '公开上游 IPA。收录不代表兼容性或安全认证。'


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
        token = os.environ.get('GITHUB_TOKEN')
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
    # Best effort: the repo's own one-line description becomes the app's
    # subtitle (cleaned in build_app). Cosmetic only -- any failure here
    # just leaves the app without a derived subtitle, never blocks it.
    try:
        info = github_json(f'https://api.github.com/repos/{repo}', timeout)
        if isinstance(info, dict):
            resolved['repo_description'] = str(info.get('description') or '')
    except Exception:
        pass
    return resolved


def clean_subtitle(text):
    """One-line app subtitle derived from an upstream repo description.

    Cosmetic only: anything unusable becomes ''. When a description
    mixes languages in '|'-separated segments, the first segment
    containing CJK wins -- that is the segment this source's readers
    can actually read. Long text is cut at a separator when possible.
    """
    if not isinstance(text, str):
        return ''
    text = re.sub(r'https?://\S+', '', text)
    text = ' '.join(text.split())
    if not text:
        return ''
    segments = [s.strip(' -—·') for s in text.split('|')]
    segments = [s for s in segments if s]
    if not segments:
        return ''
    chosen = next((s for s in segments
                   if re.search('[一-鿿]', s)), segments[0])
    chosen = chosen.strip(' 。.!！?？')
    if len(chosen) > 48:
        cut = chosen[:48]
        for sep in ('·', '，', '、', ',', ' '):
            idx = cut.rfind(sep)
            if idx >= 20:
                cut = cut[:idx]
                break
        chosen = cut.rstrip(' 。.!！?？·，、,') + '…'
    return chosen


# ---------------------------------------------------------------------------
# Minimal PNG codec (8-bit, non-interlaced). Needed to undo Apple's CgBI
# crushing: raw-deflate IDAT, filter-0 rows, BGRA premultiplied pixels.
# Any failure returns None and the caller falls back to the source icon;
# icon problems must never block a source update.
# ---------------------------------------------------------------------------

def _png_chunks(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('不是 PNG')
    chunks = []
    offset = 8
    while offset + 12 <= len(data):
        length, kind = struct.unpack('>I4s', data[offset:offset + 8])
        chunks.append((kind.decode('latin1'), data[offset + 8:offset + 8 + length]))
        offset += 12 + length
        if kind == b'IEND':
            break
    return chunks


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_rgba(data):
    """Decode an 8-bit non-interlaced PNG (standard or CgBI) to RGBA bytes."""
    chunks = _png_chunks(data)
    header = None
    palette, transparency, compressed = [], b'', b''
    crushed = False
    for kind, payload in chunks:
        if kind == 'CgBI':
            crushed = True
        elif kind == 'IHDR':
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == 'PLTE':
            palette = [tuple(payload[i:i + 3]) for i in range(0, len(payload) - 2, 3)]
        elif kind == 'tRNS':
            transparency = payload
        elif kind == 'IDAT':
            compressed += payload
    if header is None:
        raise ValueError('PNG 缺少 IHDR')
    width, height, depth, colour, _comp, _filt, interlace = header
    if depth != 8 or interlace != 0 or width < 1 or height < 1 or width > 4096 or height > 4096:
        raise ValueError('不支持的 PNG 形式')
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if channels is None:
        raise ValueError('不支持的 PNG 颜色类型')
    if crushed:
        raw = zlib.decompress(compressed, -15)
        stride = width * 4
        if len(raw) == height * (stride + 1):
            rows = [raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)] for y in range(height)]
        elif len(raw) == height * stride:
            rows = [raw[y * stride:(y + 1) * stride] for y in range(height)]
        else:
            raise ValueError('CgBI 数据长度不符')
        pixels = bytearray(width * height * 4)
        for y, row in enumerate(rows):
            for x in range(width):
                i = (y * width + x) * 4
                b, g, r, a = row[x * 4:x * 4 + 4]
                if a == 0:
                    continue
                if a != 255:  # undo premultiplication
                    r, g, b = (min(255, c * 255 // a) for c in (r, g, b))
                pixels[i:i + 4] = bytes((r, g, b, a))
        return width, height, pixels
    raw = zlib.decompress(compressed)
    stride = width * channels
    if len(raw) < height * (stride + 1):
        raise ValueError('PNG 数据不完整')
    recon = bytearray(height * stride)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if kind == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                corner = previous[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, up, corner)) & 0xFF
        elif kind != 0:
            raise ValueError('未知 PNG 滤波器')
        recon[y * stride:(y + 1) * stride] = line
        previous = line
    pixels = bytearray(width * height * 4)
    for i in range(width * height):
        src = recon[i * channels:(i + 1) * channels]
        out = i * 4
        if colour == 6:
            pixels[out:out + 4] = src
        elif colour == 2:
            pixels[out:out + 4] = bytes((src[0], src[1], src[2], 255))
        elif colour == 0:
            pixels[out:out + 4] = bytes((src[0], src[0], src[0], 255))
        elif colour == 4:
            pixels[out:out + 4] = bytes((src[0], src[0], src[0], src[1]))
        else:  # palette
            r, g, b = palette[src[0]] if src[0] < len(palette) else (0, 0, 0)
            a = transparency[src[0]] if src[0] < len(transparency) else 255
            pixels[out:out + 4] = bytes((r, g, b, a))
    return width, height, pixels


def encode_png_rgba(width, height, pixels):
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload +
                struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF))
    rows = b''.join(b'\x00' + bytes(pixels[y * width * 4:(y + 1) * width * 4])
                    for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def normalise_icon(data):
    """Return (standard_png_bytes, tint_hex) or None; never raises."""
    try:
        width, height, pixels = decode_png_rgba(data)
        total_r = total_g = total_b = count = 0
        for i in range(0, len(pixels), 4):
            if pixels[i + 3] >= 128:
                total_r += pixels[i]
                total_g += pixels[i + 1]
                total_b += pixels[i + 2]
                count += 1
        tint = None
        if count:
            tint = '%02X%02X%02X' % (total_r // count, total_g // count, total_b // count)
        return encode_png_rgba(width, height, pixels), tint
    except Exception:
        return None


def _icon_base_names(info):
    bases = []

    def collect(node):
        if isinstance(node, dict):
            for name in node.get('CFBundleIconFiles') or []:
                if isinstance(name, str):
                    bases.append(name)
            single = node.get('CFBundleIconFile')
            if isinstance(single, str):
                bases.append(single)

    for key in ('CFBundleIcons', 'CFBundleIcons~ipad'):
        section = info.get(key)
        if isinstance(section, dict):
            collect(section.get('CFBundlePrimaryIcon'))
    collect(info)
    return bases


def extract_icon(archive, app_prefix, info):
    """Largest declared app icon in the .app root, normalised, or None."""
    bases = _icon_base_names(info)
    candidates = []
    for entry in archive.infolist():
        name = entry.filename
        if not name.startswith(app_prefix) or '/' in name[len(app_prefix):]:
            continue
        filename = name[len(app_prefix):]
        if not filename.lower().endswith('.png') or entry.file_size > 8 * 1024 * 1024:
            continue
        if not (filename.startswith('AppIcon') or
                any(filename.startswith(base) for base in bases)):
            continue
        try:
            data = archive.read(entry)
            chunks = _png_chunks(data)
            header = next(payload for kind, payload in chunks if kind == 'IHDR')
            width, height = struct.unpack('>II', header[:8])
        except Exception:
            continue
        candidates.append((width * height, len(data), data))
    candidates.sort(reverse=True)
    for _area, _size, data in candidates:
        result = normalise_icon(data)
        if result is not None:
            return result  # (png_bytes, tint)
    return None


class IPAMetadata(dict):
    """Metadata dict; icon data rides as attributes, never in JSON."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon_png = None
        self.icon_tint = None


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
    metadata = IPAMetadata({
        'bundleIdentifier': bundle,
        'version': version,
        'name': str(info.get('CFBundleDisplayName') or info.get('CFBundleName') or bundle),
    })
    metadata.info = info
    metadata.app_prefix = entry.filename[:-len('Info.plist')]
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
            # The IPA is already fully downloaded for hashing; extracting
            # its icon here costs no extra traffic. Cosmetic only: any icon
            # failure leaves icon_png unset and the source icon is used.
            try:
                file.seek(0)
                with zipfile.ZipFile(file) as archive:
                    found = extract_icon(archive, metadata.app_prefix, metadata.info)
                if found is not None:
                    metadata.icon_png, metadata.icon_tint = found
            except Exception:
                pass
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


class ConfigFailure(ValueError):
    """Maintainer/config mistake: always fatal, never masked by a fallback."""


def build_app(project, resolved, metadata, base_url, previous=None):
    expected = project.get('bundle_identifier')
    if expected and expected != metadata['bundleIdentifier']:
        raise ConfigFailure('IPA Bundle ID 与锁定的 bundle_identifier 不一致，拒绝发布')
    icon_png = getattr(metadata, 'icon_png', None)
    explicit_icon = project.get('icon_url')
    if explicit_icon:
        icon = explicit_icon
    elif icon_png and base_url:
        icon = base_url + '/icons/' + metadata['bundleIdentifier'] + '.png'
    elif base_url:
        icon = base_url + '/AltStore-Gallery.png'
    else:
        icon = ''
    if not icon:
        raise ConfigFailure('收录应用前请设置 source.base_url 或 SOURCE_BASE_URL')
    https_url(icon)
    tint = getattr(metadata, 'icon_tint', None) if (icon_png and not explicit_icon) else None
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
        # Release notes belong to the version, not the app description:
        # dumping a full changelog here bloated source.json and read badly
        # in clients. A custom project description still wins.
        'localizedDescription': project.get('description') or DEFAULT_DESCRIPTION,
        'iconURL': icon,
        'tintColor': tint or DEFAULT_TINT,
        'versions': [version],
        # Legacy single-version fields for older source readers.
        'version': version['version'],
        'versionDate': date,
        'versionDescription': resolved.get('description', ''),
        'downloadURL': resolved['url'],
        'size': metadata['size'],
    }
    subtitle = project.get('subtitle')
    if subtitle is not None:
        # Maintainer-written subtitle wins over the derived one; an
        # explicit empty string suppresses the subtitle entirely.
        if not isinstance(subtitle, str):
            raise ConfigFailure('subtitle 必须是字符串')
        subtitle = ' '.join(subtitle.split())
        if len(subtitle) > 80:
            raise ConfigFailure('subtitle 过长（最多 80 字）')
    else:
        subtitle = clean_subtitle(resolved.get('repo_description'))
    if subtitle:
        app['subtitle'] = subtitle
    if 'app_permissions' in project:
        permissions = project['app_permissions']
        if not isinstance(permissions, dict) or not all(
                isinstance(permissions.get(key), list) for key in ('entitlements', 'privacy')):
            raise ConfigFailure('app_permissions 必须是含 entitlements 和 privacy 数组的对象')
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


def write_icons(output, icons):
    """Write extracted icons under output/icons, pruning stale ones.

    Only files this generator owns live in that directory (named by bundle
    identifier). Unchanged icons are not rewritten.
    """
    directory = output / 'icons'
    wanted = {bundle + '.png': data for bundle, data in icons.items()}
    if directory.exists():
        for existing in directory.glob('*.png'):
            if existing.name not in wanted:
                existing.unlink()
    for name, data in wanted.items():
        path = directory / name
        if path.exists() and path.read_bytes() == data:
            continue
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.png.tmp')
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _project_key(project):
    if project.get('repo'):
        return 'github:' + repo_name(project['repo'])
    url = https_url(project['url'])
    return 'direct:' + hashlib.sha256(url.encode()).hexdigest()


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
        old_cache = read_json(cache_path, {'schema': CACHE_SCHEMA, 'items': {}})
        if old_cache.get('schema') != CACHE_SCHEMA:
            # Older caches lack extracted icons and last-good app entries;
            # treat them as empty so everything is fetched once, fresh.
            old_cache = {'schema': CACHE_SCHEMA, 'items': {}}
    except (ValueError, OSError):
        old_cache = {'schema': CACHE_SCHEMA, 'items': {}}
    new_cache = {'schema': CACHE_SCHEMA, 'items': {}}
    apps, status, bundle_ids = [], [], set()
    icons = {}
    errors, warnings = [], []
    for number, project in enumerate(projects, 1):
        try:
            if not isinstance(project, dict):
                raise ConfigFailure('项目条目必须是对象')
            if project.get('enabled', True) is False:
                continue
            if bool(project.get('repo')) == bool(project.get('url')):
                raise ConfigFailure('每个项目必须且只能设置 repo 或 url 中的一项')
            if not base_url and not project.get('icon_url'):
                raise ConfigFailure('收录应用前需设置 source.base_url 或 SOURCE_BASE_URL')
            key = _project_key(project)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            errors.append(f'项目 #{number}: {detail}')
            continue
        previous = old_cache.get('items', {}).get(key, {})
        try:
            if project.get('repo'):
                resolved = resolve_github(project, timeout)
            else:
                url = https_url(project['url'])
                resolved = {'url': url, 'key': 'direct:' + hashlib.sha256(url.encode()).hexdigest()}
            advertised = resolved.get('asset_size')
            if advertised and int(advertised) > maximum:
                raise ValueError('发行文件超过 max_ipa_mb 限制')
            fingerprint = resolved.get('fingerprint')
            cached = previous.get('metadata', {})
            if fingerprint and previous.get('fingerprint') == fingerprint and cached \
                    and 'iconB64' in previous:
                metadata = IPAMetadata(cached)
                if previous.get('iconB64'):
                    metadata.icon_png = base64.b64decode(previous['iconB64'])
                    metadata.icon_tint = previous.get('iconTint')
            else:
                metadata = download_metadata(resolved['url'], maximum, timeout)
            if metadata['size'] > maximum:
                raise ConfigFailure('IPA 超过当前 max_ipa_mb 限制')
            if advertised is not None and int(advertised) != metadata['size']:
                raise ValueError('IPA 实际大小与 GitHub 发行资产记录不符')
        except ConfigFailure as exc:
            errors.append(f'项目 #{number}: {exc}')
            continue
        except Exception as exc:
            # Upstream/network failure. If this project published fine
            # before, keep its last-good entry and say so loudly instead of
            # blocking every other app in the source. Without a last-good
            # entry there is nothing honest to publish: stay fatal.
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            stale_app = previous.get('app')
            if not stale_app:
                errors.append(f'项目 #{number}: {detail}')
                continue
            bundle = stale_app['bundleIdentifier']
            if bundle in bundle_ids:
                errors.append(f'项目 #{number}: 源中出现重复 Bundle ID，请只保留一个发行渠道')
                continue
            bundle_ids.add(bundle)
            apps.append(copy.deepcopy(stale_app))
            stale_status = copy.deepcopy(previous.get('status') or {
                'name': stale_app['name'], 'bundleIdentifier': bundle,
                'version': stale_app['version'],
                'buildVersion': stale_app['versions'][0].get('buildVersion'),
                'sha256': previous.get('sha256'), 'size': stale_app['size'],
                'projectURL': None, 'releaseTag': None,
                'downloadURL': stale_app['downloadURL'],
            })
            stale_status['stale'] = True
            status.append(stale_status)
            new_cache['items'][key] = previous
            if previous.get('iconB64') and \
                    stale_app.get('iconURL', '').endswith('/icons/' + bundle + '.png'):
                icons[bundle] = base64.b64decode(previous['iconB64'])
            warnings.append(f'项目 #{number}（{bundle}）上游暂时失败，已保留上次成功版本：{detail}')
            print(f'STALE #{number}: {bundle} ({detail})')
            continue
        try:
            app, date = build_app(project, resolved, metadata, base_url, previous)
            bundle = app['bundleIdentifier']
            if bundle in bundle_ids:
                raise ConfigFailure('源中出现重复 Bundle ID，请只保留一个发行渠道')
        except Exception as exc:
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            errors.append(f'项目 #{number}: {detail}')
            continue
        bundle_ids.add(bundle)
        apps.append(app)
        icon_png = getattr(metadata, 'icon_png', None)
        if icon_png and not project.get('icon_url'):
            icons[bundle] = icon_png
        entry_status = {
            'name': app['name'], 'bundleIdentifier': bundle,
            'version': metadata['version'], 'buildVersion': metadata.get('buildVersion'),
            'sha256': metadata['sha256'], 'size': metadata['size'],
            'projectURL': resolved.get('project_url'),
            'releaseTag': resolved.get('release_tag'),
            'downloadURL': resolved['url'],
        }
        status.append(entry_status)
        new_cache['items'][key] = {
            'fingerprint': fingerprint, 'metadata': dict(metadata),
            'date': date, 'sha256': metadata['sha256'],
            'app': app, 'status': entry_status,
            'iconB64': base64.b64encode(icon_png).decode() if icon_png else None,
            'iconTint': getattr(metadata, 'icon_tint', None),
        }
        print(f'OK #{number}: {bundle} {metadata["version"]}')
    if errors:
        raise ValueError('\n'.join(errors) + '\n本次不写入或发布任何源更新；请修复配置后重试。')
    for warning in warnings:
        print('警告：' + warning)
    result = {
        'name': source['name'], 'identifier': source['identifier'],
        'subtitle': source.get('subtitle', ''),
        'description': source.get('description', ''), 'apps': apps, 'news': [],
    }
    if base_url:
        result.update(website=base_url + '/', iconURL=base_url + '/AltStore-Gallery.png',
                      sourceURL=base_url + '/source.json')
    # Only public metadata goes in public/. IPA bytes and credentials are never published.
    write_icons(output, icons)
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
