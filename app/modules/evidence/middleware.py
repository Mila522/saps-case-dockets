"""Bound multipart bodies before FastAPI spools uploaded files to temporary disk."""
import re

from fastapi import HTTPException
from starlette.responses import JSONResponse

from app.core.config import settings


class EvidenceUploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or not re.fullmatch(
                r'/api/v1/evidence/[^/]+/files/?', scope['path']):
            return await self.app(scope, receive, send)
        # Permit multipart headers alongside the stricter stored-file byte limit.
        maximum = settings.evidence_max_file_bytes + 1024 * 1024
        headers = dict(scope['headers'])
        declared = headers.get(b'content-length')
        if declared is not None:
            try:
                length = int(declared)
                if length < 0:
                    raise ValueError
            except ValueError:
                return await JSONResponse({'detail': 'Invalid content length'}, status_code=400)(scope, receive, send)
            if length > maximum:
                return await JSONResponse({'detail': 'Evidence upload request is too large'}, status_code=413)(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message['type'] == 'http.request':
                total += len(message.get('body', b''))
                if total > maximum:
                    raise HTTPException(413, 'Evidence upload request is too large')
            return message

        await self.app(scope, bounded_receive, send)
