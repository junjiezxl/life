#!/usr/bin/env python3
"""Cloudflare DDNS updater for LifeHub (supports IPv4 A and IPv6 AAAA records)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


def load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def fetch_public_ip(urls: List[str]) -> Optional[str]:
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LifeHub-DDNS/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                ip = resp.read().decode("utf-8", errors="ignore").strip()
            if ip and len(ip) <= 64 and "<" not in ip:
                return ip
        except Exception:
            continue
    return None


def cf_request(method: str, path: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"https://api.cloudflare.com/client/v4{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "LifeHub-DDNS/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("success"):
        raise RuntimeError(f"Cloudflare API error on {path}: {body.get('errors')}")
    return body


def resolve_zone_id(domain: str, token: str) -> str:
    parts = domain.strip(".").split(".")
    for i in range(max(1, len(parts) - 1)):
        candidate = ".".join(parts[i:])
        q = urllib.parse.urlencode({"name": candidate})
        res = cf_request("GET", f"/zones?{q}", token)
        zones = res.get("result") or []
        if zones:
            return str(zones[0]["id"])
    raise RuntimeError(f"Could not find Cloudflare zone for domain: {domain}")


def sync_record(
    zone_id: str,
    domain: str,
    rtype: str,
    ip: str,
    token: str,
    proxied: bool = False,
) -> str:
    q = urllib.parse.urlencode({"type": rtype, "name": domain})
    existing = (cf_request("GET", f"/zones/{zone_id}/dns_records?{q}", token).get("result") or [])
    payload = {
        "type": rtype,
        "name": domain,
        "content": ip,
        "ttl": 1,  # Auto
        "proxied": proxied,
        "comment": "LifeHub-DDNS",
    }
    if existing:
        rec = existing[0]
        if str(rec.get("content") or "").strip() == ip and bool(rec.get("proxied", False)) == proxied:
            return f"unchanged ({ip})"
        cf_request("PATCH", f"/zones/{zone_id}/dns_records/{rec['id']}", token, payload)
        return f"updated {rec.get('content')} -> {ip}"
    cf_request("POST", f"/zones/{zone_id}/dns_records", token, payload)
    return f"created ({ip})"


def main() -> int:
    env_file = sys.argv[1] if len(sys.argv) > 1 else "/etc/lifehub/cloudflare.env"
    load_env_file(env_file)

    token = (os.environ.get("CF_API_TOKEN") or "").strip()
    domain = (os.environ.get("CF_DOMAIN") or "").strip()
    if not token or not domain:
        print("CF_API_TOKEN and CF_DOMAIN must be set", file=sys.stderr)
        return 1

    zone_id = (os.environ.get("CF_ZONE_ID") or "").strip() or resolve_zone_id(domain, token)
    proxied = (os.environ.get("CF_PROXIED") or "false").strip().lower() in ("1", "true", "yes")
    update_aaaa = (os.environ.get("CF_UPDATE_AAAA") or "true").strip().lower() in ("1", "true", "yes")

    ipv4 = fetch_public_ip(
        [
            "https://4.ident.me",
            "https://api.ipify.org",
            "https://ipv4.icanhazip.com",
        ]
    )
    if ipv4:
        print(f"[DDNS] {domain} A: {sync_record(zone_id, domain, 'A', ipv4, token, proxied=proxied)}")
    else:
        print(f"[DDNS] {domain} A: failed to detect public IPv4", file=sys.stderr)

    if update_aaaa:
        ipv6 = fetch_public_ip(
            [
                "https://6.ident.me",
                "https://ipv6.icanhazip.com",
                "https://api6.ipify.org",
            ]
        )
        if ipv6 and ":" in ipv6:
            print(f"[DDNS] {domain} AAAA: {sync_record(zone_id, domain, 'AAAA', ipv6, token, proxied=proxied)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
