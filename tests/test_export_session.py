"""Session export from the operator's personal browser.

The cookie filter is the security-critical part: the personal browser holds
cookies for mail, banking and everything else, and only Upwork's may ever be
written to disk.
"""
import export_session as es


def _c(name, domain):
    return {"name": name, "domain": domain, "value": "x"}


def test_only_upwork_cookies_are_kept():
    cookies = [
        _c("master_access_token", ".upwork.com"),
        _c("XSRF-TOKEN", "www.upwork.com"),
        _c("SESSION", ".mybank.example"),
        _c("SID", ".google.com"),
        _c("auth", "mail.example.org"),
    ]
    kept = es._upwork_cookies(cookies)
    assert {c["name"] for c in kept} == {"master_access_token", "XSRF-TOKEN"}


def test_lookalike_domain_is_not_kept():
    # Must match on the upwork.com domain, not merely contain "upwork".
    kept = es._upwork_cookies([_c("evil", "upwork.attacker.example")])
    assert kept == []


def test_missing_domain_does_not_crash():
    assert es._upwork_cookies([{"name": "x"}]) == []


def test_report_true_when_login_markers_present(capsys):
    ok = es._report([_c("master_access_token", ".upwork.com"),
                     _c("user_uid", ".upwork.com")])
    assert ok is True
    assert "master_access_token" in capsys.readouterr().out


def test_report_false_for_logged_out_session(capsys):
    # Tracking/consent cookies exist even when signed out - not a session.
    ok = es._report([_c("visitor_id", ".upwork.com"), _c("_ga", ".upwork.com")])
    assert ok is False
    assert "NONE" in capsys.readouterr().out


def test_port_defaults_to_9222():
    assert es._arg_port([]) == 9222


def test_port_can_be_overridden():
    assert es._arg_port(["--port", "9333"]) == 9333


def test_bad_port_falls_back_to_default():
    assert es._arg_port(["--port", "abc"]) == 9222
    assert es._arg_port(["--port"]) == 9222


def test_suffix_lookalike_domain_is_not_kept():
    # "upwork.com.example.org" contains "upwork.com" but is NOT Upwork.
    kept = es._upwork_cookies([_c("evil", "upwork.com.example.org")])
    assert kept == []


def test_apex_and_subdomains_are_kept():
    kept = es._upwork_cookies([
        _c("a", "upwork.com"), _c("b", ".upwork.com"), _c("c", "www.upwork.com"),
    ])
    assert {c["name"] for c in kept} == {"a", "b", "c"}
