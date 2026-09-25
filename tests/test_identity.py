import pytest

from sliderule.identity import identity_key, normalize_linkedin


def test_linkedin_normalization():
    variants = [
        "https://www.linkedin.com/in/dana-okafor/",
        "http://linkedin.com/in/Dana-Okafor",
        "www.linkedin.com/in/dana-okafor/?utm_source=share",
        "linkedin.com/in/dana-okafor",
    ]
    assert {normalize_linkedin(v) for v in variants} == {
        "linkedin.com/in/dana-okafor"
    }


def test_non_linkedin_url_is_rejected():
    with pytest.raises(ValueError):
        normalize_linkedin("https://example.com/in/someone")
    with pytest.raises(ValueError):
        normalize_linkedin("https://linkedin.com/")
    # substring lookalikes must not mint identity keys
    with pytest.raises(ValueError):
        normalize_linkedin("https://linkedin.com.evil-tracker.co/in/dana-okafor")
    with pytest.raises(ValueError):
        normalize_linkedin("https://mylinkedin.company.com/in/dana")


def test_linkedin_subdomains_are_accepted():
    assert normalize_linkedin("https://uk.linkedin.com/in/dana-okafor") == (
        "uk.linkedin.com/in/dana-okafor"
    )


def test_identity_key_prefers_linkedin():
    assert identity_key("Dana Okafor", "linkedin.com/in/dana-okafor", 7) == (
        "linkedin.com/in/dana-okafor"
    )


def test_provisional_key_uses_name_and_firm():
    assert identity_key(" Dana Okafor ", None, 7) == "dana okafor|firm:7"
    assert identity_key("Dana Okafor", None, None) == "dana okafor|firm:none"
