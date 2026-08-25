"""
Compare the Cisco device types available in the netbox-community
devicetype-library with the Cisco device types that already exist in the
Skynet NetBox instance, and offer to import the missing ones.

Library source:
    https://github.com/netbox-community/devicetype-library/tree/master/device-types/Cisco

Configuration is read from environment variables first, then from a local
config file (netbox_config.json next to this script, or ~/.skynet/netbox.json).
The config file is git-ignored - never commit a token.

    NETBOX_URL         Base URL of NetBox, e.g. http://fkf-cns-spectre.cisco.com
    NETBOX_TOKEN       NetBox API token with write access to dcim.device-type
    NETBOX_VERIFY_SSL  "false" to disable TLS verification (default: enabled)
    DEVICETYPE_CACHE   Optional path for the local device type inventory JSON

Create the config file with:
    python devicetype_sync.py --init-config

Usage:
    python devicetype_sync.py                 # interactive, asks before import
    python devicetype_sync.py --check-only    # only report, never import
    python devicetype_sync.py --yes           # import without asking
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

import requests
import yaml

LIBRARY_REPO = "netbox-community/devicetype-library"
LIBRARY_REF = "master"
LIBRARY_PATH = "device-types/Cisco"
LIBRARY_TARBALL = (
    f"https://codeload.github.com/{LIBRARY_REPO}/tar.gz/refs/heads/{LIBRARY_REF}"
)
LIBRARY_CACHE_TTL = 900  # seconds
MAX_DEFINITION_BYTES = 1024 * 1024

DEFAULT_NETBOX_URL = "http://fkf-cns-spectre.cisco.com"
MANUFACTURER_NAME = "Cisco"
MANUFACTURER_SLUG = "cisco"

CONFIG_PATHS = [
    Path(__file__).resolve().parent / "netbox_config.json",
    Path(__file__).resolve().parents[2] / "netbox.env",
    Path.home() / ".skynet" / "netbox.json",
    Path("/etc/skynet/netbox.env"),
]

CONFIG_KEYS = ("NETBOX_URL", "NETBOX_TOKEN", "NETBOX_VERIFY_SSL", "DEVICETYPE_CACHE")
PLACEHOLDER_PREFIX = "paste-your"

# Guards against path traversal when a file name comes from a web request.
LIBRARY_FILE_RE = re.compile(r"^[A-Za-z0-9._+()\- ]+\.(?:yaml|yml)$")

# NetBox 4.5+ v2 tokens carry this prefix and use the "Bearer" scheme.
TOKEN_PREFIX = "nbt_"


def auth_header_value(token: str) -> str:
    """Build the Authorization header for a v1 or v2 NetBox token."""
    token = token.strip()
    if token.startswith(("Token ", "Bearer ")):
        return token
    if token.startswith(TOKEN_PREFIX):
        return f"Bearer {token}"
    return f"Token {token}"

# Order matters: rear ports must exist before front ports can reference them.
COMPONENT_ENDPOINTS = [
    ("console-ports", "console-port-templates"),
    ("console-server-ports", "console-server-port-templates"),
    ("power-ports", "power-port-templates"),
    ("power-outlets", "power-outlet-templates"),
    ("rear-ports", "rear-port-templates"),
    ("front-ports", "front-port-templates"),
    ("interfaces", "interface-templates"),
    ("module-bays", "module-bay-templates"),
    ("device-bays", "device-bay-templates"),
    ("inventory-items", "inventory-item-templates"),
]

DEVICE_TYPE_FIELDS = [
    "model",
    "slug",
    "part_number",
    "u_height",
    "is_full_depth",
    "airflow",
    "subdevice_role",
    "weight",
    "weight_unit",
    "description",
    "comments",
]


class SyncError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def _read_config_file(path: Path) -> dict:
    """Read a JSON config file or a KEY=VALUE env file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)

    values: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_config() -> dict:
    """Merge all config sources; earlier paths win, environment variables win overall."""
    config: dict = {}
    for path in CONFIG_PATHS:
        if not path.is_file():
            continue
        if os.name == "posix" and path.stat().st_mode & 0o077:
            logging.warning(
                "%s is readable by other users. Run: chmod 600 %s", path, path
            )
        try:
            values = _read_config_file(path)
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncError(f"Cannot read {path}: {exc}") from exc
        for key in CONFIG_KEYS:
            value = str(values.get(key) or "").strip()
            if value and not value.startswith(PLACEHOLDER_PREFIX):
                config.setdefault(key, value)

    for key in CONFIG_KEYS:
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def init_config() -> int:
    path = CONFIG_PATHS[0]
    if path.exists():
        print(f"{path} already exists - edit it directly.")
        return 0
    template = {
        "NETBOX_URL": DEFAULT_NETBOX_URL,
        "NETBOX_TOKEN": "paste-your-netbox-api-token-here",
        "NETBOX_VERIFY_SSL": "true",
    }
    path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print(f"Created {path}\nPaste your NetBox token into NETBOX_TOKEN. Do not commit this file.")
    return 0


# --------------------------------------------------------------------------
# Device type library
# --------------------------------------------------------------------------
# The whole repository is downloaded as a single tarball. The GitHub REST API
# is deliberately avoided: it is rate limited to 60 requests per hour per IP
# for unauthenticated clients.
_library_cache: dict = {"loaded_at": 0.0, "definitions": {}}


def load_library(force: bool = False) -> dict[str, dict]:
    """Return {file_name: definition} for the Cisco folder of the library."""
    if not force and _library_cache["definitions"]:
        if time.time() - _library_cache["loaded_at"] < LIBRARY_CACHE_TTL:
            return _library_cache["definitions"]

    try:
        response = requests.get(LIBRARY_TARBALL, timeout=120, stream=True)
        response.raise_for_status()
        response.raw.decode_content = True
    except requests.RequestException as exc:
        raise SyncError(f"Could not download the device type library: {exc}") from exc

    definitions: dict[str, dict] = {}
    wanted = f"/{LIBRARY_PATH}/"
    try:
        # Streamed so the download can stop once the Cisco folder has been
        # passed; the full archive is several hundred megabytes.
        with response, tarfile.open(fileobj=response.raw, mode="r|gz") as archive:
            for member in archive:
                if wanted not in member.name:
                    if definitions:
                        break
                    continue
                if not member.isfile() or member.size > MAX_DEFINITION_BYTES:
                    continue
                file_name = PurePosixPath(member.name).name
                if not LIBRARY_FILE_RE.match(file_name):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                try:
                    definition = yaml.safe_load(handle.read(MAX_DEFINITION_BYTES))
                except yaml.YAMLError:
                    continue
                if isinstance(definition, dict) and definition.get("model"):
                    definitions[file_name] = definition
    except (tarfile.TarError, OSError) as exc:
        raise SyncError(f"Could not read the device type library archive: {exc}") from exc

    if not definitions:
        raise SyncError("No Cisco device types found in the library archive.")

    _library_cache["definitions"] = definitions
    _library_cache["loaded_at"] = time.time()
    return definitions


# --------------------------------------------------------------------------
# NetBox side
# --------------------------------------------------------------------------
class NetBox:
    def __init__(self, url: str, token: str, verify: bool = True):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": auth_header_value(token),
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self.session.verify = verify

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self.session.request(
            method, f"{self.url}/api{path}", timeout=60, **kwargs
        )
        if response.status_code in (401, 403):
            raise SyncError(
                f"NetBox rejected the API token ({response.status_code}). "
                "Check that the token is valid and has the required permissions."
            )
        if response.status_code >= 400:
            raise SyncError(
                f"NetBox {method} {path} failed ({response.status_code}): {response.text[:500]}"
            )
        return response

    def get_all(self, path: str, **params) -> list[dict]:
        params = {**params, "limit": 200}
        results: list[dict] = []
        url_path: str | None = path
        while url_path:
            response = self._request("GET", url_path, params=params)
            payload = response.json()
            results.extend(payload.get("results", []))
            next_url = payload.get("next")
            if not next_url:
                break
            url_path = next_url.split("/api", 1)[1]
            params = {}
        return results

    def post(self, path: str, data) -> dict | list:
        return self._request("POST", path, data=json.dumps(data)).json()

    def patch(self, path: str, data: dict) -> dict:
        return self._request("PATCH", path, data=json.dumps(data)).json()

    def status(self) -> dict:
        return self._request("GET", "/status/").json()

    @staticmethod
    def provision_token(
        url: str, username: str, password: str, verify: bool = True
    ) -> str:
        """Exchange NetBox credentials for a short lived API token.

        NetBox <4.5 returns the plaintext in ``key``. NetBox 4.5+ returns it in
        ``token`` and, for v2 tokens, a separate ``key`` that has to be combined
        into ``nbt_<key>.<token>``.
        """
        response = requests.post(
            f"{url.rstrip('/')}/api/users/tokens/provision/",
            json={"username": username, "password": password},
            headers={"Accept": "application/json"},
            timeout=30,
            verify=verify,
        )
        if response.status_code in (400, 401, 403):
            raise SyncError("NetBox rejected these credentials.")
        if response.status_code >= 400:
            raise SyncError(
                f"NetBox token provisioning failed ({response.status_code})."
            )

        payload = response.json()
        plaintext = payload.get("token") or payload.get("key")
        if not plaintext:
            raise SyncError("NetBox did not return an API token.")

        key = payload.get("key")
        if key and key != plaintext:
            return f"{TOKEN_PREFIX}{key}.{plaintext}"
        return plaintext

    def cisco_device_types(self) -> list[dict]:
        return self.get_all("/dcim/device-types/", manufacturer=MANUFACTURER_SLUG)

    def manufacturer_id(self) -> int:
        wanted = {normalise(MANUFACTURER_NAME), normalise(MANUFACTURER_SLUG)}
        for manufacturer in self.get_all("/dcim/manufacturers/"):
            if normalise(manufacturer["name"]) in wanted or normalise(manufacturer["slug"]) in wanted:
                return manufacturer["id"]
        created = self.post(
            "/dcim/manufacturers/",
            {"name": MANUFACTURER_NAME, "slug": MANUFACTURER_SLUG},
        )
        return created["id"]  # type: ignore[index]

    def create_device_type(self, definition: dict, manufacturer_id: int) -> dict:
        payload = {
            key: definition[key] for key in DEVICE_TYPE_FIELDS if key in definition
        }
        payload["manufacturer"] = manufacturer_id
        device_type = self.post("/dcim/device-types/", payload)
        self._create_components(definition, device_type["id"])  # type: ignore[index]
        return device_type  # type: ignore[return-value]

    def _create_components(self, definition: dict, device_type_id: int) -> None:
        rear_port_ids: dict[str, int] = {}
        for yaml_key, endpoint in COMPONENT_ENDPOINTS:
            items = definition.get(yaml_key)
            if not items:
                continue
            payload = []
            for item in items:
                entry = {**item, "device_type": device_type_id}
                if yaml_key == "front-ports":
                    rear_name = entry.get("rear_port")
                    if rear_name in rear_port_ids:
                        entry["rear_port"] = rear_port_ids[rear_name]
                payload.append(entry)
            created = self.post(f"/dcim/{endpoint}/", payload)
            if yaml_key == "rear-ports" and isinstance(created, list):
                rear_port_ids = {item["name"]: item["id"] for item in created}


# --------------------------------------------------------------------------
# Comparison / reporting
# --------------------------------------------------------------------------
def normalise(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def find_missing(
    library: dict[str, dict], existing: list[dict]
) -> list[tuple[str, dict]]:
    """Return the library definitions that NetBox does not know yet."""
    known = {normalise(dt["model"]) for dt in existing}
    known |= {normalise(dt["slug"]) for dt in existing}
    known |= {normalise(dt["part_number"]) for dt in existing if dt.get("part_number")}

    missing: list[tuple[str, dict]] = []
    for file_name, definition in library.items():
        names = {normalise(definition["model"])}
        if definition.get("slug"):
            names.add(normalise(definition["slug"]))
        if definition.get("part_number"):
            names.add(normalise(str(definition["part_number"])))
        if names & known:
            continue
        missing.append((file_name, definition))

    missing.sort(key=lambda item: item[1]["model"].lower())
    return missing


def write_cache(cache_path: Path, device_types: list[dict]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    inventory = sorted(
        (
            {
                "model": dt["model"],
                "slug": dt["slug"],
                "part_number": dt.get("part_number") or "",
                "u_height": dt.get("u_height"),
            }
            for dt in device_types
        ),
        key=lambda dt: dt["model"].lower(),
    )
    cache_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N]: ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only", action="store_true", help="only report, never import"
    )
    parser.add_argument(
        "--yes", action="store_true", help="import missing device types without asking"
    )
    parser.add_argument(
        "--init-config", action="store_true", help="create a local config file template"
    )
    args = parser.parse_args()

    if args.init_config:
        return init_config()

    try:
        config = load_config()
    except SyncError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    netbox_url = config.get("NETBOX_URL") or DEFAULT_NETBOX_URL
    token = config.get("NETBOX_TOKEN")
    if not token or token.startswith("paste-your"):
        print(
            "No NetBox token found. Run 'python devicetype_sync.py --init-config' and "
            "paste your token into netbox_config.json, or export NETBOX_TOKEN.",
            file=sys.stderr,
        )
        return 2
    verify = str(config.get("NETBOX_VERIFY_SSL", "true")).strip().lower() not in {
        "false",
        "0",
        "no",
    }

    cache_path = Path(
        config.get("DEVICETYPE_CACHE")
        or Path(__file__).resolve().parents[2] / "data" / "device_types_cisco.json"
    )

    netbox = NetBox(netbox_url, token, verify=verify)

    try:
        print(f"Reading Cisco device types from {netbox_url} ...")
        existing = netbox.cisco_device_types()
        print(f"  {len(existing)} Cisco device types currently in Skynet NetBox")

        print("Downloading the devicetype-library ...")
        library = load_library()
        print(f"  {len(library)} Cisco device type definitions in the library")

        missing = find_missing(library, existing)
    except (requests.RequestException, SyncError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not missing:
        print("\nSkynet is up to date - no new Cisco device types in the library.")
        write_cache(cache_path, existing)
        return 0

    print(f"\n{len(missing)} new Cisco device type(s) found in the library:")
    for file_name, definition in missing:
        part = definition.get("part_number") or "-"
        print(f"  - {definition['model']:<40} part: {part:<20} ({file_name})")

    if args.check_only:
        return 0
    if not args.yes and not confirm("\nImport these device types into Skynet NetBox?"):
        print("Aborted - nothing was imported.")
        return 0

    try:
        manufacturer_id = netbox.manufacturer_id()
    except (requests.RequestException, SyncError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    imported = 0
    for file_name, definition in missing:
        try:
            netbox.create_device_type(definition, manufacturer_id)
        except (requests.RequestException, SyncError) as exc:
            print(f"  x {definition['model']}: {exc}", file=sys.stderr)
            continue
        imported += 1
        print(f"  + imported {definition['model']}")

    print(f"\nImported {imported} of {len(missing)} device type(s).")

    try:
        write_cache(cache_path, netbox.cisco_device_types())
        print(f"Local inventory updated: {cache_path}")
    except (requests.RequestException, SyncError, OSError) as exc:
        print(f"Could not update local inventory: {exc}", file=sys.stderr)

    return 0 if imported == len(missing) else 1


if __name__ == "__main__":
    sys.exit(main())
