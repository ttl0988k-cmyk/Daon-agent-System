# agent/openai_client_lifecycle.py
#
# OpenAI client lifecycle management — extracted from AIAgent in run_agent.py.
# All functions take an ``agent`` parameter (the AIAgent instance) instead of ``self``.

from __future__ import annotations

import logging
import os
import socket
import threading
from types import SimpleNamespace
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_proxy_from_env() -> Optional[str]:
    """Read proxy settings from environment variables."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var)
        if val:
            return val
    return None


def thread_identity(agent: Any) -> str:
    """Return a short thread identifier for log context."""
    thread = threading.current_thread()
    return f"{thread.name}:{thread.ident}"


def client_log_context(agent: Any) -> str:
    """Return a concise log context string for the current client state."""
    provider = getattr(agent, "provider", "unknown")
    base_url = getattr(agent, "base_url", "unknown")
    model = getattr(agent, "model", "unknown")
    return (
        f"thread={thread_identity(agent)} provider={provider} "
        f"base_url={base_url} model={model}"
    )


def openai_client_lock(agent: Any) -> threading.RLock:
    """Return the reentrant lock that guards OpenAI client replacement."""
    lock = getattr(agent, "_client_lock", None)
    if lock is None:
        lock = threading.RLock()
        agent._client_lock = lock
    return lock


def is_openai_client_closed(agent: Any, client: Any) -> bool:
    """Check whether an OpenAI client has been closed.

    Handles both property and method forms of is_closed:
    - httpx.Client.is_closed is a bool property
    - openai.OpenAI.is_closed is a method returning bool

    Prior bug: getattr(client, "is_closed", False) returned the bound method,
    which is always truthy, causing unnecessary client recreation on every call.
    """
    from unittest.mock import Mock

    if isinstance(client, Mock):
        return False

    is_closed_attr = getattr(client, "is_closed", None)
    if is_closed_attr is not None:
        # Handle method (openai SDK) vs property (httpx)
        if callable(is_closed_attr):
            if is_closed_attr():
                return True
        elif bool(is_closed_attr):
            return True

    http_client = getattr(client, "_client", None)
    if http_client is not None:
        return bool(getattr(http_client, "is_closed", False))
    return False


def build_keepalive_http_client(agent: Any) -> Any:
    """Build an ``httpx.Client`` with keep-alive and connection-pool settings.

    Used as the ``http_client`` kwarg when creating OpenAI clients so
    that TCP connections are reused across requests.
    """
    try:
        import httpx as _httpx
        import socket as _socket

        _sock_opts = [(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1)]
        if hasattr(_socket, "TCP_KEEPIDLE"):
            _sock_opts.append((_socket.IPPROTO_TCP, _socket.TCP_KEEPIDLE, 30))
            _sock_opts.append((_socket.IPPROTO_TCP, _socket.TCP_KEEPINTVL, 10))
            _sock_opts.append((_socket.IPPROTO_TCP, _socket.TCP_KEEPCNT, 3))
        elif hasattr(_socket, "TCP_KEEPALIVE"):
            _sock_opts.append((_socket.IPPROTO_TCP, _socket.TCP_KEEPALIVE, 30))
        # When a custom transport is provided, httpx won't auto-read proxy
        # from env vars (allow_env_proxies = trust_env and transport is None).
        # Explicitly read proxy settings to ensure HTTP_PROXY/HTTPS_PROXY work.
        _proxy = _get_proxy_from_env()
        return _httpx.Client(
            transport=_httpx.HTTPTransport(socket_options=_sock_opts),
            proxy=_proxy,
        )
    except Exception:
        return None


def create_openai_client(
    agent: Any,
    client_kwargs: dict,
    *,
    reason: str,
    shared: bool,
) -> Any:
    """Create a new OpenAI client from the given kwargs.

    Handles Copilot ACP, Gemini CloudCode, Gemini native, and standard
    OpenAI clients.  Injects TCP keepalive http_client for all standard
    OpenAI clients.
    """
    from agent.auxiliary_client import _validate_base_url, _validate_proxy_env_urls

    # Treat client_kwargs as read-only. Callers pass self._client_kwargs (or shallow
    # copies of it) in; any in-place mutation leaks back into the stored dict and is
    # reused on subsequent requests. #10933 hit this by injecting an httpx.Client
    # transport that was torn down after the first request, so the next request
    # wrapped a closed transport and raised "Cannot send a request, as the client
    # has been closed" on every retry. The revert resolved that specific path; this
    # copy locks the contract so future transport/keepalive work can't reintroduce
    # the same class of bug.
    client_kwargs = dict(client_kwargs)
    _validate_proxy_env_urls()
    _validate_base_url(client_kwargs.get("base_url"))

    provider = getattr(agent, "provider", None)

    if provider == "copilot-acp" or str(client_kwargs.get("base_url", "")).startswith("acp://copilot"):
        from agent.copilot_acp_client import CopilotACPClient

        client = CopilotACPClient(**client_kwargs)
        logger.info(
            "Copilot ACP client created (%s, shared=%s) %s",
            reason,
            shared,
            client_log_context(agent),
        )
        return client

    if provider == "google-gemini-cli" or str(client_kwargs.get("base_url", "")).startswith("cloudcode-pa://"):
        from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient

        # Strip OpenAI-specific kwargs the Gemini client doesn't accept
        safe_kwargs = {
            k: v for k, v in client_kwargs.items()
            if k in {"api_key", "base_url", "default_headers", "project_id", "timeout"}
        }
        client = GeminiCloudCodeClient(**safe_kwargs)
        logger.info(
            "Gemini Cloud Code Assist client created (%s, shared=%s) %s",
            reason,
            shared,
            client_log_context(agent),
        )
        return client

    if provider == "gemini":
        from agent.gemini_native_adapter import GeminiNativeClient, is_native_gemini_base_url

        base_url = str(client_kwargs.get("base_url", "") or "")
        if is_native_gemini_base_url(base_url):
            safe_kwargs = {
                k: v for k, v in client_kwargs.items()
                if k in {"api_key", "base_url", "default_headers", "timeout", "http_client"}
            }
            if "http_client" not in safe_kwargs:
                keepalive_http = build_keepalive_http_client(agent)
                if keepalive_http is not None:
                    safe_kwargs["http_client"] = keepalive_http
            client = GeminiNativeClient(**safe_kwargs)
            logger.info(
                "Gemini native client created (%s, shared=%s) %s",
                reason,
                shared,
                client_log_context(agent),
            )
            return client

    # Inject TCP keepalives so the kernel detects dead provider connections
    # instead of letting them sit silently in CLOSE-WAIT (#10324).  Without
    # this, a peer that drops mid-stream leaves the socket in a state where
    # epoll_wait never fires, ``httpx`` read timeout may not trigger, and
    # the agent hangs until manually killed.  Probes after 30s idle, retry
    # every 10s, give up after 3 -> dead peer detected within ~60s.
    #
    # Safety against #10933: the ``client_kwargs = dict(client_kwargs)``
    # above means this injection only lands in the local per-call copy,
    # never back into ``self._client_kwargs``.  Each ``create_openai_client``
    # invocation therefore gets its OWN fresh ``httpx.Client`` whose
    # lifetime is tied to the OpenAI client it is passed to.  When the
    # OpenAI client is closed (rebuild, teardown, credential rotation),
    # the paired ``httpx.Client`` closes with it, and the next call
    # constructs a fresh one -- no stale closed transport can be reused.
    # Tests in ``tests/run_agent/test_create_openai_client_reuse.py`` and
    # ``tests/run_agent/test_sequential_chats_live.py`` pin this invariant.
    if "http_client" not in client_kwargs:
        keepalive_http = build_keepalive_http_client(agent)
        if keepalive_http is not None:
            client_kwargs["http_client"] = keepalive_http

    from openai import OpenAI as _OpenAI

    client = _OpenAI(**client_kwargs)
    logger.info(
        "OpenAI client created (%s, shared=%s) %s",
        reason,
        shared,
        client_log_context(agent),
    )
    return client


def force_close_tcp_sockets(agent: Any, client: Any) -> int:
    """Force-close underlying TCP sockets to prevent CLOSE-WAIT accumulation.

    When a provider drops a connection mid-stream, httpx's ``client.close()``
    performs a graceful shutdown which leaves sockets in CLOSE-WAIT until the
    OS times them out (often minutes).  This method walks the httpx transport
    pool and issues ``socket.shutdown(SHUT_RDWR)`` + ``socket.close()`` to
    force an immediate TCP RST, freeing the file descriptors.

    Returns the number of sockets force-closed.
    """
    import socket as _socket

    closed = 0
    try:
        http_client = getattr(client, "_client", None)
        if http_client is None:
            return 0
        transport = getattr(http_client, "_transport", None)
        if transport is None:
            return 0
        pool = getattr(transport, "_pool", None)
        if pool is None:
            return 0
        # httpx uses httpcore connection pools; connections live in
        # _connections (list) or _pool (list) depending on version.
        connections = (
            getattr(pool, "_connections", None)
            or getattr(pool, "_pool", None)
            or []
        )
        for conn in list(connections):
            stream = (
                getattr(conn, "_network_stream", None)
                or getattr(conn, "_stream", None)
            )
            if stream is None:
                continue
            sock = getattr(stream, "_sock", None)
            if sock is None:
                sock = getattr(stream, "stream", None)
                if sock is not None:
                    sock = getattr(sock, "_sock", None)
            if sock is None:
                continue
            try:
                sock.shutdown(_socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            closed += 1
    except Exception as exc:
        logger.debug("Force-close TCP sockets sweep error: %s", exc)
    return closed


def close_openai_client(
    agent: Any,
    client: Any,
    *,
    reason: str,
    shared: bool,
) -> None:
    """Close an OpenAI client and force-close its TCP sockets.

    Force-closes TCP sockets first to prevent CLOSE-WAIT accumulation,
    then does the graceful SDK-level close.
    """
    if client is None:
        return
    # Force-close TCP sockets first to prevent CLOSE-WAIT accumulation,
    # then do the graceful SDK-level close.
    force_closed = force_close_tcp_sockets(agent, client)
    try:
        client.close()
        logger.info(
            "OpenAI client closed (%s, shared=%s, tcp_force_closed=%d) %s",
            reason,
            shared,
            force_closed,
            client_log_context(agent),
        )
    except Exception as exc:
        logger.debug(
            "OpenAI client close failed (%s, shared=%s) %s error=%s",
            reason,
            shared,
            client_log_context(agent),
            exc,
        )


def replace_primary_openai_client(agent: Any, *, reason: str) -> bool:
    """Replace the shared primary OpenAI client with a fresh instance.

    Acquires ``_openai_client_lock``, closes the old client, and
    creates a new one from ``_client_kwargs``.
    """
    with openai_client_lock(agent):
        old_client = getattr(agent, "client", None)
        try:
            new_client = create_openai_client(
                agent,
                getattr(agent, "_client_kwargs", {}),
                reason=reason,
                shared=True,
            )
        except Exception as exc:
            logger.warning(
                "Failed to rebuild shared OpenAI client (%s) %s error=%s",
                reason,
                client_log_context(agent),
                exc,
            )
            return False
        agent.client = new_client
    close_openai_client(agent, old_client, reason=f"replace:{reason}", shared=True)
    return True


def ensure_primary_openai_client(agent: Any, *, reason: str) -> Any:
    """Return the primary OpenAI client, creating it if necessary.

    Thread-safe: uses ``_openai_client_lock`` to guard creation.
    """
    with openai_client_lock(agent):
        client = getattr(agent, "client", None)
        if client is not None and not is_openai_client_closed(agent, client):
            return client

    logger.warning(
        "Detected closed shared OpenAI client; recreating before use (%s) %s",
        reason,
        client_log_context(agent),
    )
    if not replace_primary_openai_client(agent, reason=f"recreate_closed:{reason}"):
        raise RuntimeError("Failed to recreate closed OpenAI client")
    with openai_client_lock(agent):
        return agent.client


def cleanup_dead_connections(agent: Any) -> bool:
    """Detect and clean up dead TCP connections on the primary client.

    Inspects the httpx connection pool for sockets in unhealthy states
    (CLOSE-WAIT, errors).  If any are found, force-closes all sockets
    and rebuilds the primary client from scratch.

    Returns True if dead connections were found and cleaned up.
    """
    client = getattr(agent, "client", None)
    if client is None:
        return False
    try:
        http_client = getattr(client, "_client", None)
        if http_client is None:
            return False
        transport = getattr(http_client, "_transport", None)
        if transport is None:
            return False
        pool = getattr(transport, "_pool", None)
        if pool is None:
            return False
        connections = (
            getattr(pool, "_connections", None)
            or getattr(pool, "_pool", None)
            or []
        )
        dead_count = 0
        for conn in list(connections):
            # Check for connections that are idle but have closed sockets
            stream = (
                getattr(conn, "_network_stream", None)
                or getattr(conn, "_stream", None)
            )
            if stream is None:
                continue
            sock = getattr(stream, "_sock", None)
            if sock is None:
                sock = getattr(stream, "stream", None)
                if sock is not None:
                    sock = getattr(sock, "_sock", None)
            if sock is None:
                continue
            # Probe socket health with a non-blocking recv peek
            import socket as _socket
            try:
                sock.setblocking(False)
                data = sock.recv(1, _socket.MSG_PEEK | _socket.MSG_DONTWAIT)
                if data == b"":
                    dead_count += 1
            except BlockingIOError:
                pass  # No data available -- socket is healthy
            except OSError:
                dead_count += 1
            finally:
                try:
                    sock.setblocking(True)
                except OSError:
                    pass
        if dead_count > 0:
            logger.warning(
                "Found %d dead connection(s) in client pool -- rebuilding client",
                dead_count,
            )
            replace_primary_openai_client(agent, reason="dead_connection_cleanup")
            return True
    except Exception as exc:
        logger.debug("Dead connection check error: %s", exc)
    return False


def create_request_openai_client(agent: Any, *, reason: str) -> Any:
    """Create a short-lived OpenAI client for a single request.

    These clients are not shared and are closed after the request
    completes (see ``close_request_openai_client``).
    """
    from unittest.mock import Mock

    primary_client = ensure_primary_openai_client(agent, reason=reason)
    if isinstance(primary_client, Mock):
        return primary_client
    with openai_client_lock(agent):
        request_kwargs = dict(getattr(agent, "_client_kwargs", {}))
    return create_openai_client(agent, request_kwargs, reason=reason, shared=False)


def close_request_openai_client(
    agent: Any,
    client: Any,
    *,
    reason: str,
) -> None:
    """Close a request-scoped OpenAI client and force-close its sockets."""
    close_openai_client(agent, client, reason=reason, shared=False)
