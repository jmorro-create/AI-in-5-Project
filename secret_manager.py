"""
Secure Secret Management Layer (Google Cloud Secret Manager + Environment Variable Injection).

Provides zero-hardcoded-credential retrieval for API keys, Twilio credentials, and Firebase/Firestore
tokens using Google Cloud Secret Manager (`google-cloud-secret-manager`) with in-memory TTL caching
and safe local environment variable fallback for offline development and automated test suites.
"""

import os
from pathlib import Path
import time
from typing import Dict, Optional, Tuple

try:
    from google.cloud import secretmanager  # type: ignore[import-untyped]

    HAS_GCP_SECRET_MANAGER = True
except ImportError:
    secretmanager = None  # type: ignore[assignment]
    HAS_GCP_SECRET_MANAGER = False

_SECRET_CACHE: Dict[str, Tuple[str, float]] = {}
_DEFAULT_CACHE_TTL_SECONDS = 300.0
_ENV_LOADED = False


def load_local_env_file(env_path: Optional[Path] = None) -> None:
    """Loads non-empty key=value pairs from `.env` into `os.environ` if not already set.

    Args:
        env_path (Optional[Path]): Explicit path to a `.env` file. Defaults to project root `.env`.

    Returns:
        None
    """
    global _ENV_LOADED
    if _ENV_LOADED and env_path is None:
        return
    target = env_path or (Path(__file__).resolve().parent.parent / ".env")
    if target.is_file():
        try:
            with target.open("r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    k_clean = key.strip()
                    clean_val = val.strip().strip('"').strip("'")
                    # Ignore placeholder template markers so they don't mask real env/Secret Manager values
                    if clean_val.startswith(" SecretManager:") or clean_val in (
                        "YOUR_SECRET_IN_GCP_SECRET_MANAGER",
                        "REDACTED_USE_SECRET_MANAGER",
                        "<FETCHED_FROM_GCP_SECRET_MANAGER>",
                    ):
                        continue
                    if not clean_val:
                        continue
                    if not os.environ.get(k_clean, ""):
                        os.environ[k_clean] = clean_val
        except OSError:
            pass
    _ENV_LOADED = True


def _fetch_from_gcp_secret_manager(
    secret_id: str,
    project_id: str,
    version_id: str = "latest",
) -> Optional[str]:
    """Queries Google Cloud Secret Manager for the specified secret payload.

    Args:
        secret_id (str): Secret identifier in Google Cloud Secret Manager (e.g., 'GOOGLE_API_KEY').
        project_id (str): GCP project ID hosting the secret.
        version_id (str): Secret version (defaults to 'latest').

    Returns:
        Optional[str]: Decoded UTF-8 secret value if accessible, or None if unavailable.
    """
    if not HAS_GCP_SECRET_MANAGER or secretmanager is None or not project_id:
        return None
    if os.environ.get("DISABLE_GCP_SECRET_MANAGER", "").lower() in ("1", "true", "yes"):
        return None
    try:
        client = secretmanager.SecretManagerServiceClient()
        secret_name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
        response = client.access_secret_version(
            request={"name": secret_name},
            timeout=1.5,
        )
        payload = response.payload.data.decode("utf-8").strip()
        return payload if payload else None
    except Exception:
        return None


def get_secret(
    secret_id: str,
    default: str = "",
    project_id: Optional[str] = None,
    ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
    prefer_secret_manager: bool = False,
) -> str:
    """Retrieves a secret securely via Environment Variable injection or Google Cloud Secret Manager.

    Resolution order:
    1. In-memory TTL cache (to avoid repeated network round-trips).
    2. Process environment variables (`os.environ`) if set and `prefer_secret_manager=False`.
    3. Google Cloud Secret Manager (`projects/{project_id}/secrets/{secret_id}/versions/latest`).
    4. Fallback `default` value.

    Args:
        secret_id (str): Name of the secret or environment variable (e.g., 'GOOGLE_API_KEY').
        default (str): Fallback value if the secret is not found in env or Secret Manager.
        project_id (Optional[str]): GCP project ID for Secret Manager lookup.
        ttl_seconds (float): Cache time-to-live in seconds.
        prefer_secret_manager (bool): If True, attempts GCP Secret Manager before `os.environ`.

    Returns:
        str: Resolved secret value (or `default` if not configured).
    """
    load_local_env_file()
    now = time.time()
    cached = _SECRET_CACHE.get(secret_id)
    if cached is not None and now < cached[1]:
        return cached[0]

    eff_project = (
        project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("FIRESTORE_PROJECT_ID")
        or ""
    ).strip()

    env_val = os.environ.get(secret_id, "").strip()
    if env_val and not prefer_secret_manager:
        _SECRET_CACHE[secret_id] = (env_val, now + ttl_seconds)
        return env_val

    sm_val = _fetch_from_gcp_secret_manager(secret_id=secret_id, project_id=eff_project)
    if sm_val:
        _SECRET_CACHE[secret_id] = (sm_val, now + ttl_seconds)
        os.environ.setdefault(secret_id, sm_val)
        return sm_val

    if env_val:
        _SECRET_CACHE[secret_id] = (env_val, now + ttl_seconds)
        return env_val

    return default


def clear_secret_cache() -> None:
    """Clears the in-memory secret cache (used in unit tests).

    Returns:
        None
    """
    _SECRET_CACHE.clear()
