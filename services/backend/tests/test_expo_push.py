from central_monitor import expo_push as ep


def test_token_validation_accepts_expo_format_only():
    assert ep.valid_expo_token("ExponentPushToken[abcdefghijklmnop]")
    assert ep.valid_expo_token("ExpoPushToken[abcdefghijklmnop]")
    assert not ep.valid_expo_token("https://fcm.googleapis.com/fcm/send/abc")  # token Web Push não é Expo
    assert not ep.valid_expo_token("ExponentPushToken[]")
    assert not ep.valid_expo_token("")


def test_token_is_never_exposed_in_full():
    token = "ExponentPushToken[abcdefghijklmnopqrstuvwx]"
    assert token not in ep.mask_token(token)
    assert ep.token_hash(token) == ep.token_hash(token) and len(ep.token_hash(token)) == 64


def test_backoff_is_exponential_capped_and_jittered():
    assert ep.backoff_seconds(1, jitter=1.0) == 30
    assert ep.backoff_seconds(2, jitter=1.0) == 60
    assert ep.backoff_seconds(20, jitter=1.0) == 1800
    assert ep.backoff_seconds(3, jitter=0.0) == 60  # metade da base: nunca zero


def test_only_p0_p1_incidents_generate_push():
    assert ep.build_event("incident.created", {"priority": "critical"})["severity"] == "P0"
    assert ep.build_event("incident.created", {"priority": "critical"})["require_ack"] is True
    assert ep.build_event("incident.created", {"priority": "high"})["require_ack"] is False
    assert ep.build_event("incident.created", {"priority": "medium"}) is None
    assert ep.build_event("incident.resolved", {"priority": "low"}) is None
    assert ep.build_event("incident.updated", {"priority": "critical"}) is None


def test_lock_screen_text_has_no_incident_title():
    spec = ep.build_event("incident.created", {"priority": "critical", "title": "Banco do cliente ACME parado"})
    assert "ACME" not in spec["title"] + spec["body_safe"]


def test_sla_events_skip_low_severity():
    assert ep.build_event("sla.resolution.level_1", {"priority": "low"}) is None
    assert ep.build_event("sla.resolution.level_1", {"priority": "high"})["category"] == "sla"


def test_severity_aliases_from_business_monitor_are_normalized():
    assert ep.normalize_severity("high") == "P1"
    assert ep.normalize_severity("critical") == "P0"
    assert ep.normalize_severity("P2") == "P2"
    try:
        ep.normalize_severity("urgent")
        raise AssertionError("severidade inválida deveria falhar")
    except ValueError:
        pass


def test_unknown_domain_is_rejected():
    try:
        ep.enqueue_notification(None, "t", domain="OTHER", source_type="x", source_id="1", event_key="k", severity="P1",
                                category="c", title="t", body="b", action_type="a", action_ref=None, recipients=[])
        raise AssertionError("domínio inválido deveria falhar")
    except ValueError:
        pass


def _payload(project="proj-test-0001", token="ExponentPushToken[abcdefghijklmnop]"):
    return ep.DeviceIn(expoPushToken=token, platform="android", installationId="inst-12345678", expoProjectId=project)


def _with_projects(monkeypatch, value):
    monkeypatch.setattr(ep, "get_settings", lambda: type("S", (), {"expo_project_ids": value})())


def test_register_device_fails_closed_without_configured_projects(monkeypatch):
    import pytest
    from fastapi import HTTPException
    _with_projects(monkeypatch, "")
    with pytest.raises(HTTPException) as error:
        ep.register_device(_payload(), identity=None)
    assert error.value.status_code == 503


def test_register_device_rejects_token_from_another_project(monkeypatch):
    import pytest
    from fastapi import HTTPException
    _with_projects(monkeypatch, "proj-prod-0001, proj-prod-0002")
    with pytest.raises(HTTPException) as error:
        ep.register_device(_payload(project="proj-test-0001"), identity=None)
    assert error.value.status_code == 403


def test_register_device_rejects_web_push_endpoint_as_expo_token(monkeypatch):
    import pytest
    from fastapi import HTTPException
    _with_projects(monkeypatch, "proj-test-0001")
    with pytest.raises(HTTPException) as error:
        ep.register_device(_payload(token="https://fcm.googleapis.com/fcm/send/abcdefghijklmnop"), identity=None)
    assert error.value.status_code == 422
