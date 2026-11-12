"""A session cookie must be a valid response header for any username.

Login used to raise UnicodeEncodeError for a username holding a character
outside latin-1, because Starlette encodes Set-Cookie as latin-1. The signed
payload is therefore ASCII-only, and the escape has to stay reversible so the
username read back from a cookie still matches the row in the database.
"""
import os
import unittest
from itsdangerous import BadSignature

os.environ.setdefault("SECRET_KEY", "test-only-session-cookie-encoding")
os.environ.setdefault("CSRF_KEY", "test-only-session-cookie-csrf")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app import security


HEADER_SAFE_USERNAMES = [
    "kiss.peter",
    "Kovács Ágnes",
    "árvíztűrő tükörfúrógép",
    "naïve café",
    "Üdv\U0001f40dRózsa",
    "日本語ユーザー",
]

SEPARATOR_SAFE_USERNAMES = [
    "a|b",
    "back\\slash",
    "a\\x61",
    "\\x41|b",
    "tab\ttab",
    "ctrl\x01x",
]


class SessionCookieEncodingTests(unittest.TestCase):
    def assertRoundTrip(self, username):
        value = security.create_session_value(username, "jti-cookie-test")
        # The header Starlette builds from this value must be encodable.
        value.encode("latin-1")
        self.assertTrue(value.isascii(), "the signed payload must stay ASCII")
        self.assertEqual(security.verify_session_value(value),
                         (username, "jti-cookie-test"))

    def test_latin1_incompatible_names_survive_the_cookie(self):
        for username in HEADER_SAFE_USERNAMES:
            with self.subTest(username=username):
                self.assertRoundTrip(username)

    def test_separator_and_backslash_stay_unambiguous(self):
        for username in SEPARATOR_SAFE_USERNAMES:
            with self.subTest(username=username):
                self.assertRoundTrip(username)

    def test_generated_jti_is_kept_separate_from_the_name(self):
        username, jti = security.verify_session_value(
            security.create_session_value("árvíztűrő"))
        self.assertEqual(username, "árvíztűrő")
        self.assertTrue(jti)

    def test_a_foreign_signature_is_still_rejected(self):
        self.assertIsNone(security.verify_session_value("not-a-signed-cookie"))

    def test_an_escaped_separator_cannot_claim_another_session(self):
        # The separator only ever appears escaped in the name part, so a
        # username holding "|" cannot shift the jti into someone else's slot.
        self.assertNotIn("|", security.escape_username("a|b"))
        signed = security._session_signer.sign("a\\x7cb|jti-split").decode()
        self.assertEqual(security.verify_session_value(signed), ("a|b", "jti-split"))

    def test_a_payload_without_a_separator_has_no_session_identity(self):
        signed = security._session_signer.sign("felhasznalonev").decode()
        self.assertIsNone(security.verify_session_value(signed))


if __name__ == "__main__":
    unittest.main()
