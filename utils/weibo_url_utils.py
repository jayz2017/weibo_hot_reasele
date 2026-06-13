# -*- coding: utf-8 -*-
from urllib.parse import unquote, urlparse, urlunparse


def normalize_weibo_detail_url(url: str) -> str:
    """Return a stable Weibo detail URL without query strings or fragments."""
    if not url:
        return ""

    value = str(url).strip()
    if not value:
        return ""

    if value.startswith("//"):
        value = f"https:{value}"
    elif "://" not in value and "weibo.com" in value:
        value = f"https://{value.lstrip('/')}"

    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    segments = [unquote(part) for part in parsed.path.split("/") if part]

    if host == "app.weibo.com" and len(segments) >= 3 and segments[:2] == ["t", "feed"]:
        return f"https://weibo.com/detail/{segments[2]}"

    if host == "m.weibo.cn" and len(segments) >= 2 and segments[0] == "detail":
        return f"https://weibo.com/detail/{segments[1]}"

    if host.endswith("weibo.com"):
        clean_path = "/" + "/".join(segments) if segments else ""
        if len(segments) >= 2 and segments[0] == "detail":
            clean_path = f"/detail/{segments[1]}"
        return urlunparse(("https", "weibo.com", clean_path, "", "", ""))

    clean_path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme or "https", host, clean_path, "", "", ""))


def extract_weibo_status_key(url: str) -> str:
    """Extract the status id/base62 id used to compare article and comment URLs."""
    normalized = normalize_weibo_detail_url(url)
    if not normalized:
        return ""

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    segments = [part for part in parsed.path.split("/") if part]
    if host != "weibo.com" or not segments:
        return normalized

    if len(segments) >= 2 and segments[0] == "detail":
        return segments[1]

    if len(segments) >= 2:
        return segments[-1]

    return normalized


def same_weibo_detail_url(left: str, right: str) -> bool:
    """Compare Weibo article URLs across search, detail, and redirected forms."""
    left_key = extract_weibo_status_key(left)
    right_key = extract_weibo_status_key(right)
    if left_key and right_key:
        return left_key == right_key
    return normalize_weibo_detail_url(left) == normalize_weibo_detail_url(right)
