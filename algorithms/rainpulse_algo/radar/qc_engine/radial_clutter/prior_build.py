"""Streaming, reviewed clear-air counts; no missing-as-zero denominator."""
import numpy as np

from .geometry import measured, validate_native
from .prior_types import align_rows, utc


class PriorBuilder:
    def __init__(self, first_sample, minimum_days=20, minimum_observations=200, echo_dbz=0):
        first_sample.validate()
        validate_native(first_sample.native)
        if minimum_days < 2 or minimum_observations < 2:
            raise ValueError("multi-day and repeated observed support required")
        self.reference = first_sample.native
        self.radar = first_sample.radar_id
        self.partition = first_sample.partition_id
        self.minimum_days = minimum_days
        self.minimum_observations = minimum_observations
        self.threshold = float(echo_dbz)
        shape = self.reference.shape
        self.count = np.zeros(shape, "uint32")
        self.hits = np.zeros(shape, "uint32")
        self.days = np.zeros(shape, "uint16")
        self.today = np.zeros(shape, bool)
        self.current_day = None
        self.last_time = None
        self.identities = set()
        self.hashes = set()
        self.receipts = []

    def add(self, sample):
        sample.validate()
        n = sample.native
        validate_native(n)
        when = utc(sample.observed_at_utc)
        if (sample.radar_id, sample.partition_id) != (self.radar, self.partition):
            raise ValueError("radar/hardware/scan partition changed")
        if sample.scan_id in self.identities or sample.input_sha256 in self.hashes:
            raise ValueError("duplicate physical scan or copied content")
        if self.last_time is not None and when < self.last_time:
            raise ValueError("clear-air samples must be ordered by UTC")
        if not np.array_equal(n.ranges, self.reference.ranges):
            raise ValueError("range geometry differs; no interpolation")
        no_echo = np.asarray(sample.valid_no_echo, bool)
        finite = measured(n, "DBZH") & n.geometry_good[:, None]
        if np.any(no_echo & finite & (n.fields["DBZH"] >= self.threshold)):
            raise ValueError("valid-no-echo contradicts measured echo")
        observed = (finite | no_echo) & n.geometry_good[:, None]
        hit = finite & (n.fields["DBZH"] >= self.threshold) & ~no_echo
        if self.current_day != when.date():
            self.days += self.today.astype("uint16")
            self.today[:] = False
            self.current_day = when.date()
        rows, valid = align_rows(self.reference.azimuth, self.reference.elevation, n)
        src = np.flatnonzero(valid)
        dst = rows[src]
        self.count[dst] += observed[src].astype("uint32")
        self.hits[dst] += hit[src].astype("uint32")
        self.today[dst] |= observed[src]
        self.last_time = when
        self.identities.add(sample.scan_id)
        self.hashes.add(sample.input_sha256)
        self.receipts.append({"scan_id": sample.scan_id, "input_sha256": sample.input_sha256,
                              "review_sha256": sample.review_receipt_sha256,
                              "time_utc": when.isoformat()})
