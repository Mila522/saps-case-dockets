import test from 'node:test';
import assert from 'node:assert/strict';
import {staffWorkspace} from '../../frontend/officer/assets/workspace.mjs';

const user=(role,permission)=>({roles:[{code:role}],permissions:[{code:permission}]});
test('shared staff sign-in selects fixed destinations from verified roles and permissions',()=>{
  for(const role of ['CHARGE_OFFICER','STATION_COMMANDER']) assert.equal(staffWorkspace(user(role,'complaint.view_station')),'/officer/');
  assert.equal(staffWorkspace(user('INVESTIGATING_OFFICER','docket.view_assigned')),'/investigator/');
  assert.equal(staffWorkspace({...user('COMPLAINANT','docket.view_assigned'),redirect:'https://attacker.invalid'}),null);
  assert.equal(staffWorkspace(user('INVESTIGATING_OFFICER','complaint.submit')),null);
  assert.equal(staffWorkspace(user('STATION_COMMANDER','complaint.submit')),null);
  assert.equal(staffWorkspace({roles:[{code:'SYSTEM_ADMINISTRATOR'}],permissions:[{code:'user.manage'},{code:'audit.view_all'}]}),'/admin/');
  assert.equal(staffWorkspace(user('SYSTEM_ADMINISTRATOR','user.manage')),null);
});
