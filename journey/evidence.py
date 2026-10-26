"""Append evidence as it arrives; credentials never enter reports or archives."""
import hashlib
import json
import re
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

TOKEN_PATTERN=re.compile(r"\b(?:sk-(?:uca-v1-|sa-v1-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
TOKEN_BYTES=re.compile(TOKEN_PATTERN.pattern.encode('ascii'))

def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Evidence:
    def __init__(self, directory, secrets=()):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        expanded = [s for s in secrets if s]
        for secret in list(expanded):
            parsed = urllib.parse.urlsplit(secret)
            if parsed.scheme in {'http','https'} and parsed.netloc:
                expanded.append(parsed.scheme+'://'+parsed.netloc)
        self.secrets = tuple(sorted(set(expanded),key=len,reverse=True))

    def text(self, value):
        for secret in self.secrets:
            for form in (secret, urllib.parse.quote(secret, safe="")):
                value = value.replace(form, "[REDACTED]")
        value = TOKEN_PATTERN.sub("[REDACTED]",value)
        value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
        value = re.sub(r'(?i)(["\'](?:access_token|refresh_token|api_key|client_secret|password)["\']\s*:\s*["\'])[^"\']*', r'\1[REDACTED]', value)
        value = re.sub(r"(?i)([?&](?:token|access_token|api_key|key|sig|signature|x-amz-signature|x-amz-credential|x-amz-security-token|x-goog-signature|x-goog-credential|credential|secret)=)[^&\s\"<>]+", r"\1[REDACTED]", value)
        return value

    def clean(self, value):
        if isinstance(value, dict):
            return {k: "[REDACTED]" if re.fullmatch(
                r"authorization|cookie|set-cookie|x-api-key|api[_-]?key|token|(?:access|refresh|id)[_-]?token|client[_-]?secret|password",
                k, re.I) else self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        return self.text(value) if isinstance(value, str) else value

    def write(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(self.clean(value), ensure_ascii=False, indent=2) + "\n")
        temporary.replace(target)

    def append(self, name, value):
        with (self.root / name).open("a") as output:
            output.write(json.dumps(self.clean(value), ensure_ascii=False) + "\n")
            output.flush()

    def observe(self, exchange):
        self.append("http.jsonl", {"recorded_at": utc_now(), **exchange})


def safe_extract(archive, destination, max_bytes=1_000_000_000):
    """Reject unsafe names, links and decompression bombs before writing."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        if sum(info.file_size for info in bundle.infolist()) > max_bytes:
            raise ValueError("archive_expanded_size_exceeded")
        names = set()
        for info in bundle.infolist():
            name = info.filename
            target = (destination / name).resolve()
            if ("\\" in name or target == destination or not target.is_relative_to(destination)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000 or name in names):
                raise ValueError("unsafe_archive_member")
            names.add(name)
        bundle.extractall(destination)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
