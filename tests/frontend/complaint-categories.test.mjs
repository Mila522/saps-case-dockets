import test from 'node:test';
import assert from 'node:assert/strict';
import {CRIME_CATEGORIES, categoryChoices, bindCrimeCategory, complaintPayload, stationLabel, stationAddress} from '../../app/frontend/complaint-fields.mjs';

test('all predefined categories and Other serialize within the storage contract',()=>{
  assert.deepEqual(categoryChoices[0],['','Select a crime category']);
  for(const crime_category of CRIME_CATEGORIES.filter(value=>value!=='Other')) {
    assert.deepEqual(complaintPayload({crime_category,crime_category_other:'stale value',incident_location:'Actual street'}),{crime_category,incident_location:'Actual street'});
  }
  assert.equal(complaintPayload({crime_category:'Other',crime_category_other:'  Unlisted incident  '}).crime_category,'Other: Unlisted incident');
  assert.equal(complaintPayload({crime_category:'Other',crime_category_other:'x'.repeat(143)}).crime_category.length,150);
  for(const crime_category_other of ['', '  ', 'x'.repeat(144)])
    assert.throws(()=>complaintPayload({crime_category:'Other',crime_category_other}),/specify/);
  assert.throws(()=>complaintPayload({crime_category:''}),/Select/);
});

test('Other visibility/validation toggles and switching away clears old input',()=>{
  class Node {
    constructor(){this.value='';this.children=[];this.handlers={};}
    append(...nodes){this.children.push(...nodes);}
    after(node){this.next=node;}
    setAttribute(){}
    setCustomValidity(value){this.validation=value;}
    addEventListener(name,handler){this.handlers[name]=handler;}
  }
  const select=new Node();select.id='category';
  const form=new Node();form.querySelector=()=>select;
  const previous=globalThis.document;
  globalThis.document={createElement:()=>new Node()};
  try{
    bindCrimeCategory(form);
    const group=select.next, input=group.children[1];
    assert(group.hidden && input.disabled && !input.required);
    select.value='Other';select.handlers.change();
    assert(!group.hidden && input.required && !input.disabled && input.validation);
    input.value='An old Other entry';input.handlers.input();assert.equal(input.validation,'');
    select.value='Fraud';select.handlers.change();
    assert(group.hidden && input.disabled && !input.required);
    assert.equal(input.value,'');assert.equal(input.validation,'');
    assert.equal(complaintPayload({crime_category:select.value,crime_category_other:input.value}).crime_category,'Fraud');
  }finally{globalThis.document=previous;}
});

test('station labels include locality and address formatting has no incident side effects',()=>{
  const station={name:'Point',city:'Durban',province:'KwaZulu-Natal',address_line_1:'165 Prince Street'};
  const incident={incident_location:'My incident street',incident_city:'Other city'};
  assert.equal(stationLabel(station),'Point — Durban, KwaZulu-Natal');
  assert.equal(stationAddress(station),'165 Prince Street, Durban, KwaZulu-Natal');
  assert.equal(stationAddress(null),'');
  assert.deepEqual(incident,{incident_location:'My incident street',incident_city:'Other city'});
});
