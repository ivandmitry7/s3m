#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sqlite3
import sys
import threading
import unittest

import s3m

__all__ = ["S3MTestCase"]

class S3MTestCase(unittest.TestCase):
    def setUp(self):
        self.n_connections = 25
        self.db_path = "s3m_test.db"

        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def insert_func(self, *args, **kwargs):
        conn = self.connect_db(*args, **kwargs)
        if conn.path == ":memory:":
            self.assertFalse(conn.path in s3m.DB_STATES)
        else:
            self.assertIs(conn.db_state, s3m.DB_STATES[s3m.normalize_path(conn.path)].peek()[0])

        queries = ["CREATE TABLE IF NOT EXISTS a(id INTEGER)",
                   "BEGIN TRANSACTION",
                   "INSERT INTO a VALUES(1)",
                   "INSERT INTO a VALUES(2)",
                   "INSERT INTO a VALUES(3)",
                   "COMMIT"]

        if os.environ.get("S3M_TEST_DEBUG"):
            for query in queries:
                print("%s: %s" % (threading.get_ident(), query))
                conn.execute(query)
        else:
            for query in queries:
                conn.execute(query)

    def connect_db(self, path=None, *args, **kwargs):
        path = self.db_path if path is None else path
        kwargs.setdefault("isolation_level", None)
        kwargs.setdefault("single_cursor_mode", False)
        kwargs.setdefault("check_same_thread", False)
        return s3m.connect(path, *args, **kwargs)

    def setup_db(self, *args, **kwargs):
        threads = [threading.Thread(target=self.insert_func, args=args, kwargs=kwargs)
                   for i in range(self.n_connections)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

    def test_s3m(self):
        self.setup_db(self.db_path)
        conn = self.connect_db(self.db_path)
        result = conn.execute("SELECT id FROM a").fetchall()
        self.assertEqual(result, [(1,), (2,), (3,)] * self.n_connections)

    def test_s3m_lock_transactions(self):
        conn1 = self.connect_db(self.db_path, lock_transactions=False, lock_timeout=0.5)
        conn2 = self.connect_db(self.db_path, lock_transactions=False, lock_timeout=0.5,
                                check_same_thread=False)

        def thread_func():
            conn2.execute("BEGIN TRANSACTION")
            conn2.execute("CREATE TABLE b(id INTEGER);")

        conn1.execute("BEGIN TRANSACTION")

        thread = threading.Thread(target=thread_func)
        thread.start()
        thread.join()

        conn1.rollback()
        conn2.rollback()

    def test_s3m_lock_timeout(self):
        conn1 = self.connect_db(self.db_path, lock_timeout=0.01)
        conn2 = self.connect_db(self.db_path, lock_timeout=0.01)

        def thread_func():
            self.assertRaises(s3m.LockTimeoutError, conn2.execute, "BEGIN TRANSACTION")

        conn1.execute("BEGIN TRANSACTION")

        thread = threading.Thread(target=thread_func)
        thread.start()
        thread.join()

        conn1.rollback()

    def test_in_memory1(self):
        self.setup_db(":memory:")

    def test_in_memory2(self):
        conn1 = self.connect_db(":memory:")
        conn2 = self.connect_db(":memory:")

        conn1.execute("BEGIN TRANSACTION")
        conn2.execute("BEGIN TRANSACTION")
        conn1.execute("CREATE TABLE a(id INTEGER)")
        conn2.execute("CREATE TABLE a(id INTEGER)")
        conn1.commit()
        conn2.commit()

    def test_sharing(self):
        conn = self.connect_db(":memory:", check_same_thread=False)

        conn.execute("CREATE TABLE a(id INTEGER)")
        conn.execute("BEGIN TRANSACTION")

        def func():
            for i in range(25):
                conn.execute("INSERT INTO a VALUES(?)", (i,))

        thread = threading.Thread(target=func)

        thread.start()
        func()
        thread.join()

        conn.commit()

        cur = conn.execute("SELECT id FROM a")

        self.assertEqual(len(cur.fetchall()), 50)

    def test_s3m_release_without_acquire(self):
        import random

        conn = self.connect_db(check_same_thread=False, isolation_level='')

        conn.execute("CREATE TABLE IF NOT EXISTS numbers(number INTEGER)")

        def thread_func():
            conn.execute("INSERT INTO numbers VALUES(?)", (random.randint(1, 100),))

        threads = [threading.Thread(target=thread_func) for i in range(10)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        conn.commit()

    def test_create_with(self):
        conn = self.connect_db(isolation_level=None)
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS a (b INTEGER)")
            conn.execute("CREATE INDEX IF NOT EXISTS b_idx ON a(b ASC)")

    def test_bad_connect(self):
        with self.assertRaises(sqlite3.OperationalError):
            conn = self.connect_db("/nonexistent-path/a/b/c/test.db")
            conn.close()

    def test_close_in_transaction(self):
        conn = self.connect_db(isolation_level=None, lock_timeout=0.1)

        conn.execute("BEGIN IMMEDIATE")
        conn.close()

        conn2 = self.connect_db(isolation_level=None, lock_timeout=0.1)
        conn2.execute("SELECT 1")

    def test_close_in_with(self):
        conn = self.connect_db()

        with conn:
            with conn:
                with conn:
                    conn.close()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

        self.assertEqual(len(s3m.DB_STATES), 0)
