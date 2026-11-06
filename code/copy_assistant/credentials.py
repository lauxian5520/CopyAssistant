"""Keep per-endpoint secrets in Windows Credential Manager."""

from keyring.backends.Windows import WinVaultKeyring
from keyring.errors import PasswordDeleteError
from win32ctypes.pywin32 import pywintypes

from config import APP_NAME, ServiceProfile, completion_url


CredentialError = pywintypes.error


class Credentials:
    """Read and update credentials using the Windows backend."""

    def __init__(self) -> None:
        self.backend = WinVaultKeyring()
        self.backend.persist = "local machine"

    def account(self, profile: ServiceProfile) -> str:
        """Scope a saved key to both the profile and its endpoint."""
        return f"{APP_NAME}:{profile.id}:{completion_url(profile.url)}"

    def get(self, profile: ServiceProfile) -> str | None:
        """Return the API key for the configured endpoint."""
        return self.backend.get_password(self.account(profile), "api_key")

    def save(self, profile: ServiceProfile, key: str) -> None:
        """Store a new API key."""
        self.backend.set_password(self.account(profile), "api_key", key)

    def delete(self, profile: ServiceProfile) -> None:
        """Remove the saved key if present."""
        try:
            self.backend.delete_password(self.account(profile), "api_key")
        except PasswordDeleteError:
            if self.get(profile) is not None:
                raise
