const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
export async function api(path, options) {
  const response = await fetch(`${base}${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

export async function streamChat(question, onEvent) {
  const response = await fetch(`${base}/api/chat/stream`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({question}),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  }
  if (!response.body) throw new Error('Streaming is unavailable in this browser');
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '', complete = false;
  try {
    while (!complete) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.type === 'error') throw new Error(event.message);
        onEvent(event);
        if (event.type === 'done') {complete = true; break;}
      }
      if (done && !complete) throw new Error('Answer interrupted; please retry.');
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
