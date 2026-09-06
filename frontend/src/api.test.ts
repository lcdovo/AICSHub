import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from './api';

describe('apiFetch', () => {
  afterEach(() => vi.restoreAllMocks());

  it('解包后端统一的 code/message/data 响应', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, message: 'success', data: { id: 'session-1' } }), { status: 200 })));

    await expect(apiFetch<{ id: string }>('/api/v1/chat/sessions')).resolves.toEqual({ id: 'session-1' });
  });

  it('把后端 detail 转换为可展示的错误', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '会话已关闭' }), { status: 400 })));

    await expect(apiFetch('/api/v1/chat/send')).rejects.toThrow('会话已关闭');
  });
});
