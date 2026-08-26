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
