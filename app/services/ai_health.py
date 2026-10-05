"""Cached, read-only reachability checks; never invoke paid model inference."""
import asyncio
from time import monotonic

import httpx


class ApiHealth:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.signature = None
        self.checked = 0
        self.result = None

    async def check(self, settings, transport=None):
        if not settings.api_url or not settings.model:
            return {'configured':False,'available':False,'message':'Skonfiguruj adres API i model w Ustawieniach AI'}
        key = settings.api_key.get_secret_value() if settings.api_key else ''
        signature = (settings.api_url, settings.model, key)
        async with self.lock:
            if self.signature == signature and self.result is not None and monotonic()-self.checked < 30:
                return self.result
            base = settings.api_url.removesuffix('/chat/completions')
            headers = {'Authorization':f'Bearer {key}'} if key else {}
            try:
                async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False, transport=transport) as client:
                    async with asyncio.timeout(4):
                        async with client.stream('GET', base+'/models', headers=headers) as response:
                            status = response.status_code
                        if status in {404,405,501}:
                            async with client.stream('HEAD', base+'/chat/completions', headers=headers) as response:
                                status = response.status_code
                                available = 200 <= status < 300 or status == 405
                        else:
                            available = 200 <= status < 300
                message = ('Serwer API odpowiada. Test połączenia sprawdza konkretny model i żądanie.' if available
                           else 'API odrzuciło uwierzytelnienie' if status in {401,403}
                           else f'API odpowiada HTTP {status}; sprawdź konfigurację')
                result = {'configured':True,'available':available,'message':message}
            except (httpx.HTTPError, TimeoutError):
                result = {'configured':True,'available':False,'message':'Brak odpowiedzi API; sprawdź adres i sieć'}
            self.signature,self.checked,self.result = signature,monotonic(),result
            return result
