"""Is a VPN connected? Best effort, by the names of the network interfaces (no extra packages)."""

from __future__ import annotations

import re
import socket
import sys
from pathlib import Path

# WireGuard/OpenVPN/IPsec/PPP/tailscale-style names on Linux, adapter names of common VPN clients on Windows
_NAME = re.compile(r"^(tun\d*|tap\d*|wg\d*|ppp\d*|ipsec\d*|nordlynx|proton\d*|pvpn.*|mullvad.*|tailscale\d*|"
                   r"zt.*|vpn.*|.*wireguard.*|.*openvpn.*|.*nordlynx.*|.*protonvpn.*|.*mullvad.*|.*tap-windows.*|.*vpn.*)$", re.I)


def is_vpn_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def _is_up(name: str) -> bool:
    if sys.platform.startswith("linux"):
        try:
            state = Path(f"/sys/class/net/{name}/operstate").read_text().strip()
        except OSError:
            return False
        return state in ("up", "unknown")        # tun/wg devices usually report "unknown" while working
    return True                                  # elsewhere only connected adapters are listed with an interface index


def vpn_connected() -> bool:
    try:
        return any(is_vpn_name(n) and _is_up(n) for _i, n in socket.if_nameindex())
    except OSError:
        return False
