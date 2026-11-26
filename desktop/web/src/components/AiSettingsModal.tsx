import React, { useEffect, useState } from "react";
import type { LlmConfigPublic } from "../api/workbenchApi";

interface AiSettingsModalProps {
  open: boolean;
  config: LlmConfigPublic | null;
  onClose: () => void;
  onSave: (payload: {
    enabled: boolean;
    provider: string;
    baseUrl: string;
    apiKey?: string;
    model: string;
    temperature: number;
    clearApiKey?: boolean;
    test?: boolean;
  }) => Promise<{ testMessage?: string }>;
}

export const AiSettingsModal: React.FC<AiSettingsModalProps> = ({ open, config, onClose, onSave }) => {
  const [enabled, setEnabled] = useState(false);
  const [provider, setProvider] = useState("deepseek");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com/v1");
  const [model, setModel] = useState("deepseek-chat");
  const [apiKey, setApiKey] = useState("");
  const [temperature, setTemperature] = useState("0.3");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    if (!config || !open) return;
    setEnabled(config.enabled);
    setProvider(config.provider || "deepseek");
    setBaseUrl(config.baseUrl || "");
    setModel(config.model || "");
    setTemperature(String(config.temperature ?? 0.3));
    setApiKey("");
    setStatus(null);
  }, [config, open]);

  if (!open) return null;

  const presets = config?.presets || {
    deepseek: { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
    gemini: {
      label: "Gemini (OpenAI 兼容)",
      baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
      model: "gemini-2.5-flash",
    },
    openai: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
    ollama: { label: "本地 Ollama", baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:14b" },
    custom: { label: "自定义 OpenAI 兼容", baseUrl: "", model: "" },
  };

  function handleProviderChange(next: string) {
    setProvider(next);
    const p = presets[next];
    if (p && p.baseUrl) setBaseUrl(p.baseUrl);
    if (p && p.model) setModel(p.model);
  }

  async function submit(test: boolean) {
    setSaving(true);
    setStatus(null);
    try {
      const res = await onSave({
        enabled,
        provider,
        baseUrl: baseUrl.trim(),
        apiKey: apiKey.trim() || undefined,
        model: model.trim(),
        temperature: Number(temperature) || 0.3,
        test,
      });
      setApiKey("");
      setStatus({
        kind: "ok",
        text: test ? `连通测试成功：${res.testMessage || "OK"}` : "AI 配置已保存到本机 ~/.poe2-workbench/llm_config.json",
      });
    } catch (err) {
      setStatus({ kind: "error", text: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.65)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        className="card gold"
        style={{ width: 520, maxWidth: "100%", margin: 0, background: "#141821" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-header">
          <span className="card-title">🤖 大模型（LLM）深度解读配置</span>
          <button type="button" className="btn sm" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="muted" style={{ marginBottom: 12 }}>
          PoB2 引擎与本地规则继续负责精确算力；开启大模型后，可对目标 BD、13+ 颗珠宝协同与自身差距生成深度人话攻略。密钥仅保存在本机。
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label className="row" style={{ gap: 8, cursor: "pointer" }}>
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <strong>启用大模型深度解读</strong>
          </label>

          <div className="grid-2">
            <div>
              <div className="muted" style={{ marginBottom: 4 }}>接口预设</div>
              <select className="input" style={{ width: "100%" }} value={provider} onChange={(e) => handleProviderChange(e.target.value)}>
                {Object.entries(presets).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <div className="muted" style={{ marginBottom: 4 }}>模型名称 (Model)</div>
              <input className="input" style={{ width: "100%" }} value={model} onChange={(e) => setModel(e.target.value)} />
            </div>
          </div>

          <div>
            <div className="muted" style={{ marginBottom: 4 }}>API Base URL (OpenAI 兼容端点)</div>
            <input
              className="input"
              style={{ width: "100%" }}
              placeholder="https://api.deepseek.com/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>

          <div className="grid-2">
            <div>
              <div className="muted" style={{ marginBottom: 4 }}>
                API Key {config?.hasApiKey ? `(已存 ${config.apiKeyPreview}，留空保持不变)` : ""}
              </div>
              <input
                type="password"
                className="input"
                style={{ width: "100%" }}
                placeholder={provider === "ollama" ? "本地 Ollama 可留空" : "sk-..."}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>

            <div>
              <div className="muted" style={{ marginBottom: 4 }}>温度 (Temperature)</div>
              <input
                type="number"
                step={0.1}
                min={0}
                max={1.5}
                className="input"
                style={{ width: "100%" }}
                value={temperature}
                onChange={(e) => setTemperature(e.target.value)}
              />
            </div>
          </div>

          {status && (
            <div className={`banner ${status.kind === "ok" ? "success" : "error"}`}>
              <span>{status.text}</span>
            </div>
          )}

          <div className="row between" style={{ marginTop: 4 }}>
            <button type="button" className="btn sm" disabled={saving} onClick={() => void submit(true)}>
              {saving ? "测试中..." : "🔌 保存并测试连通性"}
            </button>
            <button type="button" className="btn primary" disabled={saving} onClick={() => void submit(false)}>
              {saving ? "保存中..." : "保存配置"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
