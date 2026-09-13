"""
Tests for DATABASE_URL parsing.

Managed hosts hand out one connection string rather than separate variables,
and a misparse shows up as an authentication failure at deploy time with no
useful message — so the shape is worth pinning down here.
"""

from django.test import SimpleTestCase

from config.settings import _database_from_url


class DatabaseUrlTests(SimpleTestCase):
    def test_parses_a_standard_connection_string(self):
        parsed = _database_from_url("postgresql://acme:pw@db.example.com:5432/academy")
        self.assertEqual(parsed["NAME"], "academy")
        self.assertEqual(parsed["USER"], "acme")
        self.assertEqual(parsed["PASSWORD"], "pw")
        self.assertEqual(parsed["HOST"], "db.example.com")
        self.assertEqual(parsed["PORT"], "5432")
        self.assertEqual(parsed["ENGINE"], "django.db.backends.postgresql")

    def test_accepts_the_postgres_scheme_as_well_as_postgresql(self):
        # Render and Heroku emit "postgres://"; Neon emits "postgresql://".
        self.assertIsNotNone(_database_from_url("postgres://u:p@h/db"))
        self.assertIsNotNone(_database_from_url("postgresql://u:p@h/db"))

    def test_decodes_a_percent_encoded_password(self):
        # Generated passwords routinely contain "@", "/" and "+", which must be
        # percent-encoded in a URL. Failing to decode them breaks auth.
        parsed = _database_from_url("postgresql://u:p%40ss%2Fword%2B1@h:5432/db")
        self.assertEqual(parsed["PASSWORD"], "p@ss/word+1")

    def test_defaults_the_port_when_omitted(self):
        self.assertEqual(_database_from_url("postgresql://u:p@h/db")["PORT"], "5432")

    def test_defaults_the_database_name_when_path_is_empty(self):
        self.assertEqual(_database_from_url("postgresql://u:p@h")["NAME"], "postgres")

    def test_ignores_a_non_postgres_url(self):
        # Falling through to the discrete DB_* variables is safer than silently
        # pointing Django at a database engine it was not configured for.
        self.assertIsNone(_database_from_url("mysql://u:p@h/db"))
        self.assertIsNone(_database_from_url("redis://h:6379"))
