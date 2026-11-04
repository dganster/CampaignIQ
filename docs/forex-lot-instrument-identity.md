# Forex identity in carried lot state

The lot-book serializer previously encoded `ForexPair` as a generic instrument
because ForexPair inherits Instrument. Reloading a carried USD/JPY short therefore
produced `Instrument('USD/JPY')`. A new Forex close used `ForexPair('USD','JPY')`,
which is a different dictionary key. The close could create an offsetting new
position instead of consuming the carried lot.

New Forex lots retain an explicit `forex_pair` instrument descriptor, including
symbol, base and quote currencies. The loader checks that those fields agree.
Existing generic BASE/QUOTE descriptors are restored as Forex pairs only when
both uppercase codes belong to the conservative fiat-code allowlist in the
loader and the currencies differ. Crypto pairs, arbitrary slash names and
unclassified symbols retain their generic type. Unrecognized legacy types are
not guessed or silently corrected.

The compatibility conversion occurs only in the returned in-memory lot book.
Existing saved artifact bytes, quantities, lot IDs, campaign IDs, dates, basis
amounts and provenance are not rewritten. No migration or customer document
replacement is required. New financial state retains the Forex subtype when
saved for subsequent months.

Regression tests cover exact round trips, the legacy 50,000-unit USD/JPY close,
partial close basis and campaign preservation, unrelated instruments, and
malformed explicit Forex descriptors. The development installer runs the full
suite and does not connect to production or publish a month.

A subsequent release must verify the current retained histories and October
preview before deployment/publication. Code predating this fix cannot read the
new explicit Forex descriptor; a rollback after new publication requires a
compatible reader. Existing published financial artifacts remain unchanged.
