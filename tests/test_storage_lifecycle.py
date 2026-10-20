"""SQLite handles close on every path; failed operations never commit changes."""

from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from pocket_media_manager.config import Settings
from pocket_media_manager.storage import Database


class ConnectionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Settings(Path(self.temporary.name)))
        self.database.initialize()
        self.connections = []
        self.fail_setup = False
        self.fail_commit = False
        actual_connect = sqlite3.connect
        test = self

        class RecordedConnection(sqlite3.Connection):
            closed = False
            rolled_back = False

            def execute(self, query, *args, **kwargs):
                if test.fail_setup and query == "PRAGMA foreign_keys=ON":
                    raise sqlite3.DatabaseError("Cannot configure connection")
                return super().execute(query, *args, **kwargs)

            def commit(self):
                if test.fail_commit:
                    raise sqlite3.DatabaseError("Commit failed")
                return super().commit()

            def rollback(self):
                self.rolled_back = True
                return super().rollback()

            def close(self):
                self.closed = True
                return super().close()

        def connect(*args, **kwargs):
            connection = actual_connect(*args, factory=RecordedConnection, **kwargs)
            self.connections.append(connection)
            return connection

        self.patched = patch("pocket_media_manager.storage.sqlite3.connect", side_effect=connect)
        self.patched.start()
        self.addCleanup(self.patched.stop)

    def assert_closed(self, connection):
        self.assertTrue(connection.closed)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_successful_read_and_committed_write_close_handles(self):
        with self.database.connect(write=True) as connection:
            connection.execute("INSERT INTO settings VALUES ('check', '1')")
        self.assert_closed(connection)
        with self.database.connect() as reader:
            self.assertEqual(
                reader.execute("SELECT value FROM settings WHERE key='check'").fetchone()[0], "1"
            )
        self.assert_closed(reader)

    def test_operation_error_and_interruption_roll_back_before_close(self):
        for error in (ValueError("operation failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    with self.database.connect(write=True) as connection:
                        connection.execute("INSERT INTO settings VALUES ('check', '1')")
                        raise error
                self.assertTrue(connection.rolled_back)
                self.assert_closed(connection)
                with self.database.connect() as reader:
                    self.assertIsNone(
                        reader.execute("SELECT value FROM settings WHERE key='check'").fetchone()
                    )

    def test_connection_configuration_error_still_closes_new_handle(self):
        self.fail_setup = True
        with self.assertRaisesRegex(sqlite3.DatabaseError, "Cannot configure"):
            with self.database.connect():
                self.fail("Connection setup should fail before yielding")
        self.assert_closed(self.connections[-1])

    def test_commit_failure_rolls_back_and_closes_handle(self):
        self.fail_commit = True
        with self.assertRaisesRegex(sqlite3.DatabaseError, "Commit failed"):
            with self.database.connect(write=True) as connection:
                connection.execute("INSERT INTO settings VALUES ('check', '1')")
        self.assertTrue(connection.rolled_back)
        self.assert_closed(connection)
        self.fail_commit = False
        with self.database.connect() as reader:
            self.assertIsNone(
                reader.execute("SELECT value FROM settings WHERE key='check'").fetchone()
            )


if __name__ == "__main__":
    unittest.main()
