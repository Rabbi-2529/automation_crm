"""Outbound HTTP safety for user-supplied URLs (workflow webhooks, webhook endpoints)."""

import ipaddress
import socket
from urllib.parse import urlsplit

from django.conf import settings


def check_public_url(url):
    """Raises ValueError for URLs that resolve to localhost / private networks (SSRF), unless ALLOW_PRIVATE_WEBHOOKS."""
    host = urlsplit(url).hostname
    if not host:
        raise ValueError("URL has no host")
    if getattr(settings, "ALLOW_PRIVATE_WEBHOOKS", False):
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ValueError(f"Can't resolve {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError(f"Sending to a private address ({host}) isn't allowed")
