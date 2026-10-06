"""Platform adapters, and the registry that picks one for an instance.

Only classic CONTENTdm has an adapter. An instance whose ``platform`` names
anything else, or whose ``status`` is not ``supported``, gets an
:class:`Unsupported` refusal that says where the material went.
"""

from __future__ import annotations

from ..client import CdmHttp
from ..instances import Instance
from .base import Adapter
from .classic import ClassicAdapter

#: Adapters by the ``platform`` value they serve.
ADAPTERS: dict[str, type[Adapter]] = {ClassicAdapter.platform: ClassicAdapter}


class Unsupported(RuntimeError):
    """Raised for an instance no adapter can read."""

    def __init__(self, instance: Instance):
        """Explain why, and where the material is now."""
        self.instance = instance
        if instance.status == "blocked":
            reason = f"{instance.institution} cannot be reached by a script."
        elif instance.status == "moved":
            reason = f"{instance.institution} has left CONTENTdm."
        else:
            reason = (
                f"{instance.institution} runs on {instance.platform}, which this server "
                "cannot read yet."
            )
        where = f" Its material is now at {instance.moved_to}." if instance.moved_to else ""
        note = f" {instance.note}" if instance.note else ""
        super().__init__(reason + where + note)


def adapter_for(instance: Instance, http: CdmHttp) -> Adapter:
    """The adapter that reads this instance.

    Raises
    ------
    Unsupported
        If the instance has moved, is blocked, or runs on a platform with no
        adapter.
    """
    cls = ADAPTERS.get(instance.platform)
    if cls is None or instance.status != "supported":
        raise Unsupported(instance)
    return cls(instance, http)


__all__ = ["ADAPTERS", "Adapter", "ClassicAdapter", "Unsupported", "adapter_for"]
