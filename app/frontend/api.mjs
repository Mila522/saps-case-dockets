export function errorMessage(status, body) {
  if (status === 401) return 'Your session expired or was rejected. Please sign in again.';
  if (status === 403) return 'Permission denied. Your account cannot perform this action.';
  if (status === 404) return 'This record is unavailable or you no longer have access to it.';
  if (status === 409) return 'The record changed or this action is no longer allowed. Review the refreshed record before trying again.';
  if (status === 413) return 'The file exceeds the server upload limit. Choose a smaller file.';
  if (status >= 500) return 'The service is unavailable. Refresh the record before retrying a submission; it may already have been saved.';
  if (Array.isArray(body?.detail)) return body.detail.map(e => `${e.loc?.slice(1).join(' / ') || 'Form'}: ${e.msg}`).join('\n');
  return typeof body?.detail === 'string' ? body.detail : 'The request could not be completed.';
}

// The complainant portal stays memory-only. C opts into B's existing tab session.
export function createClient(fetcher = fetch, {storage = null, onExpired = () => {}} = {}) {
  let tokens = null;
  let refreshing = null;
  let generation = 0;
  function current() {
    if (storage) {
      const access_token = storage.getItem('saps_access_token');
      const refresh_token = storage.getItem('saps_refresh_token');
      tokens = access_token && refresh_token ? {access_token, refresh_token} : null;
    }
    return tokens;
  }
  function setTokens(value) {
    tokens = value;
    if (storage) {
      storage.setItem('saps_access_token', value.access_token);
      storage.setItem('saps_refresh_token', value.refresh_token);
    }
  }
  function clear() {
    generation++;
    tokens = null;
    storage?.removeItem('saps_access_token');
    storage?.removeItem('saps_refresh_token');
  }
  async function send(path, {method = 'GET', body} = {}, access = null) {
    const multipart = body instanceof FormData;
    try {
      return await fetcher('/api/v1' + path, {method, cache: 'no-store', credentials: 'omit',
        headers: {...(body !== undefined && !multipart ? {'Content-Type': 'application/json'} : {}),
          ...(access ? {Authorization: 'Bearer ' + access} : {})},
        ...(body === undefined ? {} : {body: multipart ? body : JSON.stringify(body)})});
    } catch { throw new Error('Connection failed. Refresh the record before retrying a submission; it may already have been saved.'); }
  }
  async function refresh() {
    if (!refreshing) {
      const version = generation;
      refreshing = (async () => {
        const token = current()?.refresh_token;
        if (!token) throw new Error('Session expired');
        const response = await send('/auth/refresh', {method: 'POST', body: {refresh_token: token}});
        const data = await response.json().catch(() => null);
        if (!response.ok || !data?.access_token || !data?.refresh_token || version !== generation) throw new Error('Session expired');
        setTokens(data);
      })().finally(() => { refreshing = null; });
    }
    return refreshing;
  }
  async function request(path, options = {}) {
    const version = generation;
    const access = current()?.access_token;
    let response = await send(path, options, access);
    if (response.status === 401 && (!path.startsWith('/auth/') || path === '/auth/me') && current()?.refresh_token) {
      try {
        if (current()?.access_token === access) await refresh();
        response = await send(path, options, current()?.access_token);
      } catch { clear(); onExpired(); throw Object.assign(new Error(errorMessage(401)), {status: 401}); }
    }
    if (version !== generation) throw Object.assign(new Error(errorMessage(401)), {status: 401});
    if (response.status === 401 && access) { clear(); onExpired(); }
    if (response.status === 204) return null;
    if (response.ok && options.binary) return response.blob();
    const data = await response.json().catch(() => null);
    if (!response.ok) throw Object.assign(new Error(errorMessage(response.status, data)), {status: response.status, fields: Array.isArray(data?.detail) ? data.detail : []});
    if (data === null) throw new Error('The server returned an unreadable response. Refresh before retrying.');
    return data;
  }
  return {setTokens, clear, request, hasSession: () => Boolean(current()),
    async logout() {
      const session = current();
      clear();
      if (session) {
        const response = await send('/auth/logout', {method: 'POST', body: {refresh_token: session.refresh_token}}, session.access_token);
        if (!response.ok) throw new Error('Signed out on this device. Server revocation could not be confirmed.');
      }
    }
  };
}
