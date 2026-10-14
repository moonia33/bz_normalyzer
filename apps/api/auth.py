from django.conf import settings
from ninja.security import APIKeyHeader


class ApiKeyAuth(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request, key):
        expected = settings.API_KEY
        if expected and key == expected:
            return key
        if settings.DEBUG and not expected:
            return "anonymous"
        return None
