"""File-backed request bodies and lossless edits of small JSON control fields.

Large conversation/image strings stay in a temporary file. JSON boundaries
are validated without decoding the entire conversation into a Python tree.
"""

from __future__ import annotations

import gzip
import codecs
import json
import mmap
import re
import tempfile

_STRING_END = re.compile(br'["\\\x00-\x1f]')
_NUMBER = re.compile(br'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_WS = re.compile(br'[ \t\r\n]*')
_ESCAPE = re.compile(br'(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')
BLOCK = 64 * 1024


class _Json:
    def __init__(self, data):
        self.data = data

    def space(self, pos):
        return _WS.match(self.data, pos).end()

    def string(self, pos):
        pos += 1
        while match := _STRING_END.search(self.data, pos):
            char = self.data[match.start()]
            if char == 34:
                return match.end()
            if char != 92:
                break
            escaped = _ESCAPE.match(self.data, match.end())
            if escaped is None:
                break
            pos = escaped.end()
        raise ValueError('Invalid JSON string')

    def value(self, pos, depth=0):
        if depth > 512 or pos >= len(self.data):
            raise ValueError('Invalid JSON nesting')
        char = self.data[pos]
        if char == 34:
            return self.string(pos)
        if char in (123, 91):
            closing = 125 if char == 123 else 93
            pos = self.space(pos + 1)
            if pos < len(self.data) and self.data[pos] == closing:
                return pos + 1
            while pos < len(self.data):
                if char == 123:
                    if self.data[pos] != 34:
                        break
                    pos = self.space(self.string(pos))
                    if pos >= len(self.data) or self.data[pos] != 58:
                        break
                    pos = self.space(pos + 1)
                pos = self.space(self.value(pos, depth + 1))
                if pos >= len(self.data):
                    break
                if self.data[pos] == closing:
                    return pos + 1
                if self.data[pos] != 44:
                    break
                pos = self.space(pos + 1)
            raise ValueError('Invalid JSON container')
        for literal in (b'true', b'false', b'null', b'NaN', b'Infinity', b'-Infinity'):
            if self.data[pos:pos + len(literal)] == literal:
                return pos + len(literal)
        number = _NUMBER.match(self.data, pos)
        if number:
            return number.end()
        raise ValueError('Invalid JSON value')

    def object_fields(self, start=0, end=None):
        end = len(self.data) if end is None else end
        pos = self.space(start)
        if pos >= end or self.data[pos] != 123:
            raise ValueError('Request must be a JSON object')
        result = {}
        pos = self.space(pos + 1)
        while pos < end and self.data[pos] != 125:
            if self.data[pos] != 34:
                raise ValueError('Invalid JSON key')
            key_end = self.string(pos)
            key = json.loads(self.data[pos:key_end])
            pos = self.space(key_end)
            if pos >= end or self.data[pos] != 58:
                raise ValueError('Missing JSON colon')
            start_value = self.space(pos + 1)
            value_end = self.value(start_value)
            result[key] = (start_value, value_end)
            pos = self.space(value_end)
            if pos < end and self.data[pos] == 44:
                pos = self.space(pos + 1)
                if pos < end and self.data[pos] == 125:
                    raise ValueError('Trailing JSON comma')
            elif pos >= end or self.data[pos] != 125:
                raise ValueError('Invalid JSON object')
        if pos >= end or (pos + 1 != end and self.space(pos + 1) != end):
            raise ValueError('Trailing JSON content')
        return result, pos

    def array_items(self, start, end):
        pos = self.space(start)
        if self.data[pos] != 91:
            raise ValueError('Expected JSON array')
        pos = self.space(pos + 1)
        while pos < end and self.data[pos] != 93:
            finish = self.value(pos)
            yield pos, finish
            pos = self.space(finish)
            if self.data[pos] == 44:
                pos = self.space(pos + 1)
            elif self.data[pos] != 93:
                raise ValueError('Invalid JSON array')


class BodyFiles:
    def __init__(self):
        self.files = []

    def new(self):
        file = tempfile.TemporaryFile(prefix='codex-hud-request-')
        self.files.append(file)
        return RequestBody(self, file)

    def close(self):
        for file in self.files:
            file.close()
        self.files.clear()


class RequestBody:
    def __init__(self, owner, file):
        self.owner = owner
        self.file = file
        self.size = 0
        self._fields = None
        self._encoding_checked = False

    def write(self, data):
        self._fields = None
        self._encoding_checked = False
        self.file.write(data)
        self.size += len(data)

    def read(self, size=-1):
        return self.file.read(size)

    def rewind(self):
        self.file.seek(0)
        return self

    def __bool__(self):
        return self.size > 0

    def mapping(self):
        self.file.flush()
        if not self.size:
            raise ValueError('Empty JSON body')
        if not self._encoding_checked:
            self._check_encoding()
        return mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)

    def _check_encoding(self):
        self.rewind()
        encoding = json.detect_encoding(self.file.read(4))
        decoder = codecs.getincrementaldecoder(encoding)()
        converted = self.owner.new() if encoding != 'utf-8' else None
        self.rewind()
        while block := self.file.read(BLOCK):
            text = decoder.decode(block)
            if converted is not None:
                converted.write(text.encode('utf-8'))
        final = decoder.decode(b'', final=True)
        if converted is not None:
            converted.write(final.encode('utf-8'))
            self.file, self.size = converted.file, converted.size
        self._encoding_checked = True
        self.rewind()

    def fields(self, data):
        if self._fields is None:
            self._fields = _Json(data).object_fields()
        return self._fields

    def field(self, name, default=None):
        with self.mapping() as data:
            fields, _end = self.fields(data)
            span = fields.get(name)
            return json.loads(data[span[0]:span[1]]) if span else default

    def replace(self, updates):
        with self.mapping() as data:
            fields, end = self.fields(data)
            edits = []
            additions = []
            for key, value in updates.items():
                encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                if key in fields:
                    edits.append((*fields[key], encoded))
                else:
                    additions.append(json.dumps(key).encode() + b':' + encoded)
            if additions:
                edits.append((end, end, (b',' if fields else b'') + b','.join(additions)))
            output = self.owner.new()
            start = 0
            for begin, finish, replacement in sorted(edits):
                self._copy(data, start, begin, output)
                output.write(replacement)
                start = finish
            self._copy(data, start, len(data), output)
        return output.rewind()

    def background_payload(self):
        from collections import deque

        with self.mapping() as data:
            parser = _Json(data)
            fields, _end = self.fields(data)
            payload = {}
            for key in ('instructions', 'prompt'):
                if key in fields:
                    start, end = fields[key]
                    payload[key] = json.loads(data[start:end])
            key = 'input' if 'input' in fields else 'messages'
            if key in fields:
                start, end = fields[key]
                if data[start] == 91:
                    spans = deque(parser.array_items(start, end), maxlen=3)
                    payload[key] = [json.loads(data[a:b]) for a, b in spans]
                else:
                    payload[key] = json.loads(data[start:end])
            return payload

    def image_payload(self):
        """Read only the newest user input; omit unrelated conversation bodies."""
        with self.mapping() as data:
            fields, _end = self.fields(data)
            parser = _Json(data)
            payload = {key: json.loads(data[a:b]) for key, (a, b) in fields.items() if key in {'model', 'stream'}}
            if 'input' not in fields:
                return payload
            start, end = fields['input']
            if data[start] != 91:
                payload['input'] = json.loads(data[start:end])
                return payload
            last = None
            for begin, finish in parser.array_items(start, end):
                if data[begin] == 123:
                    item = _ObjectView(data, parser, begin, finish)
                    if item.get('role') == 'user':
                        last = (begin, finish)
            if last:
                item = _ObjectView(data, parser, *last)
                content = item.fields.get('content')
                if content and data[content[0]] == 91:
                    for begin, finish in parser.array_items(*content):
                        if data[begin] == 123:
                            kind = _ObjectView(data, parser, begin, finish).get('type')
                            if kind not in {'input_text', 'text'}:
                                payload['input'] = [{'role': 'user', 'content': [{'type': kind}]}]
                                return payload
            payload['input'] = [json.loads(data[last[0]:last[1]])] if last else []
            return payload

    def image_base64_chunks(self):
        """Yield decoded JSON string pieces without allocating the Base64 image."""
        with self.mapping() as data:
            fields, _end = self.fields(data)
            parser = _Json(data)
            span = fields.get('data')
            first = next(parser.array_items(*span), None) if span and data[span[0]] == 91 else None
            if not first or data[first[0]] != 123:
                raise ValueError('供应商未返回有效 b64_json 图片；请确认 Images API 返回 Base64 图片。')
            item = _ObjectView(data, parser, *first)
            span = item.fields.get('b64_json')
            if not span or data[span[0]] != 34:
                raise ValueError('供应商未返回有效 b64_json 图片；请确认 Images API 返回 Base64 图片。')
            pos, end = span[0] + 1, span[1] - 1
            while pos < end:
                escape = data.find(b'\\', pos, min(pos + BLOCK, end))
                if escape < 0:
                    finish = min(pos + BLOCK, end)
                    yield data[pos:finish]
                    pos = finish
                else:
                    if escape > pos:
                        yield data[pos:escape]
                    match = _ESCAPE.match(data, escape + 1)
                    yield json.loads(b'"' + data[escape:match.end()] + b'"').encode('ascii')
                    pos = match.end()

    def map_input(self, transform):
        """Stream untouched input items; decode only explicitly transformed ones."""
        with self.mapping() as data:
            parser = _Json(data)
            fields, _end = self.fields(data)
            if 'input' not in fields or data[fields['input'][0]] != 91:
                return self
            start, end = fields['input']
            output = self.owner.new()
            self._copy(data, 0, start + 1, output)
            first = True
            for begin, finish in parser.array_items(start, end):
                item = _ObjectView(data, parser, begin, finish) if data[begin] == 123 else None
                replacement = transform(item)
                if replacement is False:
                    continue
                if not first:
                    output.write(b',')
                first = False
                if replacement is None:
                    self._copy(data, begin, finish, output)
                else:
                    output.write(json.dumps(replacement, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            self._copy(data, end - 1, len(data), output)
        return output.rewind()

    @staticmethod
    def _copy(data, start, end, output):
        for pos in range(start, end, BLOCK):
            output.write(data[pos:min(pos + BLOCK, end)])

    def load(self):
        # Compatibility path for transformations requiring full semantic input.
        # Ordinary model/tier/image-mode dispatch never enters this path.
        self.rewind()
        try:
            return json.load(self.file)
        finally:
            self.rewind()

    def dump(self, value):
        output = self.owner.new()
        for part in json.JSONEncoder(ensure_ascii=False, separators=(',', ':')).iterencode(value):
            output.write(part.encode('utf-8'))
        return output.rewind()


class _ObjectView:
    def __init__(self, data, parser, start, end):
        self.data = data
        self.start = start
        self.end = end
        self.fields, _close = parser.object_fields(start, end)

    def get(self, key, default=None):
        span = self.fields.get(key)
        return json.loads(self.data[span[0]:span[1]]) if span else default

    def without(self, key):
        # Sealed reasoning items are small control records. Ordinary messages,
        # including large images/tool text, are copied without decoding.
        value = json.loads(self.data[self.start:self.end])
        value.pop(key, None)
        return value


def body_field(body, key, default=None):
    if isinstance(body, RequestBody):
        return body.field(key, default)
    value = json.loads(body)
    return value.get(key, default) if isinstance(value, dict) else default


def replace_body_fields(body, updates):
    if isinstance(body, RequestBody):
        return body.replace(updates)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError('Request must be a JSON object')
    value.update(updates)
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def load_body(body):
    return body.load() if isinstance(body, RequestBody) else json.loads(body)


def dump_body(body, value):
    return body.dump(value) if isinstance(body, RequestBody) else json.dumps(
        value, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')


def receive_body(reader, headers, files):
    if 'chunked' not in headers.get('Transfer-Encoding', '').lower() and not headers.get('Content-Length'):
        return None
    output = files.new()
    if 'chunked' in headers.get('Transfer-Encoding', '').lower():
        while True:
            size = int(reader.readline().split(b';', 1)[0], 16)
            if size < 0:
                raise ValueError('Invalid chunk size')
            if size == 0:
                while reader.readline().strip():
                    pass
                break
            _receive_exact(reader, size, output)
            if _read_small(reader, 2) != b'\r\n':
                raise ValueError('Invalid chunk framing')
    elif headers.get('Content-Length'):
        size = int(headers['Content-Length'])
        if size < 0:
            raise ValueError('Invalid content length')
        _receive_exact(reader, size, output)
    else:
        return None
    return output.rewind()


def _receive_exact(reader, size, output):
    while size:
        data = reader.read(min(size, BLOCK))
        if not data:
            raise ValueError('Truncated request body')
        output.write(data)
        size -= len(data)


def _read_small(reader, size):
    result = bytearray()
    while len(result) < size:
        data = reader.read(size - len(result))
        if not data:
            raise ValueError('Truncated request body')
        result.extend(data)
    return bytes(result)


def decode_body(body, encoding):
    encoding = encoding.strip().lower()
    if not body or encoding in {'', 'identity'}:
        return body
    errors = (OSError, EOFError)
    try:
        if encoding == 'gzip':
            reader = gzip.GzipFile(fileobj=body.rewind().file)
        elif encoding == 'zstd':
            try:
                import zstandard
            except ImportError as exc:
                raise ValueError('缺少 zstandard 依赖，无法解压 Codex 请求。') from exc
            errors += (zstandard.ZstdError,)
            reader = zstandard.ZstdDecompressor().stream_reader(body.rewind().file, closefd=False)
        else:
            raise ValueError('HUD 代理不支持此请求压缩格式。')
        output = body.owner.new()
        with reader:
            while data := reader.read(BLOCK):
                output.write(data)
                if output.size > 128 * 1024 * 1024:
                    raise ValueError('解压后的 Codex 请求超过 128 MiB。')
    except errors as exc:
        raise ValueError('无法解压 Codex 请求。') from exc
    return output.rewind()
