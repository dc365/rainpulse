from __future__ import annotations

from dataclasses import replace

from rainpulse_algo.nowcast.pysteps_steps import run_pysteps_steps_fields
from rainpulse_algo.nowcast.steps_profile import StepsSupportConfig

from .test_pysteps_lk import profile as lk_profile
from .test_pysteps_lk import tiny_grid
from .test_pysteps_steps import seeded_backend, steps_fields, steps_profile


def test_partial_domain_disables_unverifiable_velocity_perturbations() -> None:
    calls: list[dict[str, object]] = []
    configured = replace(
        steps_profile(),
        support=StepsSupportConfig(
            "dry_floor_working_copy_preserve_deterministic_support",
            "deterministic_support_intersect_all_members_finite",
        ),
    )

    run_pysteps_steps_fields(
        steps_fields(missing=True),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=seeded_backend(calls),
    )

    assert calls
    assert calls[0]["vel_pert_method"] is None


def test_fully_observed_domain_retains_configured_velocity_perturbations() -> None:
    calls: list[dict[str, object]] = []
    configured = steps_profile()

    run_pysteps_steps_fields(
        steps_fields(),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=seeded_backend(calls),
    )

    assert calls
    assert calls[0]["vel_pert_method"] == configured.ensemble.velocity_perturbation_method
