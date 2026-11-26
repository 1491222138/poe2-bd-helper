/**
 * MV3 Service Worker：
 * - 点击工具栏图标打开侧边栏
 * - 代理跨域网络请求（poe.ninja / pobb.in / 官方市集 Trade2 API 带浏览器 Cookie）
 * - 协助打开官方市集或桌面协议标签页
 */

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error("SidePanel setup error:", error));

interface BgMessage {
  type: string;
  url?: string;
  body?: unknown;
}

chrome.runtime.onMessage.addListener((message: BgMessage, sender, sendResponse) => {
  if (message.type === "OPEN_SIDE_PANEL" && sender.tab?.id) {
    chrome.sidePanel
      .open({ tabId: sender.tab.id })
      .then(() => sendResponse({ ok: true }))
      .catch((err: Error) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (message.type === "OPEN_TAB" && message.url) {
    chrome.tabs
      .create({ url: message.url })
      .then((tab) => sendResponse({ ok: true, tabId: tab.id }))
      .catch((err: Error) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (message.type === "FETCH_TEXT" && message.url) {
    fetch(message.url)
      .then(async (res) => {
        const text = await res.text();
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status, error: `HTTP ${res.status}: 无法获取链接内容` });
          return;
        }
        sendResponse({ ok: true, text });
      })
      .catch((err: Error) => {
        sendResponse({ ok: false, error: `拉取链接失败：${err.message}` });
      });
    return true;
  }

  if (message.type === "FETCH_JSON" && message.url) {
    fetch(message.url, { headers: { Accept: "application/json" } })
      .then(async (res) => {
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status, error: `HTTP ${res.status}` });
          return;
        }
        const data = await res.json();
        sendResponse({ ok: true, data });
      })
      .catch((err: Error) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === "TRADE_API_POST" && message.url) {
    fetch(message.url, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(message.body),
    })
      .then(async (res) => {
        const text = await res.text();
        if (!res.ok) {
          sendResponse({
            ok: false,
            status: res.status,
            error: `官方市集返回 HTTP ${res.status}: ${text.slice(0, 220)}`,
          });
          return;
        }
        try {
          sendResponse({ ok: true, data: JSON.parse(text) });
        } catch {
          sendResponse({ ok: false, error: "官方市集返回的不是有效 JSON" });
        }
      })
      .catch((err: Error) => {
        sendResponse({ ok: false, error: `连接官方市集失败：${err.message}` });
      });
    return true;
  }

  if (message.type === "TRADE_API_GET" && message.url) {
    fetch(message.url, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    })
      .then(async (res) => {
        const text = await res.text();
        if (!res.ok) {
          sendResponse({
            ok: false,
            status: res.status,
            error: `HTTP ${res.status}: ${text.slice(0, 200)}`,
          });
          return;
        }
        try {
          sendResponse({ ok: true, data: JSON.parse(text) });
        } catch {
          sendResponse({ ok: false, error: "返回的不是有效 JSON" });
        }
      })
      .catch((err: Error) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  return false;
});
