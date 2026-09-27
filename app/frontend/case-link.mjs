// A case UUID is navigation only, never an authorization credential.
export function complaintFromHash(hash) {
  const match = /^#complaint=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.exec(hash);
  return match ? match[1] : null;
}
