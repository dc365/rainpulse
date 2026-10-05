# X QC action allowance and uncertain review

The heuristic allowance counts observed proposed gates excluding independently
qualified noise censor gates. The censor retains its separate integrity cap.
Neither detection thresholds nor the heuristic fraction is raised by this fix.

Core and complete-family reviews already retain qualified, over-allowance
members as ACTION_BUDGET reviews. Window, fragmented-carrier and SNR-carrier
increments must retain the same distinction: a refused confirmation is not a
finding of clean weather. On an action-allowance refusal only, retain exact
observed, unprotected, novel candidate membership in respectively
`XQC_POLAR_WINDOW_BUDGET_REVIEW_MASK`,
`XQC_FRAGMENTED_CARRIER_BUDGET_REVIEW_MASK` and
`XQC_SNR_CARRIER_BUDGET_REVIEW_MASK`. Record the module cause and ACTION_BUDGET;
keep state 4 and the accepted increment mask empty. Do not add review members
to PROPOSED or confirmed QUARANTINE. Subsequent modules must not reclaim them.

The sole existing writer assigns uncertain action 3 in active candidate modes,
withholds these members from numerical/display QC and CR, and retains RAW.
Audit mode exports evidence without changing numerical fields. Consequently
the confirmation allowance does not cap the union of confirmed and uncertain
withholding; this is the existing core/complete-family review semantics, not
permission to declare all over-allowance gates nonmeteorological.

Protection/source, resource or combined-evidence refusal yields no new review
members. Evidence overflow atomically discards the entire new increment before
any reason or action is attached. Prior arrays, reasons and dispositions remain
immutable outside the exact novel membership. No mode enables QPE, trusted
fusion or forecasting. Review counts are diagnostic and are not rainfall truth.

Native export includes each review mask and its exact module state; bounded
native serialization and existing gate/work/evidence allowances still apply.
New implementation/code identity and normal versioned publication are required;
old published products and failed receipts are never overwritten.
