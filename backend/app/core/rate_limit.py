from collections import defaultdict, deque
import ipaddress
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status

from app.core.config import settings

_requests: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()
_checks = 0


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    trusted = []
    for cidr in settings.trusted_proxy_cidrs.split(","):
        try:
            trusted.append(ipaddress.ip_network(cidr.strip(), strict=False))
        except ValueError:
            continue
    if any(peer_ip in network for network in trusted):
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return str(peer_ip)


def enforce_rate_limit(request: Request, *, scope: str, limit: int, window_seconds: int = 60, identity: str | None = None) -> None:
    key = f"{scope}:{_client_ip(request)}:{(identity or '').strip().lower()}"
    global _checks
    now = monotonic()
    with _lock:
        _checks += 1
        if _checks % 256 == 0:
            cutoff = now - window_seconds
            stale = [item for item, timestamps in _requests.items() if not timestamps or timestamps[-1] <= cutoff]
            for item in stale:
                _requests.pop(item, None)
        events = _requests[key]
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again shortly.",
                headers={"Retry-After": str(window_seconds)},
            )
        events.append(now)
