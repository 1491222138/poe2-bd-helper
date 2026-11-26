import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BilingualTranslator } from "@contracts";
import {
  desktopApi,
  type PassiveTreeLayout,
  type PassiveTreeLayoutNode,
  type PassiveTreePlan,
} from "../api/workbenchApi";

interface PassiveTreePanelProps {
  t: BilingualTranslator;
  targetPobCode: string;
  myPobCode?: string;
  characterLevel: number;
  backendOnline: boolean;
  onNotify?: (msg: string) => void;
}

type TreeViewMode = "level" | "full" | "diff";

interface CameraState {
  cx: number;
  cy: number;
  scale: number;
}

interface HoverState {
  node: PassiveTreeLayoutNode;
  clientX: number;
  clientY: number;
}

const NODE_WORLD_RADIUS: Record<PassiveTreeLayoutNode["k"], number> = {
  start: 145,
  asc_start: 115,
  keystone: 115,
  ascendancy: 92,
  notable: 78,
  jewel: 82,
  asc_small: 50,
  small: 46,
};

function drawOrbitEdge(
  ctx: CanvasRenderingContext2D,
  n1: PassiveTreeLayoutNode,
  n2: PassiveTreeLayoutNode,
  orbit: number,
  orbitRadii: number[]
) {
  const absOrb = Math.abs(orbit);
  const r = absOrb > 0 && absOrb < orbitRadii.length ? orbitRadii[absOrb] : 0;
  if (r > 0) {
    const dx = n2.x - n1.x;
    const dy = n2.y - n1.y;
    const dist = Math.hypot(dx, dy);
    if (dist > 1e-3 && dist < r * 2 - 1e-3) {
      const perp = Math.sqrt(Math.max(0, r * r - (dist * dist) / 4)) * (orbit > 0 ? 1 : -1);
      const cx = n1.x + dx / 2 + perp * (dy / dist);
      const cy = n1.y + dy / 2 - perp * (dx / dist);
      let a1 = Math.atan2(n1.y - cy, n1.x - cx);
      let a2 = Math.atan2(n2.y - cy, n2.x - cx);
      let diff = a2 - a1;
      while (diff > Math.PI) diff -= Math.PI * 2;
      while (diff < -Math.PI) diff += Math.PI * 2;
      const anticlockwise = diff < 0;
      ctx.beginPath();
      ctx.arc(cx, cy, r, a1, a2, anticlockwise);
      ctx.stroke();
      return;
    }
  }
  ctx.beginPath();
  ctx.moveTo(n1.x, n1.y);
  ctx.lineTo(n2.x, n2.y);
  ctx.stroke();
}

function drawDiamond(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x, y - r);
  ctx.lineTo(x + r, y);
  ctx.lineTo(x, y + r);
  ctx.lineTo(x - r, y);
  ctx.closePath();
}

function drawHexagon(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i - Math.PI / 6;
    const px = x + Math.cos(a) * r;
    const py = y + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
}

const PassiveTreePanelInner: React.FC<PassiveTreePanelProps> = ({
  t,
  targetPobCode,
  myPobCode,
  characterLevel,
  backendOnline,
  onNotify,
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [viewMode, setViewMode] = useState<TreeViewMode>("level");
  const [layout, setLayout] = useState<PassiveTreeLayout | null>(null);
  const [plan, setPlan] = useState<PassiveTreePlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);
  const [copiedKey, setCopiedKey] = useState<"full" | "pruned" | null>(null);
  const [camera, setCamera] = useState<CameraState>({ cx: 0, cy: 0, scale: 0.032 });

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef<{ active: boolean; lastX: number; lastY: number }>({
    active: false,
    lastX: 0,
    lastY: 0,
  });
  const cameraRef = useRef<CameraState>(camera);
  cameraRef.current = camera;
  const autoFittedBuildRef = useRef<string>("");

  // 拉取当前目标 BD + 等级裁剪方案 + 静态几何布局（等级变更防抖 160ms）
  useEffect(() => {
    if (!backendOnline || !targetPobCode?.trim()) {
      setPlan(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const nextPlan = await desktopApi.getPassiveTreePlan({
          targetPobCode,
          myPobCode: myPobCode?.trim() || undefined,
          characterLevel,
        });
        if (cancelled) return;
        setPlan(nextPlan);
        const nextLayout = await desktopApi.getTreeLayout(nextPlan.treeVersion || undefined);
        if (cancelled) return;
        setLayout(nextLayout);
      } catch (err) {
        if (!cancelled) {
          setError((err as Error).message || "加载天赋树失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 160);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [backendOnline, targetPobCode, myPobCode, characterLevel]);

  // 退出全屏快捷键 Esc
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  // 集合查找表（高频 Canvas 渲染 O(1) 查询）
  const sets = useMemo(() => {
    const empty = {
      allocated: new Set<string>(),
      keptMain: new Set<string>(),
      pruned: new Set<string>(),
      asc: new Set<string>(),
      ws1: new Set<string>(),
      ws2: new Set<string>(),
      anointed: new Set<string>(),
      toAdd: new Set<string>(),
      toRefund: new Set<string>(),
      sharedDiff: new Set<string>(),
    };
    if (!plan) return empty;
    return {
      allocated: new Set(plan.allocatedIds),
      keptMain: new Set(plan.keptMainIds),
      pruned: new Set(plan.prunedIds),
      asc: new Set(plan.ascendancyAllocatedIds),
      ws1: new Set(plan.ws1Ids),
      ws2: new Set(plan.ws2Ids),
      anointed: new Set(plan.anointedIds),
      toAdd: new Set(plan.diff?.toAddIds || []),
      toRefund: new Set(plan.diff?.toRefundIds || []),
      sharedDiff: new Set(plan.diff?.sharedIds || []),
    };
  }, [plan]);

  const effectiveMode: TreeViewMode = useMemo(() => {
    if (viewMode === "diff" && !plan?.diff) return plan && plan.missingPoints > 0 ? "level" : "full";
    if (viewMode === "level" && plan && plan.missingPoints <= 0) return "full";
    return viewMode;
  }, [viewMode, plan]);

  const fitNodesInView = useCallback(
    (nodeIds: string[], paddingRatio = 0.16) => {
      if (!layout || !wrapRef.current || nodeIds.length === 0) return;
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (const id of nodeIds) {
        const n = layout.nodes[id];
        if (!n) continue;
        if (n.x < minX) minX = n.x;
        if (n.x > maxX) maxX = n.x;
        if (n.y < minY) minY = n.y;
        if (n.y > maxY) maxY = n.y;
      }
      if (!Number.isFinite(minX)) return;
      const rect = wrapRef.current.getBoundingClientRect();
      const w = Math.max(300, rect.width || 800);
      const h = Math.max(260, rect.height || 440);
      const spanX = Math.max(1400, maxX - minX);
      const spanY = Math.max(1200, maxY - minY);
      const usableW = w * (1 - paddingRatio * 2);
      const usableH = h * (1 - paddingRatio * 2);
      const scale = Math.max(0.012, Math.min(0.16, Math.min(usableW / spanX, usableH / spanY)));
      setCamera({
        cx: (minX + maxX) / 2,
        cy: (minY + maxY) / 2,
        scale,
      });
    },
    [layout]
  );

  // 首次加载或切换目标 BD 时自动聚焦到主树已点区域
  useEffect(() => {
    if (!layout || !plan || collapsed) return;
    const key = `${targetPobCode.slice(0, 48)}:${targetPobCode.length}`;
    if (autoFittedBuildRef.current === key) return;
    autoFittedBuildRef.current = key;
    const focusIds = [...plan.mainAllocatedIds];
    if (plan.classStartId) focusIds.push(plan.classStartId);
    if (focusIds.length > 0) {
      window.requestAnimationFrame(() => fitNodesInView(focusIds, 0.14));
    }
  }, [layout, plan, collapsed, targetPobCode, fitNodesInView]);

  // 全屏切换时重新适应画布尺寸
  useEffect(() => {
    if (!layout || !plan || collapsed) return;
    const timer = window.setTimeout(() => {
      const focusIds = [...plan.mainAllocatedIds];
      if (plan.classStartId) focusIds.push(plan.classStartId);
      if (focusIds.length > 0) fitNodesInView(focusIds, 0.14);
    }, 40);
    return () => window.clearTimeout(timer);
  }, [fullscreen]); // eslint-disable-line react-hooks/exhaustive-deps

  const focusNodeById = useCallback(
    (nodeId: string) => {
      if (!layout) return;
      const n = layout.nodes[nodeId];
      if (!n) return;
      setHighlightNodeId(nodeId);
      setCamera((prev) => ({
        cx: n.x,
        cy: n.y,
        scale: Math.max(prev.scale, 0.085),
      }));
    },
    [layout]
  );

  const searchMatches = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q || !layout) return [];
    const results: Array<{ node: PassiveTreeLayoutNode; label: string; isAlloc: boolean }> = [];
    for (const node of Object.values(layout.nodes)) {
      if (!node.n || node.k === "small" || node.k === "asc_small") continue;
      const en = node.n.toLowerCase();
      const zh = t.term(node.n, "passives").toLowerCase();
      const statsMatch = node.s.some((st) => st.toLowerCase().includes(q));
      if (en.includes(q) || zh.includes(q) || statsMatch) {
        results.push({
          node,
          label: t.term(node.n, "passives"),
          isAlloc: sets.allocated.has(node.id) || sets.anointed.has(node.id),
        });
      }
      if (results.length >= 40) break;
    }
    results.sort((a, b) => Number(b.isAlloc) - Number(a.isAlloc));
    return results.slice(0, 12);
  }, [searchQuery, layout, t, sets.allocated, sets.anointed]);

  // Canvas 主渲染循环
  useEffect(() => {
    if (collapsed || !layout || !plan || !canvasRef.current || !wrapRef.current) return;
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(300, Math.floor(wrap.clientWidth));
    const height = Math.max(260, Math.floor(wrap.clientHeight));

    if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
      canvas.width = width * dpr;
      canvas.height = height * dpr;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 深色星图底板
    const bgGrad = ctx.createRadialGradient(width / 2, height / 2, 40, width / 2, height / 2, Math.max(width, height) * 0.72);
    bgGrad.addColorStop(0, "#121826");
    bgGrad.addColorStop(1, "#090c13");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, width, height);

    // 转换到天赋树世界坐标
    ctx.translate(width / 2, height / 2);
    ctx.scale(camera.scale, camera.scale);
    ctx.translate(-camera.cx, -camera.cy);

    const nodes = layout.nodes;
    const orbitRadii = layout.orbitRadii || [];
    const activeAsc = plan.ascendancy && plan.ascendancy !== "None" ? plan.ascendancy : "";
    const classStartId = plan.classStartId || "";
    const ascStartId = plan.ascendancyStartId || "";

    // 仅渲染当前视口内的元素（带边距）加速缩放帧率
    const halfW = width / (2 * camera.scale) + 1800;
    const halfH = height / (2 * camera.scale) + 1800;
    const viewMinX = camera.cx - halfW;
    const viewMaxX = camera.cx + halfW;
    const viewMinY = camera.cy - halfH;
    const viewMaxY = camera.cy + halfH;

    const inView = (n: PassiveTreeLayoutNode) =>
      n.x >= viewMinX && n.x <= viewMaxX && n.y >= viewMinY && n.y <= viewMaxY;

    // 绘制活跃升华背景光环
    if (activeAsc && layout.ascendancyCenters?.[activeAsc]) {
      const ac = layout.ascendancyCenters[activeAsc];
      const rad = Math.max(ac.w, ac.h) * 0.55;
      const g = ctx.createRadialGradient(ac.x, ac.y, rad * 0.15, ac.x, ac.y, rad);
      g.addColorStop(0, "rgba(245, 185, 82, 0.14)");
      g.addColorStop(1, "rgba(245, 185, 82, 0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(ac.x, ac.y, rad, 0, Math.PI * 2);
      ctx.fill();
    }

    const isNodeKeptOrRoot = (id: string) =>
      id === classStartId ||
      id === ascStartId ||
      sets.asc.has(id) ||
      (effectiveMode === "level" ? sets.keptMain.has(id) : sets.allocated.has(id));

    const isNodeTargetAlloc = (id: string) =>
      id === classStartId || id === ascStartId || sets.allocated.has(id);

    const isNodeMyAlloc = (id: string) =>
      id === classStartId || id === ascStartId || sets.sharedDiff.has(id) || sets.toRefund.has(id);

    // 第 1 层：底层未分配边（半透明网格）
    ctx.lineWidth = Math.max(12, 1.1 / camera.scale);
    ctx.strokeStyle = "rgba(128, 146, 178, 0.16)";
    ctx.setLineDash([]);
    for (let i = 0; i < layout.edges.length; i++) {
      const [a, b, orb] = layout.edges[i];
      const n1 = nodes[a];
      const n2 = nodes[b];
      if (!n1 || !n2) continue;
      if (!inView(n1) && !inView(n2)) continue;
      // 非当前升华的区域进一步淡化
      if ((n1.a && n1.a !== activeAsc) || (n2.a && n2.a !== activeAsc)) {
        continue;
      }
      drawOrbitEdge(ctx, n1, n2, orb, orbitRadii);
    }

    // 第 2 层：高亮已分配连线
    const activeLineWidth = Math.max(26, 2.5 / camera.scale);
    for (let i = 0; i < layout.edges.length; i++) {
      const [a, b, orb] = layout.edges[i];
      const n1 = nodes[a];
      const n2 = nodes[b];
      if (!n1 || !n2) continue;
      if (!inView(n1) && !inView(n2)) continue;

      let stroke: string | null = null;
      let dashed = false;

      if (effectiveMode === "diff") {
        const bothShared =
          (sets.sharedDiff.has(a) || a === classStartId || a === ascStartId) &&
          (sets.sharedDiff.has(b) || b === classStartId || b === ascStartId) &&
          (sets.sharedDiff.has(a) || sets.sharedDiff.has(b));
        const inTargetBoth = isNodeTargetAlloc(a) && isNodeTargetAlloc(b);
        const inMineBoth = isNodeMyAlloc(a) && isNodeMyAlloc(b);
        if (bothShared) {
          stroke = "rgba(232, 194, 108, 0.72)";
        } else if (inTargetBoth && (sets.toAdd.has(a) || sets.toAdd.has(b))) {
          stroke = "#45ca8c";
        } else if (inMineBoth && (sets.toRefund.has(a) || sets.toRefund.has(b))) {
          stroke = "#f06562";
          dashed = true;
        }
      } else {
        const bothTarget = isNodeTargetAlloc(a) && isNodeTargetAlloc(b);
        if (!bothTarget) continue;
        const bothKept = isNodeKeptOrRoot(a) && isNodeKeptOrRoot(b);
        if (effectiveMode === "level" && !bothKept) {
          stroke = "rgba(245, 185, 82, 0.36)";
          dashed = true;
        } else if (sets.asc.has(a) || sets.asc.has(b)) {
          stroke = "#ffb703";
        } else if (sets.ws1.has(a) || sets.ws1.has(b)) {
          stroke = "#5aa9ff";
        } else if (sets.ws2.has(a) || sets.ws2.has(b)) {
          stroke = "#c77dff";
        } else {
          stroke = "#f0c45e";
        }
      }

      if (!stroke) continue;
      ctx.lineWidth = dashed ? activeLineWidth * 0.8 : activeLineWidth;
      ctx.strokeStyle = stroke;
      ctx.setLineDash(dashed ? [80, 65] : []);
      drawOrbitEdge(ctx, n1, n2, orb, orbitRadii);
    }
    ctx.setLineDash([]);

    // 第 3 层：绘制节点（先未点，后已点与关键大点，保证清晰层次）
    const activeNodeIds: PassiveTreeLayoutNode[] = [];
    for (const node of Object.values(nodes)) {
      if (!inView(node)) continue;
      if (node.a && node.a !== activeAsc && !sets.allocated.has(node.id)) continue;
      const isSpecial =
        sets.allocated.has(node.id) ||
        sets.anointed.has(node.id) ||
        sets.toRefund.has(node.id) ||
        node.id === classStartId ||
        node.id === ascStartId ||
        node.id === highlightNodeId;
      if (isSpecial) {
        activeNodeIds.push(node);
        continue;
      }
      const r = NODE_WORLD_RADIUS[node.k] || 46;
      ctx.fillStyle =
        node.k === "keystone"
          ? "rgba(180, 155, 105, 0.25)"
          : node.k === "notable" || node.k === "jewel"
          ? "rgba(130, 148, 178, 0.22)"
          : "rgba(110, 126, 154, 0.16)";
      ctx.beginPath();
      if (node.k === "jewel") {
        drawDiamond(ctx, node.x, node.y, r * 0.85);
      } else {
        ctx.arc(node.x, node.y, r * 0.72, 0, Math.PI * 2);
      }
      ctx.fill();
    }

    // 第 4 层：高亮已分配 / 裁剪 / 涂油 / 差异 / 珠宝孔节点
    for (const node of activeNodeIds) {
      const id = node.id;
      const r = NODE_WORLD_RADIUS[node.k] || 46;
      const jewel = plan.jewelsByNode[id];
      const isAnointed = sets.anointed.has(id);
      const isPruned = effectiveMode === "level" && sets.pruned.has(id);

      let fill = "#f0c45e";
      let border = "#fff3cc";
      let alpha = 1.0;

      if (id === classStartId || id === ascStartId) {
        fill = "#ffffff";
        border = "#f8de96";
      } else if (effectiveMode === "diff") {
        if (sets.toAdd.has(id)) {
          fill = "#45ca8c";
          border = "#d1fae5";
        } else if (sets.toRefund.has(id)) {
          fill = "#f06562";
          border = "#fee2e2";
        } else {
          fill = "#e5be6b";
          border = "#fff3cc";
          alpha = 0.82;
        }
      } else if (isPruned) {
        fill = "rgba(245, 185, 82, 0.22)";
        border = "#f5b952";
        alpha = 0.85;
      } else if (isAnointed && !sets.allocated.has(id)) {
        fill = "#14b8a6";
        border = "#99f6e4";
      } else if (sets.asc.has(id)) {
        fill = "#ffb703";
        border = "#fff8dc";
      } else if (sets.ws1.has(id)) {
        fill = "#5aa9ff";
        border = "#dbeafe";
      } else if (sets.ws2.has(id)) {
        fill = "#c77dff";
        border = "#f3e8ff";
      }

      if (jewel && !isPruned && effectiveMode !== "diff") {
        const rar = (jewel.rarity || "").toUpperCase();
        fill = rar === "UNIQUE" ? "#e08241" : rar === "MAGIC" ? "#66abff" : "#f5c542";
        border = "#ffffff";
      }

      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.fillStyle = fill;
      ctx.strokeStyle = border;
      ctx.lineWidth = Math.max(16, 1.8 / camera.scale);

      if (node.k === "keystone") {
        drawHexagon(ctx, node.x, node.y, r * 1.12);
        ctx.fill();
        ctx.stroke();
      } else if (node.k === "jewel") {
        drawDiamond(ctx, node.x, node.y, r * 1.15);
        ctx.fill();
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
        ctx.fill();
        if (isPruned) {
          ctx.setLineDash([45, 35]);
        }
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // 涂油节点专属青色外环
      if (isAnointed) {
        ctx.strokeStyle = "#2dd4bf";
        ctx.lineWidth = Math.max(22, 2.4 / camera.scale);
        ctx.beginPath();
        ctx.arc(node.x, node.y, r * 1.48, 0, Math.PI * 2);
        ctx.stroke();
      }

      // 搜索/点击定位光圈
      if (id === highlightNodeId) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = Math.max(28, 3.0 / camera.scale);
        ctx.beginPath();
        ctx.arc(node.x, node.y, r * 1.9, 0, Math.PI * 2);
        ctx.stroke();
      }

      // 放大到足够层级时在关键大点旁显示可读标签
      const showLabel =
        node.n &&
        ((node.k === "keystone" && camera.scale >= 0.024) ||
          (isAnointed && camera.scale >= 0.024) ||
          ((node.k === "ascendancy" || node.k === "jewel") && camera.scale >= 0.042) ||
          (node.k === "notable" && camera.scale >= 0.062) ||
          id === highlightNodeId);
      if (showLabel) {
        const fontPx = Math.max(10, Math.min(13, 11.5)) / camera.scale;
        ctx.font = `600 ${fontPx}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        const label =
          t.mode === "en"
            ? node.n
            : t.term(node.n, "passives").split(" (")[0];
        const textY = node.y + r * 1.25;
        ctx.lineWidth = fontPx * 0.28;
        ctx.strokeStyle = "rgba(9, 12, 19, 0.9)";
        ctx.strokeText(label, node.x, textY);
        ctx.fillStyle = isPruned ? "#f5b952" : isAnointed ? "#5eead4" : "#f8fafc";
        ctx.fillText(label, node.x, textY);
      }

      ctx.restore();
    }

    ctx.restore();
  }, [collapsed, layout, plan, camera, sets, effectiveMode, highlightNodeId, t]);

  // 鼠标滚轮缩放（以光标位置为中心）
  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    if (!canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const cur = cameraRef.current;
    const wx = (mx - rect.width / 2) / cur.scale + cur.cx;
    const wy = (my - rect.height / 2) / cur.scale + cur.cy;
    const zoomFactor = e.deltaY < 0 ? 1.18 : 1 / 1.18;
    const nextScale = Math.max(0.012, Math.min(0.24, cur.scale * zoomFactor));
    const nextCx = wx - (mx - rect.width / 2) / nextScale;
    const nextCy = wy - (my - rect.height / 2) / nextScale;
    setCamera({ cx: nextCx, cy: nextCy, scale: nextScale });
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    draggingRef.current = { active: true, lastX: e.clientX, lastY: e.clientY };
    (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
  }, []);

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (draggingRef.current.active) {
        const dx = e.clientX - draggingRef.current.lastX;
        const dy = e.clientY - draggingRef.current.lastY;
        draggingRef.current.lastX = e.clientX;
        draggingRef.current.lastY = e.clientY;
        setCamera((prev) => ({
          ...prev,
          cx: prev.cx - dx / prev.scale,
          cy: prev.cy - dy / prev.scale,
        }));
        setHover(null);
        return;
      }

      if (!layout || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const cur = cameraRef.current;
      const wx = (mx - rect.width / 2) / cur.scale + cur.cx;
      const wy = (my - rect.height / 2) / cur.scale + cur.cy;
      const hitPad = Math.max(95, 13 / cur.scale);
      let best: PassiveTreeLayoutNode | null = null;
      let bestDistSq = hitPad * hitPad;

      for (const node of Object.values(layout.nodes)) {
        const dx = node.x - wx;
        const dy = node.y - wy;
        const d2 = dx * dx + dy * dy;
        const nr = (NODE_WORLD_RADIUS[node.k] || 46) + hitPad * 0.45;
        if (d2 <= nr * nr && d2 < bestDistSq) {
          bestDistSq = d2;
          best = node;
        }
      }
      if (best) {
        setHover({ node: best, clientX: e.clientX, clientY: e.clientY });
      } else {
        setHover(null);
      }
    },
    [layout]
  );

  const handlePointerUp = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    if (draggingRef.current.active) {
      draggingRef.current.active = false;
      try {
        (e.currentTarget as HTMLCanvasElement).releasePointerCapture(e.pointerId);
      } catch {
        // ignore
      }
    }
  }, []);

  const handleCopyCode = async (kind: "full" | "pruned") => {
    if (!plan) return;
    const text = kind === "pruned" ? plan.prunedPobCode : plan.targetPobCode;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(kind);
      onNotify?.(
        kind === "pruned"
          ? `已复制 Lv.${plan.characterLevel} 裁剪后的连通 PoB2 分享码，可直接导入 PoB2！`
          : "已复制目标完整 PoB2 分享码！"
      );
      window.setTimeout(() => setCopiedKey(null), 1800);
    } catch {
      onNotify?.("剪贴板复制失败，请检查系统剪贴板权限。");
    }
  };

  if (!targetPobCode?.trim()) return null;

  const hoverStatusBadge = (node: PassiveTreeLayoutNode): { label: string; cls: string } => {
    const id = node.id;
    if (effectiveMode === "diff") {
      if (sets.toAdd.has(id)) return { label: "+ 待补点", cls: "green" };
      if (sets.toRefund.has(id)) return { label: "- 可洗掉", cls: "red" };
      if (sets.sharedDiff.has(id)) return { label: "✓ 已对齐", cls: "gold" };
    }
    if (effectiveMode === "level" && sets.pruned.has(id)) return { label: `Lv.${plan?.characterLevel} 建议暂缓`, cls: "amber" };
    if (sets.anointed.has(id)) return { label: "💧 项链涂油", cls: "blue" };
    if (sets.asc.has(id)) return { label: `👑 升华 · ${node.a || plan?.ascendancy || ""}`, cls: "gold" };
    if (sets.ws1.has(id)) return { label: "⚔️ 武器组 1 天赋", cls: "blue" };
    if (sets.ws2.has(id)) return { label: "🗡️ 武器组 2 天赋", cls: "purple" };
    if (sets.allocated.has(id)) return { label: "✓ 目标已点", cls: "gold" };
    return { label: "未分配", cls: "" };
  };

  const panelBody = (
    <div className={`passive-tree-panel ${fullscreen ? "is-fullscreen" : ""}`}>
      <div className="row between" style={{ flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <strong style={{ color: "var(--gold-strong)", fontSize: 13 }}>
            🌳 抄作业交互式天赋图
          </strong>
          {plan && (
            <>
              <span className="badge gold">
                {t.term(plan.ascendancy !== "None" ? plan.ascendancy : plan.className, plan.ascendancy !== "None" ? "ascendancies" : "classes")}
              </span>
              <span className="badge">
                主树 {effectiveMode === "level" && plan.missingPoints > 0 ? `${plan.points.keptMain}/${plan.points.fullMain}` : plan.points.fullMain} 点
              </span>
              {plan.points.ascendancy > 0 && <span className="badge amber">升华 {plan.points.ascendancy} 点</span>}
              {(plan.points.ws1 > 0 || plan.points.ws2 > 0) && (
                <span className="badge blue">
                  双武器组 WS1:{plan.points.ws1} / WS2:{plan.points.ws2}
                </span>
              )}
              {plan.missingPoints > 0 && (
                <span className="badge purple">
                  Lv.{plan.characterLevel} 少 {plan.missingPoints} 点（已自动拓扑裁剪）
                </span>
              )}
            </>
          )}
        </div>

        <div className="row" style={{ gap: 6 }}>
          {plan && plan.missingPoints > 0 && (
            <button
              type="button"
              className="btn sm primary"
              onClick={() => void handleCopyCode("pruned")}
              title={`复制按 Lv.${plan.characterLevel} 自动拓扑裁剪后的 PoB2 码`}
            >
              {copiedKey === "pruned" ? "✓ 已复制裁剪码" : `📋 复制 Lv.${plan.characterLevel} 裁剪 PoB 码`}
            </button>
          )}
          {plan && (
            <button
              type="button"
              className="btn sm"
              onClick={() => void handleCopyCode("full")}
            >
              {copiedKey === "full" ? "✓ 已复制" : "📋 复制完整码"}
            </button>
          )}
          <button
            type="button"
            className="btn sm"
            onClick={() => setFullscreen((v) => !v)}
          >
            {fullscreen ? "✕ 退出全屏" : "⛶ 全屏大图"}
          </button>
          {!fullscreen && (
            <button
              type="button"
              className="btn sm"
              onClick={() => setCollapsed((v) => !v)}
            >
              {collapsed ? "展开天赋图" : "收起"}
            </button>
          )}
        </div>
      </div>

      {!collapsed && (
        <>
          {/* 模式切换 + 快捷聚焦 + 搜索 */}
          <div className="row between" style={{ flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
            <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
              {plan && plan.missingPoints > 0 && (
                <button
                  type="button"
                  className={`btn sm ${effectiveMode === "level" ? "primary" : ""}`}
                  onClick={() => setViewMode("level")}
                >
                  🎚️ Lv.{plan.characterLevel} 裁剪路线
                </button>
              )}
              <button
                type="button"
                className={`btn sm ${effectiveMode === "full" ? "primary" : ""}`}
                onClick={() => setViewMode("full")}
              >
                🎯 目标完整树 (Lv.{plan?.targetLevel ?? 100})
              </button>
              {plan?.diff && (
                <button
                  type="button"
                  className={`btn sm ${effectiveMode === "diff" ? "primary" : ""}`}
                  onClick={() => setViewMode("diff")}
                >
                  🔀 对照我的 BD (+{plan.diff.toAddIds.length} / -{plan.diff.toRefundIds.length})
                </button>
              )}
            </div>

            <div className="row" style={{ gap: 6, flexWrap: "wrap", position: "relative" }}>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="🔍 搜索大点/基石 (中/英文)..."
                className="passive-tree-search"
              />
              {searchMatches.length > 0 && (
                <div className="passive-tree-search-dropdown">
                  {searchMatches.map((m) => (
                    <button
                      key={m.node.id}
                      type="button"
                      className="passive-tree-search-item"
                      onClick={() => {
                        focusNodeById(m.node.id);
                        setSearchQuery("");
                      }}
                    >
                      <span>{m.label}</span>
                      {m.isAlloc && <span className="badge gold" style={{ fontSize: 10 }}>已点</span>}
                    </button>
                  ))}
                </div>
              )}
              <button
                type="button"
                className="btn sm"
                onClick={() => {
                  if (!plan) return;
                  const ids = [...plan.mainAllocatedIds];
                  if (plan.classStartId) ids.push(plan.classStartId);
                  fitNodesInView(ids, 0.14);
                }}
              >
                🎯 居中主树
              </button>
              {plan && plan.ascendancyAllocatedIds.length > 0 && (
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => {
                    const ids = [...plan.ascendancyAllocatedIds];
                    if (plan.ascendancyStartId) ids.push(plan.ascendancyStartId);
                    fitNodesInView(ids, 0.22);
                  }}
                >
                  👑 聚焦升华
                </button>
              )}
              <button
                type="button"
                className="btn sm"
                onClick={() => setCamera((c) => ({ ...c, scale: Math.min(0.24, c.scale * 1.28) }))}
                title="放大"
              >
                ＋
              </button>
              <button
                type="button"
                className="btn sm"
                onClick={() => setCamera((c) => ({ ...c, scale: Math.max(0.012, c.scale / 1.28) }))}
                title="缩小"
              >
                －
              </button>
            </div>
          </div>

          {error && (
            <div className="banner error" style={{ marginBottom: 8 }}>
              <span>{error}</span>
            </div>
          )}

          {/* Canvas 画布容器 */}
          <div
            ref={wrapRef}
            className="passive-tree-canvas-wrap"
            style={{ height: fullscreen ? "calc(100vh - 210px)" : 430 }}
          >
            {loading && !layout && (
              <div className="passive-tree-overlay">正在加载 PoE2 天赋树几何数据...</div>
            )}
            <canvas
              ref={canvasRef}
              className="passive-tree-canvas"
              onWheel={handleWheel}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerLeave={() => {
                draggingRef.current.active = false;
                setHover(null);
              }}
            />

            {/* 图例浮层 */}
            <div className="passive-tree-legend">
              {effectiveMode === "diff" ? (
                <>
                  <span><i style={{ background: "#45ca8c" }} /> +待补点 ({plan?.diff?.toAddIds.length ?? 0})</span>
                  <span><i style={{ background: "#f06562" }} /> -可洗掉 ({plan?.diff?.toRefundIds.length ?? 0})</span>
                  <span><i style={{ background: "#e5be6b" }} /> 已对齐 ({plan?.diff?.sharedIds.length ?? 0})</span>
                </>
              ) : (
                <>
                  <span><i style={{ background: "#f0c45e" }} /> 主树路线</span>
                  <span><i style={{ background: "#5aa9ff" }} /> 武器组 1</span>
                  <span><i style={{ background: "#c77dff" }} /> 武器组 2</span>
                  <span><i style={{ background: "#ffb703" }} /> 升华</span>
                  <span><i style={{ background: "#2dd4bf" }} /> 涂油</span>
                  {effectiveMode === "level" && plan && plan.missingPoints > 0 && (
                    <span><i style={{ background: "transparent", border: "1.5px dashed #f5b952" }} /> 满级再补 ({plan.points.prunedMain}点)</span>
                  )}
                </>
              )}
              <span className="muted" style={{ marginLeft: 6 }}>滚轮缩放 · 拖拽平移 · 悬停看中英词缀</span>
            </div>
          </div>

          {/* 快捷点击定位条：基石 / 升华 / 涂油 / 珠宝孔 / 建议暂缓大点 */}
          {plan && (
            <div className="passive-tree-chips-grid">
              {plan.keystones.length > 0 && (
                <div className="passive-tree-chip-row">
                  <span className="muted" style={{ fontSize: 11, minWidth: 68 }}>💎 基石：</span>
                  <div className="chips">
                    {plan.keystones.map((ks) => (
                      <button
                        key={ks.id}
                        type="button"
                        className={`badge gold passive-jump-chip ${highlightNodeId === ks.id ? "active" : ""}`}
                        onClick={() => focusNodeById(ks.id)}
                      >
                        {t.term(ks.name, "passives")}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {plan.anointedNodes.length > 0 && (
                <div className="passive-tree-chip-row">
                  <span className="muted" style={{ fontSize: 11, minWidth: 68 }}>💧 涂油：</span>
                  <div className="chips">
                    {plan.anointedNodes.map((an) => (
                      <button
                        key={an.id}
                        type="button"
                        className={`badge blue passive-jump-chip ${highlightNodeId === an.id ? "active" : ""}`}
                        onClick={() => focusNodeById(an.id)}
                      >
                        {t.term(an.name, "passives")}（{an.source}）
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {Object.keys(plan.jewelsByNode).length > 0 && (
                <div className="passive-tree-chip-row">
                  <span className="muted" style={{ fontSize: 11, minWidth: 68 }}>💠 珠宝孔：</span>
                  <div className="chips">
                    {Object.values(plan.jewelsByNode).map((jw) => (
                      <button
                        key={jw.nodeId}
                        type="button"
                        className={`badge ${jw.rarity.toUpperCase() === "UNIQUE" ? "unique" : "purple"} passive-jump-chip ${highlightNodeId === jw.nodeId ? "active" : ""}`}
                        onClick={() => focusNodeById(jw.nodeId)}
                      >
                        {t.itemName(jw.name, jw.baseType)}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {effectiveMode === "level" && plan.prunedNotables.length > 0 && (
                <div className="passive-tree-chip-row">
                  <span className="muted" style={{ fontSize: 11, minWidth: 68, color: "var(--amber)" }}>
                    ✂️ Lv.{plan.characterLevel} 暂缓：
                  </span>
                  <div className="chips">
                    {plan.prunedNotables.map((pn) => (
                      <button
                        key={pn.id}
                        type="button"
                        className={`badge amber passive-jump-chip ${highlightNodeId === pn.id ? "active" : ""}`}
                        onClick={() => focusNodeById(pn.id)}
                        title="点击在图中定位该暂缓大点"
                      >
                        {t.term(pn.name, "passives")}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* 悬停节点双语详情浮窗 */}
      {hover && plan && (
        <div
          className="passive-tree-tooltip"
          style={{
            left: Math.min(window.innerWidth - 340, Math.max(12, hover.clientX + 16)),
            top: Math.min(window.innerHeight - 240, Math.max(12, hover.clientY + 14)),
          }}
        >
          <div className="row between" style={{ gap: 8, marginBottom: 4 }}>
            <strong style={{ color: "var(--gold-strong)", fontSize: 12.5 }}>
              {hover.node.n ? t.term(hover.node.n, "passives") : `节点 #${hover.node.id}`}
            </strong>
            <span className={`badge ${hoverStatusBadge(hover.node).cls}`} style={{ fontSize: 10 }}>
              {hoverStatusBadge(hover.node).label}
            </span>
          </div>
          {hover.node.s.length > 0 && (
            <ul className="passive-tree-tip-stats">
              {hover.node.s.slice(0, 8).map((st, idx) => (
                <li key={idx}>{t.mod(st)}</li>
              ))}
            </ul>
          )}
          {plan.jewelsByNode[hover.node.id] && (
            <div className="passive-tree-tip-jewel">
              <div style={{ fontWeight: 700, color: "var(--unique)", marginBottom: 2 }}>
                💠 已镶嵌珠宝：{t.itemName(
                  plan.jewelsByNode[hover.node.id].name,
                  plan.jewelsByNode[hover.node.id].baseType
                )}
              </div>
              <ul className="passive-tree-tip-stats">
                {plan.jewelsByNode[hover.node.id].mods.slice(0, 6).map((m, i) => (
                  <li key={i}>{t.mod(m)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );

  if (fullscreen) {
    return (
      <div className="passive-tree-fullscreen-backdrop" onClick={() => setFullscreen(false)}>
        <div className="passive-tree-fullscreen-modal" onClick={(e) => e.stopPropagation()}>
          {panelBody}
        </div>
      </div>
    );
  }

  return panelBody;
};

export const PassiveTreePanel = React.memo(PassiveTreePanelInner);
