# Narrow radial source candidate

Separate from wide-sector acceptance. Requires native long RAW opening support,
existing held-out source reference, finite target source residual <=2.5dB, and
measured DBZH contrast >=10dB above BOTH immediate neighbouring rays. Geometry
gaps, duplicate/invalid rays, missing neighbours and sector endpoints abstain.
Contrast support must itself remain continuous for >=20km (scikit-image opening).
Explicit weather/conflict vetoes remain; outputs candidate/reason, no hard delete.
Missing reference and missing side evidence are separate diagnostics.
