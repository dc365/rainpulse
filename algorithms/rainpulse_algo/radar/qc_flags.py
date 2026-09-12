# Confirmed non-meteorological causes are diagnostic in polar QC, but cannot
# enter the Phase-1 gridded analysis, QPE, or nowcasting path.
PHASE1_HARD_REJECT_FLAGS = frozenset(
    {
        "MISSING",
        "HARDWARE_ANOMALY",
        "RADIAL_INTERFERENCE",
        "GROUND_CLUTTER",
        "SEA_CLUTTER",
        "ANOMALOUS_PROPAGATION",
        "BIOLOGICAL_ECHO",
    }
)


def hard_reject_flags(definition_version: str) -> frozenset[str]:
    """Keep frozen v1 masks, require generic non-meteorological rejection in v2."""
    if definition_version == "qc-flags-v1":
        return PHASE1_HARD_REJECT_FLAGS
    if definition_version == "qc-flags-v2":
        return PHASE1_HARD_REJECT_FLAGS | {"NON_METEOROLOGICAL"}
    raise ValueError("unsupported QC flag definition version")
