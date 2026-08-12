"""设置存储：base_url / model / api_key。

api_key 用 Windows DPAPI（CryptProtectData）加密后存 BLOB，明文不落盘。
_encrypt/_decrypt 可注入（测试用假实现），生产默认 DPAPI。
"""

import ctypes
import sys
from ctypes import wintypes

_DPAPI_AVAILABLE = sys.platform == "win32"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(data: bytes) -> bytes:
    if not _DPAPI_AVAILABLE:
        raise OSError("DPAPI 仅 Windows 可用")
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData 失败")
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return out


def _dpapi_unprotect(data: bytes) -> bytes:
    if not _DPAPI_AVAILABLE:
        raise OSError("DPAPI 仅 Windows 可用")
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData 失败")
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return out


_encrypt = _dpapi_protect
_decrypt = _dpapi_unprotect
_dpapi_available = _DPAPI_AVAILABLE


def ensure_table(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        base_url TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        api_key_enc BLOB
    );
    INSERT OR IGNORE INTO settings (id, base_url, model) VALUES (1, '', '');
    """)
    conn.commit()


def _row(conn):
    return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()


def save_settings(conn, base_url: str, model: str, api_key: str | None = None) -> None:
    ensure_table(conn)
    if api_key is not None and api_key.strip():
        enc = _encrypt(api_key.strip().encode("utf-8"))
        conn.execute(
            "UPDATE settings SET base_url=?, model=?, api_key_enc=? WHERE id=1",
            (base_url.strip(), model.strip(), enc),
        )
    else:
        conn.execute(
            "UPDATE settings SET base_url=?, model=? WHERE id=1",
            (base_url.strip(), model.strip()),
        )
    conn.commit()


def get_settings(conn) -> dict:
    ensure_table(conn)
    row = _row(conn)
    return {
        "configured": bool(row["base_url"] and row["model"] and row["api_key_enc"]),
        "base_url": row["base_url"],
        "model": row["model"],
        "has_key": bool(row["api_key_enc"]),
    }


def get_api_key(conn) -> str:
    ensure_table(conn)
    row = _row(conn)
    if not row or not row["api_key_enc"]:
        return ""
    return _decrypt(bytes(row["api_key_enc"])).decode("utf-8")
