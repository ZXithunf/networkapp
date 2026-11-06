"""Subnet calculator built on the standard library ipaddress module."""

import ipaddress


def calculate(cidr: str) -> dict:
    """Return details for a CIDR block such as "192.168.10.0/26".

    Host bits are allowed ("192.168.10.5/26" is treated as 192.168.10.0/26).
    Raises ValueError with a readable message for bad input.
    """
    text = (cidr or "").strip()
    if "/" not in text:
        raise ValueError("CIDR must look like 192.168.10.0/26 (address/prefix)")
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid CIDR '{text}': {exc}") from exc
    return describe(net)


def describe(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> dict:
    """Describe an already-parsed network (re-used by the VLSM planner)."""
    if net.version == 4:
        first, last, usable = _ipv4_host_range(net)
        # /31 and /32 have no broadcast address.
        broadcast = str(net.broadcast_address) if net.prefixlen <= 30 else None
    else:
        # IPv6 has no broadcast; every address in the prefix is assignable.
        first, last, usable = net.network_address, net.broadcast_address, net.num_addresses
        broadcast = None

    return {
        "version": net.version,
        "cidr": str(net),
        "network": str(net.network_address),
        "broadcast": broadcast,
        "netmask": str(net.netmask),
        "wildcard": str(net.hostmask),
        "prefix": net.prefixlen,
        "first_host": str(first),
        "last_host": str(last),
        "usable_hosts": usable,
        "total_addresses": net.num_addresses,
    }


def _ipv4_host_range(net: ipaddress.IPv4Network):
    """First host, last host and usable count, with the /31 and /32 special cases."""
    if net.prefixlen == 32:
        # A single host route: the address itself is the only host.
        return net.network_address, net.network_address, 1
    if net.prefixlen == 31:
        # RFC 3021 point-to-point link: both addresses are usable, no broadcast.
        return net.network_address, net.broadcast_address, 2
    # Normal case: exclude the network and broadcast addresses.
    return net.network_address + 1, net.broadcast_address - 1, net.num_addresses - 2
