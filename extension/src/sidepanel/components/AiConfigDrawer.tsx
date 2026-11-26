import React, { useState } from "react";
import {
  EXTENSION_LLM_PRESETS,
  type ExtensionLlmConfig,
} from "../../ai/llmStreamClient";
import type { PatchDictData } from "../../parsers/patchDict";

interface AiConfigDrawerProps {
  open: boolean;
  onClose: () => void;
  config: ExtensionLlmConfig;
  onSaveConfig: (cfg: ExtensionLlmConfig) => Promise<void>;
  patchDict: PatchDictData | null;
  buildTreeVersion?: string;
  dictUpdating: boolean;
  dictMessage: string;
  onUpdatePatchDict: () => Promise<void>;
  backendOnline: boolean;
}

export const AiConfigDrawer: React.FC<AiConfigDrawerProps> = ({
  open,
  onClose,
  config,
  onSaveConfig,
  patchDict,
  buildTreeVersion,
  dictUpdating,
  dictMessage,
  onUpdatePatchDict,
  backendOnline,
}) => {
  const [draft, setDraft] = useState<ExtensionLlmConfig>(config);
  const [savedTip, setSavedTip] = useState("");

  React.useEffect(() => {
    setDraft(config);
  }, [config, open]);

  if (!open) return null;

  const handleProviderChange = (provider: ExtensionLlmConfig["provider"]) => {
    const preset = EXTENSION_LLM_PRESETS[provider];
    setDraft((prev) => ({
      ...prev,
      provider,
      baseUrl: preset.baseUrl || prev.baseUrl,
      model: preset.model || prev.model,
    }));
  };

  const handleSave = async () => {
    await onSaveConfig(draft);
    setSavedTip("已保存到本机扩展存储");
    window.setTimeout(() => setSavedTip(""), 2000);
  };

  return (
    <section className="card gold" style={{ marginBottom: 10 }}>
      <div className="card-title">
        <span>⚙️ 独立插件 AI 与版本词典设置</span>
        <button type="button" className="btn sm" onClick={onClose}>
          收起
        </button>
      </div>

      {/* 离线版本词典状态 & 3 层热更新 */}
      <div className="gear-card" style={{ marginBottom: 10 }}>
        <div className="row between">
          <div>
            <strong>📚 PoE2 离线版本词典</strong>
            <div className="muted" style={{ fontSize: 11 }}>
              {patchDict
                ? `v${patchDict.treeVersion} · 天赋 ${patchDict.nodeCount} · 宝石 ${patchDict.gemCount} (${
                    patchDict.source === "github-upstream"
                      ? "GitHub 热更"
                      : patchDict.source === "local-backend"
                      ? "本地后端同步"
                      : "插件内置"
                  })`
                : "未加载词典"}
              {buildTreeVersion && patchDict && buildTreeVersion !== patchDict.treeVersion
                ? ` · 当前 BD 为 v${buildTreeVersion}`
                : ""}
            </div>
          </div>
          <button type="button" className="btn sm primary" disabled={dictUpdating} onClick={() => void onUpdatePatchDict()}>
            {dictUpdating ? "更新中..." : "🔄 热更新词典"}
          </button>
        </div>
        {dictMessage && (
          <div className="muted" style={{ fontSize: 11, color: "var(--green)" }}>
            {dictMessage}
          </div>
        )}
      </div>

      {/* 独立 AI 配置 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label className="row" style={{ gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
          />
          <span>启用插件直连流式 AI（离线不依赖本地桌面端；在线时优先复用桌面端设置与 PoB2 引擎实算）</span>
        </label>

        <div className="muted" style={{ fontSize: 11 }}>
          当前连接：{backendOnline ? "本地后端在线（支持满血 PoB2 引擎 + 消融 + 流式 AI）" : "纯插件离线模式（离线词典 + 扩展直连流式 AI）"}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          <select
            className="select"
            value={draft.provider}
            onChange={(e) => handleProviderChange(e.target.value as ExtensionLlmConfig["provider"])}
          >
            {Object.entries(EXTENSION_LLM_PRESETS).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
          <input
            type="text"
            className="input"
            placeholder="模型名 (如 deepseek-chat)"
            value={draft.model}
            onChange={(e) => setDraft({ ...draft, model: e.target.value })}
          />
        </div>

        <input
          type="text"
          className="input"
          placeholder="API Base URL (OpenAI 兼容，如 https://api.deepseek.com/v1)"
          value={draft.baseUrl}
          onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
        />

        <input
          type="password"
          className="input"
          placeholder="API Key（仅保存在本机 chrome.storage.local，本地 Ollama 可留空）"
          value={draft.apiKey}
          onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
        />

        <label className="row" style={{ gap: 6, cursor: "pointer", fontSize: 12 }}>
          <input
            type="checkbox"
            checked={draft.autoRunOnImport}
            onChange={(e) => setDraft({ ...draft, autoRunOnImport: e.target.checked })}
          />
          <span>导入目标 BD 后自动触发 AI 流式本质拆解</span>
        </label>

        <div className="row between">
          <span className="muted" style={{ fontSize: 11, color: "var(--green)" }}>
            {savedTip}
          </span>
          <button type="button" className="btn sm primary" onClick={() => void handleSave()}>
            💾 保存设置
          </button>
        </div>
      </div>
    </section>
  );
};
