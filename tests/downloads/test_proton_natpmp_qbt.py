from apps.downloads.scripts.proton_natpmp_qbt import parse_natpmp_port


def test_parse_natpmp_port_from_mapping_output():
    output = """
    initnatpmp() returned 0 (SUCCESS)
    using gateway : 10.2.0.1
    Mapped public port 53186 protocol TCP to local port 1 lifetime 60
    epoch = 123456
    """
    assert parse_natpmp_port(output) == 53186


def test_parse_natpmp_port_rejects_missing_mapping():
    output = "initnatpmp() returned 0 (SUCCESS)"
    try:
        parse_natpmp_port(output)
    except ValueError as exc:
        assert "public port" in str(exc)
    else:
        raise AssertionError("expected ValueError")
