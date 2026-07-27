"""Provider interfaces and adapters.

Every external dependency sits behind a typed Protocol with structured
results, an explicit error taxonomy (``ai_providers.errors``), bounded
retries (``ai_providers.retry``), and a mock implementation for tests
and the browser simulator. Services receive providers through dependency
injection — nothing constructs a vendor client inline.

Interfaces: TelephonyProvider, STTProvider, LLMProvider, TTSProvider,
CalendarProvider, SMSProvider, EmailProvider, StorageProvider,
CacheProvider, AuthenticationProvider. (EmbeddingProvider and
BillingProvider arrive with their milestones.)
"""

from ai_providers.auth import AuthenticationProvider
from ai_providers.cache import CacheProvider
from ai_providers.calendar import CalendarProvider
from ai_providers.errors import ProviderError
from ai_providers.llm import LLMProvider
from ai_providers.messaging import EmailProvider, SMSProvider
from ai_providers.storage import StorageProvider
from ai_providers.stt import STTProvider
from ai_providers.telephony import TelephonyProvider
from ai_providers.tts import TTSProvider

__all__ = [
    "AuthenticationProvider",
    "CacheProvider",
    "CalendarProvider",
    "EmailProvider",
    "LLMProvider",
    "ProviderError",
    "SMSProvider",
    "STTProvider",
    "StorageProvider",
    "TTSProvider",
    "TelephonyProvider",
]
