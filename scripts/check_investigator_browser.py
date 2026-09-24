"""Optional real Edge browser check, using existing Python dependencies only.

Run: .venv/Scripts/python.exe scripts/check_investigator_browser.py
Uses a rollback-only database fixture and temporary browser/storage directories.
No credentials, tokens, notes or database records are printed or retained.
"""
import base64
from contextlib import nullcontext
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
import uuid

import pyotp
from sqlalchemy import select, text
import uvicorn
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from app.main import app
from app.core.config import settings
from app.db.session import get_db
from app.modules.access.models import User, Role, UserRole
from app.modules.stations.models import Station, Officer
from test_authentication import registration
from test_decisions_dockets import workflow_context, officer_account, complaint_at_station


class Browser:
    def __init__(self, websocket):
        self.connection = connect(websocket, max_size=20_000_000)
        self.ws = self.connection.__enter__()
        self.sequence = 0
        self.errors = []
        self.call('Runtime.enable')
        self.call('Log.enable')
        self.call('Page.enable')

    def call(self, method, params=None):
        self.sequence += 1
        self.ws.send(json.dumps({'id': self.sequence, 'method': method, 'params': params or {}}))
        while True:
            response = json.loads(self.ws.recv(timeout=30))
            if response.get('method') == 'Runtime.exceptionThrown':
                detail = response['params']['exceptionDetails']
                self.errors.append(detail.get('exception', {}).get('description', detail.get('text', 'JavaScript exception')))
            if response.get('method') == 'Log.entryAdded' and response['params']['entry'].get('level') == 'error':
                entry = response['params']['entry']
                # HTTP failures are deliberately exercised; only module loading errors are fatal.
                if 'module script' in entry['text']:
                    self.errors.append(entry['text'] + ' ' + entry.get('url', ''))
            if response.get('id') == self.sequence:
                if 'error' in response:
                    raise RuntimeError(response['error']['message'])
                return response.get('result', {})

    def js(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
        if result.get('exceptionDetails'):
            raise AssertionError('Browser expression failed')
        return result.get('result', {}).get('value')

    def wait(self, expression, timeout=15):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self.js(expression):
                return
            time.sleep(.1)
        print('Browser condition failed: ' + expression + '\n' + '\n'.join(self.errors), flush=True)
        raise AssertionError('Browser condition timed out: ' + expression)

    def click(self, selector):
        self.js(f'document.querySelector({json.dumps(selector)}).click()')

    def fill(self, selector, value):
        self.js(f"(()=>{{const n=document.querySelector({json.dumps(selector)});n.value={json.dumps(value)};n.dispatchEvent(new Event('input',{{bubbles:true}}));n.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")

    def submit(self, selector):
        self.js(f'document.querySelector({json.dumps(selector)}).requestSubmit()')

    def navigate(self, url):
        self.call('Page.navigate', {'url': url})

    def screenshot(self, path):
        path.write_bytes(base64.b64decode(self.call('Page.captureScreenshot', {'captureBeyondViewport': False})['data']))


def check():
    edge = Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    if not edge.exists():
        raise SystemExit('Microsoft Edge is required for this optional browser check.')
    fixture = workflow_context.__wrapped__()
    client, db = next(fixture)
    original_storage = settings.evidence_storage_path
    server = process = browser = None
    artifacts = Path(os.environ.get('SAPS_BROWSER_ARTIFACTS', str(ROOT / 'docs' / 'investigator-preview')))
    artifacts.mkdir(parents=True, exist_ok=True)
    temporary_context = tempfile.TemporaryDirectory(prefix='investigator-browser-')
    try:
        with nullcontext(temporary_context.name) as temporary:
            temp = Path(temporary)
            settings.evidence_storage_path = temp / 'evidence'
            station = Station(station_code='UI-' + uuid.uuid4().hex[:8], name='Demonstration station', province='Test')
            db.add(station); db.commit()
            charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
            commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', station)
            _, _, destination = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
            credentials = registration()
            assert client.post('/api/v1/auth/register', json=credentials).status_code == 201
            user = db.scalar(select(User).where(User.username == credentials['username']))
            role = db.scalar(select(Role.id).where(Role.code == 'INVESTIGATING_OFFICER'))
            db.execute(UserRole.__table__.delete().where(UserRole.user_id == user.id))
            db.add(UserRole(user_id=user.id, role_id=role))
            officer = Officer(user_id=user.id, station_id=station.id, service_number='UI-' + uuid.uuid4().hex, rank='Detective')
            db.add(officer); db.commit()
            complaint = complaint_at_station(client, db, station)
            assert client.post(f'/api/v1/complaints/{complaint.id}/decisions', headers=charge, json={'decision':'ACCEPTED'}).status_code == 201
            docket = client.post(f'/api/v1/complaints/{complaint.id}/dockets', headers=charge).json()
            docket_id = docket['id']
            assert client.post(f'/api/v1/dockets/{docket_id}/approvals', headers=commander, json={'decision':'APPROVED'}).status_code == 201
            assert client.post(f'/api/v1/dockets/{docket_id}/assignments', headers=commander, json={'investigating_officer_id':str(officer.id),'reason':'Browser demonstration'}).status_code == 201
            db.execute(text('SET LOCAL ROLE saps_api')); db.commit()
            # Serialize the rollback fixture's shared connection across HTTP threads.
            lock = threading.Lock()
            def isolated_db():
                with lock:
                    yield db
            app.dependency_overrides[get_db] = isolated_db
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1', 0)); port = port_socket.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline: time.sleep(.1)
            assert server.started
            base = f'http://127.0.0.1:{port}'
            profile = temp / 'profile'
            process = subprocess.Popen([str(edge), '--headless=new', '--disable-gpu', '--no-first-run',
                '--no-default-browser-check', '--remote-debugging-port=0', f'--user-data-dir={profile}',
                '--window-size=1440,1000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            port_file = profile / 'DevToolsActivePort'
            deadline = time.monotonic() + 15
            while not port_file.exists() and time.monotonic() < deadline: time.sleep(.1)
            debugging_port = port_file.read_text().splitlines()[0]
            pages = json.load(urlopen(f'http://127.0.0.1:{debugging_port}/json/list'))
            browser = Browser(next(page['webSocketDebuggerUrl'] for page in pages if page['type']=='page'))
            browser.navigate(base + '/investigator/')
            browser.wait("document.body.innerText.includes('Sign in securely')")
            browser.click('a[href="/officer/?workspace=investigator"]')
            browser.wait("!!document.querySelector('#identifier')")
            browser.fill('#identifier', credentials['username']); browser.fill('#password', credentials['password'])
            browser.submit('#login-form')
            browser.wait("!document.querySelector('#setup-form').hidden && document.querySelector('#setup-secret').textContent.length>10")
            secret = browser.js("document.querySelector('#setup-secret').textContent")
            browser.fill('#setup-code', pyotp.TOTP(secret).now()); browser.submit('#setup-form')
            browser.wait("location.pathname==='/investigator/' && !!document.querySelector('#dockets a')")
            assert browser.js("document.querySelector('#dockets').innerText.includes('Open docket')")
            browser.screenshot(artifacts / 'queue-desktop.png')
            browser.fill('#search','no-match'); browser.wait("document.querySelector('#dockets').innerText.includes('No dockets match')")
            browser.click('#clear'); browser.click('#dockets a')
            browser.wait("!!document.querySelector('#note-form')")
            browser.js("document.querySelector('#note-form').parentElement.open=true")
            browser.fill('#content','  Browser verification note  ')
            browser.submit('#note-form'); browser.submit('#note-form')
            browser.wait("document.querySelector('#notice').textContent==='Investigation note saved.'")
            assert browser.js("document.querySelectorAll('#notes li').length") == 1
            browser.js("document.querySelector('#evidence-form').parentElement.open=true")
            for selector, value in [('#title','Demonstration recording'),('#description','Synthetic evidence for the UI check'),('#storage_location','Secure locker A')]: browser.fill(selector,value)
            browser.fill('#evidence_type','VIDEO'); browser.click('#is_digital'); browser.submit('#evidence-form')
            browser.wait("document.querySelector('#notice').textContent.startsWith('Evidence registered:')")
            headers={'Authorization':'Bearer '+browser.js("sessionStorage.getItem('saps_access_token')")}
            # Status conflict refreshes rather than applying a stale change.
            assert client.post(f'/api/v1/dockets/{docket_id}/status',headers=headers,json={'expected_status':'ACTIVE','status':'ON_HOLD','reason':'Concurrent status update'}).status_code==200
            browser.js("document.querySelector('#status-form').parentElement.open=true")
            browser.fill('#reason','Stale change');browser.submit('#status-form')
            browser.wait("document.querySelector('#confirmation').open")
            browser.click('#confirmation button[value="confirm"]')
            browser.wait("document.querySelector('#notice').textContent.includes('record changed') && document.querySelector('#status').value==='ACTIVE'")
            browser.js("document.querySelector('#status-form').parentElement.open=true")
            browser.fill('#reason','Resume investigation');browser.submit('#status-form')
            browser.wait("document.querySelector('#confirmation').open");browser.click('#confirmation button[value="confirm"]')
            browser.wait("document.querySelector('#notice').textContent.includes('status updated')")
            browser.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            assert browser.js('document.documentElement.scrollWidth<=innerWidth'), 'Docket overflows mobile width'
            browser.screenshot(artifacts / 'docket-mobile.png')
            browser.click('#evidence-list a');browser.wait("!!document.querySelector('#upload-form')")
            browser.js("document.querySelector('#upload-form').parentElement.open=true;const dt=new DataTransfer();dt.items.add(new File(['demonstration file'],'recording.txt',{type:'text/plain'}));document.querySelector('#file').files=dt.files")
            browser.submit('#upload-form')
            browser.wait("document.querySelector('#notice').textContent.includes('Metadata recorded as version 1')")
            assert browser.js("document.querySelector('#files').innerText.includes('SHA-256')")
            item_id=browser.js("new URLSearchParams(location.search).get('id')")
            metadata=client.get(f'/api/v1/evidence/{item_id}/files',headers=headers).json()[0]
            assert len(metadata['sha256_hash'])==64 and metadata['file_size_bytes']>0
            item=client.get(f'/api/v1/evidence/{item_id}',headers=headers).json()
            assert client.post(f'/api/v1/evidence/{item_id}/custody-events',headers=headers,json={'event_type':'ANALYSIS_STARTED','expected_custody_event_id':item['custody_version'],'to_location':'Laboratory','notes':'Concurrent custody update'}).status_code==201
            browser.js("document.querySelector('#custody-form').parentElement.open=true")
            browser.fill('#to_custodian_officer_id',str(destination.id));browser.fill('#to_location','Locker B');browser.fill('#notes','Signed handover')
            browser.submit('#custody-form');browser.wait("document.querySelector('#confirmation').open");browser.click('#confirmation button[value="confirm"]')
            browser.wait("document.querySelector('#notice').textContent.includes('record changed') && document.querySelector('#event_type').value==='ANALYSIS_COMPLETED'")
            browser.js("document.querySelector('#custody-form').parentElement.open=true")
            browser.fill('#to_location','Laboratory');browser.fill('#notes','Analysis completed');browser.submit('#custody-form')
            browser.wait("document.querySelector('#confirmation').open");browser.click('#confirmation button[value="confirm"]')
            browser.wait("document.querySelector('#notice').textContent.includes('Custody event recorded')")
            browser.js("document.querySelector('#custody-form').parentElement.open=true")
            browser.fill('#to_custodian_officer_id',str(destination.id));browser.fill('#to_location','Locker B');browser.fill('#notes','Signed handover')
            browser.submit('#custody-form');browser.wait("document.querySelector('#confirmation').open");browser.click('#confirmation button[value="confirm"]')
            browser.wait("document.querySelector('#notice').textContent.includes('Custody event recorded')")
            assert browser.js('document.documentElement.scrollWidth<=innerWidth'), 'Evidence overflows mobile width'
            browser.screenshot(artifacts / 'evidence-mobile.png')
            browser.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
            browser.screenshot(artifacts / 'evidence-desktop.png')
            # Expired access refreshes with the real endpoint; revoked refresh returns to sign-in.
            browser.js("sessionStorage.setItem('saps_access_token','expired-test-value')")
            browser.click('#refresh');browser.wait("document.querySelector('#notice').textContent==='Evidence refreshed.'")
            saved = browser.js("({access:sessionStorage.getItem('saps_access_token'),refresh:sessionStorage.getItem('saps_refresh_token')})")
            browser.js("sessionStorage.setItem('saps_access_token','expired-test-value');sessionStorage.setItem('saps_refresh_token','revoked-test-value')")
            browser.click('#refresh');browser.wait("document.body.innerText.includes('Sign in securely')")
            assert browser.js("sessionStorage.getItem('saps_access_token')===null")
            # Restore only the real session obtained above, to exercise loss of assignment and logout.
            browser.js(f"sessionStorage.setItem('saps_access_token',{json.dumps(saved['access'])});sessionStorage.setItem('saps_refresh_token',{json.dumps(saved['refresh'])})")
            assert client.post(f'/api/v1/dockets/{docket_id}/assignments/end',headers=commander,json={'reason':'Browser access-revocation check'}).status_code==200
            # Browser-only fault injection: a temporary /me outage must allow a complete bootstrap retry.
            injection=browser.call('Page.addScriptToEvaluateOnNewDocument',{'source': "const originalFetch=window.fetch;let failMeOnce=true;window.fetch=(url,options)=>{if(failMeOnce&&String(url)==='/api/v1/auth/me'){failMeOnce=false;return Promise.resolve(new Response(JSON.stringify({detail:'Temporary test outage'}),{status:503,headers:{'Content-Type':'application/json'}}));}return originalFetch(url,options);};"})['identifier']
            browser.navigate(base+'/investigator/')
            browser.wait("!!document.querySelector('#retry')")
            browser.click('#retry')
            browser.wait("!!document.querySelector('#dockets') && document.querySelector('#dockets').innerText.includes('No dockets assigned')")
            browser.call('Page.removeScriptToEvaluateOnNewDocument',{'identifier':injection})
            browser.navigate(base+f'/investigator/docket?id={docket_id}')
            browser.wait("document.body.innerText.includes('Record unavailable')")
            assert not browser.js("document.body.innerText.includes('Browser verification note')")
            with lock:
                db.execute(text('RESET ROLE'))
                db.execute(UserRole.__table__.delete().where(UserRole.user_id==user.id))
                denied_role=db.scalar(select(Role.id).where(Role.code=='CHARGE_OFFICER'))
                db.add(UserRole(user_id=user.id,role_id=denied_role));db.commit()
                db.execute(text('SET LOCAL ROLE saps_api'));db.commit()
            browser.navigate(base+'/investigator/')
            browser.wait("document.body.innerText.includes('Permission denied')")
            # The same real MFA session now has the fixture's charge-officer role.
            browser.navigate(base+'/officer/')
            browser.wait("!document.querySelector('#app-view').hidden && !document.querySelector('#intake-open').hidden")
            browser.click('#intake-open')
            for name,value in [('first_name','Walk'),('last_name','In'),('phone_number','0123456789'),('crime_category','Theft'),
                               ('incident_description','Actual walk-in account'),('incident_location','Test location'),('incident_province','Gauteng')]:
                browser.fill(f'#intake-content [name={name}]',value)
            browser.click('#intake-content input[type=checkbox]');browser.submit('#intake-content form')
            browser.wait("document.querySelector('#global-message').textContent.startsWith('In-station complaint registered:')")
            new_headers={'Authorization':'Bearer '+saved['access']}
            incoming=client.get('/api/v1/complaints/station',headers=new_headers).json()['items']
            walk=next(row for row in incoming if row['channel']=='IN_STATION')
            assert walk['station_id']==str(station.id) and walk['registered_by_officer_id']==str(officer.id)
            browser.click(f'[data-complaint-action=materials][data-id="{walk["id"]}"]')
            browser.wait("!!document.querySelector('#materials-content form[data-action=witness]')")
            browser.js("document.querySelector('#materials-content form[data-action=witness]').parentElement.open=true")
            for name,value in [('first_name','Actual'),('last_name','Witness'),('statement_text','Actual witness statement')]:
                browser.fill(f'#materials-content form[data-action=witness] [name={name}]',value)
            browser.submit('#materials-content form[data-action=witness]')
            browser.wait("document.querySelector('#materials-content').innerText.includes('Actual Witness')")
            browser.click('#materials-dialog .dialog-close')
            browser.click(f'[data-complaint-action=start][data-id="{walk["id"]}"]')
            browser.wait("document.querySelector('#complaint-dialog').open")
            browser.click('input[name=decision][value=ACCEPTED]');browser.submit('#complaint-action-form')
            browser.wait("document.querySelector('#global-message').textContent.includes('created and awaiting commander approval')")
            assert client.get('/api/v1/complaints/station',headers=new_headers).json()['items'][0]['cas_number']
            browser.click(f'[data-complaint-action=materials][data-id="{walk["id"]}"]')
            browser.wait("!!document.querySelector('#materials-content form[data-action=evidence]')")
            browser.js("document.querySelector('#materials-content form[data-action=evidence]').parentElement.open=true")
            for name,value in [('title','Actual initial item'),('description','Actual item description'),('storage_location','Locker A')]:
                browser.fill(f'#materials-content form[data-action=evidence] [name={name}]',value)
            browser.submit('#materials-content form[data-action=evidence]')
            browser.wait("document.querySelector('#materials-content [role=status]').textContent.startsWith('Evidence registered:')")
            browser.click('#materials-dialog .dialog-close')
            browser.navigate(base+'/investigator/')
            browser.wait("document.body.innerText.includes('Permission denied')")
            browser.click('#logout')
            browser.wait("document.querySelector('#notice').textContent==='You have signed out.'")
            assert browser.js("sessionStorage.getItem('saps_access_token')===null && sessionStorage.getItem('saps_refresh_token')===null")
            assert not browser.errors, 'Uncaught browser exceptions occurred'
            print('PASS: shared login/MFA, assigned queue/search/empty, note duplicate guard, evidence registration, status success/conflict, protected upload/hash metadata, custody success/conflict, real refresh/expiry/logout, revoked assignment/permission denial, desktop/mobile overflow checks.',flush=True)
            print('PASS: walk-in intake, actual witness statement, acceptance automatically allocates CAS/docket, initial evidence registration.',flush=True)
            print('Screenshots saved to the configured artifact directory.')
            browser.call('Browser.close')
            browser.connection.__exit__(None,None,None); browser = None
            process.wait(timeout=10); process = None
    finally:
        if browser:
            try: browser.call('Browser.close')
            except Exception: pass
            browser.connection.__exit__(None,None,None)
        if process:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=5)
        if server:
            server.should_exit=True
            thread.join(timeout=10)
        settings.evidence_storage_path=original_storage
        fixture.close()
        temporary_context.cleanup()


if __name__ == '__main__':
    check()
