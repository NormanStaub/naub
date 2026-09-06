"""How every meter in naub moves.  One home, because they must agree.

    import ballistics
    x = v if v >= x else x + (v - x) * ballistics.FALL
    p = x if x >= p else p + (x - p) * ballistics.PEAK_FALL

Three screens draw meters -- the mixer's bands, the EQ screen's spectrum, the
switcher's single bar -- and all three had their own copy of these numbers with
a comment saying "same as the mixer's".  Tuning one meant remembering all
three, so they live here now and nowhere else.

**Rise instantly, fall gradually.**  That asymmetry is the whole trick: a meter
that fell as fast as it rose would flicker, and one that rose as slowly as it
falls would miss every transient.  FALL is the fraction of the remaining gap
the bar closes per update; PEAK_FALL is the same for the marker riding above
it, and being ~8x slower is what makes the marker detach, hang, and settle
rather than sit on the bar.

Fractions of a gap, not pixels per second, so they are tied to the update rate:
tuned for a 16 ms tick (62.5 Hz).  At half the rate, roughly double both.

**PEAK_GAP** is a drawing rule rather than a ballistic: the marker is drawn one
pixel clear of the bar's leading edge instead of on top of it.  Drawn at the
same height it merges into the bar whenever the signal is at its own peak --
which is most of the time on a loud passage -- and the peak hold reads as a
slightly taller bar rather than as a separate thing hanging above one.
"""

FALL = 0.18           # the bar: ~90 ms to close the gap
PEAK_FALL = 0.022     # the marker: ~730 ms, so it visibly hangs
PEAK_GAP = 1          # px between the bar's leading edge and its marker
