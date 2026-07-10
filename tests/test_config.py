import pytest

from janus.config import Config, load


def test_load_valid_config(tmp_path):
    cfg_file = tmp_path / "janus.toml"
    cfg_file.write_text(
        'server_port = 1717\n'
        'bind_address = "0.0.0.0"\n'
        'delay = 30\n'
        'rbls = ["bl.spamcop.net."]\n'
    )
    cfg = load(str(cfg_file))
    assert cfg.server_port == 1717
    assert cfg.bind_address == "0.0.0.0"
    assert cfg.delay == 30
    assert cfg.rbls == ["bl.spamcop.net."]


def test_missing_server_port_raises(tmp_path):
    cfg_file = tmp_path / "janus.toml"
    cfg_file.write_text('delay = 30\n')
    with pytest.raises(ValueError, match="server_port"):
        load(str(cfg_file))


def test_defaults_applied():
    cfg = Config(server_port=1717)
    assert cfg.bind_address == "127.0.0.1"
    assert cfg.delay == 60
    assert cfg.rbl_delay == 3600
    assert cfg.gc_days == 5
    assert cfg.gc_interval == 60
    assert cfg.rbls == []
    assert "@SECONDS@" in cfg.gl_message


def test_load_minimal_config(tmp_path):
    cfg_file = tmp_path / "janus.toml"
    cfg_file.write_text('server_port = 9999\n')
    cfg = load(str(cfg_file))
    assert cfg.server_port == 9999
    assert cfg.delay == 60


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(str(tmp_path / "nonexistent.toml"))
