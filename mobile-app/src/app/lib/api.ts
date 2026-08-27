// 鍚庣 API 瀹㈡埛绔?鈥?缁熶竴 token 娉ㄥ叆 + 401 鑷姩鐧诲嚭 + SSE 娴佸紡鑱婂ぉ
const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000';

function getToken(): string | null {
  return localStorage.getItem('access_token');
}

function clearAuth() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('current_username');
  localStorage.removeItem('mobile_session_id');
}

function notifyAuthExpired() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mobile-auth-expired'));
  }
}

async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (options.body && !headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (response.status === 401) {
    clearAuth();
    notifyAuthExpired();
    throw new Error('登录已失效');
  }

  const contentType = response.headers.get('content-type') || '';
  const payload: any = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail = payload && typeof payload === 'object' ? payload.detail : payload;
    const message = typeof detail === 'string'
      ? detail
      : detail
        ? JSON.stringify(detail)
        : response.statusText || '请求失败';
    throw new Error(message);
  }
  return payload as T;
}
// 鈹€鈹€鈹€ SSE 浜嬩欢绫诲瀷瀹氫箟锛堜笌鍚庣 _chat_sse_generator 瀵归綈锛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
export type SSEEvent =
  | { type: 'status'; message: string }
  | { type: 'maddx_step'; phase: string; [k: string]: any }
  | { type: 'rumor_step'; phase: string; [k: string]: any }
  | { type: 'hallucination_check'; report: any }
  | { type: 'done';
      answer: string;
      images?: string[];
      is_finished?: boolean;
      options?: string[];
      turn_count?: number;
      current_slots?: Record<string, any>;
      route?: string;
      trace_data?: Record<string, any>;
      run_id?: string;
      state_version?: number;
    }
  | { type: 'error'; message: string; status?: number };

export interface ChatStreamPayload {
  query: string;
  session_id?: number;
  messages_history?: Array<{ role: string; content: string }>;
  turn_count?: number;
  current_slots?: Record<string, any>;
  current_route?: string;
  image_data?: string | number | null;
  vision_context?: any;
  med_precheck?: any;
}

/**
 * 娴佸紡鍙戦€?chat銆傚洖璋冮噷閫愭潯鏀跺埌 SSE 浜嬩欢锛宔nd 鏃惰繑鍥?abort handle銆? *
 * 浣跨敤锛? *   const ctrl = api.sendChatStream(payload, (evt) => { ... });
 *   await ctrl.done;
 *   ctrl.abort();  // 鎻愬墠涓
 */
export function sendChatStream(
  payload: ChatStreamPayload,
  onEvent: (evt: SSEEvent) => void,
): { done: Promise<void>; abort: () => void } {
  const controller = new AbortController();
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const done = (async () => {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          // 鍚庣 ChatRequest 瀛楁锛堜笌 api_server.py 涓ユ牸涓€鑷达級
          session_id: payload.session_id ?? 0,
          query: payload.query,
          messages_history: payload.messages_history ?? [],
          turn_count: payload.turn_count ?? 0,
          current_slots: payload.current_slots ?? {},
          current_route: payload.current_route ?? '',
          image_data: payload.image_data ?? null,
          vision_context: payload.vision_context ?? null,
          med_precheck: payload.med_precheck ?? null,
        }),
        signal: controller.signal,
      });
    } catch (err) {
      if ((err as any)?.name !== 'AbortError') {
        onEvent({ type: 'error', message: '网络连接失败，请检查后端服务' });
      }
      return;
    }

    if (response.status === 401) {
      clearAuth();
      notifyAuthExpired();
      onEvent({ type: 'error', message: '登录已失效，请重新登录' });
      return;
    }
    if (response.status === 409) {
      let detail = '该会话正在生成中，请稍后再试';
      try {
        const payload = await response.json();
        if (payload?.detail) detail = payload.detail;
      } catch {
        /* ignore */
      }
      onEvent({ type: 'error', message: detail, status: response.status });
      return;
    }
    if (!response.ok || !response.body) {
      onEvent({ type: 'error', message: `请求失败 (HTTP ${response.status})`, status: response.status });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err) {
        if ((err as any)?.name === 'AbortError') return;
        onEvent({ type: 'error', message: '流式读取异常' });
        return;
      }
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data: ')) continue;
        try {
          const evt = JSON.parse(line.slice(6)) as SSEEvent;
          onEvent(evt);
        } catch {
          /* 蹇界暐鎹熷潖鐨?SSE 琛?*/
        }
      }
    }
  })();

  return { done, abort: () => controller.abort() };
}

// 鈹€鈹€鈹€ 鍚勪笟鍔＄鐐?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
export const api = {
  // 璁よ瘉
  login: (username: string, password: string) =>
    request<{ access_token: string; username: string }>('/api/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  register: (username: string, password: string) =>
    request('/api/register', { method: 'POST', body: JSON.stringify({ username, password }) }),

  // 涓婚〉 / 妗ｆ
  getDashboard: () => request<any>('/api/home/dashboard'),
  getProfile: () => request<any>('/api/profile'),
  getInsights: () => request<any>('/api/profile/ai-insights'),
  saveProfile: (profileData: Record<string, any>) =>
    request<{ message: string; status: string }>('/api/profile', {
      method: 'POST',
      body: JSON.stringify({ profile_data: profileData }),
    }),

  // 浼氳瘽
  getSessions: () => request<any[]>('/api/sessions'),
  createSession: () => request<any>('/api/sessions', { method: 'POST' }),
  getSessionMessages: (sessionId: number) =>
    request<any[]>(`/api/sessions/${sessionId}/messages`),

  // 鐭ヨ瘑 / 鏂囩珷
  getArticles: () => request<any[]>('/api/articles'),
  searchArticles: (params: { q?: string; category?: string; tag?: string; entity?: string; sort?: string; page?: number; page_size?: number } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && String(value).trim() !== '') q.set(key, String(value));
    });
    const query = q.toString();
    return request<any>(`/api/articles/search${query ? `?${query}` : ''}`);
  },
  getHotArticles: (refresh = false) =>
    request<any>(`/api/articles/hot-realtime${refresh ? '?refresh=true' : ''}`),
  getRecommendedArticles: () => request<any>('/api/articles/recommended'),
  getArticleDetail: (id: number) => request<any>(`/api/articles/${id}`),
  getHealthArticles: (params: { q?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.q && params.q.trim()) q.set('q', params.q.trim());
    if (params.limit) q.set('limit', String(params.limit));
    if (params.offset) q.set('offset', String(params.offset));
    const query = q.toString();
    return request<any>(`/api/health-articles${query ? `?${query}` : ''}`);
  },
  getHealthArticleDetail: (id: number) => request<any>(`/api/health-articles/${id}`),
  likeArticle: (id: number) => request(`/api/articles/${id}/like`, { method: 'POST' }),
  favoriteArticle: (id: number) => request(`/api/articles/${id}/favorite`, { method: 'POST' }),
  unfavoriteArticle: (id: number) => request(`/api/articles/${id}/favorite`, { method: 'DELETE' }),
  getFavoriteArticles: () => request<any>('/api/articles/favorites'),
  trackArticle: (payload: { event_type: string; article_id?: number; duration_ms?: number; query?: string; meta_data?: Record<string, any> }) =>
    request('/api/articles/track', { method: 'POST', body: JSON.stringify(payload) }),

  // 鎵撳崱锛堟瘡鏃ユ墦鍗¤褰曪級
  getTodayCheckins: () => request<any>('/api/checkins/today'),
  getCheckinItems: () => request<any>('/api/checkins/items'),
  getCheckinHistory: (itemCode: string, from?: string, to?: string) => {
    const q = new URLSearchParams();
    if (from) q.set('from', from);
    if (to) q.set('to', to);
    const query = q.toString();
    return request<any>(`/api/checkins/history/${encodeURIComponent(itemCode)}${query ? `?${query}` : ''}`);
  },
  getCheckinStats: (itemCode: string) =>
    request<any>(`/api/checkins/stats/${encodeURIComponent(itemCode)}`),
  saveCheckin: (payload: Record<string, any>) =>
    request('/api/checkins', { method: 'POST', body: JSON.stringify(payload) }),
  deleteCheckin: (itemCode: string, checkinDate?: string) =>
    request(
      `/api/checkins/${itemCode}${checkinDate ? `?checkin_date=${encodeURIComponent(checkinDate)}` : ''}`,
      { method: 'DELETE' },
    ),
  // 馃啎 鑷畾涔夋墦鍗￠」 CRUD
  createCheckinItem: (payload: { name: string; icon?: string; icon_bg?: string; category?: string; points?: number }) =>
    request<any>('/api/checkins/items', { method: 'POST', body: JSON.stringify(payload) }),
  updateCheckinItem: (itemCode: string, payload: Partial<{ name: string; icon: string; icon_bg: string; category: string; points: number; is_active: boolean }>) =>
    request<any>(`/api/checkins/items/${encodeURIComponent(itemCode)}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteCheckinItem: (itemCode: string) =>
    request<any>(`/api/checkins/items/${encodeURIComponent(itemCode)}`, { method: 'DELETE' }),

  // 馃啎 鐭ヨ瘑鍥捐氨
  graphPopular: (limit = 8) => request<any>(`/api/graph/popular?limit=${limit}`),
  graphSearch: (params: { keyword: string; main_type?: string; target_types?: string; depth?: number; max_nodes?: number }) => {
    const q = new URLSearchParams({
      keyword: params.keyword,
      main_type: params.main_type ?? '鍏ㄩ儴',
      target_types: params.target_types ?? '鍏ㄩ儴',
      depth: String(params.depth ?? 1),
    });
    if (params.max_nodes) q.set('max_nodes', String(params.max_nodes));
    return request<any>(`/api/graph/search?${q.toString()}`);
  },
  graphExplain: (name: string, label?: string) => {
    const q = new URLSearchParams({ name });
    if (label) q.set('label', label);
    return request<any>(`/api/graph/explain?${q.toString()}`);
  },

  // 鎺ㄨ崘 query / 鍥剧墖
  getRecommendQueries: () => request<any>('/api/recommend_queries'),
  uploadImage: (imageBase64: string, sessionId?: number) =>
    request<{ file_id?: number; url: string | null; storage_key?: string; mime_type?: string; size?: number }>('/api/upload_image', {
      method: 'POST',
      body: JSON.stringify({ image_base64: imageBase64, session_id: sessionId || null }),
    }),

  // 流式聊天（SSE）
  sendChatStream,

  // 宸ュ叿
  clearAuth,
  getToken,
  API_BASE,
};

