"""People by name: screens show "Anthony Ruiz", never the user id U-US-00007."""
from __future__ import annotations

from typing import Any

from . import ddb


def directory(env_prefix: str | None = None) -> dict[str, str]:
    """user_id → display name for every PLAT_Users row (one scan per read model)."""
    out: dict[str, str] = {}
    for u in ddb.scan_all("PLAT_Users", env_prefix):
        uid = u.get("user_id")
        if uid:
            out[uid] = " ".join(filter(None, [u.get("given_name"), u.get("family_name")])) or uid
    return out


def name(uid: Any, names: dict[str, str]) -> str | None:
    """A user's name; an agent id or unknown id comes back unchanged (the screen maps agents)."""
    if not uid:
        return None
    return names.get(str(uid), str(uid))
