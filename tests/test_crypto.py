from backend.app.crypto import SecretBox


def test_secret_box_roundtrip():
    box = SecretBox("test-key")
    encrypted = box.encrypt("sk-test")
    assert encrypted != "sk-test"
    assert box.decrypt(encrypted) == "sk-test"

