from rainpulse_algo import package_info


def test_package_identifies_the_rainpulse_compute_plane() -> None:
    assert package_info() == {
        "name": "rainpulse-algo",
        "version": "0.1.0",
    }
