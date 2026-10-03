import json

from guide.contracts import Selection
from guide.providers.base import ProviderError
from guide.providers.models import HttpModel
from guide.providers.search import OpenAISearch, TavilySearch
from guide.research import Research
from guide.settings import ROOT

CATALOG = json.loads((ROOT / "guide/data/models.json").read_text())
ROLES = {
    "stt": ("openai", "deepgram"),
    "tts": ("openai", "deepgram", "elevenlabs"),
    "research": ("openai", "gemini", "anthropic"),
    "search": ("openai", "tavily"),
}


class Registry:
    def __init__(self, settings, client, store, retriever):
        self.settings, self.client, self.store, self.retriever = settings, client, store, retriever

    def available(self, provider, role):
        return bool(self.settings.key(provider)) and (
            provider != "elevenlabs" or bool(self.settings.elevenlabs_voice_id)
        )

    def catalog(self):
        return {
            role: [
                {
                    "provider": p,
                    "available": self.available(p, role),
                    "model": CATALOG.get(p, {}).get(role, "web-search"),
                    "verification": "requires live account verification"
                    if p != "openai"
                    else "see build report",
                }
                for p in providers
            ]
            for role, providers in ROLES.items()
        }

    def validate(self, selection: Selection, voice=True):
        if (
            not self.settings.dev_provider_selection
            and selection != self.settings.production_selection
        ):
            raise ProviderError("Provider selection is disabled")
        roles = ROLES if voice else ("research", "search")
        for role in roles:
            if not self.available(getattr(selection, role), role):
                raise ProviderError(f"{role} provider is not configured")

    def research(self, selection):
        model = HttpModel(selection.research, self.settings, self.client, self.store)
        search = (OpenAISearch if selection.search == "openai" else TavilySearch)(
            self.settings, self.client, self.store
        )
        research = Research(self.settings, self.store, self.retriever, model, search)
        research.providers = selection.model_dump()
        return research

    def voice_reservation(self, selection):
        bound = (
            CATALOG[selection.stt]["stt_call_reserve_usd"]
            + CATALOG[selection.tts]["tts_call_reserve_usd"]
        )
        return max(self.settings.audio_call_reserve_usd, bound)

    def speech(self, selection, session):
        from pipecat.services.deepgram.stt import DeepgramSTTService
        from pipecat.services.deepgram.tts import DeepgramHttpTTSService
        from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService
        from pipecat.services.openai.stt import OpenAISTTService
        from pipecat.services.openai.tts import OpenAITTSService

        if selection.stt == "openai":
            stt = OpenAISTTService(
                api_key=self.settings.key("openai"),
                settings=OpenAISTTService.Settings(model=CATALOG["openai"]["stt"]),
            )
            stt._client = stt._client.with_options(max_retries=0, timeout=10)
        else:
            stt = DeepgramSTTService(
                api_key=self.settings.key("deepgram"),
                mip_opt_out=True,
                settings=DeepgramSTTService.Settings(model=CATALOG["deepgram"]["stt"]),
            )
        if selection.tts == "openai":
            tts = OpenAITTSService(
                api_key=self.settings.key("openai"),
                settings=OpenAITTSService.Settings(
                    model=CATALOG["openai"]["tts"], voice=CATALOG["openai"]["voice"]
                ),
                sample_rate=24000,
            )
            tts._client = tts._client.with_options(max_retries=0, timeout=10)
        elif selection.tts == "deepgram":
            tts = DeepgramHttpTTSService(
                api_key=self.settings.key("deepgram"),
                aiohttp_session=session,
                settings=DeepgramHttpTTSService.Settings(voice=CATALOG["deepgram"]["tts"]),
                sample_rate=24000,
            )
        else:
            tts = ElevenLabsHttpTTSService(
                api_key=self.settings.key("elevenlabs"),
                aiohttp_session=session,
                settings=ElevenLabsHttpTTSService.Settings(
                    model=CATALOG["elevenlabs"]["tts"], voice=self.settings.elevenlabs_voice_id
                ),
                sample_rate=24000,
            )
        return stt, tts

    async def close_speech(self, selection, stt, tts):
        for role, service in (("stt", stt), ("tts", tts)):
            if getattr(selection, role) == "openai" and service is not None:
                client = getattr(service, "_client", None)
                if client is not None:
                    await client.close()
