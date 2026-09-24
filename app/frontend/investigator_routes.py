"""Static investigator workspace; data authorization stays on API requests."""
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
ROOT = Path(__file__).resolve().parents[2] / 'frontend' / 'investigator'
HEADERS = {
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; "
                             "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                             "frame-ancestors 'none'; form-action 'self'",
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
}


@router.get('/investigator')
def redirect():
    return RedirectResponse('/investigator/', status_code=307)


@router.get('/investigator/')
@router.get('/investigator/docket')
@router.get('/investigator/evidence')
def workspace():
    return FileResponse(ROOT / 'index.html', headers=HEADERS)
