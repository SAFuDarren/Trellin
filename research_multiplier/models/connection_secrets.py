"""Access app-owned model keys through macOS Security without command arguments."""

import ctypes
import sys
from functools import cached_property


_SERVICE = b"org.researchmultiplier.trellin.model-connections"
_NOT_FOUND = -25300
_POINTER = ctypes.c_void_p
_SIZE = ctypes.c_uint32


class CredentialStoreError(RuntimeError):
    """The OS credential store refused or could not complete an operation."""


def _function(library, name, result, *parameters):
    function = getattr(library, name)
    function.argtypes = parameters
    function.restype = result
    return function


class Keychain:
    """Store one generic password per connection ID in the user's keychain."""

    @cached_property
    def _security(self):
        if sys.platform != "darwin":
            raise CredentialStoreError()
        try:
            return ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        except OSError:
            raise CredentialStoreError() from None

    @staticmethod
    def _query(identifier):
        account = identifier.encode("ascii")
        return (None, len(_SERVICE), _SERVICE, len(account), account)

    @staticmethod
    def _check(status):
        if status != 0:
            raise CredentialStoreError()

    def add(self, identifier, value):
        password = value.encode("ascii")
        add = _function(self._security, "SecKeychainAddGenericPassword", ctypes.c_int32,
                        _POINTER, _SIZE, ctypes.c_char_p, _SIZE, ctypes.c_char_p,
                        _SIZE, _POINTER, _POINTER)
        self._check(add(*self._query(identifier), len(password), password, None))

    def _find(self, identifier, length=None, content=None, item=None):
        find = _function(self._security, "SecKeychainFindGenericPassword", ctypes.c_int32,
                         _POINTER, _SIZE, ctypes.c_char_p, _SIZE, ctypes.c_char_p,
                         ctypes.POINTER(_SIZE), ctypes.POINTER(_POINTER), ctypes.POINTER(_POINTER))
        status = find(*self._query(identifier), length, content, item)
        if status == _NOT_FOUND:
            return False
        self._check(status)
        return True

    def get(self, identifier):
        length, content = _SIZE(), _POINTER()
        if not self._find(identifier, length, content):
            return None
        free = _function(self._security, "SecKeychainItemFreeContent", ctypes.c_int32, _POINTER, _POINTER)
        try:
            return ctypes.string_at(content, length.value).decode("ascii")
        except UnicodeError:
            raise CredentialStoreError() from None
        finally:
            free(None, content)

    def remove(self, identifier):
        item = _POINTER()
        if not self._find(identifier, item=item):
            return
        delete = _function(self._security, "SecKeychainItemDelete", ctypes.c_int32, _POINTER)
        # Security exports Core Foundation symbols through its dependency.
        release = _function(self._security, "CFRelease", None, _POINTER)
        try:
            self._check(delete(item))
        finally:
            release(item)
