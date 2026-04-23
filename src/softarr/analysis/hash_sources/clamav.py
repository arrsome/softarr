"""ClamAV local daemon integration.

Connects to a running clamd instance via Unix socket or TCP to scan a file
path (or, if no path is available, checks a known-hash database via ZVER
protocol). The primary use case is scanning a downloaded file on disk before
it is moved to its final destination.

Requires:
  - clamav_enabled = true  in [hash_sources] of softarr.ini
  - clamav_socket set in [hash_sources]  (default: /var/run/clamav/clamd.ctl)
    Alternatively clamav_host + clamav_port for TCP mode.

The lookup is best-effort: if the daemon is unreachable the verdict is
"unknown" rather than blocking the release.
"""

import asyncio
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("softarr.hash_sources.clamav")

DEFAULT_SOCKET = "/var/run/clamav/clamd.ctl"
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 3310
REQUEST_TIMEOUT = 15.0


async def _clamd_command(
    command: bytes,
    socket_path: Optional[str] = None,
    host: Optional[str] = None,
    port: int = DEFAULT_TCP_PORT,
) -> Optional[str]:
    """Send a command to clamd and return the response string.

    Supports both Unix socket and TCP connections. Returns None on failure.
    """
    try:
        if socket_path:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path),
                timeout=REQUEST_TIMEOUT,
            )
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host or DEFAULT_TCP_HOST, port),
                timeout=REQUEST_TIMEOUT,
            )
        writer.write(command)
        await writer.drain()
        writer.write_eof()
        response = await asyncio.wait_for(reader.read(4096), timeout=REQUEST_TIMEOUT)
        writer.close()
        await writer.wait_closed()
        return response.decode("utf-8", errors="replace").strip()
    except (OSError, asyncio.TimeoutError, ConnectionRefusedError) as exc:
        logger.debug("ClamAV connection failed: %s", exc)
        return None


async def ping(
    socket_path: Optional[str] = None,
    host: Optional[str] = None,
    port: int = DEFAULT_TCP_PORT,
) -> bool:
    """Return True if the clamd daemon is reachable."""
    response = await _clamd_command(
        b"zPING\0", socket_path=socket_path, host=host, port=port
    )
    return response == "PONG"


async def scan_file(
    file_path: str,
    socket_path: Optional[str] = None,
    host: Optional[str] = None,
    port: int = DEFAULT_TCP_PORT,
) -> Optional[Dict[str, Any]]:
    """Request clamd to scan a file at the given path.

    Returns a dict with:
      - infected (bool): True if a signature was matched
      - signature (str): matched signature name, or "" if clean
      - raw_response (str): full clamd response

    Returns None if the daemon is unreachable or the file cannot be scanned.
    """
    command = f"zSCAN {file_path}\0".encode("utf-8")
    response = await _clamd_command(
        command, socket_path=socket_path, host=host, port=port
    )
    if response is None:
        return None

    # clamd SCAN response format: "<path>: <signature> FOUND" or "<path>: OK"
    infected = response.upper().endswith("FOUND")
    signature = ""
    if infected:
        # Extract signature name: everything between ": " and " FOUND"
        m = re.search(r":\s+(.+?)\s+FOUND", response, re.IGNORECASE)
        if m:
            signature = m.group(1)

    return {
        "infected": infected,
        "signature": signature,
        "raw_response": response,
    }


async def lookup(
    sha256: str,
    socket_path: Optional[str] = None,
    host: Optional[str] = None,
    port: int = DEFAULT_TCP_PORT,
) -> Optional[Dict[str, Any]]:
    """Check a SHA-256 hash using clamd's ZHASHLOOKUP command (ClamAV 1.x+).

    Falls back gracefully if the daemon does not support the command or is
    unreachable.

    Returns a dict with:
      - found (bool): whether the hash was known to ClamAV
      - infected (bool): whether ClamAV considers the hash malicious
      - signature (str): matched signature name, or ""

    Returns None on connection failure.
    """
    # ClamAV 1.x supports hash lookup via ZHASHLOOKUP
    command = f"zHASHLOOKUP sha256:{sha256}\0".encode("utf-8")
    response = await _clamd_command(
        command, socket_path=socket_path, host=host, port=port
    )

    if response is None:
        return None

    response_upper = response.upper()

    # "UNKNOWN HASH" -- not in database, treat as inconclusive
    if "UNKNOWN HASH" in response_upper or "UNKNOWN" in response_upper:
        return {"found": False, "infected": False, "signature": ""}

    # "OK" response -- hash is known clean
    if response_upper in ("OK", "") or response_upper.endswith(": OK"):
        return {"found": True, "infected": False, "signature": ""}

    # "FOUND" response -- hash matched a signature
    if "FOUND" in response_upper:
        signature = ""
        m = re.search(r":\s+(.+?)\s+FOUND", response, re.IGNORECASE)
        if m:
            signature = m.group(1)
        return {"found": True, "infected": True, "signature": signature}

    # Unexpected response -- treat as inconclusive
    logger.debug("ClamAV unexpected response for hash %s: %r", sha256[:8], response)
    return {"found": False, "infected": False, "signature": ""}
