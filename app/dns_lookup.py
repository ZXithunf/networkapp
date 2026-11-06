"""DNS lookups with dnspython, a strict domain check and a 3 second budget."""

import re

import dns.exception
import dns.resolver

ALLOWED_TYPES = ("A", "AAAA", "MX", "TXT", "NS", "CNAME")
TIMEOUT_SECONDS = 3.0

# One label: 1-63 chars, letters/digits/hyphen/underscore, no leading/trailing hyphen.
# Underscore is allowed so names like _dmarc.example.com work for TXT lookups.
_LABEL = r"(?!-)[A-Za-z0-9_-]{1,63}(?<!-)"
_DOMAIN_RE = re.compile(rf"^(?:{_LABEL}\.)+[A-Za-z]{{2,63}}$")


class DnsError(Exception):
    """A lookup problem with the HTTP status the API should return."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def validate_domain(domain: str) -> str:
    """Return the normalised domain or raise DnsError(422)."""
    name = (domain or "").strip().rstrip(".").lower()
    if len(name) > 253 or not _DOMAIN_RE.match(name):
        raise DnsError(422, f"'{domain}' is not a valid domain name (e.g. example.com)")
    return name


def validate_type(record_type: str) -> str:
    rtype = (record_type or "").strip().upper()
    if rtype not in ALLOWED_TYPES:
        raise DnsError(422, f"type must be one of {', '.join(ALLOWED_TYPES)}")
    return rtype


def _resolver() -> dns.resolver.Resolver:
    """Build a resolver with the timeout applied. Tests replace this function."""
    resolver = dns.resolver.Resolver()
    resolver.timeout = TIMEOUT_SECONDS  # per nameserver attempt
    resolver.lifetime = TIMEOUT_SECONDS  # total time for the whole query
    return resolver


def _format(rtype: str, rdata) -> str:
    if rtype in ("A", "AAAA"):
        return rdata.address
    if rtype == "MX":
        return f"{rdata.preference} {rdata.exchange.to_text(omit_final_dot=True)}"
    if rtype == "TXT":
        return b"".join(rdata.strings).decode("utf-8", errors="replace")
    # NS and CNAME both carry a target name
    return rdata.target.to_text(omit_final_dot=True)


def lookup(domain: str, record_type: str = "A") -> dict:
    name = validate_domain(domain)
    rtype = validate_type(record_type)
    try:
        answer = _resolver().resolve(name, rtype)
    except dns.resolver.NXDOMAIN as exc:
        raise DnsError(404, f"{name} does not exist (NXDOMAIN)") from exc
    except dns.resolver.NoAnswer:
        return {"domain": name, "type": rtype, "ttl": None, "records": []}
    except dns.exception.Timeout as exc:
        raise DnsError(504, f"DNS lookup for {name} timed out after {TIMEOUT_SECONDS:g}s") from exc
    except dns.exception.DNSException as exc:
        raise DnsError(502, f"DNS lookup failed: {exc}") from exc

    return {
        "domain": name,
        "type": rtype,
        "ttl": answer.rrset.ttl if answer.rrset is not None else None,
        "records": sorted(_format(rtype, r) for r in answer),
    }
