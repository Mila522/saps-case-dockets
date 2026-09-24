// Literal values from NoteRequest, EvidenceRequest and CustodyRequest. Checked by tests.
export const NOTE_TYPES = ['GENERAL','PROGRESS','LEAD','INTERVIEW','FORENSIC','ADMINISTRATIVE','CORRECTION'];
export const EVIDENCE_TYPES = ['DOCUMENT','IMAGE','VIDEO','AUDIO','PHYSICAL_OBJECT','DIGITAL_DEVICE','OTHER'];
export const CUSTODY_TYPES = ['TRANSFERRED','ANALYSIS_STARTED','ANALYSIS_COMPLETED','RELEASED','DISPOSED'];
export function custodyActions(status) {
  if (status === 'UNDER_ANALYSIS') return ['ANALYSIS_COMPLETED'];
  return ['IN_CUSTODY','TRANSFERRED'].includes(status) ? CUSTODY_TYPES.filter(x=>x!=='ANALYSIS_COMPLETED') : [];
}
export const writable = docket => ['APPROVED','ACTIVE','ON_HOLD'].includes(docket.status);
