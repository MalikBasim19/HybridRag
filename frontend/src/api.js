const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
export async function api(path, options) {
  const response = await fetch(`${base}${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}
