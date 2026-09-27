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
from app.modules.refusals.models import RefusalReason
from test_authentication import registration
from auth_mailbox import send, code_for
from app.modules.authentication import mail
from test_decisions_dockets import workflow_context, officer_account, complaint_at_station


def finish_process(process):
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print('Edge cleanup timed out; continuing database rollback.', flush=True)


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
    original_mail, original_cooldown = mail.send_code, settings.email_resend_seconds
    original_case_mail = mail.send_case_message
    mail.send_case_message = lambda *args, **kwargs: ('ACCEPTED', 'SMTP_ACCEPTED')
    def slow_fake_mail(recipient, code):
        time.sleep(.5)  # Keep pending UI observable; no SMTP connection.
        send(recipient, code)
    mail.send_code = slow_fake_mail
    settings.email_resend_seconds = 0
    fixture = workflow_context.__wrapped__()
    client, db = next(fixture)
    original_storage = settings.evidence_storage_path
    server = process = browser = None
    artifacts = Path(os.environ.get('SAPS_BROWSER_ARTIFACTS', str(ROOT / 'docs' / 'investigator-preview')))
    artifacts.mkdir(parents=True, exist_ok=True)
    temporary_context = tempfile.TemporaryDirectory(prefix='investigator-browser-', ignore_cleanup_errors=True)
    try:
        with nullcontext(temporary_context.name) as temporary:
            temp = Path(temporary)
            settings.evidence_storage_path = temp / 'evidence'
            station = Station(station_code='UI-' + uuid.uuid4().hex[:8], name='Demonstration station', province='Test')
            db.add(station); db.commit()
            charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
            commander, commander_user_id, _ = officer_account(client, db, 'STATION_COMMANDER', station)
            _, admin_user_id, _ = officer_account(client, db, 'SYSTEM_ADMINISTRATOR', station)
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
            refused = complaint_at_station(client, db, station)
            refusal_reason = db.scalar(select(RefusalReason).where(RefusalReason.code == 'SUSPECT_UNKNOWN'))
            assert client.post(f'/api/v1/complaints/{refused.id}/decisions', headers=charge, json={'decision':'REFUSED','refusal_reason_id':str(refusal_reason.id)}).status_code == 201
            escalation_id = next(row['id'] for row in client.get('/api/v1/refusal-escalations', headers=commander).json() if row['complaint_id'] == str(refused.id))
            ordinary_refusal = complaint_at_station(client, db, station)
            ordinary_reason = db.scalar(select(RefusalReason).where(RefusalReason.code == 'DUPLICATE_COMPLAINT'))
            assert client.post(f'/api/v1/complaints/{ordinary_refusal.id}/decisions', headers=charge, json={'decision':'REFUSED','refusal_reason_id':str(ordinary_reason.id),'officer_notes':'Duplicate complaint checked'}).status_code == 201
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
            # A portal registration/login through the same fake-email backend.
            citizen = registration()
            browser.navigate(base + '/portal/')
            browser.wait("!!document.querySelector('#field-username')")
            browser.click('#register-tab')
            for key,value in citizen.items(): browser.fill('#field-'+key, value)
            browser.submit('#view form')
            browser.wait("!!document.querySelector('#field-code')")
            assert browser.js("document.querySelector('#view').textContent.includes('***@')")
            browser.wait("!document.querySelector('#resend-code').disabled")
            browser.click('#resend-code')
            assert browser.js("document.querySelector('#resend-code').disabled && document.querySelector('#resend-code').textContent.includes('Sending')")
            browser.wait("document.querySelector('#message').textContent.includes('A new code was submitted')")
            browser.fill('#field-code', '999999' if code_for(citizen['email']) != '999999' else '888888')
            browser.submit('#view form')
            browser.wait("document.querySelector('#message').textContent.includes('incorrect')")
            assert browser.js("!!document.querySelector('#field-code')")
            browser.fill('#field-code', code_for(citizen['email'])); browser.submit('#view form')
            browser.wait("document.querySelector('#view h2').textContent==='My complaints'")
            browser.wait("!document.querySelector('#logout').disabled")
            browser.click('#logout')
            browser.wait("!!document.querySelector('#field-username')")
            browser.fill('#field-username', citizen['username']); browser.fill('#field-password', citizen['password'])
            browser.submit('#view form')
            browser.wait("!!document.querySelector('#field-code')")
            browser.fill('#field-code', code_for(citizen['email'])); browser.submit('#view form')
            browser.wait("document.querySelector('#view h2').textContent==='My complaints'")
            browser.wait("!document.querySelector('#logout').disabled")
            browser.click('#logout')
            browser.navigate(base + '/investigator/')
            browser.wait("document.body.innerText.includes('Sign in securely')")
            browser.click('a[href="/officer/?workspace=investigator"]')
            browser.wait("document.readyState==='complete' && !!document.querySelector('#identifier')")
            browser.fill('#identifier', credentials['username']); browser.fill('#password', credentials['password'])
            browser.submit('#login-form')
            browser.wait("!document.querySelector('#mfa-form').hidden && document.querySelector('#email-guidance').textContent.includes('five minutes')")
            browser.wait("!document.querySelector('#resend-code').disabled")
            browser.click('#resend-code')
            assert browser.js("document.querySelector('#resend-code').disabled && document.querySelector('#mfa-form button[type=submit]').disabled")
            browser.wait("document.querySelector('#auth-message').textContent.includes('A new code was submitted')")
            browser.fill('#mfa-code', '999999' if code_for(credentials['email']) != '999999' else '888888')
            browser.submit('#mfa-form')
            browser.wait("document.querySelector('#auth-message').textContent.includes('incorrect')")
            assert browser.js("!document.querySelector('#mfa-form').hidden")
            browser.fill('#mfa-code', code_for(credentials['email'])); browser.submit('#mfa-form')
            browser.wait("location.pathname==='/investigator/' && !!document.querySelector('#dockets a')")
            assert browser.js("document.querySelector('#dockets').innerText.includes('Open docket')")
            browser.screenshot(artifacts / 'queue-desktop.png')
            browser.fill('#search','no-match'); browser.wait("document.querySelector('#dockets').innerText.includes('No dockets match')")
            browser.click('#clear'); browser.click('#dockets a')
            browser.wait("!!document.querySelector('#note-form')")
            assert browser.js("!!document.querySelector('#complainant-contact') && !!document.querySelector('#invitation-form')")
            assert browser.js("document.querySelector('#invitation-form').parentElement.open && !!document.querySelector('a[href=\"#complainant-contact\"]')")
            browser.fill('#starts_at','2099-01-15T10:30')
            browser.submit('#invitation-form')
            browser.wait("document.querySelector('#confirmation').open")
            browser.click('#confirmation button[value=confirm]')
            browser.wait("document.querySelector('#notice').textContent.includes('Invitation recorded')")
            assert browser.js("document.querySelector('#status-form').parentElement.open")
            assert browser.js("[...document.querySelector('#status').options].map(o=>o.value)")==['ON_HOLD','CLOSED']
            assert browser.js("document.querySelector('#reason').required")
            browser.js("document.querySelector('#note-form').parentElement.open=true")
            browser.fill('#content','  Browser verification note  ')
            # Dispatch in one browser task: the first response may replace the
            # form before a second round trip through the debugger completes.
            browser.js("(()=>{const form=document.querySelector('#note-form');form.requestSubmit();form.requestSubmit();})()")
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
            browser.wait("!!document.querySelector('#intake-content [name=station_id]') && !document.querySelector('#intake-content [name=station_id]').disabled")
            assert browser.js("document.querySelector('#intake-content [name=station_id]').value")==str(station.id)
            assert browser.js("document.querySelector('#intake-content [name=station_id]').options.length")==1
            assert browser.js("document.querySelector('#intake-content [name=station_id]').required")
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
            accepted = next(row for row in client.get('/api/v1/complaints/station',headers=new_headers).json()['items'] if row['id']==walk['id'])
            assert accepted['cas_number']
            # Success feedback precedes the asynchronous queue refresh. Wait for
            # the new row before opening its docket-dependent material actions.
            selector = f'[data-complaint-action=view][data-id="{walk["id"]}"]'
            browser.wait(f'!!document.querySelector({json.dumps(selector)})')
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
            # Finish this same walk-in complaint through commander and investigator UIs.
            def staff_login(user_id):
                staff=db.get(User,user_id)
                browser.navigate(base+'/officer/')
                browser.wait("document.readyState==='complete' && !!document.querySelector('#identifier')")
                browser.fill('#identifier',staff.username);browser.fill('#password','Testing-Password12!')
                browser.submit('#login-form');browser.wait("!document.querySelector('#mfa-form').hidden")
                browser.fill('#mfa-code',code_for(staff.email));browser.submit('#mfa-form')

            staff_login(commander_user_id)
            browser.wait("!document.querySelector('#app-view').hidden && !!document.querySelector('#complaint-table tr')")
            assert browser.js("document.querySelector('#complaint-table').innerText.includes('Receiving station: Demonstration station')")
            assert browser.js("!document.querySelector('[data-complaint-action=start]') && !document.querySelector('[data-complaint-action=decide]')")
            browser.click(f'[data-complaint-action=view][data-id="{ordinary_refusal.id}"]')
            browser.wait("!!document.querySelector('[data-review-action=ACKNOWLEDGE]')")
            browser.click('[data-review-action=ACKNOWLEDGE]')
            browser.wait("document.querySelector('#commander-refusal-review').textContent.includes('Refusal acknowledged.')")
            browser.click('[data-review-action=ESCALATE]')
            assert browser.js("!document.querySelector('#commander-escalation-reason').validity.valid")
            browser.fill('#commander-escalation-reason','NCC review requested by commander')
            browser.click('[data-review-action=ESCALATE]')
            browser.wait("document.querySelector('#commander-refusal-review').textContent.includes('Refusal escalated to NCC.')")
            assert browser.js("!document.querySelector('[data-review-action=ESCALATE]')")
            browser.click('#complaint-dialog .dialog-close')
            browser.click('[data-view=escalations]')
            acknowledge_selector=f'[data-escalation-action=acknowledge][data-id="{escalation_id}"]'
            resolve_selector=f'[data-escalation-action=resolve][data-id="{escalation_id}"]'
            browser.wait(f'!!document.querySelector({json.dumps(acknowledge_selector)})')
            browser.click(acknowledge_selector)
            browser.wait(f'!document.querySelector({json.dumps(acknowledge_selector)})')
            browser.click(resolve_selector)
            browser.fill('#resolution-notes','Commander browser verification')
            browser.submit('#resolve-form')
            browser.wait(f'!document.querySelector({json.dumps(resolve_selector)})')
            assert browser.js("document.querySelector('#escalation-list').textContent.includes('This escalation is resolved.')")
            browser.click('[data-view=dockets]')
            review_selector=f'[data-docket-action=review][data-id="{accepted["docket_id"]}"]'
            browser.wait(f'!!document.querySelector({json.dumps(review_selector)})')
            browser.click(review_selector)
            browser.click('input[name=docket-decision][value=APPROVED]');browser.submit('#docket-action-form')
            assign_selector=f'[data-docket-action=assign][data-id="{accepted["docket_id"]}"]'
            browser.wait(f'!!document.querySelector({json.dumps(assign_selector)})')
            browser.click(assign_selector);browser.fill('#investigator',str(destination.id))
            browser.fill('#assignment-reason','Investigate the recorded walk-in complaint')
            browser.submit('#assignment-form')
            browser.wait("document.querySelector('#global-message').textContent.includes('assigned')")
            browser.click('#logout-button');browser.wait("!document.querySelector('#auth-view').hidden")
            staff_login(destination.user_id)
            browser.wait("location.pathname==='/investigator/' && !!document.querySelector('#dockets a')")
            browser.click('#dockets a');browser.wait("!!document.querySelector('#status-form')")
            browser.js("document.querySelector('#note-form').parentElement.open=true")
            browser.fill('#content','Follow-up on the walk-in complaint');browser.submit('#note-form')
            browser.wait("document.querySelector('#notice').textContent==='Investigation note saved.'")
            for next_status,reason in [('ON_HOLD','Awaiting witness availability'),('ACTIVE','Witness available'),('CLOSED','Investigation concluded')]:
                browser.fill('#status',next_status);browser.fill('#reason',reason);browser.submit('#status-form')
                browser.wait("document.querySelector('#confirmation').open");browser.click('#confirmation button[value=confirm]')
                browser.wait("document.querySelector('#notice').textContent.includes('status updated')")
                assert browser.js('document.querySelector("#status-history").textContent.includes('+json.dumps(reason)+')')
            assert browser.js("!document.querySelector('#status-form') && !document.querySelector('#note-form') && !document.querySelector('#evidence-form')")
            assert not browser.errors, 'Uncaught browser exceptions occurred'
            browser.click('#logout')
            staff_login(admin_user_id)
            browser.wait("location.pathname==='/admin/' && !document.querySelector('#workspace').hidden")
            browser.click('#create-staff')
            admin_staff_name='browser_staff_'+uuid.uuid4().hex
            for key,value in {'username':admin_staff_name,'email':admin_staff_name+'@example.com','password':'Testing-Password12!',
                'role':'CHARGE_OFFICER','station_id':str(station.id),'service_number':'B-'+uuid.uuid4().hex,'rank':'Officer'}.items():
                browser.fill('#staff-form [name='+key+']',value)
            browser.submit('#staff-form')
            browser.wait("document.querySelector('#notice').textContent.includes('Staff account created')")
            browser.fill('#user-filters [name=q]',admin_staff_name);browser.submit('#user-filters')
            browser.wait("!!document.querySelector('[data-edit]') && document.querySelector('#user-count').textContent.startsWith('1 accounts')")
            browser.click('[data-edit]');browser.fill('#staff-form [name=is_active]','false');browser.submit('#staff-form')
            browser.wait("document.querySelector('#notice').textContent.includes('Staff account updated')")
            browser.click('#audit-tab');browser.wait("!!document.querySelector('[data-audit]')")
            browser.click('[data-audit]');browser.wait("!document.querySelector('#audit-detail').hidden")
            assert browser.js("document.querySelector('#audit-detail').textContent.includes('Sensitive case contents')")
            assert not browser.errors
            print('PASS: administrator shared email-code sign-in, staff create/search/deactivate and read-only audit detail; mocked email and rollback-only records.',flush=True)
            print('PASS: portal registration/login and shared staff password/email verification, assigned queue/search/empty, note duplicate guard, evidence registration, status success/conflict, protected upload/hash metadata, custody success/conflict, real refresh/expiry/logout, revoked assignment/permission denial, desktop/mobile overflow checks.',flush=True)
            print('PASS: explicit assigned walk-in station, witness statement, automatic CAS/docket, initial evidence, commander email login/approval/assignment, direct investigator staff login, note and ACTIVE/ON_HOLD/CLOSED status history; closed forms absent.',flush=True)
            print('Screenshots saved to the configured artifact directory.')
            browser.call('Browser.close')
            browser.connection.__exit__(None,None,None); browser = None
            finish_process(process)
            process = None
    finally:
        if browser:
            try: browser.call('Browser.close')
            except Exception: pass
            browser.connection.__exit__(None,None,None)
        if process:
            finish_process(process)
        if server:
            server.should_exit=True
            thread.join(timeout=10)
        settings.evidence_storage_path=original_storage
        mail.send_code = original_mail
        mail.send_case_message = original_case_mail
        settings.email_resend_seconds = original_cooldown
        fixture.close()
        temporary_context.cleanup()


if __name__ == '__main__':
    check()
