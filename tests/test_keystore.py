import json, os, stat
import pytest
from btc_wallet_mcp.keystore import KeystoreError, read_seed_file, write_seed_file

M = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
PW = "correct horse battery"


def test_roundtrip_and_no_plaintext(tmp_path):
    p = tmp_path / "w" / "seed.enc"
    write_seed_file(p, M, PW, n=2**10)
    raw = p.read_text()
    assert "abandon" not in raw
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert read_seed_file(p, PW) == M


def test_wrong_passphrase(tmp_path):
    p = tmp_path / "seed.enc"
    write_seed_file(p, M, PW, n=2**10)
    with pytest.raises(KeystoreError) as e:
        read_seed_file(p, "wrong passphrase!!")
    assert "abandon" not in str(e.value)


def test_tamper_detected(tmp_path):
    p = tmp_path / "seed.enc"
    write_seed_file(p, M, PW, n=2**10)
    blob = json.loads(p.read_text()); blob["ct"] = ("00" if blob["ct"][:2] != "00" else "11") + blob["ct"][2:]
    p.write_text(json.dumps(blob)); os.chmod(p, 0o600)
    with pytest.raises(KeystoreError):
        read_seed_file(p, PW)


def test_no_overwrite_short_pw_and_perms(tmp_path):
    p = tmp_path / "seed.enc"
    with pytest.raises(KeystoreError):
        write_seed_file(p, M, "short")
    write_seed_file(p, M, PW, n=2**10)
    with pytest.raises(KeystoreError):
        write_seed_file(p, M, PW, n=2**10)
    os.chmod(p, 0o644)
    with pytest.raises(KeystoreError):
        read_seed_file(p, PW)
