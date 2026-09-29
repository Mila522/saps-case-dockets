"""Real Edge complaint-form checks; fake authentication email, rollback-only data."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts')]
from check_investigator_browser import Browser
from app.main import app
from app.core.config import settings
from app.db.session import get_db
from app.modules.authentication import mail
from app.modules.access.models import User
from app.modules.complaints.models import Complaint
from app.modules.stations.models import Station
from app.modules.stations.seed_kzn import seed
from sqlalchemy import select
import uvicorn
from auth_mailbox import send, code_for
from test_authentication import enroll
from test_decisions_dockets import workflow_context, officer_account


def finish_process(process):
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


def check():
    edge = Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    if not edge.exists(): raise SystemExit('Microsoft Edge is unavailable; browser checks not run.')
    with patch.object(mail, 'send_code', send), patch.object(mail, 'send_case_message', return_value=('ACCEPTED', 'SMTP_ACCEPTED')), patch.object(settings, 'email_resend_seconds', 0):
        fixture = workflow_context.__wrapped__()
        client, db = next(fixture)
        browser = process = server = None
        evidence_temp=tempfile.TemporaryDirectory(prefix='complaint-files-')
        storage_patch=patch.object(settings,'evidence_storage_path',Path(evidence_temp.name));storage_patch.start()
        try:
            records = seed(db); db.commit()
            point = db.get(Station, uuid.UUID(records[0]['id']))
            citizen, _, _ = enroll(client)
            _, staff_id, _ = officer_account(client, db, 'CHARGE_OFFICER', point)
            staff = db.get(User, staff_id)
            lock = threading.Lock()
            def isolated_db():
                with lock: yield db
            app.dependency_overrides[get_db] = isolated_db
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            deadline = time.monotonic()+10
            while not server.started and time.monotonic()<deadline: time.sleep(.1)
            assert server.started
            base = f'http://127.0.0.1:{port}'
            with tempfile.TemporaryDirectory(prefix='complaint-browser-', ignore_cleanup_errors=True) as temporary:
                profile = Path(temporary) / 'profile'
                process = subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--no-first-run',
                    '--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                port_file = profile / 'DevToolsActivePort'
                deadline=time.monotonic()+15
                while not port_file.exists() and time.monotonic()<deadline: time.sleep(.1)
                debug_port=port_file.read_text().splitlines()[0]
                pages=json.load(urlopen(f'http://127.0.0.1:{debug_port}/json/list'))
                browser=Browser(next(page['webSocketDebuggerUrl'] for page in pages if page['type']=='page'))
                browser.navigate(base+'/portal/')
                browser.wait("!!document.querySelector('#field-username')")
                browser.fill('#field-username',citizen['username']); browser.fill('#field-password',citizen['password'])
                browser.submit('#view form')
                browser.wait("document.querySelector('#view h2').textContent==='My complaints'")
                browser.wait("!document.querySelector('#new-tab').disabled")
                browser.click('#new-tab')
                browser.wait("!!document.querySelector('#field-station_id') && !document.querySelector('#field-station_id').disabled")
                assert browser.js("document.querySelector('#field-station_id').value==='' && document.querySelector('#field-station_id option').textContent==='Select a receiving station'")
                assert browser.js("document.querySelector('#field-crime_category').value==='' && document.querySelector('#field-crime_category option').textContent==='Select a crime category'")
                browser.fill('#field-incident_location','Actual incident street')
                browser.fill('#field-incident_city','Incident city')
                browser.fill('#field-incident_province','Gauteng')
                browser.fill('#field-incident_description','Statement kept after validation failure')
                browser.fill('#field-station_id',str(point.id))
                assert browser.js("document.querySelector('.station-address').textContent.includes('165 Prince Street')")
                assert browser.js("document.querySelector('#field-station_id').selectedOptions[0].textContent.includes('Durban')")
                assert browser.js("document.querySelector('#field-incident_location').value==='Actual incident street' && document.querySelector('#field-incident_city').value==='Incident city' && document.querySelector('#field-incident_province').value==='Gauteng'")
                browser.fill('#field-crime_category','Other')
                assert browser.js("document.querySelector('[name=crime_category_other]').required && !document.querySelector('#view form').checkValidity()")
                browser.fill('[name=crime_category_other]','Stale category')
                browser.fill('#field-crime_category','Fraud')
                assert browser.js("document.querySelector('[name=crime_category_other]').disabled && document.querySelector('[name=crime_category_other]').value===''")
                # A real API rejection must not destroy the entered values.
                point.is_active=False; db.commit()
                browser.submit('#view form')
                browser.wait("document.querySelector('#message').textContent.includes('unavailable')")
                assert browser.js("document.querySelector('#field-incident_description').value==='Statement kept after validation failure' && document.querySelector('#field-crime_category').value==='Fraud'")
                point.is_active=True; db.commit()
                browser.submit('#view form')
                browser.wait("document.querySelector('#view h2').textContent==='Complaint submitted'")
                saved=db.scalar(select(Complaint).where(Complaint.incident_description=='Statement kept after validation failure'))
                assert saved.station_id==point.id and saved.crime_category=='Fraud' and saved.incident_location=='Actual incident street'
                browser.wait("!document.querySelector('#new-tab').disabled")
                browser.click('#new-tab')
                browser.wait("!!document.querySelector('#field-station_id') && !document.querySelector('#field-station_id').disabled")
                for name,value in {'station_id':str(point.id),'crime_category':'Other','incident_location':'Another location','incident_province':'KwaZulu-Natal','incident_description':'Other complaint'}.items():
                    browser.fill('#field-'+name,value)
                browser.fill('[name=crime_category_other]','Specific unlisted category')
                browser.submit('#view form')
                browser.wait("document.querySelector('#view h2').textContent==='Complaint submitted'")
                assert db.scalar(select(Complaint.crime_category).where(Complaint.incident_description=='Other complaint'))=='Other: Specific unlisted category'
                # Vehicle registration requires both a plate and an actual private upload.
                browser.wait("!document.querySelector('#new-tab').disabled");browser.click('#new-tab')
                browser.wait("!!document.querySelector('#field-station_id') && !document.querySelector('#field-station_id').disabled")
                for name,value in {'station_id':str(point.id),'crime_category':'Vehicle theft','incident_location':'Vehicle incident location','incident_province':'KwaZulu-Natal','incident_description':'Vehicle browser test'}.items():
                    browser.fill('#field-'+name,value)
                assert browser.js("document.querySelector('[data-plate]').required && document.querySelector('[data-registration]').required && !document.querySelector('#view form').checkValidity()")
                browser.fill('[data-plate]','ND 123 456')
                browser.js("(()=>{const transfer=new DataTransfer();transfer.items.add(new File(['%PDF-test'],'registration.pdf',{type:'application/pdf'}));document.querySelector('[data-registration]').files=transfer.files;})()")
                browser.js("document.querySelector('[data-witnesses]').parentElement.open=true")
                browser.click('[data-add-witness]');browser.fill('[data-key=first_name]','Test');browser.fill('[data-key=last_name]','Witness')
                artifacts=ROOT/'.cache'/'requested-preview';artifacts.mkdir(parents=True,exist_ok=True)
                browser.screenshot(artifacts/'vehicle-form.png')
                browser.submit('#view form');browser.wait("document.querySelector('#view h2').textContent==='Complaint submitted'")
                vehicle=db.scalar(select(Complaint).where(Complaint.incident_description=='Vehicle browser test'))
                assert vehicle.vehicle_number_plate=='ND 123 456'
                browser.js("[...document.querySelectorAll('#view button')].find(b=>b.textContent==='Track this complaint').click()")
                browser.wait("document.querySelector('#view').textContent.includes('Download registration.pdf')")
                assert browser.js("document.querySelector('#view').textContent.includes('Download registration confirmation')")
                browser.screenshot(artifacts/'complaint-details.png')
                browser.click('#track-tab')
                browser.wait("!!document.querySelector('#field-reference_number')")
                assert browser.js("document.querySelector('#track-tab').closest('nav')!==null")
                browser.fill('#field-reference_number',vehicle.reference_number);browser.submit('#view form')
                browser.wait("document.querySelector('#view h2').textContent==='Complaint details'")
                browser.screenshot(artifacts/'track-reference-nav.png')
                browser.navigate(base+'/portal/home.html');browser.wait("!!document.querySelector('#stakeholder-title')")
                browser.call('Emulation.setDeviceMetricsOverride',{'width':1280,'height':850,'deviceScaleFactor':1,'mobile':False})
                browser.screenshot(artifacts/'home-desktop.png')
                browser.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':850,'deviceScaleFactor':1,'mobile':False})
                assert browser.js("document.documentElement.scrollWidth<=document.documentElement.clientWidth")
                browser.screenshot(artifacts/'home-mobile.png')
                browser.call('Emulation.setDeviceMetricsOverride',{'width':1280,'height':850,'deviceScaleFactor':1,'mobile':False})
                browser.navigate(base+'/officer/')
                browser.wait("document.readyState==='complete' && !!document.querySelector('#identifier')")
                browser.fill('#identifier',staff.username);browser.fill('#password','Testing-Password12!');browser.submit('#login-form')

                browser.wait("!document.querySelector('#app-view').hidden && !document.querySelector('#intake-open').hidden")
                browser.click('#intake-open')
                browser.wait("!!document.querySelector('#intake-content [name=crime_category]')")
                assert browser.js("document.querySelector('#intake-content [name=crime_category]').value===''")
                browser.fill('#intake-content [name=crime_category]','Other')
                assert browser.js("document.querySelector('#intake-content [name=crime_category_other]').required")
                browser.fill('#intake-content [name=crime_category_other]','Old staff category')
                browser.fill('#intake-content [name=crime_category]','Theft')
                assert browser.js("document.querySelector('#intake-content [name=crime_category_other]').disabled && document.querySelector('#intake-content [name=crime_category_other]').value===''")
                for width in (1280,390):
                    browser.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':850,'deviceScaleFactor':1,'mobile':False})
                    geometry=browser.js("(()=>{const d=document.querySelector('#intake-dialog'),r=d.getBoundingClientRect();return {left:r.left,right:r.right,viewport:document.documentElement.clientWidth,scroll:d.scrollWidth,client:d.clientWidth,padding:getComputedStyle(d.querySelector('.dialog-shell')).paddingLeft};})()")
                    assert abs(geometry['left']-(geometry['viewport']-geometry['right']))<3 and geometry['scroll']<=geometry['client']+1 and geometry['padding']!='0px', geometry
                    assert browser.js("[...document.querySelectorAll('#intake-dialog input,#intake-dialog select,#intake-dialog textarea')].every(n=>n.getBoundingClientRect().right<=document.querySelector('#intake-dialog').getBoundingClientRect().right)")
                assert not browser.errors
                print('PASS: email login, database station dropdown/address, explicit selection, independent incident fields, Other validation/switching, API rejection retains form, saved station/category/details, shared staff category form. No real email sent.')
                browser.call('Browser.close');browser.connection.__exit__(None,None,None);browser=None
                finish_process(process);process=None
        finally:
            if browser:
                try: browser.call('Browser.close')
                except Exception: pass
                browser.connection.__exit__(None,None,None)
            if process:
                finish_process(process)
            if server: server.should_exit=True;thread.join(timeout=10)
            fixture.close()
            storage_patch.stop();evidence_temp.cleanup()


if __name__=='__main__': check()
