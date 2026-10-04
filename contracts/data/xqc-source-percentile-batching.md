# X source model percentile batching

The source detector measures the same original observed gates in each distance
block. Equal-length blocks may be stacked as separate rows and passed to NumPy
percentile along the range axis. No sample belongs to another block or RAW ray.
Empty blocks remain NaN. Target/guard exclusion, source fits, native geometry,
weather protections, model counters and action budgets keep their existing rules.

Scalar percentiles must remain scalar when passed to NumPy. In particular,
float32 scalar percentiles and a one-element quantile array can differ in their
interpolation and dtype. Output values and dtype must match the former scalar
block loop exactly. Response subtraction retains the original operation order:
`DBZH[g] - range_law[g] - fitted_trend[g]`.

Temporary batching is call-local. Before allocating the stack, reserve
48 bytes per selected gate plus 256 + 64 times the quantile count per block.
Only unused existing source-summary allowance may be used; when fits are live,
subtract their resident bytes too. If the allowance is insufficient, run the
original scalar loop. This optimization must not increase a resource limit,
change a resource refusal, cache fitted target decisions, or change QC evidence.

Acceptance requires scalar-versus-batch exact value/dtype tests, a regression
showing fewer NumPy calls on equal original blocks, and paired real native
caller/export comparison with unchanged RAW, masks, QC and diagnostics. Timing
alone does not authorize publication or establish meteorological QC accuracy.
