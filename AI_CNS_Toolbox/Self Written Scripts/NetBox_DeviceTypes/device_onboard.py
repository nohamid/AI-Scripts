"""
Connect to a Cisco device over SSH, read the details required to register it,
and create the device in NetBox.

Only the device itself is created. Rack and position assignment is left to be
done in NetBox.

Collected from the device:
    hostname, device type (PID), management interface and its IP address

Provided by the user:
    device role (Wireless Lan Controller / Router / Switch) and the site
"""

from __future__ import annotations

import ipaddress
import re

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

import devicetype_sync
from devicetype_sync import MANUFACTURER_SLUG, NetBox, SyncError

# Management addressing in the CNS lab is always inside this range.
MANAGEMENT_PREFIX = "10.49."

DEVICE_ROLES = {
    "wlc": ("Wireless Lan Controller", "wireless-lan-controller"),
    "router": ("Router", "router"),
    "switch": ("Switch", "switch"),
}

# Evaluated in order; the first matching pattern wins.
ROLE_HINTS = [
    (r"^(C9800|AIR-CT|AIR-WLC|WLC)", "wlc"),
    (r"^(ISR|ASR|CSR|C8[0-9]{3}|C11[0-9]{2}|19[0-9]{2}|29[0-9]{2}|39[0-9]{2}|4[0-9]{3})", "router"),
    (r"^(C9[2-6][0-9]{2}|C10[0-9]{2}|WS-C|CBS|N[0-9]K|ME-)", "switch"),
]


def suggest_role(model: str) -> str:
    """Best guess of the device role from the product ID, or '' if unclear."""
    candidate = (model or "").strip().upper()
    for pattern, role_key in ROLE_HINTS:
        if re.match(pattern, candidate):
            return role_key
    return ""


class OnboardError(RuntimeError):
    pass


def validate_management_ip(ip: str) -> str:
    ip = (ip or "").strip()
    try:
        ipaddress.IPv4Address(ip)
    except ValueError as exc:
        raise OnboardError(f"{ip!r} is not a valid IPv4 address.") from exc
    if not ip.startswith(MANAGEMENT_PREFIX):
        raise OnboardError(
            f"Management IP addresses must start with {MANAGEMENT_PREFIX}"
        )
    return ip


# --------------------------------------------------------------------------
# Device side
# --------------------------------------------------------------------------
def _parse_model(inventory: str, version: str) -> str:
    if match := re.search(r"PID:\s*(\S+)", inventory):
        return match.group(1).strip(",")
    for pattern in (
        r"Model [Nn]umber\s*:\s*(\S+)",
        r"^[Cc]isco\s+(\S+)\s+\(",
        r"Model\s*:\s*(\S+)",
    ):
        if match := re.search(pattern, version, re.MULTILINE):
            return match.group(1).strip(",")
    raise OnboardError("Could not determine the device model from the CLI output.")


def _parse_management_interface(brief_output: str, ip: str) -> str | None:
    for line in brief_output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == ip:
            return parts[0]
    return None


def _parse_prefix_length(interface_output: str) -> int:
    if match := re.search(r"Internet address is \S+/(\d+)", interface_output):
        return int(match.group(1))
    return 32


def discover_device(ip: str, username: str, password: str, secret: str = "") -> dict:
    """SSH to the device and return hostname, model, interface and IP details."""
    ip = validate_management_ip(ip)
    connection = None
    try:
        connection = ConnectHandler(
            device_type="cisco_ios",
            host=ip,
            username=username,
            password=password,
            secret=secret or password,
            fast_cli=False,
            conn_timeout=20,
        )
        hostname = connection.find_prompt().strip().rstrip("#>")
        version = connection.send_command("show version", read_timeout=60)
        inventory = connection.send_command("show inventory", read_timeout=60)
        brief = connection.send_command("show ip interface brief", read_timeout=60)

        interface = _parse_management_interface(brief, ip)
        prefix_length = 32
        if interface:
            detail = connection.send_command(
                f"show ip interface {interface}", read_timeout=60
            )
            prefix_length = _parse_prefix_length(detail)
    except (NetmikoAuthenticationException, NetmikoTimeoutException) as exc:
        raise OnboardError(f"Could not connect to {ip}: {exc}") from exc
    except OSError as exc:
        raise OnboardError(f"Could not connect to {ip}: {exc}") from exc
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass

    serial = ""
    if match := re.search(r"SN:\s*(\S+)", inventory):
        serial = match.group(1)

    model = _parse_model(inventory, version)
    return {
        "hostname": hostname,
        "model": model,
        "suggested_role": suggest_role(model),
        "serial": serial,
        "management_ip": ip,
        "management_interface": interface or "",
        "prefix_length": prefix_length,
    }


# --------------------------------------------------------------------------
# NetBox side
# --------------------------------------------------------------------------
def list_sites(netbox: NetBox) -> list[dict]:
    return [
        {"id": site["id"], "name": site["name"]}
        for site in netbox.get_all("/dcim/sites/")
    ]


def resolve_device_type(netbox: NetBox, model: str) -> dict:
    """Find the device type in NetBox, importing it from the library if needed."""
    for params in ({"model": model}, {"part_number": model}, {"q": model}):
        matches = netbox.get_all("/dcim/device-types/", **params)
        for candidate in matches:
            if devicetype_sync.normalise(candidate["model"]) == devicetype_sync.normalise(model):
                return candidate
            if candidate.get("part_number") and devicetype_sync.normalise(
                candidate["part_number"]
            ) == devicetype_sync.normalise(model):
                return candidate

    library = devicetype_sync.load_library()
    for definition in library.values():
        names = {devicetype_sync.normalise(definition["model"])}
        if definition.get("part_number"):
            names.add(devicetype_sync.normalise(str(definition["part_number"])))
        if devicetype_sync.normalise(model) in names:
            manufacturer_id = netbox.manufacturer_id()
            return netbox.create_device_type(definition, manufacturer_id)

    raise OnboardError(
        f"Device type {model!r} does not exist in NetBox and is not in the "
        "devicetype-library. Create it in NetBox first."
    )


def resolve_role(netbox: NetBox, role_key: str) -> dict:
    """Reuse an existing device role, matching on slug or name, else create it."""
    try:
        name, slug = DEVICE_ROLES[role_key]
    except KeyError as exc:
        raise OnboardError(f"Unknown device role: {role_key!r}") from exc

    wanted = {devicetype_sync.normalise(name), devicetype_sync.normalise(slug)}
    for role in netbox.get_all("/dcim/device-roles/"):
        if devicetype_sync.normalise(role["name"]) in wanted:
            return role
        if devicetype_sync.normalise(role["slug"]) in wanted:
            return role

    return netbox.post("/dcim/device-roles/", {"name": name, "slug": slug})


def onboard_device(
    netbox: NetBox,
    hostname: str,
    role_key: str,
    model: str,
    site_id: int,
    management_ip: str,
    management_interface: str,
    prefix_length: int = 32,
    serial: str = "",
) -> dict:
    """Create the device, its management IP and the primary IP assignment."""
    hostname = (hostname or "").strip()
    if not hostname:
        raise OnboardError("Hostname is required.")
    management_ip = validate_management_ip(management_ip)

    if netbox.get_all("/dcim/devices/", name=hostname):
        raise OnboardError(f"A device named {hostname!r} already exists in NetBox.")

    device_type = resolve_device_type(netbox, model)
    role = resolve_role(netbox, role_key)

    device = netbox.post(
        "/dcim/devices/",
        {
            "name": hostname,
            "device_type": device_type["id"],
            "role": role["id"],
            "site": site_id,
            "serial": serial or "",
            "status": "active",
        },
    )

    warnings: list[str] = []
    interface_id = None
    if management_interface:
        interfaces = netbox.get_all(
            "/dcim/interfaces/", device_id=device["id"], name=management_interface
        )
        if interfaces:
            interface_id = interfaces[0]["id"]
        else:
            created = netbox.post(
                "/dcim/interfaces/",
                {
                    "device": device["id"],
                    "name": management_interface,
                    "type": "virtual",
                },
            )
            interface_id = created["id"]
    else:
        warnings.append(
            "The management interface could not be determined; the IP address was "
            "created without an interface assignment."
        )

    ip_payload = {
        "address": f"{management_ip}/{prefix_length}",
        "status": "active",
    }
    if interface_id:
        ip_payload["assigned_object_type"] = "dcim.interface"
        ip_payload["assigned_object_id"] = interface_id

    ip_address = netbox.post("/ipam/ip-addresses/", ip_payload)

    if interface_id:
        netbox.patch(f"/dcim/devices/{device['id']}/", {"primary_ip4": ip_address["id"]})

    return {
        "device_id": device["id"],
        "device_name": device["name"],
        "device_url": f"{netbox.url}/dcim/devices/{device['id']}/",
        "device_type": device_type["model"],
        "role": role["name"],
        "management_ip": ip_payload["address"],
        "management_interface": management_interface,
        "warnings": warnings,
    }


__all__ = [
    "DEVICE_ROLES",
    "MANAGEMENT_PREFIX",
    "OnboardError",
    "SyncError",
    "discover_device",
    "list_sites",
    "onboard_device",
    "validate_management_ip",
]
