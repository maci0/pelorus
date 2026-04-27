"""
Certificate management.

`urllib3`/`requests`-based libraries use `certifi` for up-to-date,
platform-agnostic certificate handling.

There are also plenty of internal services that do not have certificates
signed by one of the `certifi`-trusted roots.

Giving custom certificates to `requests` means it _overrides_ checking certifi,
so we have to combine them ourselves.
"""
import atexit
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional, Union

import certifi

DEFAULT_CERT_DIR = Path("/etc/pelorus/custom_certs")

_cached_cert_path: Optional[str] = None
_cert_lock = threading.Lock()


def _combine_certificates(dir_to_check: Path = DEFAULT_CERT_DIR) -> str:
    """
    Combines the certificates with the certificates from `certifi`.
    All certificates ending in `.pem` under each directory under `dir_to_check`
    is combined (e.g. `dir_to_check/*/*.pem`).
    Returns the path of the combined file.
    """
    target_fd, target_path = tempfile.mkstemp(suffix=".pem", prefix="custom-certs")

    with open(target_fd, "wb") as target:
        with open(certifi.where(), "rb") as source:
            shutil.copyfileobj(source, target)

        for source_path in dir_to_check.glob("*/*.pem"):
            if source_path.is_symlink() or not source_path.resolve().is_relative_to(dir_to_check.resolve()):
                logging.warning("Skipping certificate outside trust directory: %s", source_path)
                continue
            logging.info("Combining custom certificate file %s", source_path)

            with source_path.open("rb") as source:
                target.write(f"# custom cert from {source_path}\n".encode())
                shutil.copyfileobj(source, target)

    logging.debug("Combined certificate bundle created at %s", target_path)
    return target_path


def _register_cleanup(path: str):
    """
    Clean up the tempfile at program exit.
    """
    atexit.register(os.remove, path)


def set_up_requests_certs(verify: Optional[bool] = None) -> Union[bool, str]:
    """
    Set up custom certificates based on the way requests is configured.

    In summary:

    If you already ask for a `tls_verify` variable, you'd do:
    `session.verify = set_up_requests_certs(tls_verify)`

    Otherwise, just do `session.verify = set_up_requests_certs()`.

    If `verify` is set to `True` or `None`, then this function will combine
    the certifi certs and the custom certs under `/etc/pelorus/custom_certs/*/*.pem`.

    It will combine them into a temporary file, the path of which is returned.
    The result is cached so that multiple collectors share the same bundle.
    It will also register that file for removal at program exit.

    If `verify` is `False`, `False` is returned for ease of use with the above example.
    """
    global _cached_cert_path

    if verify is False:
        logging.warning(
            "Disabling TLS verification. Custom certificates are now supported, consider using them: "
            "https://pelorus.readthedocs.io/en/latest/GettingStarted/configuration/PelorusExporters/"
            "#custom-certificates"
        )
        return False

    # Fast path: return cached result without acquiring the lock
    if _cached_cert_path is not None:
        return _cached_cert_path

    with _cert_lock:
        # Re-check after acquiring the lock (double-checked locking)
        if _cached_cert_path is not None:
            return _cached_cert_path

        file = _combine_certificates()
        _register_cleanup(file)
        _cached_cert_path = file

        return file


__all__ = ["set_up_requests_certs"]
