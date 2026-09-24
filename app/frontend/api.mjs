export function errorMessage(status, body) {
  if (status === 401) return 'Sign-in or verification expired or was rejected. Please sign in again.';
  if (status === 403) return 'Your account does not have access to this action.';
  if (status === 404) return 'This record is unavailable or you do not have access to it.';
  if (status >= 500) return 'The service is unavailable. Please try again later. If you were submitting a complaint, check My complaints before submitting again.';
  if (Array.isArray(body?.detail)) return body.detail.map(e => `${e.loc?.slice(1).join(' / ') || 'Form'}: ${e.msg}`).join('\n');
  return typeof body?.detail === 'string' ? body.detail : 'The request could not be completed.';
}

export function createClient(fetcher = fetch) {
  let tokens = null;
  return {
    setTokens(value) { tokens = value; },
    clear() { tokens = null; },
    async request(path, {method = 'GET', body} = {}) {
      let response;
      try {
        response = await fetcher('/api/v1' + path, {method, cache: 'no-store', credentials: 'omit',
          headers: {'Content-Type': 'application/json', ...(tokens ? {Authorization: 'Bearer ' + tokens.access_token} : {})},
          ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      } catch { throw new Error('Connection failed. Check My complaints before retrying a submission.'); }
      if (response.status === 204) return null;
      const data = await response.json().catch(() => ({}));
      if (!response.ok) { const error = new Error(errorMessage(response.status, data)); error.status = response.status; throw error; }
      return data;
    },
    async logout() {
      if (tokens) await this.request('/auth/logout', {method: 'POST', body: {refresh_token: tokens.refresh_token}});
      tokens = null;
    }
  };
}
