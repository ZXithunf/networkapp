"""VLSM planner: carve a base IPv4 network into right-sized subnets."""

import ipaddress

from app.subnet import describe

MAX_SUBNETS = 64


class VlsmError(ValueError):
    """Raised when the request is invalid or the hosts do not fit."""


def prefix_for_hosts(hosts: int) -> int:
    """Smallest IPv4 prefix whose usable host count (size - 2) is >= hosts."""
    needed = hosts + 2  # network + broadcast addresses
    size = 1 << (needed - 1).bit_length()  # round up to a power of two
    return 32 - size.bit_length() + 1


def plan(base: str, hosts: list[int]) -> dict:
    """Allocate subnets largest-first and report the leftover space."""
    try:
        network = ipaddress.ip_network((base or "").strip(), strict=False)
    except ValueError as exc:
        raise VlsmError(f"Invalid base network '{base}': {exc}") from exc
    if network.version != 4:
        raise VlsmError("VLSM planning supports IPv4 only")
    if not hosts:
        raise VlsmError("hosts must contain at least one value")
    if len(hosts) > MAX_SUBNETS:
        raise VlsmError(f"At most {MAX_SUBNETS} subnets per plan")
    if any(h < 1 for h in hosts):
        raise VlsmError("Each host count must be at least 1")

    # Largest first keeps every block naturally aligned, so a simple
    # moving pointer is enough - no fragmentation, no searching.
    order = sorted(range(len(hosts)), key=lambda i: hosts[i], reverse=True)
    pointer = int(network.network_address)
    end = int(network.broadcast_address)
    allocations = []

    for index in order:
        requested = hosts[index]
        prefix = prefix_for_hosts(requested)
        if prefix < network.prefixlen:
            raise VlsmError(f"{requested} hosts needs a /{prefix}, larger than {network}")
        size = 1 << (32 - prefix)
        if pointer + size - 1 > end:
            raise VlsmError(f"Not enough space in {network} for {requested} hosts")
        subnet = ipaddress.IPv4Network((pointer, prefix))
        details = describe(subnet)
        details.update(
            {
                "name": f"Subnet {index + 1}",
                "hosts_requested": requested,
                "wasted": details["usable_hosts"] - requested,
            }
        )
        allocations.append(details)
        pointer += size

    leftover = []
    if pointer <= end:
        leftover = [
            str(n)
            for n in ipaddress.summarize_address_range(
                ipaddress.IPv4Address(pointer), ipaddress.IPv4Address(end)
            )
        ]

    return {
        "base": str(network),
        "allocations": allocations,
        "leftover": leftover,
        "leftover_addresses": end - pointer + 1 if pointer <= end else 0,
    }
