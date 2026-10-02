"""Cut-local, raw-only source statistics. Never caches a fitted target model."""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit


@dataclass
class SourceStatistics:
    sweep: object
    blocks: np.ndarray
    ids: np.ndarray
    indices: tuple
    block_index: np.ndarray
    law: np.ndarray
    center_range: np.ndarray
    receiver: np.ndarray
    coverage: np.ndarray
    parameter_sha256: str
    maximum_trials: int
    maximum_models: int
    trials: int = 0
    models: int = 0
    seed_comparisons: int = 0
    geometry_comparisons: int = 0
    workspace_bytes: int = 0
    fit_cache_hits: int = 0
    fit_cache_misses: int = 0
    fit_cache_evictions: int = 0
    fit_cache_peak_bytes: int = 0

    @classmethod
    def build(cls, s, cfg):
        blocks = (s.ranges // cfg.receiver.block_m).astype(int)
        ids, block_index = np.unique(blocks, return_inverse=True)
        # Check before allocating a potentially large ray-by-block workspace.
        expected = s.shape[0] * len(ids) * 16 + len(s.ranges) * 40
        largest = max(int(np.count_nonzero(blocks == b)) for b in ids)
        expected += s.shape[0] * largest * 32  # conservative median-workspace estimate
        expected += len(ids) ** 2 * 32  # bounded per-ray membership temporaries
        if expected > cfg.source_maximum_summary_bytes:
            raise ResourceLimit('X source summary byte budget exceeded')
        indices = tuple(np.flatnonzero(blocks == b) for b in ids)
        receiver = np.full((s.shape[0], len(ids)), np.nan)
        coverage = np.zeros_like(receiver)
        sn, sa = s.moment('SNR')
        for i, g in enumerate(indices):
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', RuntimeWarning)
                receiver[:, i] = np.nanmedian(np.where(sa[:, g], sn[:, g], np.nan), axis=1)
            coverage[:, i] = np.mean(sa[:, g], axis=1)
        receiver[coverage < cfg.noise_censor_minimum_coverage] = np.nan
        law = 20 * np.log10(np.maximum(s.ranges, s.dr / 2) / 1000)
        center = np.asarray([np.median(s.ranges[g]) for g in indices])
        for a in (blocks, ids, block_index, receiver, coverage, law, center, *indices):
            a.setflags(write=False)
        return cls(s, blocks, ids, indices, block_index, law, center, receiver, coverage,
                   cfg.digest, cfg.source_maximum_trials, cfg.source_maximum_models,
                   workspace_bytes=expected)

    def use(self, s, cfg):
        if s is not self.sweep or cfg.digest != self.parameter_sha256:
            raise ValueError('source summary cannot cross raw sweep/config identity')
        return self

    def trial(self, count=1):
        self.trials += count
        if self.trials > self.maximum_trials:
            raise ResourceLimit('X source model-trial budget exceeded')

    def geometry(self, count=1):
        # Six detector passes plus family support have at most 24 ray-pair
        # traversals. Keep a separate shape-bounded allowance for cheap
        # geometry checks; only held-out model candidates consume trials.
        self.geometry_comparisons += count
        if self.geometry_comparisons > 32 * self.sweep.shape[0] ** 2:
            raise ResourceLimit("X source geometry-comparison budget exceeded")

    def model(self):
        self.models += 1
        if self.models > self.maximum_models:
            raise ResourceLimit('X source model-record budget exceeded')

    def receipt(self):
        return {'scope': 'current_raw_cut_only', 'receiver_summary_builds': 1,
                'distance_blocks': len(self.ids), 'model_trials': self.trials,
                'model_records': self.models, 'seed_comparisons': self.seed_comparisons,
                'geometry_comparisons': self.geometry_comparisons,
                'fitted_model_cache': bool(self.fit_cache_hits),
                'reference_fit_cache_scope': 'current_detector_call_and_raw_ray_only',
                'reference_fit_cache_hits': self.fit_cache_hits,
                'reference_fit_cache_misses': self.fit_cache_misses,
                'reference_fit_cache_evictions': self.fit_cache_evictions,
                'reference_fit_cache_peak_bytes': self.fit_cache_peak_bytes,
                'summary_workspace_bytes': self.workspace_bytes}
