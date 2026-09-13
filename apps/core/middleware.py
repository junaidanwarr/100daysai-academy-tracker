"""Request-scoped context for the audit trail."""

import threading

_local = threading.local()


def get_client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class AuditContextMiddleware:
    """
    Stashes the request IP and user agent so service functions can attribute an
    audit entry without every caller having to thread the request through.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.ip_address = get_client_ip(request)
        _local.user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
        try:
            return self.get_response(request)
        finally:
            _local.ip_address = None
            _local.user_agent = None


def current_request_meta() -> dict[str, str | None]:
    return {
        "ip_address": getattr(_local, "ip_address", None),
        "user_agent": getattr(_local, "user_agent", None),
    }
