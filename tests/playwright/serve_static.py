"""Serve the production React bundle for browser regression tests."""
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIST = PROJECT_ROOT / 'frontend' / 'dist'
app = FastAPI(title='playwright-react-server')
@app.get('/api/config')
async def config(): return {'clerk_publishable_key': 'pk_test_dummy'}
@app.get('/')
async def index(): return FileResponse(DIST / 'index.html')
app.mount('/assets', StaticFiles(directory=str(DIST / 'assets')), name='assets')
@app.get('/{path:path}')
async def fallback(path: str):
    if path.startswith(('api/', 'health')): return JSONResponse(status_code=404, content={'detail':'Not Found'})
    return FileResponse(DIST / 'index.html')
if __name__ == '__main__': uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PLAYWRIGHT_PORT','8123')), log_level='warning')
