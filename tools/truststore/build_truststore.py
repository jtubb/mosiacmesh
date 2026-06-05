#!/usr/bin/env python
"""Build an iOS 5.1.1 TrustStore.sqlite3 with current root CAs injected.

Takes the device's (empty) TrustStore as a schema template, copies it, and
inserts every root in the certifi (Mozilla) bundle as a trusted anchor using
the exact column format iOS expects:

    sha1 = SHA-1(DER cert)
    subj = DER-encoded Subject Name
    tset = fixed XML plist <array/>  (trust for all purposes)
    data = raw DER cert

Reference: alibaba/iOSSecAudit iosCertTrustManager.py and the iOS Simulator
trust-store method. Root certs are not device-specific, so the resulting file
can be pushed to every device.

Usage:
    python build_truststore.py TrustStore.original.sqlite3 TrustStore.new.sqlite3 [extra_roots.pem ...]
"""
import hashlib
import shutil
import sqlite3
import sys

import certifi
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

# iOS trust-settings plist meaning "trusted for all purposes" (empty array).
TSET = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
    b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    b'<plist version="1.0">\n'
    b'<array/>\n'
    b'</plist>\n'
)


def load_pem_bundle(path):
    """Yield cryptography cert objects from a multi-cert PEM file."""
    with open(path, "rb") as f:
        blob = f.read()
    marker = b"-----BEGIN CERTIFICATE-----"
    end = b"-----END CERTIFICATE-----"
    i = 0
    while True:
        s = blob.find(marker, i)
        if s == -1:
            break
        e = blob.find(end, s)
        if e == -1:
            break
        e += len(end)
        pem = blob[s:e] + b"\n"
        try:
            yield x509.load_pem_x509_certificate(pem)
        except Exception as ex:  # noqa: BLE001
            print("  skip (parse error):", ex)
        i = e


def row_for(cert):
    der = cert.public_bytes(Encoding.DER)
    sha1 = hashlib.sha1(der).digest()
    subj = cert.subject.public_bytes()  # DER of the Subject Name
    return (sha1, subj, TSET, der)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "TrustStore.original.sqlite3"
    dst = sys.argv[2] if len(sys.argv) > 2 else "TrustStore.new.sqlite3"
    extra = sys.argv[3:]

    shutil.copyfile(src, dst)
    con = sqlite3.connect(dst)
    cur = con.cursor()
    before = cur.execute("SELECT COUNT(*) FROM tsettings").fetchone()[0]

    sources = [certifi.where()] + extra
    seen = set()
    added = 0
    for path in sources:
        print("source:", path)
        for cert in load_pem_bundle(path):
            sha1, subj, tset, data = row_for(cert)
            if sha1 in seen:
                continue
            seen.add(sha1)
            cur.execute(
                "INSERT OR REPLACE INTO tsettings (sha1, subj, tset, data) VALUES (?, ?, ?, ?)",
                (sqlite3.Binary(sha1), sqlite3.Binary(subj), sqlite3.Binary(tset), sqlite3.Binary(data)),
            )
            added += 1

    con.commit()
    after = cur.execute("SELECT COUNT(*) FROM tsettings").fetchone()[0]
    con.close()
    print(f"rows before={before}  inserted={added}  after={after}")
    print("wrote:", dst)


if __name__ == "__main__":
    main()
