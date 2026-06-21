"""Non-interactive SCP uploader for Cisco IOS images.

Wraps the logic of `Self Written Scripts/SCP Cisco IOS/scp_ios_upload.py`
so it can be driven from the Flask WebUI (no terminal prompts, no
progress printer).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Iterable

import paramiko
from scp import SCPClient, SCPException


logger = logging.getLogger(__name__)


DEFAULT_DESTINATIONS = [
    "flash:/",
    "bootflash:/",
    "disk0:/",
    "usbflash0:/",
    "harddisk:/",
]


def _build_ssh_client(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: float,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = dict(
        hostname=host,
        port=port,
        username=username,
        password=password,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )

    try:
        client.connect(**connect_kwargs)
    except paramiko.SSHException:
        # Retry with legacy KEX algorithms enabled (older IOS).
        try:
            current = paramiko.Transport._preferred_kex  # type: ignore[attr-defined]
            paramiko.Transport._preferred_kex = tuple(current) + (  # type: ignore[attr-defined]
                "diffie-hellman-group14-sha1",
                "diffie-hellman-group1-sha1",
            )
        except Exception:  # noqa: BLE001
            pass
        client.connect(**connect_kwargs)

    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(15)
    return client


def _normalize_destination(destination: str) -> str:
    """Treat the user-provided destination as a directory prefix."""
    base = destination.strip()
    if not base:
        return "flash:/"
    if "/" in base:
        base = base[: base.rfind("/") + 1]
    elif base.endswith(":"):
        base = base + "/"
    else:
        base = base + "/"
    return base


def upload_ios_image(
    host: str,
    port: int,
    username: str,
    password: str,
    files: Iterable[str | os.PathLike],
    destination: str,
    timeout: float = 30.0,
    socket_timeout: float = 1800.0,
    progress_callback=None,
) -> dict:
    """Upload one or more local files to a Cisco device via SCP.

    Returns a dict suitable for jsonify():
        {
            "status": "success" | "error",
            "message": "...",
            "results": [
                {
                    "filename": "c1100-...bin",
                    "remote_target": "bootflash:/c1100-...bin",
                    "size_bytes": 123456789,
                    "elapsed_seconds": 42.1,
                    "status": "success" | "error",
                    "error": "..." (only when status == "error")
                },
                ...
            ]
        }
    """
    file_paths = [Path(p) for p in files]
    if not file_paths:
        return {
            "status": "error",
            "message": "No files were provided for upload.",
            "results": [],
        }

    base = _normalize_destination(destination)

    results: list[dict] = []
    ssh: paramiko.SSHClient | None = None
    try:
        ssh = _build_ssh_client(host, int(port), username, password, timeout)
    except paramiko.AuthenticationException:
        return {
            "status": "error",
            "message": (
                "Authentication failed. Check the SSH username and password "
                "and that the user has privilege 15 with exec authorization."
            ),
            "results": [],
        }
    except paramiko.SSHException as exc:
        return {
            "status": "error",
            "message": (
                f"SSH error: {exc}. Verify 'ip scp server enable' and "
                "'ip ssh version 2' are configured on the device."
            ),
            "results": [],
        }
    except OSError as exc:
        return {
            "status": "error",
            "message": f"Network error connecting to {host}:{port} — {exc}",
            "results": [],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Unexpected error opening SSH session: {exc}",
            "results": [],
        }

    transport = ssh.get_transport()
    if transport is None:
        ssh.close()
        return {
            "status": "error",
            "message": "Could not obtain SSH transport for SCP.",
            "results": [],
        }

    # Compute total size up front so the progress callback can report a
    # combined percentage across all files.
    total_size = 0
    for p in file_paths:
        try:
            if p.is_file():
                total_size += p.stat().st_size
        except OSError:
            pass
    cumulative = {"sent": 0, "started": time.time()}

    def _scp_progress(filename, size, sent):
        if progress_callback is None:
            return
        try:
            name = filename.decode() if isinstance(filename, bytes) else filename
            overall_sent = cumulative["sent"] + int(sent)
            elapsed = max(time.time() - cumulative["started"], 0.001)
            rate = overall_sent / elapsed if elapsed > 0 else 0.0
            percent = (overall_sent / total_size * 100.0) if total_size else 0.0
            progress_callback({
                "current_file": os.path.basename(name),
                "current_size": int(size),
                "current_sent": int(sent),
                "total_size": int(total_size),
                "total_sent": int(overall_sent),
                "percent": round(percent, 2),
                "rate_bps": rate,
            })
        except Exception:  # noqa: BLE001
            # Never let a callback error break the upload.
            pass

    failures = 0
    try:
        with SCPClient(transport, progress=_scp_progress, socket_timeout=socket_timeout) as scp:
            for path in file_paths:
                entry = {
                    "filename": path.name,
                    "remote_target": "",
                    "size_bytes": 0,
                    "elapsed_seconds": 0.0,
                    "status": "error",
                }
                if not path.is_file():
                    entry["error"] = f"Not a file: {path}"
                    results.append(entry)
                    failures += 1
                    continue

                size = path.stat().st_size
                remote_target = f"{base}{path.name}"
                entry["remote_target"] = remote_target
                entry["size_bytes"] = size

                started = time.time()
                try:
                    scp.put(str(path), remote_path=remote_target)
                except SCPException as exc:
                    entry["elapsed_seconds"] = round(time.time() - started, 2)
                    entry["error"] = f"SCP error: {exc}"
                    failures += 1
                    results.append(entry)
                    continue
                except Exception as exc:  # noqa: BLE001
                    entry["elapsed_seconds"] = round(time.time() - started, 2)
                    entry["error"] = f"Transfer failed: {exc}"
                    failures += 1
                    results.append(entry)
                    continue

                entry["status"] = "success"
                entry["elapsed_seconds"] = round(time.time() - started, 2)
                cumulative["sent"] += size
                results.append(entry)
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001
            pass

    total = len(results)
    success_count = sum(1 for r in results if r["status"] == "success")

    if failures == 0:
        message = (
            f"Uploaded {success_count} file(s) to {host} successfully."
        )
        status = "success"
    elif success_count == 0:
        message = f"All {total} upload(s) failed. See details below."
        status = "error"
    else:
        message = (
            f"Completed with {failures} failure(s): "
            f"{success_count} of {total} file(s) uploaded successfully."
        )
        status = "error"

    return {
        "status": status,
        "message": message,
        "results": results,
    }
