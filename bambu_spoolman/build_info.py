import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BuildInfo:
    version: str
    build_number: str
    revision: str
    build_date: str


def get_build_info() -> BuildInfo:
    """Return the identity embedded in the container at image build time."""
    return BuildInfo(
        version=os.environ.get("BAMBU_SPOOLMAN_VERSION") or "local",
        build_number=os.environ.get("BAMBU_SPOOLMAN_BUILD_NUMBER") or "local",
        revision=os.environ.get("BAMBU_SPOOLMAN_REVISION") or "unknown",
        build_date=os.environ.get("BAMBU_SPOOLMAN_BUILD_DATE") or "unknown",
    )
