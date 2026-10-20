import re

from django import template

register = template.Library()

SAFE_SCHEMES = {"http", "https", "mailto", "tel"}
SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
HOSTNAME = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?")


@register.filter
def external_href(value):
    """A safe href for a stored external link, or "" when there is none.

    Purchase links were entered without a scheme in places ("mcmaster.com/#...")
    and a bare value renders as a path relative to this site. Leading slashes
    and anchors are left alone because they are meant to be relative.
    """
    url = (value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url[0] in "/#?":
        return url

    scheme = SCHEME.match(url)
    if scheme and scheme.group(1).lower() in SAFE_SCHEMES:
        return url

    host = url.split("/", 1)[0]
    if HOSTNAME.fullmatch(host):
        return "https://" + url
    return ""
