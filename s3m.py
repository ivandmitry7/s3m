#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sqlite3
import threading

__all__ = ["connect", "Connection", "S3MError", "LockTimeoutError"]

version = "1.0.0"

# Global lock storage
CONNECTION_LOCKS = {}

# Locks access to CONNECTION_LOCKS
DICT_LOCK = threading.Lock()
DEFAULT_FETCHMANY_SIZE = 1000

class S3MError(BaseException):
    pass

class LockTimeoutError(S3MError):
    def __init__(self, conn, msg=None):
        if msg is None:
            if conn is None or not isinstance(conn.lock_timeout, (int, float)):
                msg = "Lock timeout exceeded"
            else:
                msg = "Lock timeout exceeded (> %s)" % (conn.lock_timeout)

        S3MError.__init__(self, msg)

        self.connection = conn

def normalize_path(path):
    """
    >>> normalize_path("/a/b/c/")
    '/a/b/c'
    >>> normalize_path("/a/b/c")
    '/a/b/c'
    >>> normalize_path("/a/b/c////////")
    '/a/b/c'
    >>> normalize_path("///a/b/c")
    '/a/b/c'
    >>> normalize_path("/a/././b/../b/c/")
    '/a/b/c'
    >>> normalize_path(":memory:")
    ':memory:'
    """
    if path == ":memory:":
        return path

    return os.path.normcase(os.path.normpath(os.path.realpath(path)))

class FakeLock(object):
    """Only pretends to be a lock"""

    def acquire(self, *args, **kwargs):
        return True

    def release(self, *args, **kwargs):
        return True

def chain(f):
    def wrapper(self, *args, **kwargs):
        f(self, *args, **kwargs)
        return self

    return wrapper

class Connection(object):
    def __init__(self, path, lock_transactions=True, lock_timeout=-1, *args, **kwargs):
        self.path = normalize_path(path)
        self.connection = sqlite3.connect(self.path, *args, **kwargs)
        self.cursor = self.connection.cursor()
        self.closed = False

        # Maximum amount of time the connection is allowed to wait when acquiring the lock.
        self.lock_timeout = lock_timeout

        # Used in with block
        self.was_in_transaction = False

        # Should parallel transactions be allowed?
        self.lock_transactions = lock_transactions

        if self.path == ":memory:":
            # No 2 :memory: connections point to the same database => locks are not needed
            self.lock = FakeLock()
            return

        with DICT_LOCK:
            self.lock = CONNECTION_LOCKS.get(self.path)

            # If the lock doesn't already exist, make a new one
            if self.lock is None:
                self.lock = threading.RLock()
                CONNECTION_LOCKS[self.path] = self.lock

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    @property
    def isolation_level(self):
        return self.connection.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):
        self.connection.isolation_level = value

    @property
    def row_factory(self):
        return self.connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.connection.row_factory = value

    @property
    def text_factory(self):
        return self.connection.text_factory

    @property
    def total_changes(self):
        return self.connection.total_changes

    def __enter__(self):
        # If connection is already in a transaction, then the lock was not released last time
        if not self.connection.in_transaction or not self.lock_transactions:
            if not self.lock.acquire(timeout=self.lock_timeout):
                raise LockTimeoutError(self)

        self.was_in_transaction = self.connection.in_transaction

    def __exit__(self, *args, **kwargs):
        try:
            # if the connection is closed, an exception is thrown
            in_transaction = self.in_transaction
        except sqlite3.ProgrammingError:
            in_transaction = False

        if not self.lock_transactions:
            self.lock.release()
            return

        # The lock should be released only if:
        # 1) the connection was previously in a transaction and now it isn't
        # 2) the connection wasn't previously in a transaction and still isn't
        if (self.was_in_transaction and not in_transaction) or not in_transaction:
            self.lock.release()

    def __del__(self):
        self.close()

    def close(self):
        """Close the connection"""

        if not self.closed:
            self.cursor.close()
            self.connection.close()
            self.closed = True

    @chain
    def execute(self, *args, **kwargs):
        with self:
            self.cursor.execute(*args, **kwargs)

    @chain
    def executemany(self, *args, **kwargs):
        with self:
            self.cursor.executemany(*args, **kwargs)

    @chain
    def executescript(self, *args, **kwargs):
        with self:
            self.cursor.executescript(*args, **kwargs)

    def commit(self):
        with self:
            self.connection.commit()

    def rollback(self):
        with self:
            self.connection.rollback()

    def fetchone(self):
        with self:
            return self.cursor.fetchone()

    def fetchmany(self, size=DEFAULT_FETCHMANY_SIZE):
        with self:
            return self.cursor.fetchmany(size)

    def fetchall(self):
        with self:
            return self.cursor.fetchall()

    def interrupt(self):
        return self.connection.interrupt()

    def create_function(self, *args, **kwargs):
        self.connection.create_function(*args, **kwargs)

    def create_aggregate(self, *args, **kwargs):
        self.connection.create_aggregate(*args, **kwargs)

    def create_collation(self, *args, **kwargs):
        self.connection.create_collation(*args, **kwargs)

    def set_authorizer(self, *args, **kwargs):
        self.connection.set_authorizer(*args, **kwargs)

    def set_progress_handler(self, *args, **kwargs):
        self.connection.set_progress_handler(*args, **kwargs)

    def set_trace_callback(self, *args, **kwargs):
        self.connection.set_trace_callback(*args, **kwargs)

    def enable_load_extension(self, *args, **kwargs):
        self.connection.enable_load_extension(*args, **kwargs)

    def load_extension(self, *args, **kwargs):
        self.connection.load_extension(*args, **kwargs)

    def iterdump(self, *args, **kwargs):
        return self.connection.iterdump()

def connect(path, factory=Connection, *args, **kwargs):
    """Analagous to sqlite3.connect()"""

    return factory(path, *args, **kwargs)
