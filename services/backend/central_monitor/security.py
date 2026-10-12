from ipaddress import ip_address
from urllib.parse import urlsplit


BLOCKED_HOSTS={"localhost","metadata.google.internal","169.254.169.254"}


def validate_outbound_url(value:str)->str:
    parsed=urlsplit(value)
    if parsed.scheme not in ("http","https") or not parsed.hostname:raise ValueError("Only absolute HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:raise ValueError("Credentials embedded in URLs are forbidden")
    hostname=parsed.hostname.lower().rstrip(".")
    if hostname in BLOCKED_HOSTS or hostname.endswith(".localhost"):raise ValueError("Loopback and metadata targets are forbidden")
    try:
        address=ip_address(hostname)
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:raise ValueError("Loopback, link-local, multicast and unspecified targets are forbidden")
    except ValueError as error:
        if "forbidden" in str(error):raise
    return value
