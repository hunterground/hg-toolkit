"""
    HunterGround Toolkit
    Test for the base catalog hub reference mapping
"""

from hg.catalog import hub_ref

def test_hub_ref_flattens_the_registry_path_and_keeps_the_tag():
    assert hub_ref("base/web-nginx:latest") == "hunterground/base-web-nginx:latest"
    assert hub_ref("builder/cpp:latest") == "hunterground/builder-cpp:latest"
    assert hub_ref("bot/xss-puppet:latest") == "hunterground/bot-xss-puppet:latest"
    assert hub_ref("range/pack/sshd:latest") == "hunterground/range-pack-sshd:latest"
    assert hub_ref("range/debian:latest") == "hunterground/range-debian:latest"

def test_hub_ref_without_a_tag_drops_nothing():
    assert hub_ref("base/web-nginx") == "hunterground/base-web-nginx"
