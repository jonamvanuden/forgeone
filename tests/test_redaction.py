import json

from forgeone.observability.redaction import redact, redact_obj


def test_redacts_keys_and_keeps_json_valid():
    obj = {"msg": 'key sk-ant-abcdefghijklmnop123 and api_key="hunter2secret"\n"quoted"', "n": 1}
    out = redact_obj(obj)
    s = json.dumps(out)
    assert "sk-ant-abc" not in s and "hunter2" not in s
    assert json.loads(s)["n"] == 1
    assert redact("password: abcdefgh") == "password: [REDACTED]"
